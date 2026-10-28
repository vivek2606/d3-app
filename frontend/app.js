import WaveSurfer from './vendor/wavesurfer.esm.js'
import RegionsPlugin from './vendor/regions.esm.js'

const $ = (id) => document.getElementById(id)
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

const state = {
  job: null,
  config: null,
  ws: null,
  regions: null,
  loopRegion: null,
  samMarkers: [],
  phraseRegions: [],
  phraseRequest: 0,
  beats: [], // job.beat_times, shifted when the user moves the Sam off the tracked grid
  samIndex: {}, // per time signature: index into state.beats of a Sam
  loopNumber: 1,
}

// ---------------------------------------------------------------- helpers
function setStatus(text, isError = false) {
  $('status').textContent = text
  $('status').classList.toggle('error', isError)
}

function tsInfo() {
  return state.config.time_signatures.find((t) => t.id === $('ts').value)
}

function bpm() {
  return Math.min(400, Math.max(30, Number($('bpm').value) || state.job.bpm))
}

function loopSeconds() {
  return (Number($('bars').value) * tsInfo().beats_per_bar * 60) / bpm()
}

function nearestBeat(time) {
  const beats = state.beats
  if (!beats.length) return { index: 0, time }
  let best = 0
  for (let i = 1; i < beats.length; i++) {
    if (Math.abs(beats[i] - time) < Math.abs(beats[best] - time)) best = i
  }
  return { index: best, time: beats[best] }
}

function gridParams() {
  const beats = state.beats
  const samIndex = state.samIndex[$('ts').value]
  return {
    time_signature: $('ts').value,
    bpm: bpm(),
    sam_time: beats.length ? beats[Math.min(samIndex, beats.length - 1)] : null,
  }
}

async function postForDownload(path, body, button) {
  button.disabled = true
  const label = button.textContent
  button.textContent = 'Preparing…'
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(typeof err.detail === 'string' ? err.detail : `Export failed (${res.status})`)
    }
    const disposition = res.headers.get('Content-Disposition') || ''
    const filename = (disposition.match(/filename="([^"]+)"/) || [])[1] || 'export'
    const url = URL.createObjectURL(await res.blob())
    const a = Object.assign(document.createElement('a'), { href: url, download: filename })
    document.body.append(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    setStatus(`Downloaded ${filename}`)
    return res
  } catch (err) {
    setStatus(err.message, true)
  } finally {
    button.disabled = false
    button.textContent = label
  }
}

// ------------------------------------------------------- gapless audition
// HTMLMediaElement looping leaves an audible gap, so the loop is auditioned
// with an AudioBufferSourceNode whose loopStart/loopEnd are sample-accurate.
const player = { ctx: null, source: null, startedAt: 0, raf: 0 }

function stopLoop() {
  if (player.source) {
    player.source.stop()
    player.source.disconnect()
    player.source = null
  }
  cancelAnimationFrame(player.raf)
  $('play-loop').textContent = '▶ Audition loop'
}

function playLoop() {
  const buffer = state.ws.getDecodedData()
  if (!buffer) return
  state.ws.pause()
  player.ctx ||= new AudioContext()
  player.ctx.resume()
  stopLoop()
  const { start, end } = state.loopRegion
  const source = player.ctx.createBufferSource()
  source.buffer = buffer
  source.loop = true
  source.loopStart = start
  source.loopEnd = end
  source.connect(player.ctx.destination)
  source.start(0, start)
  player.source = source
  player.startedAt = player.ctx.currentTime
  $('play-loop').textContent = '■ Stop loop'

  const tick = () => {
    if (!player.source) return
    const s = player.source.loopStart
    const len = player.source.loopEnd - s
    const elapsed = player.ctx.currentTime - player.startedAt
    state.ws.setTime(s + (elapsed % len))
    player.raf = requestAnimationFrame(tick)
  }
  tick()
}

function syncPlayerLoop() {
  if (!player.source) return
  const { start, end } = state.loopRegion
  player.source.loopStart = start
  player.source.loopEnd = end
  player.startedAt = player.ctx.currentTime - (state.ws.getCurrentTime() - start)
}

// ------------------------------------------------------- region and grid
function drawSamMarkers() {
  state.samMarkers.forEach((m) => m.remove())
  state.samMarkers = []
  const beats = state.beats
  const step = tsInfo().beats_per_bar
  const first = state.samIndex[$('ts').value] % step
  const color = css('--sam')
  for (let i = first; i < beats.length; i += step) {
    state.samMarkers.push(state.regions.addRegion({ start: beats[i], color, drag: false, resize: false }))
  }
}

