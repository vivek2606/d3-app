"""Tempo, beat and Sam (downbeat) estimation with taal-aware bar lengths."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

ANALYSIS_SAMPLE_RATE = 22050
HOP = 512


@dataclass(frozen=True)
class TimeSignature:
    id: str
    label: str
    beats_per_bar: int
    # Expected bass (bayan / kick) weight on each beat of the cycle, starting at
    # the Sam.  Used to guess which tracked beat is beat 1.
    bass_template: tuple[float, ...]


TIME_SIGNATURES: dict[str, TimeSignature] = {
    ts.id: ts
    for ts in (
        # Keherwa: dha ge na ti | na ka dhi na.  A 4-beat bar is half a cycle, so
        # an 8-beat cycle is simply a 2-bar loop.
        TimeSignature("4/4", "4/4 (Keherwa - 4 or 8 beats)", 4, (1.0, 0.4, 0.6, 0.3)),
        # Dadra: dha dhin na | dha tin na - bayan on 1, 2, 4; khali (open) on 5.
        TimeSignature("6/8", "3/4 or 6/8 (Dadra - 6 beats)", 6, (1.0, 0.7, 0.2, 0.8, 0.0, 0.2)),
        # Rupak: tin tin na | dhi na | dhi na - the Sam falls on the khali, so the
        # bass strokes are on beats 4 and 6, not on beat 1.
        TimeSignature("7/8", "7/8 (Rupak - 7 beats)", 7, (0.0, 0.0, 0.1, 1.0, 0.2, 1.0, 0.2)),
    )
}


def get_time_signature(ts_id: str) -> TimeSignature:
    try:
        return TIME_SIGNATURES[ts_id]
    except KeyError:
        raise ValueError(f"unsupported time signature {ts_id!r}") from None


@dataclass
class RhythmAnalysis:
    bpm: float
    beat_times: list[float]
    # Index into beat_times of the estimated Sam, per time signature id.
    downbeat_index: dict[str, int]


def _envelopes(mono: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    y = librosa.resample(mono, orig_sr=sr, target_sr=ANALYSIS_SAMPLE_RATE) if sr != ANALYSIS_SAMPLE_RATE else mono
    onset_env = librosa.onset.onset_strength(y=y, sr=ANALYSIS_SAMPLE_RATE, hop_length=HOP, aggregate=np.median)
    mel = librosa.feature.melspectrogram(y=y, sr=ANALYSIS_SAMPLE_RATE, hop_length=HOP, n_mels=64, fmax=8000)
    mel_freqs = librosa.mel_frequencies(n_mels=64, fmax=8000)
    low = librosa.power_to_db(mel[mel_freqs < 220])
    bass_env = librosa.onset.onset_strength(S=low, sr=ANALYSIS_SAMPLE_RATE, hop_length=HOP)
    return onset_env, bass_env


def estimate_downbeat(bass_env: np.ndarray, beat_frames: np.ndarray, ts: TimeSignature) -> int:
    """Pick the beat phase whose bass-accent pattern best matches the taal template."""
    bpb = ts.beats_per_bar
    if len(beat_frames) < bpb:
        return 0
    # Strongest bass onset within +/-2 frames of each beat.
    padded = np.pad(bass_env, 2)
    strength = np.array([padded[f : f + 5].max() for f in np.clip(beat_frames, 0, len(bass_env) - 1)])
    usable = (len(strength) // bpb) * bpb
    profile = strength[:usable].reshape(-1, bpb).mean(axis=0)  # mean strength per position
    template = np.asarray(ts.bass_template)
    profile = profile - profile.mean()
    template = template - template.mean()
    # Rolling the profile by p aligns tracked beat p with template position 0.
    scores = [float(np.dot(np.roll(profile, -p), template)) for p in range(bpb)]
    return int(np.argmax(scores))


def analyze(mono: np.ndarray, sr: int) -> RhythmAnalysis:
    onset_env, bass_env = _envelopes(mono, sr)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=ANALYSIS_SAMPLE_RATE, hop_length=HOP
    )
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=ANALYSIS_SAMPLE_RATE, hop_length=HOP)
    downbeats = {ts_id: estimate_downbeat(bass_env, beat_frames, ts) for ts_id, ts in TIME_SIGNATURES.items()}
    return RhythmAnalysis(bpm=bpm, beat_times=[float(t) for t in beat_times], downbeat_index=downbeats)
