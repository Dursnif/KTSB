/* Wake Word Maker — frontend */

// ── State ────────────────────────────────────
let audioCtx = null;
let workletNode = null;
let analyser = null;
let mediaStream = null;
let isRecording = false;
let recordedBuffers = [];
let recordedSamples = null; // merged Float32Array after stop
let nativeSampleRate = 0;
let recordStartTime = 0;
let recordTimerId = null;
let autoStopId = null;
let animFrameId = null;
let playbackAudio = null;

let currentType = 'positive';
let sessionStats = { positive: 0, negative: 0 };

const MAX_RECORD_S = 3;
const BAR_WIDTH = 3;
const BAR_GAP = 1;

// ── DOM ──────────────────────────────────────
const permScreen = document.getElementById('permission-screen');
const app = document.getElementById('app');
const speakerSelect = document.getElementById('speaker-select');
const recordBtn = document.getElementById('record-btn');
const recordTimer = document.getElementById('record-timer');
const waveformCanvas = document.getElementById('waveform');
const waveCtx = waveformCanvas.getContext('2d');
const dbBar = document.getElementById('db-bar');
const dbValue = document.getElementById('db-value');
const reviewPanel = document.getElementById('review-panel');
const playbackCanvas = document.getElementById('playback-waveform');
const playCtx = playbackCanvas.getContext('2d');
const statPos = document.getElementById('stat-pos');
const statNeg = document.getElementById('stat-neg');
const statusEl = document.getElementById('status');
const globalStats = document.getElementById('global-stats');

// ── Init ─────────────────────────────────────
// Restore speaker from localStorage
const savedSpeaker = localStorage.getItem('wwm-speaker');
if (savedSpeaker) speakerSelect.value = savedSpeaker;
speakerSelect.addEventListener('change', () => {
  localStorage.setItem('wwm-speaker', speakerSelect.value);
});

// Type toggle
document.querySelectorAll('#type-toggle button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#type-toggle button').forEach(b => {
      b.classList.remove('active', 'positive', 'negative');
    });
    currentType = btn.dataset.type;
    btn.classList.add('active', currentType);
  });
});

async function initAudio() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
    });
  } catch (e) {
    alert('Kunne ikke få mikrofontilgang: ' + e.message);
    return;
  }

  audioCtx = new AudioContext();
  nativeSampleRate = audioCtx.sampleRate;

  await audioCtx.audioWorklet.addModule('/static/worklet.js');

  const source = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 2048;

  workletNode = new AudioWorkletNode(audioCtx, 'recorder-worklet');
  workletNode.port.onmessage = (e) => {
    if (isRecording) recordedBuffers.push(e.data);
  };

  source.connect(analyser);
  source.connect(workletNode);

  // Show app
  permScreen.classList.add('hidden');
  app.classList.remove('hidden');

  // Size canvases
  sizeCanvas(waveformCanvas);
  sizeCanvas(playbackCanvas);

  // Start dB meter loop
  updateMeter();
  loadStats();
}

function sizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.getContext('2d').scale(dpr, dpr);
}

// ── dB meter (always running) ────────────────
function updateMeter() {
  if (analyser) {
    const buf = new Float32Array(analyser.frequencyBinCount);
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const db = 20 * Math.log10(Math.max(rms, 1e-10));
    const norm = Math.max(0, Math.min(1, (db + 60) / 55));
    dbBar.style.width = (norm * 100) + '%';
    dbBar.classList.toggle('loud', db > -10);
    dbValue.textContent = Math.round(db) + ' dB';
  }
  requestAnimationFrame(updateMeter);
}

// ── Recording ────────────────────────────────
function toggleRecord() {
  if (isRecording) stopRecording();
  else startRecording();
}

function startRecording() {
  if (!audioCtx) return;
  // Resume context if suspended (iOS requirement)
  if (audioCtx.state === 'suspended') audioCtx.resume();

  recordedBuffers = [];
  isRecording = true;
  recordStartTime = Date.now();
  recordBtn.classList.add('recording');
  reviewPanel.classList.add('hidden');
  showStatus('', '');

  // Clear waveform
  const w = waveformCanvas.getBoundingClientRect().width;
  const h = waveformCanvas.getBoundingClientRect().height;
  waveCtx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim();
  waveCtx.fillRect(0, 0, w, h);

  workletNode.port.postMessage('start');

  // Timer display
  recordTimer.textContent = '0.0s';
  recordTimerId = setInterval(() => {
    const elapsed = (Date.now() - recordStartTime) / 1000;
    recordTimer.textContent = elapsed.toFixed(1) + 's';
  }, 100);

  // Auto-stop after MAX_RECORD_S
  autoStopId = setTimeout(() => stopRecording(), MAX_RECORD_S * 1000);

  // Start waveform drawing
  drawLiveWaveform();
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  workletNode.port.postMessage('stop');
  recordBtn.classList.remove('recording');

  clearInterval(recordTimerId);
  clearTimeout(autoStopId);
  cancelAnimationFrame(animFrameId);

  const elapsed = (Date.now() - recordStartTime) / 1000;
  recordTimer.textContent = elapsed.toFixed(1) + 's';

  // Merge buffers
  const totalLen = recordedBuffers.reduce((s, b) => s + b.length, 0);
  recordedSamples = new Float32Array(totalLen);
  let offset = 0;
  for (const buf of recordedBuffers) {
    recordedSamples.set(buf, offset);
    offset += buf.length;
  }
  recordedBuffers = [];

  // Show review
  drawPlaybackWaveform(recordedSamples);
  reviewPanel.classList.remove('hidden');
}

