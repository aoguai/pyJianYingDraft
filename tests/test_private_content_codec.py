import json

import pytest

import pyJianYingDraft as draft
from pyJianYingDraft.draft_codec import DraftJsonDecodeError, DraftJsonError


class PrefixCodec:
    """A deterministic codec double that never depends on Jianying's DLL."""

    prefix = b"private:"

    def __init__(self) -> None:
        self.decode_calls = []
        self.encode_calls = []

    def decode(self, raw_data: bytes):
        self.decode_calls.append(raw_data)
        assert raw_data.startswith(self.prefix)
        return json.loads(raw_data[len(self.prefix):].decode("utf-8"))

    def encode(self, serialized_json: str) -> bytes:
        self.encode_calls.append(serialized_json)
        return self.prefix + serialized_json.encode("utf-8")


class ToggleWriteCodec(PrefixCodec):
    """Acts like a valid codec until its write path intentionally misbehaves."""

    def __init__(self) -> None:
        super().__init__()
        self.emit_broken_payload = True

    def decode(self, raw_data: bytes):
        if raw_data == b"broken":
            return {"id": "wrong"}
        return super().decode(raw_data)

    def encode(self, serialized_json: str) -> bytes:
        if self.emit_broken_payload:
            self.encode_calls.append(serialized_json)
            return b"broken"
        return super().encode(serialized_json)


class EncodeFailureCodec(PrefixCodec):
    def encode(self, serialized_json: str) -> bytes:
        raise RuntimeError("codec encoder unavailable")


class DecodeFailureCodec:
    def decode(self, raw_data: bytes):
        raise DraftJsonDecodeError("private codec diagnostic")

    def encode(self, serialized_json: str) -> bytes:
        return serialized_json.encode("utf-8")


def _draft_content() -> dict:
    return {
        "id": "template-id",
        "fps": 30.0,
        "duration": 0,
        "config": {"maintrack_adsorb": True},
        "canvas_config": {"width": 1920, "height": 1080, "ratio": "original"},
        "tracks": [],
        "materials": {},
    }


def _write_template(root, name: str, payload: bytes) -> None:
    draft_path = root / name
    draft_path.mkdir()
    (draft_path / "draft_content.json").write_bytes(payload)


def test_content_codec_is_used_only_for_non_plaintext_templates(tmp_path):
    root = tmp_path / "drafts"
    root.mkdir()
    codec = PrefixCodec()
    _write_template(root, "encrypted", codec.prefix + json.dumps(_draft_content()).encode("utf-8"))

    script = draft.DraftFolder(
        str(root),
        content_codec=codec,
        user_data_path=str(root / "User Data"),
    ).load_template("encrypted")

    assert script._loaded_content_codec is codec
    assert codec.decode_calls
    script.save()
    assert (root / "encrypted" / "draft_content.json").read_bytes().startswith(codec.prefix)
    assert codec.encode_calls


def test_plaintext_template_does_not_adopt_configured_codec(tmp_path):
    root = tmp_path / "drafts"
    root.mkdir()
    codec = PrefixCodec()
    _write_template(root, "plain", json.dumps(_draft_content()).encode("utf-8"))

    script = draft.DraftFolder(
        str(root),
        content_codec=codec,
        user_data_path=str(root / "User Data"),
    ).load_template("plain")

    assert script._loaded_content_codec is None
    assert codec.decode_calls == []
    script.save()
    assert not (root / "plain" / "draft_content.json").read_bytes().startswith(codec.prefix)
    assert codec.encode_calls == []


def test_explicit_dump_codec_does_not_change_loaded_codec(tmp_path):
    codec = PrefixCodec()
    script = draft.ScriptFile(1920, 1080, 30, True)
    output_path = tmp_path / "created.json"

    script.dump(str(output_path), content_codec=codec)

    assert output_path.read_bytes().startswith(codec.prefix)
    assert script._loaded_content_codec is None


def test_fallback_loader_and_content_codec_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        draft.DraftFolder(
            str(tmp_path),
            fallback_loader=lambda raw_data: "{}",
            content_codec=PrefixCodec(),
        )


def test_content_codec_failure_is_not_reported_as_a_fallback_loader_failure(tmp_path):
    root = tmp_path / "drafts"
    root.mkdir()
    _write_template(root, "encrypted", b"not plaintext")

    with pytest.raises(DraftJsonDecodeError, match="private codec diagnostic"):
        draft.DraftFolder(str(root), content_codec=DecodeFailureCodec()).load_template("encrypted")


def test_codec_round_trip_failure_preserves_the_loaded_source_and_allows_retry(tmp_path):
    root = tmp_path / "drafts"
    root.mkdir()
    codec = ToggleWriteCodec()
    original_payload = codec.prefix + json.dumps(_draft_content()).encode("utf-8")
    _write_template(root, "encrypted", original_payload)

    script = draft.DraftFolder(
        str(root),
        content_codec=codec,
        user_data_path=str(root / "User Data"),
    ).load_template("encrypted")
    content_path = root / "encrypted" / "draft_content.json"

    with pytest.raises(DraftJsonError, match="round-trip"):
        script.save()

    assert content_path.read_bytes() == original_payload
    assert script._loaded_content_codec is codec
    assert not (root / "encrypted" / "draft_content.json.pyjydraft.bak").exists()

    codec.emit_broken_payload = False
    script.save()

    assert content_path.read_bytes().startswith(codec.prefix)
    assert (root / "encrypted" / "draft_content.json.pyjydraft.bak").read_bytes() == original_payload


def test_codec_encode_failure_does_not_replace_existing_content(tmp_path):
    root = tmp_path / "drafts"
    root.mkdir()
    codec = EncodeFailureCodec()
    original_payload = codec.prefix + json.dumps(_draft_content()).encode("utf-8")
    _write_template(root, "encrypted", original_payload)

    script = draft.DraftFolder(
        str(root),
        content_codec=codec,
        user_data_path=str(root / "User Data"),
    ).load_template("encrypted")
    content_path = root / "encrypted" / "draft_content.json"

    with pytest.raises(DraftJsonError, match="failed to encode"):
        script.save()

    assert content_path.read_bytes() == original_payload
    assert script._loaded_content_codec is codec
