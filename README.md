# TaalLoop

Upload a song (audio or video), extract its drums/percussion, and export
**bar-exact 16-bit loops, todas (fills) and rolls** ready for the **Korg
Nautilus** sampler, **FL Studio**, **Bitwig Studio** and **Logic** (Mac and
iPad). The app understands Indian taals:
Keherwa (4/4), Dadra (6 beats) and Rupak (7 beats).

```
upload (mp3/wav/flac/m4a/mp4/mkv…)
  → ffmpeg decode
  → drum separation (Demucs htdemucs, or HPSS fallback)
  → tempo + beat tracking, Sam (beat 1) estimation per taal
  → stroke detection (Bayan / Chanti / Slap), beats snapped onto strokes
  → toda + roll detection on the bar grid
  → browser editor (WaveSurfer.js): confirm the grid, audition loops gaplessly
  → Loop Slicer & Formatter Engine → 16-bit PCM WAVs
```

## Quick start

Requires Python 3.11+ and `ffmpeg` on your `PATH`.

```bash
pip install -r requirements.txt            # core (HPSS separation)
pip install -r requirements-demucs.txt     # optional, recommended: Demucs (installs PyTorch)
uvicorn taalloop.main:app --port 8000
```

Open <http://localhost:8000>. Uploads and job files are stored in `./data`
(override with `TAALLOOP_DATA_DIR`).

With Docker:

```bash
docker build -t taalloop .                              # with Demucs (CPU PyTorch, model built in)
docker build -t taalloop --build-arg WITH_DEMUCS=0 .    # small HPSS-only image
docker run -p 8000:7860 -v taalloop-data:/home/app/data taalloop
```

Then open <http://localhost:8000>.

### Which separation to use

| | Demucs (`htdemucs` drums stem) | HPSS (librosa) |
|---|---|---|
| Quality | Clean drum stem, keeps tabla/dholak tone | Rougher: melody bleeds through, and the dayan's ring is thinned |
| Speed | ~1–2 min/song on CPU, seconds on GPU | Seconds |
| Install | PyTorch (~2 GB) | Included |

Demucs is used automatically when it's installed. HPSS is a fallback, so expect
noisier loops with it.

## Free online hosting (Hugging Face Spaces)

The free **CPU basic** Space (2 vCPU, 16 GB RAM) runs the full app,
Demucs included. The repo deploys there on its own: every push to `main`
runs `.github/workflows/deploy-hf-space.yml`, which pushes the app to your
Space, and the Space rebuilds the Docker image.

One-time setup:

1. **Create a Hugging Face account** at <https://huggingface.co/join>.
2. **Create the Space**: <https://huggingface.co/new-space>
   - Space name: `taalloop`
   - SDK: **Docker**, template **Blank**
   - Hardware: **CPU basic · free**
   - Visibility: **Private** is recommended. Only you (signed in) can use it,
     so nobody else spends its CPU or uploads songs to it.
3. **Create an access token**: <https://huggingface.co/settings/tokens>, then
   **Create new token**, type **Write**. Copy it.
4. **Give GitHub the token**: in this GitHub repo go to **Settings → Secrets
   and variables → Actions**.
   - **Secrets** tab → **New repository secret**: name `HF_TOKEN`, value =
     the token.
   - **Variables** tab → **New repository variable**: name `HF_SPACE`,
     value = `<your-hf-username>/taalloop`.
5. **Deploy**: **Actions** tab → **Deploy to Hugging Face Space** → **Run
   workflow**. Later pushes to `main` deploy automatically.
6. Open the Space at `https://huggingface.co/spaces/<your-hf-username>/taalloop`.
   The first build takes about 10–15 minutes.

Good to know:

- **Speed**: Demucs on the free 2-CPU machine takes several minutes per song.
  The page shows progress while it works. Choose **HPSS (fast)** for a quick
  look.
- **Sleeping**: a free Space goes to sleep after about 48 hours without
  visitors and wakes when you open it, which takes a minute or two.
- **Storage is temporary**: uploads and results are wiped when the Space
  restarts or sleeps, so download your loops when you make them.
- Open it from any device, including an iPad, and send the downloaded loops
  to Logic through the Files app.

## Using the editor

1. **Upload** a song and wait for the percussion to be extracted.
2. **Time signature**: pick `4/4 (Keherwa)`, `3/4 or 6/8 (Dadra)` or
   `7/8 (Rupak)`. Bar lines (the Sam of every cycle) are drawn on the waveform.
   An 8-beat Keherwa cycle is a 2-bar loop.
