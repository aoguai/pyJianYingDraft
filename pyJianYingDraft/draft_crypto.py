"""Direct Jianying draft JSON crypto support.

This module mirrors the relevant parts of ``jy-draftc`` in Python.  It calls
Jianying's local ``videoeditor.dll`` through ctypes, so the low-level
``JianyingDraftCrypto`` class is intentionally unsafe: an ABI mismatch can
crash the current Python process.  Prefer the high-level functions, which run
the direct DLL call in a child Python process by default.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union


PathLike = Union[str, os.PathLike]

K_DECRYPT_SYMBOL = (
    "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z"
)
K_ENCRYPT_SYMBOL = (
    "?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@@Z"
)
K_ENABLE_SYMBOL = "?enable@EncryptUtils@lvve@@QEAAX_N@Z"

LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_APPLICATION_DIR = 0x00000200
LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class DraftCryptoError(RuntimeError):
    """Base error for Jianying draft crypto operations."""


class DraftCryptoProcessError(DraftCryptoError):
    """Raised when the isolated child process fails."""

    def __init__(
        self,
        command: Sequence[str],
        returncode: Optional[int],
        stdout: Optional[str],
        stderr: Optional[str],
        message: Optional[str] = None,
    ):
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        if message is None:
            message = "isolated draft crypto process failed"
        detail = f"{message}: returncode={returncode}, command={' '.join(self.command)}"
        if self.stderr:
            detail += f", stderr={self.stderr.strip()}"
        super().__init__(detail)


class MsvcStringData(ctypes.Union):
    """The data union used by MSVC std::string's small string layout."""

    _fields_ = [
        ("small", ctypes.c_char * 16),
        ("ptr", ctypes.c_void_p),
    ]


class MsvcString(ctypes.Structure):
    """A minimal MSVC std::string-compatible layout used by videoeditor.dll."""

    _fields_ = [
        ("data", MsvcStringData),
        ("size", ctypes.c_ulonglong),
        ("capacity", ctypes.c_ulonglong),
    ]


class MsvcStringArg:
    """Prepare a bytes value as an MSVC std::string argument.

    The backing buffer for long strings is kept alive by this object.  Callers
    must keep the instance alive until the DLL call returns.
    """

    def __init__(self, data: bytes):
        if not isinstance(data, bytes):
            raise TypeError("MsvcStringArg data must be bytes")

        self._buffer = None
        self.value = MsvcString()
        self.value.size = len(data)

        if len(data) < 16:
            self.value.capacity = 15
            ctypes.memset(ctypes.addressof(self.value.data), 0, 16)
            if data:
                ctypes.memmove(ctypes.addressof(self.value.data), data, len(data))
            return

        self._buffer = ctypes.create_string_buffer(data)
        self.value.capacity = len(data)
        self.value.data.ptr = ctypes.cast(self._buffer, ctypes.c_void_p).value


def _take_msvc_string(value: MsvcString) -> bytes:
    """Read bytes from an MSVC-string-shaped return value."""

    size = int(value.size)
    if size > (1 << 32):
        raise DraftCryptoError(f"unexpected MsvcString size: {size}")

    if value.capacity < 16:
        return ctypes.string_at(ctypes.addressof(value.data), size)

    if not value.data.ptr:
        raise DraftCryptoError("MsvcString heap pointer is null")
    return ctypes.string_at(value.data.ptr, size)


def _default_output_path(input_path: PathLike, *, encrypt: bool) -> Path:
    suffix = ".enc.json" if encrypt else ".dec.json"
    return Path(f"{input_path}{suffix}")


def _read_bytes(path: PathLike) -> bytes:
    with open(path, "rb") as file_obj:
        return file_obj.read()


def _write_bytes_atomic(path: PathLike, data: bytes) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None

    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=str(output_path.parent),
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as file_obj:
            temp_path = Path(file_obj.name)
            file_obj.write(data)
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return output_path


