"""Decoding uploads (any audio or video container) and resampling."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

WORKING_SAMPLE_RATE = 44100


class DecodeError(RuntimeError):
    pass


def decode_to_wav(src: Path, dst: Path, sample_rate: int = WORKING_SAMPLE_RATE) -> None:
    """Extract the audio track of ``src`` (mp3, flac, m4a, mp4, mkv, ...) to float WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise DecodeError("ffmpeg is not installed or not on PATH")
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-ac", "2", "-ar", str(sample_rate), "-c:a", "pcm_f32le",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dst.exists():
        raise DecodeError(f"could not decode the uploaded file: {result.stderr.strip()[-500:]}")


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV as float32 (frames, channels)."""
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return audio, sr


def to_mono(audio: np.ndarray) -> np.ndarray:
    return audio.mean(axis=1) if audio.ndim == 2 else audio


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return audio
    return librosa.resample(audio, orig_sr=sr_in, target_sr=sr_out, axis=0, res_type="soxr_hq")