3. **Check the Sam.** Beat trackers are right about 85% of the time. On rubato
   intros, laggi tempo shifts or dense thekas they can put beat 1 on beat 3, or
   lock onto the off-beat. Drag the loop region to the real beat 1 and click
   **Set Sam at loop start**. Untick *Snap loop start to the nearest beat*
   first if beat 1 falls between the tracked beats, and the whole grid shifts.
4. **Audition**: *Audition loop* plays the region endlessly through a
   sample-accurate Web Audio loop, with no gap at the joint. Drag or resize the
   region while it plays.
5. **Download Selected Loop (16-bit WAV)** exports the region. Or bulk-export:
   - **Export all loops**: every 1/2/4-bar loop from the Sam onward. By default,
     loops containing a toda or roll are skipped, so the set is pure theka.
   - **Export todas & rolls**: the detected fills, in `Toda/` and `Roll/`
     folders. Click a chip to hear one first.

## What gets exported

| | Loops | Todas | Rolls |
|---|---|---|---|
| Filename | `Song_Loop01_120BPM_4Bar.wav` | `Song_Toda01_120BPM_1Bar.wav` | `Song_Roll01_120BPM_2Beat.wav` |
| Length | exactly 1, 2 or 4 bars | 1 or 2 whole bars | whole beats (≤ 8) |
| Loop point (`smpl` chunk) | yes, whole file | no (plays once) | no (plays once) |

Every file is:

- **16-bit PCM WAV (`PCM_16`)** with a plain `WAVE_FORMAT_PCM` header (not
  `WAVE_FORMAT_EXTENSIBLE`), TPDF-dithered from float.
- **48,000 Hz** (Nautilus native) **or 44,100 Hz**.
- **Bar-exact**: length = `bars × beats_per_bar × 60 / BPM` seconds to the
  nearest sample, using the *rounded* BPM in the filename. FL Studio's stretch
  and the Nautilus tempo knob both use that number, so the file length agrees
  with it exactly.
- **Click-free**: the start is snapped within ±5 ms of the beat to where both
  the first sample and the wrap-around point are near zero crossings, then
  2 ms / 4 ms raised-cosine micro-fades are applied.
- **Tempo-tagged**: an ACID (`acid`) chunk stores the BPM, the number of
  beats and the meter, so DAWs that read ACIDized WAVs (Bitwig, Logic, FL
  Studio, Ableton, Cubase, …) sync the file to the project tempo on import,
  without guessing or filename parsing. libsndfile reads the chunk back as
  written.
- Optionally **cue-marked**: a labelled cue point (`01 Bayan`, `02 Chanti`, …)
  on every stroke, in the standard RIFF `cue `/`LIST adtl` chunks that
  slicers such as FL Studio's Slicex read as slice points.

### How todas and rolls are detected

- **Roll**: at least 6 strokes, each within `min(beat/5, 90 ms)` of the last,
  spanning at least half a beat. The file starts on the roll's beat and ends on
  the beat after the last stroke, so its tail rings out.
- **Toda**: each bar's strokes are laid on a 4-slots-per-beat grid and compared
  with the song's typical theka bar (the slot-wise median). A bar that differs
  far more than the song's normal bar-to-bar variation, and has at least 15%
  more strokes, is a toda. Runs of 3 or more such bars count as a new section,
  not a fill.

These are transparent heuristics, not a trained model, so audition the chips
before exporting.

### Time signatures in a DAW

The BPM in every file counts **matras** (the beats you clap). DAWs count tempo
in quarter notes, so the files are tagged with one matra per DAW beat:

| Taal | Matras per bar | DAW time signature at the file's BPM |
|---|---|---|
| Keherwa | 4 (8 = 2 bars) | 4/4 |
| Dadra | 6 | 6/4 (or 3/4, two bars per cycle) |
| Rupak | 7 | 7/4 |

To write Dadra as 6/8 or Rupak as 7/8 instead, set the project to that
signature at **half** the file's BPM: the loop then fills exactly one bar.

## Loading the WAVs into your gear

### Korg Nautilus 61 AT (live keyboard + loop performance)

1. Copy the 16-bit / 48 kHz WAVs to a USB drive and plug it into the Nautilus.
2. **MEDIA** mode → **Load** tab → select the `.wav` and load it into Sampling
   Mode RAM.
3. **MODE → SAMPLING → Loop Edit** tab: tick **Loop** (and **Lock**) for loops.
   The file is already cut to an exact bar boundary with micro-fades, so the
   default `Start = 0` and `End = end of sample` loop seamlessly. Leave Loop
   **off** for todas and rolls so they play once per key press.
4. Page menu (top right) → **Convert MS To Program** to map the multisample to
   a User Program. Save the KSC file and add it to **Global → KSC Auto-Load**.
