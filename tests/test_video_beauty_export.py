import pyJianYingDraft as draft
import pytest

from pyJianYingDraft.local_materials import BEAUTY_ENABLED_CHECK_FLAG_MASK
from tests.helpers import fake_video_material, parse_dump


def test_beauty_effects_are_registered_in_the_v030_material_container():
    script = draft.ScriptFile(1920, 1080, 30, True)
    video_track = script.append_track(draft.TrackSpec(draft.TrackType.video, "video"))
    segment = draft.VideoSegment(fake_video_material(), draft.trange("0s", "2s"))
    segment.add_beauty(next(iter(draft.BeautyType)))
    script.add_segment(segment, track=video_track)

    dumped = parse_dump(script)
    figure_effects = [effect for effect in dumped["materials"]["effects"] if effect["type"] == "figure"]
    assert len(figure_effects) == 1
    assert dumped["materials"]["videos"][0]["check_flag"] & BEAUTY_ENABLED_CHECK_FLAG_MASK


@pytest.mark.parametrize("beauty_first", [False, True])
def test_reused_video_material_keeps_beauty_metadata_regardless_of_segment_order(beauty_first):
    script = draft.ScriptFile(1920, 1080, 30, True)
    video_track = script.append_track(draft.TrackSpec(draft.TrackType.video, "video"))
    material = fake_video_material()
    plain_segment = draft.VideoSegment(material, draft.trange("0s", "2s"))
    beauty_segment = draft.VideoSegment(material, draft.trange("2s", "4s"))
    beauty_segment.add_beauty(next(iter(draft.BeautyType)))

    ordered_segments = [beauty_segment, plain_segment] if beauty_first else [plain_segment, beauty_segment]
    for segment in ordered_segments:
        script.add_segment(segment, track=video_track)

    dumped = parse_dump(script)
    exported_video = dumped["materials"]["videos"][0]
    assert exported_video["check_flag"] & BEAUTY_ENABLED_CHECK_FLAG_MASK
    assert exported_video["beauty_face_auto_preset_infos"]
    assert exported_video["beauty_face_preset_infos"]


def test_set_skin_tone_replaces_the_previous_skin_tone_effect():
    script = draft.ScriptFile(1920, 1080, 30, True)
    video_track = script.append_track(draft.TrackSpec(draft.TrackType.video, "video"))
    segment = draft.VideoSegment(fake_video_material(), draft.trange("0s", "2s"))
    skin_tone_type = next(iter(draft.SkinToneType))

    segment.set_skin_tone(skin_tone_type, intensity=25)
    first_skin_tone_id = segment.beauty_effects[0].global_id
    segment.set_skin_tone(skin_tone_type, intensity=75)
    script.add_segment(segment, track=video_track)

    dumped = parse_dump(script)
    figure_effects = [effect for effect in dumped["materials"]["effects"] if effect["type"] == "figure"]
    segment_json = dumped["tracks"][0]["segments"][0]

    assert len(figure_effects) == 1
    assert figure_effects[0]["id"] != first_skin_tone_id
    assert figure_effects[0]["id"] in segment_json["extra_material_refs"]
