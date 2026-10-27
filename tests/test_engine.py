import io

import numpy as np
import pytest
import soundfile as sf

from taalloop import hits, phrases, rhythm, separation, slicer
from taalloop.wavio import Cue, encode_wav, read_wav_info

from .synth import bayan, chanti, groove, keherwa_song, render, slap

SR = 48000


# ------------------------------------------------------------------ WAV format
def test_wav_is_plain_16bit_pcm_with_cues_and_loop():
    audio = np.sin(np.linspace(0, 200, 4800))[:, None].repeat(2, axis=1) * 0.5
    data = encode_wav(audio, SR, cues=[Cue(100, "01 Bayan"), Cue(2400, "02 Slap")], loop=True)
    info = read_wav_info(data)
    assert (info.format_tag, info.bits_per_sample, info.sample_rate, info.channels) == (1, 16, SR, 2)
    assert info.frames == 4800
    assert info.chunks == ["fmt ", "data", "cue ", "LIST", "smpl"]
    assert [(c.sample, c.label) for c in info.cues] == [(100, "01 Bayan"), (2400, "02 Slap")]
    assert info.loop == (0, 4799)

    decoded, sr = sf.read(io.BytesIO(data), dtype="float32")
    assert sr == SR and sf.info(io.BytesIO(data)).subtype == "PCM_16"
    assert np.max(np.abs(decoded - audio)) < 3 / 32767  # dither is within ~1 LSB


def test_acid_chunk_carries_tempo_beats_and_meter():
    from taalloop.wavio import ACID_ONE_SHOT, ACID_STRETCH, Acid
    data = encode_wav(np.zeros((100, 2)), SR, loop=True, acid=Acid(beats=14, tempo=96.0, numerator=7))
    info = read_wav_info(data)
    assert info.chunks[-1] == "acid"
    assert info.acid == Acid(beats=14, tempo=96.0, numerator=7, denominator=4)
    assert info.acid_flags & ACID_STRETCH and not info.acid_flags & ACID_ONE_SHOT


def test_wav_without_metadata_has_only_fmt_and_data():
    info = read_wav_info(encode_wav(np.zeros(10), 44100))
    assert info.chunks == ["fmt ", "data"] and info.channels == 1


def test_clipping_is_saturated_not_wrapped():
    data = encode_wav(np.array([2.0, -2.0]), SR, dither=False)
    decoded, _ = sf.read(io.BytesIO(data), dtype="int16")
    assert list(decoded) == [32767, -32768]


# -------------------------------------------------------------------- slicer
@pytest.mark.parametrize("ts_id,bars", [("4/4", 1), ("4/4", 4), ("6/8", 2), ("7/8", 1), ("7/8", 4)])
def test_loops_are_exact_bar_length(ts_id, bars):
    ts = rhythm.get_time_signature(ts_id)
    bpm = 120
    audio = groove(SR, bpm, ["B", "C", "S", "C", "B", "C", "S"][: ts.beats_per_bar], cycles=10)
    beats = [i * 0.5 for i in range(ts.beats_per_bar * 10)]
    loops = slicer.slice_loops(audio, SR, beats, 0, bpm, ts, bars, "My Song.mp3")
    expected = round(bars * ts.beats_per_bar * 60 / bpm * SR)
    assert loops and all(len(lp.audio) == expected for lp in loops)
    assert loops[0].filename == f"My-Song_Loop01_120BPM_{bars}Bar.wav"
    assert len(loops) == 10 // bars


def test_loop_start_snaps_to_zero_crossing_and_fades():
    sr = SR
    t = np.arange(sr * 4) / sr
    tone = (0.8 * np.sin(2 * np.pi * 97 * t + 0.7)).astype(np.float32)
    audio = np.stack([tone, tone], axis=1)
    ts = rhythm.get_time_signature("4/4")
    loop = slicer.export_region(audio, sr, 1.0, 3.0, 1, ts, "Tone")  # 4 beats in 2 s = 120 BPM
    assert loop.bpm == 120 and len(loop.audio) == sr * 2
    start = round(loop.start_time * sr)
    assert abs(start - sr) <= sr * slicer.ZERO_CROSS_SEARCH_MS / 1000
    assert abs(tone[start]) < 0.02  # landed on a zero crossing
    assert abs(tone[start + len(loop.audio)]) < 0.05  # and so does the wrap-around point
    assert np.all(np.abs(loop.audio[:3]) < 1e-3) and np.all(np.abs(loop.audio[-3:]) < 1e-3)


