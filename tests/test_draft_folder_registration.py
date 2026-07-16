import json
import shutil

import pyJianYingDraft as draft
import pytest


def _root_meta_path(user_data_root):
    return user_data_root / "Projects" / "com.lveditor.draft" / "root_meta_info.json"


def _draft_content_id(drafts_root, draft_name: str) -> str:
    return json.loads((drafts_root / draft_name / "draft_content.json").read_text(encoding="utf-8"))["id"]


def test_first_save_registers_matching_content_and_sidecar_ids(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()

    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    script = folder.create_draft("registered", 1920, 1080)
    script.save()

    draft_path = drafts_root / "registered"
    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    sidecar = json.loads((draft_path / "draft_meta_info.json").read_text(encoding="utf-8"))
    root_meta_path = _root_meta_path(user_data_root)
    root_meta = json.loads(root_meta_path.read_text(encoding="utf-8"))

    assert content["id"]
    assert sidecar["draft_id"] == content["id"]
    assert root_meta["all_draft_store"][0]["draft_id"] == content["id"]


def test_logical_folder_mapping_can_be_created_and_removed(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()

    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.create_draft("registered", 1920, 1080).save()
    folder.create_folder("campaign")
    folder.create_folder("campaign/launch")
    folder.move_draft_to_folder("registered", "campaign/launch")
    folder.remove_folder("campaign", on_non_empty="move_drafts_to_root")

    mappings_path = user_data_root / "Config" / "LocalDraftFolder" / "draft_folder_mappings.json"
    mappings = json.loads(mappings_path.read_text(encoding="utf-8"))
    assert mappings["mappings"] == []


def test_content_only_draft_is_registered_with_a_new_sidecar_on_save(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()
    content_only_path = drafts_root / "content_only"
    content_only_path.mkdir()
    content_only_path.joinpath("draft_content.json").write_text(
        draft.ScriptFile(1920, 1080, 30, True).dumps(),
        encoding="utf-8",
    )

    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.load_template("content_only").save()

    content = json.loads((content_only_path / "draft_content.json").read_text(encoding="utf-8"))
    sidecar = json.loads((content_only_path / "draft_meta_info.json").read_text(encoding="utf-8"))
    root_entries = json.loads(_root_meta_path(user_data_root).read_text(encoding="utf-8"))["all_draft_store"]

    assert content["id"]
    assert sidecar["draft_id"] == content["id"]
    assert root_entries[0]["draft_id"] == content["id"]
    assert root_entries[0]["draft_fold_path"] == str(content_only_path)


def test_duplicate_as_template_registers_a_distinct_draft_id(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()
    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.create_draft("source", 1920, 1080).save()

    folder.duplicate_as_template("source", "clone").save()

    source_id = _draft_content_id(drafts_root, "source")
    clone_id = _draft_content_id(drafts_root, "clone")
    root_entries = json.loads(_root_meta_path(user_data_root).read_text(encoding="utf-8"))["all_draft_store"]

    assert clone_id != source_id
    assert {entry["draft_id"] for entry in root_entries} == {source_id, clone_id}
    assert {entry["draft_fold_path"] for entry in root_entries} == {
        str(drafts_root / "source"),
        str(drafts_root / "clone"),
    }


def test_loading_a_filesystem_clone_rejects_rebinding_an_existing_root_entry(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()
    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.create_draft("source", 1920, 1080).save()
    folder.create_folder("campaign")
    folder.move_draft_to_folder("source", "campaign")
    shutil.copytree(drafts_root / "source", drafts_root / "clone")

    root_meta_path = _root_meta_path(user_data_root)
    mappings_path = user_data_root / "Config" / "LocalDraftFolder" / "draft_folder_mappings.json"
    source_content_before = (drafts_root / "source" / "draft_content.json").read_bytes()
    clone_content_before = (drafts_root / "clone" / "draft_content.json").read_bytes()
    root_meta_before = root_meta_path.read_bytes()
    mappings_before = mappings_path.read_bytes()

    with pytest.raises(ValueError, match="已注册到其他草稿目录"):
        folder.load_template("clone").save()

    assert (drafts_root / "source" / "draft_content.json").read_bytes() == source_content_before
    assert (drafts_root / "clone" / "draft_content.json").read_bytes() == clone_content_before
    assert root_meta_path.read_bytes() == root_meta_before
    assert mappings_path.read_bytes() == mappings_before


def test_delete_drafts_preflight_keeps_everything_when_one_mapped_directory_is_missing(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()
    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.create_draft("first", 1920, 1080).save()
    folder.create_draft("missing", 1920, 1080).save()
    folder.create_folder("campaign")
    folder.move_draft_to_folder("first", "campaign")
    folder.move_draft_to_folder("missing", "campaign")

    root_meta_path = _root_meta_path(user_data_root)
    folder_meta_path = user_data_root / "Config" / "LocalDraftFolder" / "folder_meta_info.json"
    mappings_path = user_data_root / "Config" / "LocalDraftFolder" / "draft_folder_mappings.json"
    root_meta_before = root_meta_path.read_bytes()
    folder_meta_before = folder_meta_path.read_bytes()
    mappings_before = mappings_path.read_bytes()
    shutil.rmtree(drafts_root / "missing")

    with pytest.raises(FileNotFoundError, match="缺失的草稿目录"):
        folder.remove_folder("campaign", on_non_empty="delete_drafts")

    assert (drafts_root / "first").is_dir()
    assert root_meta_path.read_bytes() == root_meta_before
    assert folder_meta_path.read_bytes() == folder_meta_before
    assert mappings_path.read_bytes() == mappings_before


def test_loaded_draft_preserves_an_existing_root_draft_new_version(tmp_path):
    drafts_root = tmp_path / "drafts"
    user_data_root = tmp_path / "User Data"
    drafts_root.mkdir()
    folder = draft.DraftFolder(str(drafts_root), user_data_path=str(user_data_root))
    folder.create_draft("registered", 1920, 1080).save()

    draft_path = drafts_root / "registered"
    sidecar_path = draft_path / "draft_meta_info.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["draft_new_version"] = ""
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    root_meta_path = _root_meta_path(user_data_root)
    root_meta = json.loads(root_meta_path.read_text(encoding="utf-8"))
    root_meta["all_draft_store"][0]["draft_new_version"] = "keep-this-version"
    root_meta_path.write_text(json.dumps(root_meta, ensure_ascii=False), encoding="utf-8")

    folder.load_template("registered").save()

    sidecar_after = json.loads(sidecar_path.read_text(encoding="utf-8"))
    root_after = json.loads(root_meta_path.read_text(encoding="utf-8"))["all_draft_store"][0]
    assert sidecar_after["draft_new_version"] == "keep-this-version"
    assert root_after["draft_new_version"] == "keep-this-version"