// ── Waveform drawing ─────────────────────────
function drawLiveWaveform() {
  if (!isRecording || !analyser) return;

  const w = waveformCanvas.getBoundingClientRect().width;
  const h = waveformCanvas.getBoundingClientRect().height;
  const buf = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteTimeDomainData(buf);

  // RMS of current frame
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / buf.length);

  // Shift left
  const step = BAR_WIDTH + BAR_GAP;
  const imgData = waveCtx.getImageData(step * (window.devicePixelRatio || 1), 0,
    waveformCanvas.width - step * (window.devicePixelRatio || 1), waveformCanvas.height);
  waveCtx.putImageData(imgData, 0, 0);

  // Draw new bar
  const barH = Math.max(2, rms * h * 4);
  const x = w - step;
  const y = (h - barH) / 2;

  waveCtx.fillStyle = '#1a1a2e';
  waveCtx.fillRect(x, 0, step, h);

  const color = currentType === 'positive' ? '#4ecca3' : '#e94560';
  waveCtx.fillStyle = color;
  waveCtx.fillRect(x, y, BAR_WIDTH, barH);

  animFrameId = requestAnimationFrame(drawLiveWaveform);
}

function drawPlaybackWaveform(samples) {
  sizeCanvas(playbackCanvas);
  const w = playbackCanvas.getBoundingClientRect().width;
  const h = playbackCanvas.getBoundingClientRect().height;

  playCtx.fillStyle = '#1a1a2e';
  playCtx.fillRect(0, 0, w, h);

  if (!samples || samples.length === 0) return;

  const step = Math.max(1, Math.floor(samples.length / w));
  const mid = h / 2;
  const color = currentType === 'positive' ? '#4ecca3' : '#e94560';

  playCtx.strokeStyle = color;
  playCtx.lineWidth = 1;

  for (let i = 0; i < w; i++) {
    const idx = i * step;
    let min = 1, max = -1;
    for (let j = 0; j < step && idx + j < samples.length; j++) {
      const s = samples[idx + j];
      if (s < min) min = s;
      if (s > max) max = s;
    }
    const yTop = mid + max * mid;
    const yBot = mid + min * mid;
    playCtx.beginPath();
    playCtx.moveTo(i, yTop);
    playCtx.lineTo(i, yBot);
    playCtx.stroke();
  }
}

// ── Playback ─────────────────────────────────
function playRecording() {
  if (!recordedSamples || !audioCtx) return;
  if (playbackAudio) { playbackAudio.stop(); playbackAudio = null; }

  const buf = audioCtx.createBuffer(1, recordedSamples.length, nativeSampleRate);
  buf.getChannelData(0).set(recordedSamples);

  playbackAudio = audioCtx.createBufferSource();
  playbackAudio.buffer = buf;
  playbackAudio.connect(audioCtx.destination);
  playbackAudio.start();
}

// ── Upload ───────────────────────────────────
function encodeWAV(samples, sampleRate) {
  const numSamples = samples.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  function writeStr(offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);

  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

async function approveRecording() {
  if (!recordedSamples) return;

  const wavBlob = encodeWAV(recordedSamples, nativeSampleRate);
  const recType = currentType === 'positive' ? 'wakeword_positive' : 'wakeword_negative';

  const form = new FormData();
  form.append('file', wavBlob, 'recording.wav');
  form.append('type', recType);
  form.append('speaker', speakerSelect.value);

  showStatus('Laster opp...', '');
  reviewPanel.classList.add('hidden');

  try {
    const resp = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await resp.json();
    if (data.ok) {
      sessionStats[currentType]++;
      updateSessionStats();
      showStatus('Lagret!', 'success');
      loadStats();
    } else {
      showStatus('Feil: ' + (data.error || 'ukjent'), 'error');
    }
  } catch (e) {
    showStatus('Upload feilet: ' + e.message, 'error');
  }

  recordedSamples = null;
}

function discardRecording() {
  recordedSamples = null;
  reviewPanel.classList.add('hidden');
  showStatus('Forkastet', '');
}

// ── UI helpers ───────────────────────────────
function updateSessionStats() {
  statPos.textContent = sessionStats.positive + ' positive';
  statNeg.textContent = sessionStats.negative + ' negative';
}

function showStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = 'status' + (msg ? ' visible' : '') + (type ? ' ' + type : '');
  if (msg) setTimeout(() => { statusEl.className = 'status'; }, 3000);
}

async function loadStats() {
  try {
    const resp = await fetch('/api/stats');
    const data = await resp.json();
    globalStats.textContent = `Totalt: ${data.positive} positive, ${data.negative} negative`;
  } catch (e) {
    globalStats.textContent = '';
  }
}

// Handle window resize
window.addEventListener('resize', () => {
  sizeCanvas(waveformCanvas);
  sizeCanvas(playbackCanvas);
});
