"""Draft JSON loading and saving with optional Jianying crypto support."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .draft_crypto import (
    DraftCryptoConfig,
    DraftCryptoError,
    PathLike,
    decrypt_draft_bytes,
    encrypt_draft_bytes,
)


class DraftJsonError(DraftCryptoError):
    """Base error for encrypted/plain draft JSON handling."""


class DraftJsonDecodeError(DraftJsonError):
    """Raised when a draft JSON file cannot be decoded as plaintext or encrypted JSON."""


@dataclass
class DraftJsonState:
    """How a draft JSON file was loaded, used to preserve save format."""

    path: Path
    encrypted: bool
    crypto_config: DraftCryptoConfig


def coerce_crypto_config(crypto_config: Optional[DraftCryptoConfig]) -> DraftCryptoConfig:
    return crypto_config if crypto_config is not None else DraftCryptoConfig()


def same_json_path(left: PathLike, right: PathLike) -> bool:
    left_path = os.path.normcase(os.path.abspath(os.fspath(left)))
    right_path = os.path.normcase(os.path.abspath(os.fspath(right)))
    return left_path == right_path


def plain_json_state(
    path: PathLike,
    *,
    crypto_config: Optional[DraftCryptoConfig] = None,
) -> DraftJsonState:
    return DraftJsonState(Path(path), False, coerce_crypto_config(crypto_config))


def load_draft_json(
    path: PathLike,
    *,
    crypto_config: Optional[DraftCryptoConfig] = None,
) -> Tuple[Any, DraftJsonState]:
    input_path = Path(path)
    config = coerce_crypto_config(crypto_config)
    raw_data = input_path.read_bytes()

    try:
        return _loads_json(raw_data), DraftJsonState(input_path, False, config)
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            decrypted = decrypt_draft_bytes(
                raw_data,
                jy_install_dir=config.jy_install_dir,
                timeout=config.timeout,
                isolated=config.isolated,
            )
        except DraftCryptoError as crypto_exc:
            raise DraftJsonDecodeError(
                "draft JSON is not valid plaintext JSON and crypto decoding failed: "
                f"{input_path}. If this is a high-version Jianying draft, configure "
                "JY_INSTALL_DIR or pass DraftCryptoConfig(jy_install_dir=...)."
            ) from crypto_exc

        try:
            return _loads_json(decrypted), DraftJsonState(input_path, True, config)
        except (UnicodeDecodeError, json.JSONDecodeError) as decrypted_exc:
            raise DraftJsonDecodeError(
                f"decrypted draft JSON is invalid: {input_path}"
            ) from decrypted_exc


def load_draft_json_object(
    path: PathLike,
    *,
    crypto_config: Optional[DraftCryptoConfig] = None,
) -> Tuple[Dict[str, Any], DraftJsonState]:
    data, state = load_draft_json(path, crypto_config=crypto_config)
    if not isinstance(data, dict):
        raise ValueError(f"JSON file top-level value is not an object: {path}")
    return data, state


def write_draft_json_object(
    path: PathLike,
    data: Dict[str, Any],
    *,
    state: Optional[DraftJsonState] = None,
    encrypted: Optional[bool] = None,
    crypto_config: Optional[DraftCryptoConfig] = None,
    indent: Optional[int] = 4,
    trailing_newline: bool = False,
) -> DraftJsonState:
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    if trailing_newline:
        text += "\n"
    return write_draft_json_text(
        path,
        text,
        state=state,
        encrypted=encrypted,
        crypto_config=crypto_config,
    )


def write_draft_json_text(
    path: PathLike,
    text: str,
    *,
    state: Optional[DraftJsonState] = None,
    encrypted: Optional[bool] = None,
    crypto_config: Optional[DraftCryptoConfig] = None,
) -> DraftJsonState:
    output_path = Path(path)
    if crypto_config is None and state is not None:
        config = state.crypto_config
    else:
        config = coerce_crypto_config(crypto_config)

    should_encrypt = state.encrypted if encrypted is None and state is not None else bool(encrypted)
    raw_data = text.encode("utf-8")

    if should_encrypt:
        encrypted_data = encrypt_draft_bytes(
            raw_data,
            jy_install_dir=config.jy_install_dir,
            timeout=config.timeout,
            isolated=config.isolated,
            validate_roundtrip=config.validate_roundtrip,
        )
        _backup_once(output_path, config)
        _write_bytes_atomic(output_path, encrypted_data)
    else:
        _write_bytes_atomic(output_path, raw_data)

    return DraftJsonState(output_path, should_encrypt, config)


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


def _backup_once(path: Path, config: DraftCryptoConfig) -> None:
    if not config.backup or not path.exists():
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