function fitRegion(side) {
  const r = state.loopRegion
  let { start, end } = r
  if ($('snap-beat').checked && side !== 'end') {
    const shift = nearestBeat(start).time - start
    start += shift
    if (!$('lock-length').checked) end += side === 'start' ? 0 : shift
  }
  if ($('lock-length').checked) {
    if (side === 'start') start = end - loopSeconds()
    else end = start + loopSeconds()
  }
  const duration = state.ws.getDuration()
  if (start < 0) { end -= start; start = 0 }
  if (end > duration) { start = Math.max(0, start - (end - duration)); end = duration }
  r.setOptions({ start, end })
  updateRegionStat()
  syncPlayerLoop()
}

function updateRegionStat() {
  const { start, end } = state.loopRegion
  const impliedBpm = (Number($('bars').value) * tsInfo().beats_per_bar * 60) / (end - start)
  $('stat-region').textContent =
    `${start.toFixed(2)}–${end.toFixed(2)} s · ${impliedBpm.toFixed(1)} BPM`
}

function placeRegionAtSam() {
  const beats = state.beats
  const index = state.samIndex[$('ts').value]
  const start = beats.length ? beats[Math.min(index, beats.length - 1)] : 0
  if (!state.loopRegion) {
    state.loopRegion = state.regions.addRegion({
      start, end: start + loopSeconds(), color: css('--loop'), drag: true, resize: true,
    })
  } else {
    state.loopRegion.setOptions({ start, end: start + loopSeconds() })
  }
  fitRegion()
}

