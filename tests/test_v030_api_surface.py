import pyJianYingDraft as draft


def test_v030_public_surface_has_no_legacy_track_or_snake_case_aliases():
    assert hasattr(draft.ScriptFile, "append_track")
    assert hasattr(draft.ScriptFile, "insert_track")
    assert not hasattr(draft.ScriptFile, "add_track")

    for legacy_alias in ["Script_file", "Draft_folder", "Track_type", "Video_segment"]:
        assert not hasattr(draft, legacy_alias)