def _strip_utf8_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def _env_unquote(text: str) -> str:
    text = text.strip()
    if len(text) < 2:
        return text
    if (text[0], text[-1]) in {('"', '"'), ("'", "'")}:
        return text[1:-1]
    return text


def _parse_install_dir_from_env_text(env_text: str) -> Optional[Path]:
    for raw_line in env_text.splitlines():
        line = _strip_utf8_bom(raw_line).strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "JY_INSTALL_DIR":
            continue
        value = _env_unquote(value)
        return Path(value) if value else None
    return None


def _env_file_candidates() -> Iterable[Path]:
    yield Path.cwd() / ".env"
    yield Path(__file__).resolve().parent / ".env"


def _resolve_install_dir(jy_install_dir: Optional[PathLike]) -> Path:
    if jy_install_dir:
        return _validate_install_dir(Path(jy_install_dir))

    env_value = os.environ.get("JY_INSTALL_DIR")
    if env_value:
        return _validate_install_dir(Path(env_value))

    for env_path in _env_file_candidates():
        if not env_path.exists():
            continue
        install_dir = _parse_install_dir_from_env_text(env_path.read_text(encoding="utf-8-sig"))
        if install_dir is not None:
            return _validate_install_dir(install_dir)

    raise DraftCryptoError(
        "JY_INSTALL_DIR is required; pass jy_install_dir, set the environment variable, "
        "or create a .env file"
    )


def _validate_install_dir(install_dir: Path) -> Path:
    install_dir = install_dir.expanduser().resolve()
    if not install_dir.exists():
        raise DraftCryptoError(f"JY_INSTALL_DIR does not exist: {install_dir}")
    if not (install_dir / "videoeditor.dll").exists():
        raise DraftCryptoError(f"videoeditor.dll not found under JY_INSTALL_DIR: {install_dir}")
    return install_dir


def _ensure_supported_runtime() -> None:
    if sys.platform != "win32":
        raise DraftCryptoError("Jianying draft crypto only supports Windows")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise DraftCryptoError("Jianying draft crypto requires 64-bit Python")


