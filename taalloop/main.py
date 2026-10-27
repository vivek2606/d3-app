"""FastAPI app: upload, job status, audio preview and the three exports."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import phrases, separation, slicer
from .jobs import JobNotFound, JobStore
from .rhythm import TIME_SIGNATURES, get_time_signature, shift_grid
from .wavio import Acid, encode_wav

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="TaalLoop")
store = JobStore()

SampleRate = Literal[48000, 44100]
TimeSig = Literal["4/4", "6/8", "7/8"]


class GridParams(BaseModel):
    """The tempo/Sam grid the user confirmed in the UI."""
    time_signature: TimeSig = "4/4"
    bpm: float | None = Field(None, gt=30, lt=400, description="Override the detected tempo")
    sam_time: float | None = Field(None, ge=0, description="Override the Sam: time of any beat 1, seconds")


class LoopsRequest(GridParams):
    bars: Literal[1, 2, 4] = 2
    sample_rate: SampleRate = 48000
    embed_cues: bool = True
    skip_fills: bool = Field(True, description="Leave out loops containing a toda or roll")
    max_loops: int = Field(64, ge=1, le=256)


class RegionRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    bars: Literal[1, 2, 4] = 2
    time_signature: TimeSig = "4/4"
    sample_rate: SampleRate = 48000
    embed_cues: bool = True
    loop_number: int = Field(1, ge=1, le=99)


class FillsRequest(GridParams):
    sample_rate: SampleRate = 48000
    embed_cues: bool = True
    kinds: list[Literal["Toda", "Roll"]] = ["Toda", "Roll"]


def _ready_job(job_id: str) -> dict:
    try:
        meta = store.get(job_id)
    except JobNotFound:
        raise HTTPException(404, "job not found") from None
    if meta["status"] != "ready":
        raise HTTPException(409, f"job is {meta['status']}: {meta.get('message', '')}")
    return meta


def _grid(meta: dict, req: GridParams):
    ts = get_time_signature(req.time_signature)
    if req.sam_time is not None:
        beats, downbeat = shift_grid(meta["beat_times"], req.sam_time)
    else:
        beats, downbeat = meta["beat_times"], meta["downbeat_index"][ts.id]
    return ts, beats, downbeat, req.bpm or meta["bpm"]


def _phrases(meta: dict, req: GridParams) -> list[phrases.Phrase]:
    ts, beats, downbeat, bpm = _grid(meta, req)
    return phrases.detect_phrases(meta["hits"], beats, downbeat, bpm, ts, meta["duration"])


def _acid(beats: int, bpm: float, ts) -> Acid:
    # One matra = one DAW (quarter-note) beat at the file's BPM, so a Dadra bar
    # is 6/4 and a Rupak bar 7/4 in DAW terms.
    return Acid(beats=beats, tempo=bpm, numerator=ts.beats_per_bar, denominator=4)


def _loop_wav(loop: slicer.Loop, ts) -> bytes:
    return encode_wav(loop.audio, loop.sample_rate, cues=loop.cues, loop=True,
                      acid=_acid(loop.bars * ts.beats_per_bar, loop.bpm, ts))


def _wav_response(data: bytes, filename: str) -> Response:
    return Response(data, media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _zip_response(files: dict[str, bytes], filename: str) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # WAV barely compresses
        for name, data in files.items():
            zf.writestr(name, data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/config")
def config() -> dict:
    return {
        "time_signatures": [
            {"id": ts.id, "label": ts.label, "beats_per_bar": ts.beats_per_bar}
            for ts in TIME_SIGNATURES.values()
        ],
        "bars": list(slicer.ALLOWED_BARS),
        "sample_rates": list(slicer.ALLOWED_SAMPLE_RATES),
        "separation_methods": separation.available_methods(),
    }


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), separation_method: str = Form("auto")) -> dict:
    if separation_method not in separation.METHODS:
        raise HTTPException(422, f"separation_method must be one of {separation.METHODS}")
    if separation_method == "demucs" and not separation.demucs_available():
        raise HTTPException(422, "demucs is not installed on this server")
    with tempfile.NamedTemporaryFile(delete=False, dir=store.root) as tmp:
        while chunk := await file.read(1 << 20):
            tmp.write(chunk)
    return store.create(Path(tmp.name), file.filename or "Song", separation_method)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return store.get(job_id)
    except JobNotFound:
        raise HTTPException(404, "job not found") from None


@app.get("/api/jobs/{job_id}/preview.wav")
def preview(job_id: str) -> FileResponse:
    _ready_job(job_id)
    return FileResponse(store.job_dir(job_id) / "preview.wav", media_type="audio/wav")


@app.post("/api/jobs/{job_id}/phrases")
def list_phrases(job_id: str, req: GridParams) -> list[dict]:
    meta = _ready_job(job_id)
    ts = get_time_signature(req.time_signature)
    return [p.to_dict(meta["song"], ts) for p in _phrases(meta, req)]


@app.post("/api/jobs/{job_id}/export/loops")
def export_loops(job_id: str, req: LoopsRequest) -> Response:
    meta = _ready_job(job_id)
    ts, beats, downbeat, bpm = _grid(meta, req)
    audio = store.drums(job_id, req.sample_rate)
    loops = slicer.slice_loops(
        audio, req.sample_rate, beats, downbeat, bpm, ts, req.bars, meta["song"],
        hits=meta["hits"] if req.embed_cues else None,
        exclude=_phrases(meta, req) if req.skip_fills else None,
        max_loops=req.max_loops,
    )
    if not loops:
        raise HTTPException(422, "no loops found; check the tempo and Sam, or allow loops with fills")
    files = {lp.filename: _loop_wav(lp, ts) for lp in loops}
    name = f"{slicer.safe_song_name(meta['song'])}_{loops[0].bars}Bar_Loops.zip"
    return _zip_response(files, name)


@app.post("/api/jobs/{job_id}/export/region")
def export_region(job_id: str, req: RegionRequest) -> Response:
    meta = _ready_job(job_id)
    ts = get_time_signature(req.time_signature)
    audio = store.drums(job_id, req.sample_rate)
    try:
        loop = slicer.export_region(
            audio, req.sample_rate, req.start, req.end, req.bars, ts, meta["song"],
            number=req.loop_number, hits=meta["hits"] if req.embed_cues else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    data = _loop_wav(loop, ts)
    response = _wav_response(data, loop.filename)
    response.headers["X-Loop-BPM"] = slicer.format_bpm(loop.bpm)
    return response


@app.post("/api/jobs/{job_id}/export/fills")
def export_fills(job_id: str, req: FillsRequest) -> Response:
    """Toda and roll phrases: whole bars / whole beats, not looped."""
    meta = _ready_job(job_id)
    ts = get_time_signature(req.time_signature)
    found = [p for p in _phrases(meta, req) if p.kind in req.kinds]
    if not found:
        raise HTTPException(422, "no toda or roll phrases were detected with this tempo and Sam")
    sr = req.sample_rate
    audio = store.drums(job_id, sr)
    hits = meta["hits"] if req.embed_cues else None
    files = {}
    for phrase in found:
        length = int(round(phrase.beats * 60.0 / phrase.bpm * sr))
        segment, start = slicer.cut_loop(audio, sr, phrase.start, length)
        cues = slicer.cues_for(hits, start, length, sr)
        files[f"{phrase.kind}/{phrase.filename(meta['song'], ts)}"] = encode_wav(
            segment, sr, cues=cues, acid=_acid(phrase.beats, phrase.bpm, ts))
    return _zip_response(files, f"{slicer.safe_song_name(meta['song'])}_Toda_Rolls.zip")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
