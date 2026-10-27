"""Automated Loop Slicer & Formatter Engine.

Rules every exported loop follows:

* Length is *exactly* ``bars * beats_per_bar * 60 / bpm`` seconds, rounded to
  the nearest sample at the output rate, where ``bpm`` is the value printed in
  the filename.  FL Studio stretches using that BPM and the Nautilus knob is set
  to it, so the file length and the filename must agree.
* The start is snapped (within a few ms of the beat) to the point where both the
  first sample and the sample just past the loop end are closest to zero, so
  the wrap-around joint lands on two near-zero crossings.
* Micro fades (raised cosine, a few ms) guarantee a click-free cycle even when
  no perfect zero crossing exists.
* Output is 16-bit PCM WAV at 48 kHz or 44.1 kHz with an ``smpl`` loop chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .rhythm import TimeSignature
from .wavio import Cue

ALLOWED_BARS = (1, 2, 4)
ALLOWED_SAMPLE_RATES = (48000, 44100)
ZERO_CROSS_SEARCH_MS = 5.0
FADE_IN_MS = 2.0
FADE_OUT_MS = 4.0
SILENCE_DBFS = -45.0


@dataclass
class Loop:
    number: int
    filename: str
    audio: np.ndarray  # (frames, channels) float32 at sample_rate
    sample_rate: int
    bpm: float
    bars: int
    start_time: float  # position in the source track, seconds
    cues: list[Cue] = field(default_factory=list)


def round_bpm(bpm: float, decimals: int = 0) -> float:
    value = round(float(bpm), decimals)
    return float(int(value)) if decimals == 0 else value


def format_bpm(bpm: float) -> str:
    return f"{bpm:.2f}".rstrip("0").rstrip(".")


def safe_song_name(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name.strip())
    # Underscores separate the filename fields, so they are not allowed inside the name.
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-")
    return cleaned or "Song"


def loop_filename(song: str, number: int, bpm: float, bars: int) -> str:
    return f"{safe_song_name(song)}_Loop{number:02d}_{format_bpm(bpm)}BPM_{bars}Bar.wav"


def bar_seconds(bpm: float, ts: TimeSignature) -> float:
    return ts.beats_per_bar * 60.0 / bpm


def loop_length_samples(bpm: float, ts: TimeSignature, bars: int, sample_rate: int) -> int:
    return int(round(bars * bar_seconds(bpm, ts) * sample_rate))


def snap_loop_start(mono: np.ndarray, target: int, length: int, search: int) -> int | None:
    """Best start near ``target`` so that x[start] and x[start + length] are both ~0.

    Returns None when a loop of ``length`` samples does not fit near ``target``.
    """
    lo = max(0, target - search)
    hi = min(target + search, len(mono) - length - 1)
    if hi < lo:
        return None
    idx = np.arange(lo, hi + 1)
    cost = np.abs(mono[idx]) + np.abs(mono[idx + length])
    cost = cost + 1e-4 * np.abs(idx - target) / max(search, 1)  # tie-break towards the beat
    return int(idx[np.argmin(cost)])


def apply_micro_fades(audio: np.ndarray, sample_rate: int,
                      fade_in_ms: float = FADE_IN_MS, fade_out_ms: float = FADE_OUT_MS) -> np.ndarray:
    out = audio.copy()
    n_in = min(len(out), int(sample_rate * fade_in_ms / 1000))
    n_out = min(len(out), int(sample_rate * fade_out_ms / 1000))
    if n_in > 0:
        out[:n_in] *= (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n_in)))[:, None]
    if n_out > 0:
        out[-n_out:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, n_out)))[:, None]
    return out


def _rms_dbfs(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    return 20 * np.log10(rms) if rms > 0 else -np.inf


def _cues_for(hits: list[dict] | None, start: int, length: int, sample_rate: int) -> list[Cue]:
    if not hits:
        return []
    cues = []
    for hit in hits:
        pos = int(round(hit["time"] * sample_rate)) - start
        if 0 <= pos < length:
            cues.append(Cue(pos, f"{len(cues) + 1:02d} {hit['label']}"))
    return cues


def cut_loop(audio: np.ndarray, sample_rate: int, start_time: float, length: int,
             snap: bool = True) -> tuple[np.ndarray, int]:
    """Cut ``length`` samples near ``start_time``; zero-pads if the track ends early."""
    mono = audio.mean(axis=1)
    target = int(round(start_time * sample_rate))
    start = None
    if snap:
        start = snap_loop_start(mono, target, length, int(sample_rate * ZERO_CROSS_SEARCH_MS / 1000))
    if start is None:
        start = max(0, min(target, len(audio) - 1))
    segment = audio[start : start + length]
    if len(segment) < length:
        segment = np.concatenate([segment, np.zeros((length - len(segment), audio.shape[1]), audio.dtype)])
    return apply_micro_fades(segment, sample_rate), start


def sam_times(beat_times: list[float], downbeat_index: int, bpm: float,
              ts: TimeSignature, bars: int, duration: float) -> list[float]:
    """Start times of consecutive ``bars``-long loops, beginning at the Sam."""
    step = ts.beats_per_bar * bars
    if beat_times:
        downbeat_index = min(max(downbeat_index, 0), len(beat_times) - 1)
        starts = list(beat_times[downbeat_index::step])
    else:
        starts = [0.0]
    # Continue the grid past the last tracked beat (the tracker trims weak tails).
    loop_seconds = bars * bar_seconds(bpm, ts)
    while starts[-1] + 2 * loop_seconds <= duration:
        starts.append(starts[-1] + loop_seconds)
    return starts


def nearest_beat_index(beat_times: list[float], time: float) -> int:
    if not beat_times:
        return 0
    return int(np.argmin(np.abs(np.asarray(beat_times) - time)))


def slice_loops(
    audio: np.ndarray,
    sample_rate: int,
    beat_times: list[float],
    downbeat_index: int,
    bpm: float,
    ts: TimeSignature,
    bars: int,
    song: str,
    hits: list[dict] | None = None,
    max_loops: int = 64,
    skip_silent: bool = True,
) -> list[Loop]:
    """Slice the whole percussion track into bar-exact loops starting on the Sam."""
    if bars not in ALLOWED_BARS:
        raise ValueError(f"bars must be one of {ALLOWED_BARS}")
    bpm = round_bpm(bpm)
    length = loop_length_samples(bpm, ts, bars, sample_rate)
    duration = len(audio) / sample_rate
    loops: list[Loop] = []
    for start_time in sam_times(beat_times, downbeat_index, bpm, ts, bars, duration):
        if len(loops) >= max_loops:
            break
        if start_time * sample_rate + length > len(audio):
            break  # never pad automatic loops; the last partial bar is dropped
        segment, start = cut_loop(audio, sample_rate, start_time, length)
        if skip_silent and _rms_dbfs(segment) < SILENCE_DBFS:
            continue
        number = len(loops) + 1
        loops.append(Loop(
            number=number,
            filename=loop_filename(song, number, bpm, bars),
            audio=segment,
            sample_rate=sample_rate,
            bpm=bpm,
            bars=bars,
            start_time=start / sample_rate,
            cues=_cues_for(hits, start, length, sample_rate),
        ))
    return loops


def export_region(
    audio: np.ndarray,
    sample_rate: int,
    start_time: float,
    end_time: float,
    bars: int,
    ts: TimeSignature,
    song: str,
    number: int = 1,
    hits: list[dict] | None = None,
) -> Loop:
    """Turn a hand-picked region into a bar-exact loop.

    The region is declared to span ``bars`` bars, so its length defines the
    tempo.  That tempo is rounded to a whole BPM and the loop is re-cut to the
    exact length of ``bars`` bars at that BPM from the (zero-cross snapped) start.
    """
    if end_time <= start_time:
        raise ValueError("region end must be after its start")
    if bars < 1:
        raise ValueError("bars must be at least 1")
    bpm = round_bpm(bars * ts.beats_per_bar * 60.0 / (end_time - start_time))
    if not 30 <= bpm <= 400:
        raise ValueError(f"region implies {bpm:g} BPM for {bars} bar(s); check the bar count")
    length = loop_length_samples(bpm, ts, bars, sample_rate)
    segment, start = cut_loop(audio, sample_rate, start_time, length)
    return Loop(
        number=number,
        filename=loop_filename(song, number, bpm, bars),
        audio=segment,
        sample_rate=sample_rate,
        bpm=bpm,
        bars=bars,
        start_time=start / sample_rate,
        cues=_cues_for(hits, start, length, sample_rate),
    )