def test_region_bpm_rounds_and_length_follows_rounded_bpm():
    audio = np.zeros((SR * 10, 2), np.float32)
    ts = rhythm.get_time_signature("7/8")
    # 7 beats in 3.55 s -> 118.3 BPM -> 118 BPM -> 7 * 60 / 118 s long.
    loop = slicer.export_region(audio, SR, 0.0, 3.55, 1, ts, "Rupak", number=3)
    assert loop.bpm == 118
    assert len(loop.audio) == round(7 * 60 / 118 * SR)
    assert loop.filename == "Rupak_Loop03_118BPM_1Bar.wav"


def test_region_near_end_is_zero_padded_to_exact_length():
    audio = np.full((SR, 2), 0.1, np.float32)
    loop = slicer.export_region(audio, SR, 0.5, 2.5, 1, rhythm.get_time_signature("4/4"), "X")
    assert len(loop.audio) == 2 * SR


def test_silent_loops_are_skipped_and_cues_embedded():
    ts = rhythm.get_time_signature("4/4")
    audio = groove(SR, 120, ["B", "C", "S", "C"], cycles=4)
    audio[: int(2.5 * SR)] = 0  # first bar (and the start of the second) silent
    beats = [i * 0.5 for i in range(16)]
    hit_list = [{"time": 2.0, "label": "Bayan"}, {"time": 2.5, "label": "Chanti"}]
    loops = slicer.slice_loops(audio, SR, beats, 0, 120, ts, 1, "S", hits=hit_list)
    assert loops[0].start_time == pytest.approx(2.0, abs=0.006)
    assert [c.label for c in loops[0].cues] == ["01 Bayan", "02 Chanti"]


def test_song_names_never_contain_underscores():
    assert slicer.safe_song_name("tum_hi ho (live).wav") == "tum-hi-ho-live"
    assert slicer.safe_song_name("???") == "Song"
    assert slicer.format_bpm(120.0) == "120" and slicer.format_bpm(98.5) == "98.5"


def test_invalid_bars_rejected():
    with pytest.raises(ValueError):
        slicer.slice_loops(np.zeros((SR, 2)), SR, [], 0, 120, rhythm.get_time_signature("4/4"), 3, "x")


# --------------------------------------------------------------- rhythm / Sam
@pytest.mark.parametrize("ts_id,pattern", [
    ("6/8", ["B", "B", "C", "B", "C", "C"]),        # dha dhin na | dha tin na
    ("7/8", ["C", "C", "C", "B", "C", "B", "C"]),   # tin tin na | dhi na | dhi na
])
def test_sam_found_from_bass_pattern(ts_id, pattern):
    ts = rhythm.get_time_signature(ts_id)
    lead_in = 2  # the song starts mid-cycle, so beat 0 is not the Sam
    audio = groove(22050, 100, pattern, cycles=8, lead_in_beats=lead_in)
    result = rhythm.analyze(audio.mean(axis=1), 22050)
    assert round(result.bpm) == 100  # filename tempo must not drift to 99
    sam_time = lead_in * 0.6
    sam = result.beat_times[result.downbeat_index[ts_id]]
    assert (sam - sam_time) / 0.6 % ts.beats_per_bar == pytest.approx(0, abs=0.1)


# ---------------------------------------------------------------------- hits
def test_strokes_are_classified():
    sr = 44100
    for maker, label in [(bayan, "Bayan"), (chanti, "Chanti"), (slap, "Slap")]:
        assert hits.classify(*hits.hit_features(maker(sr), sr)) == label


def test_detects_every_hit_including_one_at_time_zero():
    sr = 44100
    audio = groove(sr, 240, ["B", "C", "S", "C"], cycles=4).mean(axis=1)
    found = hits.detect_hits(audio, sr)
    assert "".join(h.label[0] for h in found) == "BCSC" * 4
    assert found[0].time == pytest.approx(0, abs=0.01)


def test_hpss_separation_keeps_hit_count_and_classes():
    sr = 44100
    audio = groove(sr, 100, ["C", "C", "C", "B", "C", "B", "C"], cycles=3)
    found = hits.detect_hits(separation.separate_hpss(audio, sr).mean(axis=1), sr, suppress_pre_echo=True)
    assert "".join(h.label[0] for h in found) == "CCCBCBC" * 3


