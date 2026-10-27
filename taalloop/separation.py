"""Drum / percussion stem extraction.

Two back-ends:

* ``demucs`` - Meta's Hybrid Transformer Demucs (``htdemucs``) ``drums`` stem.
  Far better quality, including on tabla/dholak, but needs PyTorch
  (``pip install -r requirements-demucs.txt``).
* ``hpss``  - librosa harmonic/percussive source separation.  Always available
  and fast, but pitched strokes (the ringing dayan "Na"/"Tin") partly leak into
  the harmonic part, so it is a fallback rather than the recommended path.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np

from .audio_io import read_audio, resample

METHODS = ("auto", "demucs", "hpss")


class SeparationError(RuntimeError):
    pass


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def available_methods() -> list[str]:
    return ["demucs", "hpss"] if demucs_available() else ["hpss"]


def separate_hpss(audio: np.ndarray, sr: int) -> np.ndarray:
    channels = []
    for ch in range(audio.shape[1]):
        stft = librosa.stft(audio[:, ch])
        # margin > 1 on the percussive side keeps only clearly transient energy.
        _, percussive = librosa.decompose.hpss(stft, margin=(1.0, 2.0))
        channels.append(librosa.istft(percussive, length=audio.shape[0]))
    return np.stack(channels, axis=1).astype(np.float32)


def separate_demucs(wav_path: Path, sr: int) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems", "drums", "-n", "htdemucs",
            "-o", tmp, str(wav_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SeparationError(f"demucs failed: {result.stderr.strip()[-500:]}")
        stems = list(Path(tmp).glob("htdemucs/*/drums.wav"))
        if not stems:
            raise SeparationError("demucs did not produce a drums stem")
        drums, stem_sr = read_audio(stems[0])
    return resample(drums, stem_sr, sr)


def separate(wav_path: Path, method: str = "auto") -> tuple[np.ndarray, int, str]:
    """Return (percussion audio (frames, channels), sample_rate, method used)."""
    if method not in METHODS:
        raise SeparationError(f"unknown separation method {method!r}")
    audio, sr = read_audio(wav_path)
    if method == "auto":
        method = "demucs" if demucs_available() else "hpss"
    if method == "demucs":
        if not demucs_available():
            raise SeparationError("demucs is not installed (pip install -r requirements-demucs.txt)")
        return separate_demucs(wav_path, sr), sr, "demucs"
    return separate_hpss(audio, sr), sr, "hpss"