class JianyingDraftCrypto:
    """Unsafe in-process caller for Jianying's local videoeditor.dll.

    This class directly calls C++ ABI exports from ``videoeditor.dll``.  Use it
    only in an isolated child process or for local diagnostics.  The high-level
    module functions use subprocess isolation by default.
    """

    def __init__(self, jy_install_dir: Optional[PathLike] = None, *, debug: bool = False):
        _ensure_supported_runtime()
        self.install_dir = _resolve_install_dir(jy_install_dir)
        self.debug = debug
        self._dll_cookie = None
        self._dll = self._load_videoeditor()
        self._decrypt = self._bind_decrypt()
        self._encrypt = self._bind_encrypt()
        self._enable = self._bind_enable()

    def decrypt_bytes(self, data: bytes) -> bytes:
        if not isinstance(data, bytes):
            raise TypeError("decrypt_bytes data must be bytes")

        in_arg = MsvcStringArg(data)
        param_arg = MsvcStringArg(b"{}")
        out = MsvcString()
        ok = ctypes.c_bool(False)
        self._decrypt(
            None,
            ctypes.byref(out),
            ctypes.byref(in_arg.value),
            ctypes.byref(param_arg.value),
            ctypes.byref(ok),
        )
        result = _take_msvc_string(out)
        if not ok.value or not result:
            raise DraftCryptoError(f"decrypt failed: ok_flag={ok.value}, output_len={len(result)}")
        return result

    def encrypt_bytes(self, data: bytes, *, validate_roundtrip: bool = True) -> bytes:
        if not isinstance(data, bytes):
            raise TypeError("encrypt_bytes data must be bytes")

        self._enable(None, True)
        in_arg = MsvcStringArg(data)
        out = MsvcString()
        self._encrypt(None, ctypes.byref(out), ctypes.byref(in_arg.value))
        result = _take_msvc_string(out)
        if not result:
            raise DraftCryptoError("encrypt failed: output_len=0")

        if validate_roundtrip:
            roundtrip = self.decrypt_bytes(result)
            if roundtrip != data:
                raise DraftCryptoError(
                    "encrypt validation failed: "
                    f"encrypted_len={len(result)}, roundtrip_len={len(roundtrip)}"
                )
        return result

    def decrypt_file(self, input_path: PathLike, output_path: Optional[PathLike] = None) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path) if output_path is not None else _default_output_path(input_path, encrypt=False)
        return _write_bytes_atomic(output_path, self.decrypt_bytes(_read_bytes(input_path)))

    def encrypt_file(
        self,
        input_path: PathLike,
        output_path: Optional[PathLike] = None,
        *,
        validate_roundtrip: bool = True,
    ) -> Path:
        input_path = Path(input_path)
        output_path = Path(output_path) if output_path is not None else _default_output_path(input_path, encrypt=True)
        encrypted = self.encrypt_bytes(_read_bytes(input_path), validate_roundtrip=validate_roundtrip)
        return _write_bytes_atomic(output_path, encrypted)

    def _load_videoeditor(self):
        dll_path = self.install_dir / "videoeditor.dll"
        flags = (
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
            | LOAD_LIBRARY_SEARCH_APPLICATION_DIR
            | LOAD_LIBRARY_SEARCH_SYSTEM32
            | LOAD_LIBRARY_SEARCH_USER_DIRS
        )

        old_path = os.environ.get("PATH", "")
        old_cwd = Path.cwd()
        os.environ["PATH"] = f"{self.install_dir}{os.pathsep}{old_path}" if old_path else str(self.install_dir)
        if hasattr(os, "add_dll_directory"):
            self._dll_cookie = os.add_dll_directory(str(self.install_dir))

        try:
            os.chdir(self.install_dir)
            try:
                return ctypes.WinDLL(str(dll_path), winmode=flags, use_last_error=True)
            except TypeError:
                return ctypes.WinDLL(str(dll_path), use_last_error=True)
        except OSError as exc:
            raise DraftCryptoError(f"LoadLibrary(videoeditor.dll) failed: {exc}") from exc
        finally:
            os.chdir(old_cwd)
            os.environ["PATH"] = old_path

    def _bind_decrypt(self):
        prototype = _WINFUNCTYPE(
            ctypes.POINTER(MsvcString),
            ctypes.c_void_p,
            ctypes.POINTER(MsvcString),
            ctypes.POINTER(MsvcString),
            ctypes.POINTER(MsvcString),
            ctypes.POINTER(ctypes.c_bool),
        )
        return self._bind_symbol(K_DECRYPT_SYMBOL, prototype, "decrypt")

    def _bind_encrypt(self):
        prototype = _WINFUNCTYPE(
            ctypes.POINTER(MsvcString),
            ctypes.c_void_p,
            ctypes.POINTER(MsvcString),
            ctypes.POINTER(MsvcString),
        )
        return self._bind_symbol(K_ENCRYPT_SYMBOL, prototype, "encrypt")

    def _bind_enable(self):
        prototype = _WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool)
        return self._bind_symbol(K_ENABLE_SYMBOL, prototype, "enable")

    def _bind_symbol(self, symbol: str, prototype, label: str):
        try:
            raw = self._dll[symbol]
        except AttributeError as exc:
            raise DraftCryptoError(f"{label} export missing") from exc
        address = ctypes.cast(raw, ctypes.c_void_p).value
        if not address:
            raise DraftCryptoError(f"{label} export address is null")
        return prototype(address)