5. In your Combination, put the percussion Program on a split key at the far
   left of the 61-key bed (e.g. loop on `C2` with Hold, todas and rolls on the
   neighbouring keys), or trigger it with a latching Chord Trigger / Arp /
   Drum Track switch, so your hands stay free for strings, bass and lead.
6. Set the Nautilus tempo to the BPM in the filename.

### FL Studio

1. Drag `Song_Loop01_120BPM_2Bar.wav` into the Playlist, or into Fruity Slicer
   / Slicex. The embedded cue markers are standard RIFF cue points that Slicex
   should pick up as slice markers. If your version ignores them, use Slicex's
   own auto-slicing instead.
2. In the Audio Clip channel settings set **Time stretching → Mode** to
   **Stretch** (or *e3 Generic*), right-click the **Time** knob and pick the
   bar count (*2 bars*). The clip locks to any project tempo.
3. Todas and rolls work the same way. For a roll, set the Time knob to its
   beat count, then drop it at the end of a loop to lead back into the Sam.

### Bitwig Studio

1. Drag a loop from the browser (or your file manager) into a Clip Launcher
   slot or onto an audio track in the Arranger. Bitwig reads the embedded ACID
   tempo, so the clip plays in sync at the project tempo straight away.
2. For Dadra or Rupak, set the transport time signature to 6/4 or 7/4 (see
   the table above) so bar lines match the cycle.
3. Put the todas and rolls in the slots under your loops, and launch them on
   the next bar, e.g. a toda in the bar before the Sam.
4. To play individual strokes, use **Slice to Drum Machine** on a clip (by
   beat or by onset) and play them from pads or keys.
5. If a clip ever doesn't follow the project tempo, check its stretch setting
   in the Inspector and set its tempo to the BPM in the filename.

### Logic (Mac and iPad)

1. Get the files onto the iPad (AirDrop, iCloud Drive or the Files app). In
   Logic, open the **Browser → Files** and drag a loop into a track or into a
   **Live Loops** cell.
2. Logic should treat the tagged WAVs like Apple Loops and follow the project
   tempo. If a region doesn't, turn on **Follow Tempo** (Flex) for it in the
   region inspector, or set its tempo to the BPM in the filename.
3. Live Loops suits this well: one row per section with a theka loop, and a
   toda or roll cell to trigger before the Sam. Cells quantize to the bar.
4. For playing todas, rolls or slices from the keyboard, load the WAV into
   **Quick Sampler** (Slice mode) or **Drum Machine Designer**.
5. Set the project time signature from the table above for Dadra and Rupak.

## API

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/config` | supported taals, bar counts, sample rates, separation methods |
| `POST` | `/api/jobs` | multipart `file`, `separation_method` = `auto`/`demucs`/`hpss` |
| `GET` | `/api/jobs/{id}` | job status; when ready: `bpm`, `beat_times`, `downbeat_index`, `hits` |
| `GET` | `/api/jobs/{id}/preview.wav` | the percussion stem (16-bit) |
| `POST` | `/api/jobs/{id}/phrases` | grid → detected todas/rolls (JSON) |
| `POST` | `/api/jobs/{id}/export/loops` | grid + `bars`, `sample_rate`, `embed_cues`, `skip_fills` → zip |
| `POST` | `/api/jobs/{id}/export/region` | `start`, `end`, `bars`, `time_signature`, `sample_rate` → WAV |
| `POST` | `/api/jobs/{id}/export/fills` | grid + `sample_rate`, `kinds` → zip |

The grid is `{"time_signature": "4/4" | "6/8" | "7/8", "bpm": <override>, "sam_time": <seconds of a beat 1>}`.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests render synthetic tabla grooves (bayan, chanti and slap strokes;
keherwa with todas and rolls; dadra; rupak) and check exact loop lengths, WAV
headers, zero-crossing snapping, Sam estimation, stroke classification, and
toda/roll detection, through to the HTTP API.

Code layout:

| Module | Role |
|---|---|
| `taalloop/wavio.py` | hand-written 16-bit WAV encoder (`fmt `, `data`, `cue `, `LIST adtl`, `smpl`) and parser |
| `taalloop/audio_io.py` | ffmpeg decoding, resampling |
| `taalloop/separation.py` | Demucs / HPSS drum separation |
| `taalloop/rhythm.py` | taals, tempo, Sam estimation, beat-to-stroke alignment, grid shift |
| `taalloop/hits.py` | stroke onsets and Bayan/Chanti/Slap classification |
| `taalloop/phrases.py` | toda and roll detection |
| `taalloop/slicer.py` | the Loop Slicer & Formatter Engine |
| `taalloop/jobs.py`, `taalloop/main.py` | background job pipeline and FastAPI app |
| `frontend/` | editor UI; WaveSurfer.js 7.12.12 vendored in `frontend/vendor` (BSD-3) |