// ------------------------------------------------------ todas and rolls
async function loadPhrases() {
  const request = ++state.phraseRequest
  const res = await fetch(`/api/jobs/${state.job.id}/phrases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(gridParams()),
  })
  if (request !== state.phraseRequest || !res.ok) return // a newer grid superseded this one
  const found = await res.json()

  state.phraseRegions.forEach((r) => r.remove())
  state.phraseRegions = []
  const chips = $('phrases')
  chips.replaceChildren()
  for (const p of found) {
    const kind = p.kind.toLowerCase()
    const length = p.kind === 'Toda' ? `${p.beats / tsInfo().beats_per_bar} bar` : `${p.beats} beat`
    const region = state.regions.addRegion({
      start: p.start, end: p.end, color: css(`--${kind}`), drag: false, resize: false,
      content: `${p.kind} ${p.number}`,
    })
    state.phraseRegions.push(region)
    const chip = Object.assign(document.createElement('button'), {
      type: 'button',
      className: `chip ${kind}`,
      textContent: `${p.kind} ${p.number} · ${length} · ${p.start.toFixed(1)} s`,
      title: p.filename,
    })
    chip.addEventListener('click', () => { stopLoop(); region.play(true) })
    chips.append(chip)
  }
  const count = (k) => found.filter((p) => p.kind === k).length
  $('stat-fills').textContent = `Toda ${count('Toda')} · Roll ${count('Roll')}`
}

function gridChanged() {
  drawSamMarkers()
  loadPhrases()
}

// ---------------------------------------------------------------- editor
function openEditor(job) {
  state.job = job
  state.beats = [...job.beat_times]
  state.samIndex = { ...job.downbeat_index }
  $('editor').hidden = false
  $('export').hidden = false
  $('stat-bpm').textContent = `${job.bpm.toFixed(1)} BPM`
  $('stat-sep').textContent = job.separation === 'demucs' ? 'Demucs (htdemucs)' : 'HPSS (fallback)'
  $('bpm').value = Math.round(job.bpm)

  state.ws?.destroy()
  stopLoop()
  state.loopRegion = null
  state.samMarkers = []
  state.phraseRegions = []
  state.regions = RegionsPlugin.create()
  state.ws = WaveSurfer.create({
    container: '#waveform',
    height: 140,
    waveColor: css('--wave'),
    progressColor: css('--progress'),
    cursorColor: css('--accent'),
    minPxPerSec: Number($('zoom').value),
    url: `/api/jobs/${job.id}/preview.wav`,
    plugins: [state.regions],
  })
  state.ws.on('decode', () => {
    gridChanged()
    placeRegionAtSam()
  })
  state.ws.on('finish', () => { $('play-track').textContent = 'Play track' })
  state.regions.on('region-updated', (region, side) => {
    if (region === state.loopRegion) fitRegion(side)
  })
}

async function pollJob(id) {
  for (;;) {
    const res = await fetch(`/api/jobs/${id}`)
    const job = await res.json()
    if (job.status === 'ready') return job
    if (job.status === 'error') throw new Error(job.message)
    setStatus(`${job.message}…`)
    await new Promise((r) => setTimeout(r, 1500))
  }
}

// ---------------------------------------------------------------- wiring
async function init() {
  state.config = await (await fetch('/api/config')).json()
  const lanUrl = state.config.lan_url
  if (lanUrl && ['localhost', '127.0.0.1'].includes(location.hostname)) {
    Object.assign($('lan-url'), { href: lanUrl, textContent: lanUrl })
    $('lan').hidden = false
  }
  const methods = state.config.separation_methods
  $('separation').innerHTML =
    `<option value="auto">Auto (${methods.includes('demucs') ? 'Demucs' : 'HPSS'})</option>` +
    methods.map((m) => `<option value="${m}">${m === 'demucs' ? 'Demucs (best)' : 'HPSS (fast)'}</option>`).join('')
  $('ts').innerHTML = state.config.time_signatures.map((t) => `<option value="${t.id}">${t.label}</option>`).join('')
  $('bars').innerHTML = state.config.bars
    .map((b) => `<option value="${b}" ${b === 2 ? 'selected' : ''}>${b} bar${b > 1 ? 's' : ''}</option>`).join('')

  $('file').addEventListener('change', () => {
    $('file-name').textContent = $('file').files[0]?.name || 'Choose an audio or video file…'
  })

  $('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault()
    const file = $('file').files[0]
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    form.append('separation_method', $('separation').value)
    $('upload-btn').disabled = true
    setStatus('Uploading…')
    try {
      const res = await fetch('/api/jobs', { method: 'POST', body: form })
      if (!res.ok) throw new Error((await res.json()).detail || 'Upload failed')
      const job = await pollJob((await res.json()).id)
      setStatus('Percussion extracted. Pick your loop below.')
      state.loopNumber = 1
      openEditor(job)
    } catch (err) {
      setStatus(err.message, true)
    } finally {
      $('upload-btn').disabled = false
    }
  })

  $('ts').addEventListener('change', () => { gridChanged(); placeRegionAtSam() })
  $('bars').addEventListener('change', () => fitRegion())
  $('bpm').addEventListener('change', () => { fitRegion(); loadPhrases() })
  $('lock-length').addEventListener('change', () => fitRegion())
  $('zoom').addEventListener('input', () => state.ws?.zoom(Number($('zoom').value)))

  $('play-loop').addEventListener('click', () => (player.source ? stopLoop() : playLoop()))
  $('play-track').addEventListener('click', () => {
    stopLoop()
    state.ws.playPause()
    $('play-track').textContent = state.ws.isPlaying() ? 'Pause track' : 'Play track'
  })

  $('set-sam').addEventListener('click', () => {
    const start = state.loopRegion.start
    const { index, time } = nearestBeat(start)
    // Between tracked beats (the tracker locked onto the off-beat): shift the whole grid.
    // Must match rhythm.shift_grid on the server.
    if (Math.abs(start - time) > 0.04) state.beats = state.beats.map((b) => b + (start - time))
    state.samIndex[$('ts').value] = index
    gridChanged()
    fitRegion()
    setStatus(`Sam moved to ${state.beats[index]?.toFixed(2) ?? 0} s for ${tsInfo().label}.`)
  })

  $('download-loop').addEventListener('click', async () => {
    const res = await postForDownload(`/api/jobs/${state.job.id}/export/region`, {
      start: state.loopRegion.start,
      end: state.loopRegion.end,
      bars: Number($('bars').value),
      time_signature: $('ts').value,
      sample_rate: Number($('sr').value),
      embed_cues: $('embed-cues').checked,
      loop_number: state.loopNumber,
    }, $('download-loop'))
    if (res) state.loopNumber = Math.min(99, state.loopNumber + 1)
  })

  $('export-loops').addEventListener('click', () => {
    postForDownload(`/api/jobs/${state.job.id}/export/loops`, {
      ...gridParams(),
      bars: Number($('bars').value),
      sample_rate: Number($('sr').value),
      embed_cues: $('embed-cues').checked,
      skip_fills: $('skip-fills').checked,
    }, $('export-loops'))
  })

  $('export-fills').addEventListener('click', () => {
    postForDownload(`/api/jobs/${state.job.id}/export/fills`, {
      ...gridParams(),
      sample_rate: Number($('sr').value),
      embed_cues: $('embed-cues').checked,
    }, $('export-fills'))
  })
}

init().catch((err) => setStatus(`Could not reach the server: ${err.message}`, true))
