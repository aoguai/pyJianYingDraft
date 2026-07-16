import pyJianYingDraft as draft

from tests.helpers import fake_video_material, parse_dump


def test_text_mask_exports_config_and_segment_material_reference():
    script = draft.ScriptFile(1920, 1080, 30, True)
    video_track = script.append_track(draft.TrackSpec(draft.TrackType.video, "video"))
    segment = draft.VideoSegment(fake_video_material(), draft.trange("0s", "2s"))
    segment.add_mask(
        draft.MaskType.文字,
        expansion=24,
        text_content="遮罩文字",
        text_font_name="思源黑体",
    )
    script.add_segment(segment, track=video_track)

    dumped = parse_dump(script)
    mask_json = dumped["materials"]["masks"][0]
    segment_json = dumped["tracks"][0]["segments"][0]

    assert mask_json["config"]["expansion"] == 0.24
    assert mask_json["text_config"]["content"] == "遮罩文字"
    assert mask_json["text_config"]["font_name"] == "思源黑体"
    assert mask_json["id"] in segment_json["extra_material_refs"]
