"""Toda (fill) and roll detection on the beat grid.

* **Roll** - a run of strokes faster than the groove's normal subdivision
  (every gap <= min(beat / 5, 90 ms), at least 6 strokes, at least half a
  beat long).  Exported from the beat before it to the beat after it, so the
  file is a whole number of beats and stays in sync when time-stretched.
* **Toda** - a bar whose stroke pattern breaks away from the song's typical
  theka bar and is busier than it.  Each bar's strokes are placed on a grid of
  4 slots per beat; the typical bar is the slot-wise median over the song.  A
  bar is a toda when its pattern differs from the typical one (cosine distance)
  well beyond the song's normal bar-to-bar variation, and holds more strokes
  than usual.  One or two consecutive bars
  form a toda; longer runs are a new section, not a fill.

Like the hit classes, these are transparent heuristics: check them by ear in
the UI before relying on them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .rhythm import TimeSignature
from .slicer import format_bpm, round_bpm, safe_song_name, sam_times

SLOTS_PER_BEAT = 4
ROLL_MIN_STROKES = 6
ROLL_MAX_GAP_SECONDS = 0.09
ROLL_MAX_BEATS = 8
TODA_MIN_DISTANCE = 0.12
TODA_MIN_DENSITY = 1.15
TODA_MAX_BARS = 2


@dataclass
class Phrase:
    kind: str  # "Toda" or "Roll"
    number: int
    start: float  # seconds, on the beat grid
    beats: int
    bpm: float
    last_stroke: float | None = None  # rolls: where the strokes stop (the file runs on for the tail)

    @property
    def end(self) -> float:
        return self.start + self.beats * 60.0 / self.bpm

    @property
    def core_end(self) -> float:
        """End of the actual playing; loops are only excluded for overlapping this."""
        return self.end if self.last_stroke is None else min(self.end, self.last_stroke + 0.05)

    def filename(self, song: str, ts: TimeSignature) -> str:
        if self.kind == "Toda":
            length = f"{self.beats // ts.beats_per_bar}Bar"
        else:
            length = f"{self.beats}Beat"
        return f"{safe_song_name(song)}_{self.kind}{self.number:02d}_{format_bpm(self.bpm)}BPM_{length}.wav"

    def to_dict(self, song: str, ts: TimeSignature) -> dict:
        return {"kind": self.kind, "number": self.number, "start": self.start, "end": self.end,
                "beats": self.beats, "filename": self.filename(song, ts)}


def beat_grid(beat_times: list[float], bpm: float, duration: float) -> np.ndarray:
    """Tracked beats, extended at both ends with the tempo so every time has a beat."""
    period = 60.0 / bpm
    if not beat_times:
        return np.arange(0.0, duration + period, period)
    before = np.arange(beat_times[0] - period, -period, -period)[::-1]
    after = np.arange(beat_times[-1] + period, duration + period, period)
    return np.concatenate([before, beat_times, after])


def detect_rolls(hits: list[dict], beat_times: list[float], bpm: float, duration: float) -> list[Phrase]:
    bpm = round_bpm(bpm)
    period = 60.0 / bpm
    max_gap = min(period / 5, ROLL_MAX_GAP_SECONDS)
    grid = beat_grid(beat_times, bpm, duration)
    times = [h["time"] for h in hits]

    runs, run = [], [0] if times else []
    for i in range(1, len(times)):
        if times[i] - times[i - 1] <= max_gap:
            run.append(i)
        else:
            runs.append(run)
            run = [i]
    if run:
        runs.append(run)

    rolls: list[Phrase] = []
    for run in runs:
        first, last = times[run[0]], times[run[-1]]
        if len(run) < ROLL_MIN_STROKES or last - first < period / 2:
            continue
        # Onset backtracking and grid jitter can put the first stroke a hair ahead
        # of its beat; anything earlier is a pickup and pulls in the beat before.
        tolerance = min(0.05, period / 8)
        start = float(grid[max(0, np.searchsorted(grid, first + tolerance, side="right") - 1)])
        beats = min(ROLL_MAX_BEATS, max(1, math.ceil((last + 0.1 - start) / period)))
        if rolls and start < rolls[-1].end:
            continue  # overlaps the previous roll's window
        rolls.append(Phrase("Roll", len(rolls) + 1, start, beats, bpm, last_stroke=last))
    return rolls


def bar_patterns(hits: list[dict], bar_starts: list[float], period: float, beats_per_bar: int) -> np.ndarray:
    slots = beats_per_bar * SLOTS_PER_BEAT
    patterns = np.zeros((len(bar_starts), slots))
    for hit in hits:
        bar = np.searchsorted(bar_starts, hit["time"] + period / (2 * SLOTS_PER_BEAT), side="right") - 1
        if bar < 0:
            continue
        slot = int(round((hit["time"] - bar_starts[bar]) / period * SLOTS_PER_BEAT))
        if 0 <= slot < slots:
            patterns[bar, slot] = max(patterns[bar, slot], hit["peak"])
    return patterns


def detect_todas(hits: list[dict], beat_times: list[float], downbeat_index: int, bpm: float,
                 ts: TimeSignature, duration: float) -> list[Phrase]:
    bpm = round_bpm(bpm)
    period = 60.0 / bpm
    bar_starts = sam_times(beat_times, downbeat_index, bpm, ts, 1, duration)
    bar_starts = [t for t in bar_starts if t + ts.beats_per_bar * period <= duration + 1e-6]
    if len(bar_starts) < 4:
        return []
    patterns = bar_patterns(hits, bar_starts, period, ts.beats_per_bar)
    counts = (patterns > 0).sum(axis=1)
    active = counts >= 2
    if active.sum() < 4:
        return []

    typical = np.median(patterns[active], axis=0)
    norms = np.linalg.norm(patterns, axis=1) * (np.linalg.norm(typical) or 1.0)
    distance = 1.0 - (patterns @ typical) / np.where(norms > 0, norms, 1.0)
    median_count = float(np.median(counts[active]))
    # Adaptive threshold: a toda must stand out from this song's normal bar-to-bar variation.
    typical_distance = float(np.median(distance[active]))
    spread = 1.4826 * float(np.median(np.abs(distance[active] - typical_distance)))
    threshold = max(TODA_MIN_DISTANCE, typical_distance + 3 * spread)
    flagged = active & (distance >= threshold) & (counts >= TODA_MIN_DENSITY * median_count)

    todas: list[Phrase] = []
    i = 0
    while i < len(flagged):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(flagged) and flagged[j + 1]:
            j += 1
        bars = j - i + 1
        if bars <= TODA_MAX_BARS:
            todas.append(Phrase("Toda", len(todas) + 1, float(bar_starts[i]), bars * ts.beats_per_bar, bpm))
        i = j + 1
    return todas


def detect_phrases(hits: list[dict], beat_times: list[float], downbeat_index: int, bpm: float,
                   ts: TimeSignature, duration: float) -> list[Phrase]:
    todas = detect_todas(hits, beat_times, downbeat_index, bpm, ts, duration)
    rolls = detect_rolls(hits, beat_times, bpm, duration)
    return todas + rolls

