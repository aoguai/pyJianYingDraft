"""Private reversible draft-content codecs and safe persistence helpers."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple

from .draft_crypto import (
    DraftCryptoConfig,
    DraftCryptoError,
    PathLike,
    decrypt_draft_bytes,
    encrypt_draft_bytes,
)


class DraftJsonError(DraftCryptoError):
    """Base error for private draft JSON codec operations."""


class DraftJsonDecodeError(DraftJsonError):
    """Raised when a draft cannot be decoded as a JSON object."""


class DraftContentCodec(Protocol):
    """A reversible private format for ``draft_content.json``-like files."""

    def decode(self, raw_data: bytes) -> Dict[str, Any]:
        """Decode raw file bytes into a JSON object."""

    def encode(self, serialized_json: str) -> bytes:
        """Encode serialized JSON before it is persisted."""


class JianyingDraftCryptoCodec:
    """Private adapter around the existing isolated Jianying DLL bridge."""

    def __init__(self, config: Optional[DraftCryptoConfig] = None) -> None:
        self.config = config if config is not None else DraftCryptoConfig()

    def decode(self, raw_data: bytes) -> Dict[str, Any]:
        try:
            plaintext = decrypt_draft_bytes(
                raw_data,
                jy_install_dir=self.config.jy_install_dir,
                timeout=self.config.timeout,
                isolated=self.config.isolated,
            )
        except DraftCryptoError as exc:
            raise DraftJsonDecodeError(
                "draft JSON is not valid plaintext JSON and Jianying crypto decoding failed. "
                "Configure JY_INSTALL_DIR before using JianyingDraftCryptoCodec."
            ) from exc

        try:
            decoded = _loads_json(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DraftJsonDecodeError("decrypted draft JSON is invalid") from exc
        if not isinstance(decoded, dict):
            raise DraftJsonDecodeError("decrypted draft JSON top-level value is not an object")
        return decoded

    def encode(self, serialized_json: str) -> bytes:
        try:
            return encrypt_draft_bytes(
                serialized_json.encode("utf-8"),
                jy_install_dir=self.config.jy_install_dir,
                timeout=self.config.timeout,
                isolated=self.config.isolated,
                validate_roundtrip=self.config.validate_roundtrip,
            )
        except DraftCryptoError as exc:
            raise DraftJsonError("Jianying crypto encoding failed") from exc


def load_json_object_with_codec(
    path: PathLike,
    *,
    content_codec: Optional[DraftContentCodec] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Load a JSON object and report whether a private codec decoded it.

    Plain JSON is always attempted first.  The explicit boolean prevents a
    configured codec from being mistaken for the source file's actual format.
    """

    input_path = Path(path)
    raw_data = input_path.read_bytes()
    try:
        decoded = _loads_json(raw_data)
    except (UnicodeDecodeError, json.JSONDecodeError) as plain_exc:
        if content_codec is None:
            raise DraftJsonDecodeError(
                "draft JSON is not valid plaintext JSON; provide DraftFolder("
                "fallback_loader=...) or a private content_codec to load it"
            ) from plain_exc
        decoded = content_codec.decode(raw_data)
        used_codec = True
    else:
        used_codec = False

    if not isinstance(decoded, dict):
        raise ValueError(f"JSON file top-level value is not an object: {input_path}")
    return decoded, used_codec


def write_json_text_with_codec(
    path: PathLike,
    serialized_json: str,
    *,
    content_codec: Optional[DraftContentCodec] = None,
) -> None:
    """Atomically persist plaintext or encoded JSON bytes.

    Encoding succeeds before backup or replacement, so failed codec operations
    leave the original target untouched.
    """

    output_path = Path(path)
    if content_codec is None:
        _write_bytes_atomic(output_path, serialized_json.encode("utf-8"))
        return

    try:
        expected_data = _loads_json(serialized_json.encode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftJsonError(f"serialized draft JSON is invalid: {output_path}") from exc
    if not isinstance(expected_data, dict):
        raise DraftJsonError(f"serialized draft JSON top-level value is not an object: {output_path}")

    try:
        encoded = content_codec.encode(serialized_json)
    except Exception as exc:
        raise DraftJsonError(f"DraftContentCodec failed to encode draft JSON: {output_path}") from exc
    if not isinstance(encoded, bytes):
        raise TypeError("DraftContentCodec.encode() must return bytes")

    # A codec can return bytes without raising while still producing an
    # unrecoverable payload.  Verify reversibility before touching either the
    # existing draft or its recovery copy.
    try:
        decoded_data = content_codec.decode(encoded)
    except Exception as exc:
        raise DraftJsonError(f"DraftContentCodec round-trip verification failed: {output_path}") from exc
    if decoded_data != expected_data:
        raise DraftJsonError(f"DraftContentCodec round-trip JSON differs from the requested draft: {output_path}")

    if isinstance(content_codec, JianyingDraftCryptoCodec):
        _backup_once(output_path, enabled=content_codec.config.backup)
    else:
        _backup_once(output_path)
    _write_bytes_atomic(output_path, encoded)


def write_json_object_with_codec(
    path: PathLike,
    data: Dict[str, Any],
    *,
    content_codec: Optional[DraftContentCodec] = None,
    indent: Optional[int] = 4,
    trailing_newline: bool = False,
) -> None:
    serialized_json = json.dumps(data, ensure_ascii=False, indent=indent)
    if trailing_newline:
        serialized_json += "\n"
    write_json_text_with_codec(path, serialized_json, content_codec=content_codec)


def _loads_json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8-sig"))


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


def _backup_once(path: Path, *, enabled: bool = True) -> None:
    if not enabled or not path.exists():
        return
    backup_path = Path(f"{path}.pyjydraft.bak")
    if backup_path.exists():
        return

    temp_path = Path(f"{backup_path}.{os.getpid()}.tmp")
    try:
        shutil.copy2(path, temp_path)
        os.replace(temp_path, backup_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
