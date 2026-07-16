import pyJianYingDraft as draft

from pyJianYingDraft.metadata import AudioSceneEffectType, SpeechToSongType, ToneEffectType

from tests.helpers import fake_audio_material, parse_dump


def test_tone_effect_type_is_exported_from_top_level():
    assert draft.ToneEffectType is ToneEffectType


def test_tone_effect_exports_private_metadata_without_hiding_audio_parameters():
    script = draft.ScriptFile(1920, 1080, 30, True)
    script.append_track(draft.TrackSpec(draft.TrackType.audio))

    segment = draft.AudioSegment(fake_audio_material(), draft.trange("0s", "2s"))
    segment.add_effect(ToneEffectType.机器人, [100])
    script.add_segment(segment)

    dumped = parse_dump(script)
    effect_json = dumped["materials"]["audio_effects"][0]
    segment_json = dumped["tracks"][0]["segments"][0]

    assert {
        "audio_adjust_params",
        "category_id",
        "category_name",
        "id",
        "name",
        "resource_id",
        "source_platform",
        "sub_type",
        "third_resource_id",
        "time_range",
        "type",
        "vc_type",
    }.issubset(effect_json)
    assert effect_json["audio_adjust_params"][0]["name"] == "强弱"
    assert effect_json["audio_adjust_params"][0]["value"] == 1.0
    assert dumped["materials"]["audio_effects"][0]["id"] in segment_json["extra_material_refs"]
    assert len(segment_json["extra_material_refs"]) == 2
    assert effect_json["time_range"] == {"duration": draft.tim("2s"), "start": 0}


def test_audio_scene_effect_keeps_legacy_export_shape():
    script = draft.ScriptFile(1920, 1080, 30, True)
    script.append_track(draft.TrackSpec(draft.TrackType.audio))

    segment = draft.AudioSegment(fake_audio_material(), draft.trange("0s", "2s"))
    segment.add_effect(next(iter(AudioSceneEffectType)))
    script.add_segment(segment)

    dumped = parse_dump(script)
    effect_json = dumped["materials"]["audio_effects"][0]

    assert effect_json["category_id"] == "sound_effect"
    assert effect_json["category_name"] == "场景音"
    assert effect_json["sub_type"] == 1
    assert effect_json["time_range"] == {"duration": draft.tim("2s"), "start": 0}


def test_speech_to_song_effect_exports_its_private_metadata_and_segment_ref():
    script = draft.ScriptFile(1920, 1080, 30, True)
    script.append_track(draft.TrackSpec(draft.TrackType.audio))

    segment = draft.AudioSegment(fake_audio_material(), draft.trange("0s", "2s"))
    segment.add_effect(next(iter(SpeechToSongType)))
    script.add_segment(segment)

    dumped = parse_dump(script)
    effect_json = dumped["materials"]["audio_effects"][0]
    segment_json = dumped["tracks"][0]["segments"][0]

    assert effect_json["category_id"] == "speech_to_song"
    assert effect_json["category_name"] == "声音成曲"
    assert effect_json["sub_type"] == 3
    assert effect_json["time_range"] == {"duration": draft.tim("2s"), "start": 0}
    assert effect_json["id"] in segment_json["extra_material_refs"]