def _run_isolated(
    args: Sequence[str],
    *,
    jy_install_dir: Optional[PathLike],
    timeout: Optional[float],
) -> subprocess.CompletedProcess:
    command = [sys.executable, str(Path(__file__).resolve()), *args]
    env = os.environ.copy()
    if jy_install_dir is not None:
        env["JY_INSTALL_DIR"] = str(jy_install_dir)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DraftCryptoProcessError(command, None, exc.stdout, exc.stderr, "isolated draft crypto timed out") from exc

    if completed.returncode != 0:
        raise DraftCryptoProcessError(command, completed.returncode, completed.stdout, completed.stderr)
    return completed


def decrypt_draft_file(
    input_path: PathLike,
    output_path: Optional[PathLike] = None,
    *,
    jy_install_dir: Optional[PathLike] = None,
    timeout: Optional[float] = 120,
    isolated: bool = True,
) -> Path:
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path is not None else _default_output_path(input_path, encrypt=False)

    if isolated:
        _run_isolated(
            ["--dec", str(input_path), str(output_path)],
            jy_install_dir=jy_install_dir,
            timeout=timeout,
        )
        return output_path

    crypto = JianyingDraftCrypto(jy_install_dir)
    return crypto.decrypt_file(input_path, output_path)


def encrypt_draft_file(
    input_path: PathLike,
    output_path: Optional[PathLike] = None,
    *,
    jy_install_dir: Optional[PathLike] = None,
    timeout: Optional[float] = 120,
    isolated: bool = True,
    validate_roundtrip: bool = True,
) -> Path:
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path is not None else _default_output_path(input_path, encrypt=True)

    if isolated:
        args = ["--enc", str(input_path), str(output_path)]
        if not validate_roundtrip:
            args.append("--no-roundtrip")
        _run_isolated(args, jy_install_dir=jy_install_dir, timeout=timeout)
        return output_path

    crypto = JianyingDraftCrypto(jy_install_dir)
    return crypto.encrypt_file(input_path, output_path, validate_roundtrip=validate_roundtrip)


def decrypt_draft_bytes(
    data: bytes,
    *,
    jy_install_dir: Optional[PathLike] = None,
    timeout: Optional[float] = 120,
    isolated: bool = True,
) -> bytes:
    if not isolated:
        return JianyingDraftCrypto(jy_install_dir).decrypt_bytes(data)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.bin"
        output_path = Path(temp_dir) / "output.bin"
        input_path.write_bytes(data)
        decrypt_draft_file(input_path, output_path, jy_install_dir=jy_install_dir, timeout=timeout, isolated=True)
        return output_path.read_bytes()


def encrypt_draft_bytes(
    data: bytes,
    *,
    jy_install_dir: Optional[PathLike] = None,
    timeout: Optional[float] = 120,
    isolated: bool = True,
    validate_roundtrip: bool = True,
) -> bytes:
    if not isolated:
        return JianyingDraftCrypto(jy_install_dir).encrypt_bytes(data, validate_roundtrip=validate_roundtrip)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.bin"
        output_path = Path(temp_dir) / "output.bin"
        input_path.write_bytes(data)
        encrypt_draft_file(
            input_path,
            output_path,
            jy_install_dir=jy_install_dir,
            timeout=timeout,
            isolated=True,
            validate_roundtrip=validate_roundtrip,
        )
        return output_path.read_bytes()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decrypt or encrypt Jianying draft JSON files.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dec", "-d", action="store_true", help="decrypt an encrypted draft JSON file")
    mode.add_argument("--enc", "-e", action="store_true", help="encrypt a plaintext draft JSON file")
    parser.add_argument("input_path")
    parser.add_argument("output_path", nargs="?")
    parser.add_argument("--jy-install-dir", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-roundtrip", action="store_true", help="skip encrypt roundtrip validation")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    crypto = JianyingDraftCrypto(ns.jy_install_dir, debug=ns.debug)

    if ns.dec:
        output_path = crypto.decrypt_file(ns.input_path, ns.output_path)
    else:
        output_path = crypto.encrypt_file(
            ns.input_path,
            ns.output_path,
            validate_roundtrip=not ns.no_roundtrip,
        )

    print(f"ok output={output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DraftCryptoError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
