import io
import time
import zipfile

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from taalloop import jobs, main
from taalloop.wavio import read_wav_info

from .synth import keherwa_song, render


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", jobs.JobStore(tmp_path / "jobs"))
    return TestClient(main.app)


@pytest.fixture()
def ready_job(client):
    audio = render(keherwa_song(120, 12, toda_bars=(3, 7), roll_bars=(9,)), 44100, 25.0)
    buf = io.BytesIO()
    sf.write(buf, audio, 44100, format="WAV")
    res = client.post("/api/jobs", files={"file": ("Test Song.wav", buf.getvalue(), "audio/wav")},
                      data={"separation_method": "hpss"})
    assert res.status_code == 200, res.text
    job_id = res.json()["id"]
    for _ in range(480):  # up to 2 min: a fresh env JIT-compiles librosa's numba code
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("ready", "error"):
            break
        time.sleep(0.25)
    assert job["status"] == "ready", job
    return job


def test_config_lists_supported_options(client):
    cfg = client.get("/api/config").json()
    assert [t["beats_per_bar"] for t in cfg["time_signatures"]] == [4, 6, 7]
    assert cfg["bars"] == [1, 2, 4] and cfg["sample_rates"] == [48000, 44100]
    assert "hpss" in cfg["separation_methods"]


def test_frontend_is_served(client):
    assert "Download Selected Loop (16-bit WAV)" in client.get("/").text


def test_full_pipeline(client, ready_job):
    job_id = ready_job["id"]
    assert ready_job["bpm"] == pytest.approx(120, rel=0.05)
    assert ready_job["separation"] == "hpss"
    assert client.get(f"/api/jobs/{job_id}/preview.wav").status_code == 200

    res = client.post(f"/api/jobs/{job_id}/export/loops",
                      json={"time_signature": "4/4", "bars": 2, "sample_rate": 48000, "bpm": 120})
    assert res.status_code == 200, res.text
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert names[0] == "Test-Song_Loop01_120BPM_2Bar.wav"
    info = read_wav_info(zf.read(names[0]))
    assert (info.format_tag, info.bits_per_sample, info.sample_rate) == (1, 16, 48000)
    assert info.frames == 4 * 48000 and info.loop == (0, info.frames - 1)
    assert (info.acid.beats, info.acid.tempo, info.acid.numerator, info.acid.denominator) == (8, 120, 4, 4)
    assert info.cues  # slice markers on the transients

    res = client.post(f"/api/jobs/{job_id}/export/region",
                      json={"start": 2.0, "end": 4.0, "bars": 1, "time_signature": "4/4",
                            "sample_rate": 44100, "loop_number": 7})
    assert res.status_code == 200, res.text
    assert 'filename="Test-Song_Loop07_120BPM_1Bar.wav"' in res.headers["content-disposition"]
    info = read_wav_info(res.content)
    assert info.frames == 2 * 44100 and info.acid.beats == 4

    grid = {"time_signature": "4/4", "bpm": 120, "sam_time": 0.0}
    found = client.post(f"/api/jobs/{job_id}/phrases", json=grid).json()
    kinds = sorted({p["kind"] for p in found})
    assert kinds == ["Roll", "Toda"], found

    res = client.post(f"/api/jobs/{job_id}/export/fills", json=grid)
    assert res.status_code == 200, res.text
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert "Toda/Test-Song_Toda01_120BPM_1Bar.wav" in names
    roll = next(n for n in names if n.startswith("Roll/"))
    info = read_wav_info(zf.read(roll))
    beats = int(roll.split("_")[-1].removesuffix("Beat.wav"))
    assert info.frames == beats * 24000 and info.loop is None  # whole beats at 48 kHz, one-shot
    assert info.acid.beats == beats and info.acid.tempo == 120  # still follows the project tempo

    res = client.post(f"/api/jobs/{job_id}/export/loops",
                      json={**grid, "bars": 1, "skip_fills": True})
    with_fills = client.post(f"/api/jobs/{job_id}/export/loops",
                             json={**grid, "bars": 1, "skip_fills": False})
    assert len(zipfile.ZipFile(io.BytesIO(res.content)).namelist()) < \
        len(zipfile.ZipFile(io.BytesIO(with_fills.content)).namelist())


def test_bad_requests(client, ready_job):
    job_id = ready_job["id"]
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    res = client.post(f"/api/jobs/{job_id}/export/loops", json={"bars": 3})
    assert res.status_code == 422
    res = client.post(f"/api/jobs/{job_id}/export/region", json={"start": 1, "end": 1.01, "bars": 4})
    assert res.status_code == 422
