"""Hardware-friendly WAV writing.

``soundfile`` can write PCM_16, but it cannot embed the RIFF ``cue ``/``LIST adtl``
chunks that FL Studio Slicex/Edison read as slice markers, nor the ``smpl`` chunk
that samplers use for loop points.  This module writes the file by hand so we
control every byte of the header:

* ``fmt `` is always the plain 16-byte WAVE_FORMAT_PCM (tag 1) header, never
  WAVE_FORMAT_EXTENSIBLE, which older hardware samplers can reject.
* samples are quantised to 16-bit with TPDF dither instead of plain truncation.
* optional ``cue `` + ``LIST adtl labl`` chunks carry labelled slice markers.
* optional ``smpl`` chunk carries a forward loop over the whole file.
* optional ``acid`` chunk carries tempo, beat count and meter, which Bitwig,
  Logic, FL Studio, Ableton and others read to sync the file to the project
  tempo without guessing.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np

PCM_FORMAT_TAG = 1
BITS_PER_SAMPLE = 16


@dataclass(frozen=True)
class Cue:
    sample: int
    label: str


ACID_ONE_SHOT = 0x01
ACID_ROOT_NOTE = 0x02
ACID_STRETCH = 0x04


@dataclass(frozen=True)
class Acid:
    """ACIDized-WAV tempo metadata.

    ``beats`` are counted at ``tempo`` (beats per minute), so the DAW knows the
    file lasts ``beats * 60 / tempo`` seconds and stretches it to the project
    tempo.  DAWs count tempo in quarter notes, so ``denominator`` is 4 when one
    counted beat (matra) is one DAW beat.
    """
    beats: int
    tempo: float
    numerator: int
    denominator: int = 4


def float_to_pcm16(audio: np.ndarray, dither: bool = True, seed: int = 0) -> np.ndarray:
    """Quantise float audio in [-1, 1] to int16 with optional TPDF dither."""
    scaled = np.asarray(audio, dtype=np.float64) * 32767.0
    if dither:
        rng = np.random.default_rng(seed)
        # Difference of two uniforms -> triangular PDF spanning +/-1 LSB.
        scaled = scaled + rng.random(scaled.shape) - rng.random(scaled.shape)
    return np.clip(np.round(scaled), -32768, 32767).astype("<i2")


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) % 2 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad


def _cue_chunks(cues: list[Cue]) -> bytes:
    cue_payload = struct.pack("<I", len(cues))
    labels = b""
    for cue_id, cue in enumerate(cues, start=1):
        # dwName, dwPosition, fccChunk, dwChunkStart, dwBlockStart, dwSampleOffset
        cue_payload += struct.pack("<II4sIII", cue_id, cue.sample, b"data", 0, 0, cue.sample)
        text = cue.label.encode("ascii", "replace") + b"\x00"
        labels += _chunk(b"labl", struct.pack("<I", cue_id) + text)
    return _chunk(b"cue ", cue_payload) + _chunk(b"LIST", b"adtl" + labels)


def _acid_chunk(acid: Acid) -> bytes:
    # flags, root note, unknown (0x8000), unknown float, beats, meter denominator,
    # meter numerator, tempo.  Drums: stretch on, no root note, not a one-shot
    # (ACID one-shots ignore the project tempo, but todas/rolls must follow it).
    return _chunk(b"acid", struct.pack(
        "<IHHfIHHf", ACID_STRETCH, 60, 0x8000, 0.0,
        acid.beats, acid.denominator, acid.numerator, float(acid.tempo),
    ))


def _smpl_chunk(sample_rate: int, loop_start: int, loop_end: int, unity_note: int) -> bytes:
    header = struct.pack(
        "<IIIIIIIII",
        0,  # manufacturer
        0,  # product
        int(round(1e9 / sample_rate)),  # sample period in ns
        unity_note,
        0,  # pitch fraction
        0,  # SMPTE format
        0,  # SMPTE offset
        1,  # number of loops
        0,  # sampler data bytes
    )
    # cue id, type (0 = forward), start, end (inclusive), fraction, play count (0 = infinite)
    loop = struct.pack("<IIIIII", 0, 0, loop_start, loop_end, 0, 0)
    return _chunk(b"smpl", header + loop)


def encode_wav(
    audio: np.ndarray,
    sample_rate: int,
    cues: Iterable[Cue] = (),
    loop: bool = False,
    dither: bool = True,
    unity_note: int = 60,
    acid: Acid | None = None,
) -> bytes:
    """Encode float audio (frames,) or (frames, channels) as a 16-bit PCM WAV."""
    audio = np.asarray(audio)
    if audio.ndim == 1:
        audio = audio[:, None]
    frames, channels = audio.shape
    pcm = float_to_pcm16(audio, dither=dither)

    block_align = channels * BITS_PER_SAMPLE // 8
    fmt = struct.pack(
        "<HHIIHH",
        PCM_FORMAT_TAG,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        BITS_PER_SAMPLE,
    )
    body = b"WAVE" + _chunk(b"fmt ", fmt) + _chunk(b"data", pcm.tobytes())

    cue_list = sorted(
        (c for c in cues if 0 <= c.sample < frames), key=lambda c: c.sample
    )
    if cue_list:
        body += _cue_chunks(cue_list)
    if loop and frames > 1:
        body += _smpl_chunk(sample_rate, 0, frames - 1, unity_note)
    if acid is not None:
        body += _acid_chunk(acid)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write_wav(path: str | Path | BinaryIO, audio: np.ndarray, sample_rate: int, **kwargs) -> None:
    data = encode_wav(audio, sample_rate, **kwargs)
    if hasattr(path, "write"):
        path.write(data)
    else:
        Path(path).write_bytes(data)


@dataclass
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    frames: int
    cues: list[Cue]
    loop: tuple[int, int] | None
    chunks: list[str]
    acid: Acid | None = None
    acid_flags: int = 0


def read_wav_info(data: bytes) -> WavInfo:
    """Parse the chunks written by :func:`encode_wav` (used by tests and the API)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, chunks = 12, []
    fmt = None
    frames = 0
    positions: dict[int, int] = {}
    labels: dict[int, str] = {}
    loop = None
    acid = None
    acid_flags = 0
    while pos + 8 <= len(data):
        fourcc = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        payload = data[pos + 8 : pos + 8 + size]
        chunks.append(fourcc.decode("ascii"))
        if fourcc == b"fmt ":
            fmt = struct.unpack("<HHIIHH", payload[:16])
        elif fourcc == b"data":
            frames = size // (fmt[1] * fmt[5] // 8)
        elif fourcc == b"cue ":
            count = struct.unpack("<I", payload[:4])[0]
            for i in range(count):
                cue_id, position = struct.unpack("<II", payload[4 + 24 * i : 12 + 24 * i])
                positions[cue_id] = position
        elif fourcc == b"LIST" and payload[:4] == b"adtl":
            sub = 4
            while sub + 8 <= len(payload):
                sub_id = payload[sub : sub + 4]
                sub_size = struct.unpack("<I", payload[sub + 4 : sub + 8])[0]
                if sub_id == b"labl":
                    cue_id = struct.unpack("<I", payload[sub + 8 : sub + 12])[0]
                    labels[cue_id] = payload[sub + 12 : sub + 8 + sub_size].rstrip(b"\x00").decode()
                sub += 8 + sub_size + (sub_size % 2)
        elif fourcc == b"acid":
            acid_flags, _, _, _, beats, denominator, numerator, tempo = struct.unpack("<IHHfIHHf", payload[:24])
            acid = Acid(beats, tempo, numerator, denominator)
        elif fourcc == b"smpl":
            start, end = struct.unpack("<II", payload[36 + 8 : 36 + 16])
            loop = (start, end)
        pos += 8 + size + (size % 2)
    if fmt is None:
        raise ValueError("missing fmt chunk")
    cues = [Cue(positions[i], labels.get(i, "")) for i in sorted(positions)]
    return WavInfo(fmt[0], fmt[1], fmt[2], fmt[5], frames, cues, loop, chunks, acid, acid_flags)
