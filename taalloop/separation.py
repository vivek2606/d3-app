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

import functools
import importlib.util
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


@functools.lru_cache(maxsize=1)
def _demucs_model():
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    model.eval()
    return model


def separate_demucs(audio: np.ndarray, sr: int) -> np.ndarray:
    """The htdemucs ``drums`` stem, computed in-process.

    Calling the model directly (instead of the ``demucs`` command line) keeps
    audio file I/O out of Demucs, whose I/O dependencies differ between
    versions and platforms (4.1 needs ``sphn``, which has no Intel Mac build;
    4.0 needs torchaudio plus a system ffmpeg).  Same steps as ``demucs.separate``.
    """
    import torch
    from demucs.apply import apply_model

    model = _demucs_model()
    wav = resample(audio, sr, model.samplerate)
    if wav.shape[1] == 1:
        wav = np.repeat(wav, 2, axis=1)
    mix = torch.from_numpy(np.ascontiguousarray(wav.T, dtype=np.float32))
    ref = mix.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        sources = apply_model(model, ((mix - mean) / std)[None], device="cpu",
                              shifts=1, split=True, overlap=0.25, progress=False)[0]
    drums = sources[model.sources.index("drums")] * std + mean
    return resample(drums.numpy().T, model.samplerate, sr).astype(np.float32)


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
        return separate_demucs(audio, sr), sr, "demucs"
    return separate_hpss(audio, sr), sr, "hpss"
