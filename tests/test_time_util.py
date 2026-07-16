import pyJianYingDraft as draft


def test_tim_parses_common_duration_strings():
    assert draft.tim("1s") == 1_000_000
    assert draft.tim("1m2s") == 62_000_000
    assert draft.tim("0.15s") == 150_000
    assert draft.tim("1h2m3s") == 3_723_000_000


def test_tim_supports_negative_offsets():
    assert draft.tim("-2s") == -2_000_000


def test_trange_uses_duration_not_end_time():
    timerange = draft.trange("1s", "2s")

    assert timerange.start == 1_000_000
    assert timerange.duration == 2_000_000
    assert timerange.end == 3_000_000


def test_timerange_overlap_semantics():
    base = draft.Timerange(1_000_000, 2_000_000)

    assert base.overlaps(draft.Timerange(2_000_000, 2_000_000))
    assert not base.overlaps(draft.Timerange(3_000_000, 1_000_000))
    assert base.overlaps(draft.Timerange(1_500_000, 200_000))


def test_timerange_import_json_defaults_missing_start_to_zero():
    timerange = draft.Timerange.import_json({"duration": "2000"})

    assert timerange.start == 0
    assert timerange.duration == 2000
