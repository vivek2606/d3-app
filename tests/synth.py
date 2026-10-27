"""Synthetic tabla-like strokes and grooves for tests."""

import numpy as np


def bayan(sr: int) -> np.ndarray:
    """Low, pitch-bending bass stroke (Ge)."""
    t = np.arange(int(0.6 * sr)) / sr
    freq = 90 + 40 * np.exp(-t * 20)
    return np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 6)


def chanti(sr: int) -> np.ndarray:
    """Bright, ringing harmonic stroke (Na / Tin)."""
    t = np.arange(int(0.5 * sr)) / sr
    tone = sum(np.sin(2 * np.pi * f * t) / (k + 1) for k, f in enumerate([1100, 2200, 3300, 4950]))
    return 0.6 * tone * np.exp(-t * 9)


def slap(sr: int, seed: int = 0) -> np.ndarray:
    """Short noisy closed stroke (Ka / Tete)."""
    t = np.arange(int(0.08 * sr)) / sr
    return np.random.default_rng(seed).standard_normal(len(t)) * np.exp(-t * 80) * 0.5


def groove(sr: int, bpm: float, pattern: list[str], cycles: int, lead_in_beats: int = 0) -> np.ndarray:
    """Render ``pattern`` (one stroke name or '-' per beat) ``cycles`` times, as stereo."""
    makers = {"B": bayan, "C": chanti, "S": slap}
    beat = 60.0 / bpm
    n_beats = lead_in_beats + len(pattern) * cycles
    out = np.zeros(int((n_beats + 2) * beat * sr))
    for i in range(n_beats):
        stroke = "C" if i < lead_in_beats else pattern[(i - lead_in_beats) % len(pattern)]
        for ch in stroke:
            if ch in makers:
                x = makers[ch](sr) * 0.5
                s = int(round(i * beat * sr))
                out[s : s + len(x)] += x[: len(out) - s]
    return np.stack([out, out], axis=1).astype(np.float32)


def render(events: list[tuple[float, str]], sr: int, duration: float) -> np.ndarray:
    """Render (time, stroke) events, stroke in B/C/S, as stereo."""
    makers = {"B": bayan, "C": chanti, "S": slap}
    out = np.zeros(int(duration * sr))
    for time, stroke in events:
        x = makers[stroke](sr) * 0.5
        s = int(round(time * sr))
        out[s : s + len(x)] += x[: len(out) - s]
    return np.stack([out, out], axis=1).astype(np.float32)


KEHERWA = ["B", "B", "C", "S", "C", "S", "B", "C"]  # dha ge na ti na ka dhi na, in eighths


def keherwa_song(bpm: float, bars: int, toda_bars=(), roll_bars=()) -> list[tuple[float, str]]:
    """4/4 keherwa in eighths; toda bars switch to sixteenths in the second half,
    roll bars end with a beat of thirty-second notes."""
    beat = 60.0 / bpm
    events = []
    for bar in range(bars):
        t0 = bar * 4 * beat
        if bar in roll_bars:
            events += [(t0 + i * beat / 2, s) for i, s in enumerate(KEHERWA[:6])]
            events += [(t0 + 3 * beat + i * beat / 8, "C") for i in range(8)]
        elif bar in toda_bars:
            events += [(t0 + i * beat / 2, s) for i, s in enumerate(KEHERWA[:4])]
            events += [(t0 + 2 * beat + i * beat / 4, "CS"[i % 2]) for i in range(8)]
        else:
            events += [(t0 + i * beat / 2, s) for i, s in enumerate(KEHERWA)]
    return events
