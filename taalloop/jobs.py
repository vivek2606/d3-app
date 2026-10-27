"""Upload processing: decode -> separate -> analyse -> detect hits.

Each upload becomes a job directory under ``DATA_DIR/jobs/<id>`` holding the
original file, the decoded mix, the percussion stem and ``meta.json``.  Jobs run
one at a time on a background thread because separation is CPU/GPU heavy.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_io, hits, rhythm, separation
from .wavio import write_wav

DATA_DIR = Path(os.environ.get("TAALLOOP_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


class JobNotFound(KeyError):
    pass


class JobStore:
    def __init__(self, root: Path = DATA_DIR / "jobs", workers: int = 1):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()

    # -- metadata -----------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise JobNotFound(job_id)
        path = self.root / job_id
        if not (path / "meta.json").exists():
            raise JobNotFound(job_id)
        return path

    def get(self, job_id: str) -> dict:
        return json.loads((self.job_dir(job_id) / "meta.json").read_text())

    def _update(self, job_dir: Path, **fields) -> dict:
        with self._lock:
            meta_path = job_dir / "meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            meta.update(fields)
            tmp = meta_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(meta))
            tmp.replace(meta_path)
            return meta

    # -- lifecycle ----------------------------------------------------------
    def create(self, upload_path: Path, filename: str, method: str = "auto") -> dict:
        job_id = uuid.uuid4().hex
        job_dir = self.root / job_id
        job_dir.mkdir()
        source = job_dir / f"source{Path(filename).suffix.lower()[:8]}"
        shutil.move(str(upload_path), source)
        meta = self._update(
            job_dir, id=job_id, filename=filename, song=Path(filename).stem,
            status="queued", message="Waiting to start", separation_requested=method,
        )
        self._executor.submit(self._run, job_dir, source, method)
        return meta

    def _run(self, job_dir: Path, source: Path, method: str) -> None:
        try:
            self._update(job_dir, status="processing", message="Decoding audio")
            mix = job_dir / "mix.wav"
            audio_io.decode_to_wav(source, mix)

            slow = method == "demucs" or (method == "auto" and separation.demucs_available())
            self._update(job_dir, message="Extracting percussion with Demucs (can take several minutes)"
                         if slow else "Extracting percussion (HPSS)")
            drums, sr, used = separation.separate(mix, method)
            sf.write(str(job_dir / "drums.wav"), drums, sr, subtype="FLOAT")
            # Lightweight 16-bit copy for the browser waveform / audition player.
            write_wav(job_dir / "preview.wav", drums, sr)

            self._update(job_dir, separation=used, message="Detecting tempo and Sam")
            mono = audio_io.to_mono(drums)
            analysis = rhythm.analyze(mono, sr)

            self._update(job_dir, message="Detecting drum hits")
            detected = [h.to_dict() for h in hits.detect_hits(mono, sr, suppress_pre_echo=used == "hpss")]

            self._update(
                job_dir,
                status="ready",
                message="Ready",
                sample_rate=sr,
                duration=len(drums) / sr,
                bpm=analysis.bpm,
                beat_times=rhythm.align_beats_to_onsets(analysis.beat_times, [h["time"] for h in detected]),
                downbeat_index=analysis.downbeat_index,
                hits=detected,
            )
            mix.unlink(missing_ok=True)
        except Exception as exc:  # surfaced to the UI via meta.json
            traceback.print_exc()
            self._update(job_dir, status="error", message=str(exc) or exc.__class__.__name__)

    # -- audio access ---------------------------------------------------------
    def drums(self, job_id: str, sample_rate: int) -> np.ndarray:
        """Percussion stem resampled to ``sample_rate`` (cached on disk)."""
        job_dir = self.job_dir(job_id)
        cached = job_dir / f"drums_{sample_rate}.wav"
        if cached.exists():
            return audio_io.read_audio(cached)[0]
        audio, sr = audio_io.read_audio(job_dir / "drums.wav")
        audio = audio_io.resample(audio, sr, sample_rate).astype(np.float32)
        sf.write(str(cached), audio, sample_rate, subtype="FLOAT")
        return audio
