"""Transient detection and stroke classification (Bayan / Chanti / Slap).

Every onset in the percussion stem is classified from the first ~100 ms of its
spectrum with a few transparent rules:

* **Bayan**  - most of the energy is below ~250 Hz (bass drum: Ge/Ghe, the low
  half of Dha/Dhin, dholak/kick).
* **Slap**   - noisy, broadband and short (closed strokes: Ka/Kat/Tete, claps,
  dholak slaps, snares).
* **Chanti** - everything else: tonal, bright, ringing strokes on the dayan rim
  (Na/Ta/Tin, bells, rims).

The hits drive roll/toda detection and label the slice (cue) markers embedded
in exported WAVs.  The rules are heuristics, not a trained classifier.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

CLASSES = ("Bayan", "Chanti", "Slap")
HOP = 256
FEATURE_MS = 100
MAX_HIT_SECONDS = 1.5
PRE_ECHO_SECONDS = 0.12


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
    # Noisiness is the defining trait of a slap; a short decay alone is not
    # enough because separation trims the ring of tonal strokes too.
    if flatness > 0.2 or (decay < 0.06 and flatness > 0.08 and centroid > 1500):
        return "Slap"
    return "Chanti"


def detect_hits(mono: np.ndarray, sr: int, suppress_pre_echo: bool = False) -> list[Hit]:
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
    return _drop_pre_echoes(hits) if suppress_pre_echo else hits


def _drop_pre_echoes(hits: list[Hit]) -> list[Hit]:
    """Remove faint onsets just before a much louder one.

    STFT-based separation (HPSS) smears a little energy ~one window ahead of a
    strong attack, which the onset detector reports as a separate quiet hit.
    Only applied after HPSS: on clean stems it would eat soft strokes in rolls.
    """
    kept: list[Hit] = []
    for i, hit in enumerate(hits):
        nxt = hits[i + 1] if i + 1 < len(hits) else None
        if nxt is not None and hit.gap < PRE_ECHO_SECONDS and hit.peak < 0.5 * nxt.peak:
            continue
        kept.append(hit)
    for i, hit in enumerate(kept[:-1]):
        hit.gap = kept[i + 1].time - hit.time
    return kept