# ------------------------------------------------------------ toda and rolls
@pytest.fixture(scope="module")
def keherwa():
    sr, bpm = 44100, 120
    audio = render(keherwa_song(bpm, 16, toda_bars=(3, 7, 11), roll_bars=(9,)), sr, 33.0)
    found = [h.to_dict() for h in hits.detect_hits(audio.mean(axis=1), sr)]
    beats = [i * 0.5 for i in range(64)]
    return audio, sr, found, beats


def test_todas_and_rolls_are_found_on_the_grid(keherwa):
    _, _, found, beats = keherwa
    ts = rhythm.get_time_signature("4/4")
    result = phrases.detect_phrases(found, beats, 0, 120, ts, 33.0)
    todas = [(p.start, p.beats) for p in result if p.kind == "Toda"]
    rolls = [(p.start, p.beats) for p in result if p.kind == "Roll"]
    assert todas == [(6.0, 4), (14.0, 4), (22.0, 4)]  # bars 3, 7, 11
    assert rolls == [(19.5, 2)]  # last beat of bar 9, plus the beat its tail rings into
    names = [p.filename("My Song", ts) for p in result]
    assert names[0] == "My-Song_Toda01_120BPM_1Bar.wav" and names[-1] == "My-Song_Roll01_120BPM_2Beat.wav"


def test_plain_theka_has_no_phrases():
    sr = 44100
    audio = render(keherwa_song(120, 12), sr, 25.0)
    found = [h.to_dict() for h in hits.detect_hits(audio.mean(axis=1), sr)]
    beats = [i * 0.5 for i in range(48)]
    assert phrases.detect_phrases(found, beats, 0, 120, rhythm.get_time_signature("4/4"), 25.0) == []


def test_long_runs_of_changed_bars_are_a_section_not_a_toda(keherwa):
    sr = 44100
    audio = render(keherwa_song(120, 16, toda_bars=(8, 9, 10, 11)), sr, 33.0)
    found = [h.to_dict() for h in hits.detect_hits(audio.mean(axis=1), sr)]
    beats = [i * 0.5 for i in range(64)]
    assert phrases.detect_todas(found, beats, 0, 120, rhythm.get_time_signature("4/4"), 33.0) == []


def test_loops_skip_bars_with_fills(keherwa):
    audio, sr, found, beats = keherwa
    ts = rhythm.get_time_signature("4/4")
    fills = phrases.detect_phrases(found, beats, 0, 120, ts, 33.0)
    loops = slicer.slice_loops(audio, sr, beats, 0, 120, ts, 1, "S", exclude=fills)
    starts = [round(lp.start_time / 2) for lp in loops]
    assert starts == [0, 1, 2, 4, 5, 6, 8, 10, 12, 13, 14, 15]  # 3, 7, 11 toda; 9 roll


def test_beats_snap_onto_stroke_onsets():
    beats = [1.02, 1.52, 2.03, 2.52]  # tracker output, late by 20-30 ms
    onsets = [1.0, 1.25, 1.5, 2.0, 3.1]  # no stroke on the 4th beat
    aligned = rhythm.align_beats_to_onsets(beats, onsets)
    assert aligned[:3] == [1.0, 1.5, 2.0]
    assert aligned[3] == pytest.approx(2.52 - 0.02)  # median lag applied


def test_sam_between_beats_shifts_the_whole_grid():
    beats = [0.25, 0.75, 1.25, 1.75]  # tracker locked onto the off-beat
    shifted, index = rhythm.shift_grid(beats, 1.0)
    assert shifted[index] == 1.0 and np.allclose(np.diff(shifted), 0.5)
    same, index = rhythm.shift_grid(beats, 1.27)  # near a tracked beat: just pick it
    assert same == beats and index == 2


def test_roll_start_absorbs_jitter_but_keeps_pickups():
    beats = [i * 0.5 for i in range(64)]
    roll = [{"time": 19.485 + i * 0.0625, "peak": 0.5, "label": "Chanti"} for i in range(8)]
    (found,) = phrases.detect_rolls(roll, beats, 120, 33.0)
    assert (found.start, found.beats) == (19.5, 2)  # 15 ms early is jitter
    pickup = [{"time": 19.4 + i * 0.0625, "peak": 0.5, "label": "Chanti"} for i in range(8)]
    (found,) = phrases.detect_rolls(pickup, beats, 120, 33.0)
    assert found.start == 19.0  # 100 ms early is a pickup: never cut it off


def test_beats_never_snap_forward_onto_later_strokes():
    # A roll right after the beat: the stroke before the beat is the right one.
    assert rhythm.align_beats_to_onsets([1.02], [0.995, 1.04, 1.06]) == [0.995]
