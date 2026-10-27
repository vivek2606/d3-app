"""Transient detection and one-shot extraction (Bayan / Chanti / Slap).

Every onset in the percussion stem is classified from the first ~100 ms of its
spectrum with a few transparent rules:

* **Bayan**  - most of the energy is below ~250 Hz (bass drum: Ge/Ghe, the low
  half of Dha/Dhin, dholak/kick).
* **Slap**   - noisy, broadband and short (closed strokes: Ka/Kat/Tete, claps,
  dholak slaps, snares).
* **Chanti** - everything else: tonal, bright, ringing strokes on the dayan rim
  (Na/Ta/Tin, bells, rims).

The rules are heuristics, not a trained classifier, so the labels are a starting
point for building a kit rather than ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

CLASSES = ("Bayan", "Chanti", "Slap")
HOP = 256
FEATURE_MS = 100
MAX_HIT_SECONDS = 1.5
TAIL_DB = -48.0
PRE_ROLL_MS = 1.0


@dataclass
class Hit:
    time: float  # seconds
    label: str
    peak: float
    gap: float  # seconds until the next onset (how isolated the hit is)
    low_ratio: float
    centroid: float
    flatness: float
    decay: float

    def to_dict(self) -> dict:
        return {k: round(float(v), 5) if not isinstance(v, str) else v for k, v in self.__dict__.items()}


def hit_features(segment: np.ndarray, sr: int) -> tuple[float, float, float, float]:
    """(low-band energy ratio, spectral centroid Hz, spectral flatness, decay seconds)."""
    window = segment[: int(sr * FEATURE_MS / 1000)]
    if len(window) < 32 or not np.any(window):
        return 0.0, 0.0, 1.0, 0.0
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window)), n=4096)) ** 2
    freqs = np.fft.rfftfreq(4096, 1 / sr)
    total = spectrum.sum() + 1e-12
    low_ratio = float(spectrum[freqs < 250].sum() / total)
    centroid = float((freqs * spectrum).sum() / total)
    band = spectrum[(freqs > 150) & (freqs < 10000)] + 1e-12
    flatness = float(np.exp(np.mean(np.log(band))) / np.mean(band))

    env = np.abs(segment)
    smooth = np.convolve(env, np.ones(int(sr * 0.005)) / int(sr * 0.005), mode="same")
    peak_idx = int(np.argmax(smooth))
    below = np.nonzero(smooth[peak_idx:] < smooth[peak_idx] * 0.1)[0]  # -20 dB
    decay = (below[0] if len(below) else len(smooth) - peak_idx) / sr
    return low_ratio, centroid, flatness, float(decay)


def classify(low_ratio: float, centroid: float, flatness: float, decay: float) -> str:
    if low_ratio > 0.45:
        return "Bayan"
    if flatness > 0.2 or (decay < 0.06 and centroid > 1500):
        return "Slap"
    return "Chanti"


def detect_hits(mono: np.ndarray, sr: int) -> list[Hit]:
    # Leading silence lets the onset envelope see a hit that starts at sample 0.
    pad = int(0.1 * sr)
    padded = np.concatenate([np.zeros(pad, mono.dtype), mono])
    onset_env = librosa.onset.onset_strength(y=padded, sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, backtrack=True, units="samples",
        wait=max(1, int(0.04 * sr / HOP)), delta=0.1,
    )
    onsets = np.clip(onsets - pad, 0, len(mono) - 1)
    hits = []
    global_peak = float(np.max(np.abs(mono))) or 1.0
    for i, onset in enumerate(onsets):
        nxt = onsets[i + 1] if i + 1 < len(onsets) else len(mono)
        segment = mono[onset : min(nxt, onset + int(sr * MAX_HIT_SECONDS))]
        peak = float(np.max(np.abs(segment))) if len(segment) else 0.0
        if peak < global_peak * 0.03:  # ignore bleed / reverb tails (< -30 dB)
            continue
        feats = hit_features(segment, sr)
        hits.append(Hit(onset / sr, classify(*feats), peak, (nxt - onset) / sr, *feats))
    return hits


def extract_one_shot(audio: np.ndarray, sr: int, time: float, next_time: float | None,
                     normalize: bool = True) -> np.ndarray:
    """Cut a single hit, trim its tail at -48 dB and fade it out."""
    start = max(0, int(round((time - PRE_ROLL_MS / 1000) * sr)))
    end = len(audio) if next_time is None else int(round(next_time * sr))
    end = min(end, start + int(sr * MAX_HIT_SECONDS))
    segment = audio[start:end].copy()
    if len(segment) == 0:
        return segment
    env = np.abs(segment).max(axis=1)
    peak = env.max()
    if peak > 0:
        loud = np.nonzero(env > peak * 10 ** (TAIL_DB / 20))[0]
        segment = segment[: loud[-1] + 1]
    n_in = min(len(segment), int(sr * 0.0005))
    n_out = min(len(segment), max(1, int(sr * 0.01)))
    if n_in:
        segment[:n_in] *= np.linspace(0, 1, n_in)[:, None]
    segment[-n_out:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, n_out)))[:, None]
    if normalize and peak > 0:
        segment *= 10 ** (-1 / 20) / np.abs(segment).max()  # -1 dBFS
    return segment


def select_best(hits: list[dict], per_class: int) -> dict[str, list[dict]]:
    """The loudest, most isolated hits of each class (overlapping hits make bad one-shots)."""
    chosen: dict[str, list[dict]] = {}
    for label in CLASSES:
        candidates = [h for h in hits if h["label"] == label]
        candidates.sort(key=lambda h: h["peak"] * min(h["gap"], 0.3) / 0.3, reverse=True)
        chosen[label] = sorted(candidates[:per_class], key=lambda h: h["time"])
    return chosen


def kit_map_text(song: str, files: dict[str, list[str]]) -> str:
    """Suggested key layout for a Nautilus drum kit / FPC (one class per octave)."""
    base_notes = {"Bayan": 36, "Chanti": 48, "Slap": 60}  # C2, C3, C4
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    lines = [f"{song} - suggested drum-kit key map", ""]
    for label in CLASSES:
        for i, name in enumerate(files.get(label, [])):
            note = base_notes[label] + i
            lines.append(f"{names[note % 12]}{note // 12 - 1:<3} {name}")
    return "\n".join(lines) + "\n"
