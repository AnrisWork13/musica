// static/tuner.js
const SR_OUT = 16000;            // must match server SR
const CHUNK_MS = 50;             // ~50 ms per message
let ws, audioCtx, processor, source, resampler, started = false;

const noteEl = document.getElementById('note');
const freqEl = document.getElementById('freq');
const centsEl = document.getElementById('cents');
const statusEl = document.getElementById('status');
const needleEl = document.getElementById('needle');
const startBtn = document.getElementById('startBtn');

function wsURL() {
  // WebSocket URL through Nginx reverse proxy
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const host  = location.hostname;
  return `${proto}://${host}/tune`;
}

function setStatus(s) { statusEl.textContent = s; }

function updateUI({note, freq, cents, state}) {
  if (freq && freq > 0) freqEl.textContent = freq.toFixed(2);
  else freqEl.textContent = '–';
  noteEl.textContent = note || '–';
  centsEl.textContent = (cents === null || cents === undefined) ? '–' : `${cents.toFixed(1)}`;

  // move needle: -50 cents = left, +50 = right, clamp
  const c = Math.max(-50, Math.min(50, cents || 0));
  const pct = 50 + (c / 100) * 100; // -50->0%, 0->50%, +50->100%
  needleEl.style.left = `${pct}%`;
  needleEl.style.background = state === 'ok' ? '#2ecc71' : (state === 'sharp' ? '#e67e22' : '#e74c3c');
}

async function start() {
  if (started) return;
  started = true;

  // HTTPS or localhost is required for getUserMedia
  if (!(location.protocol === 'https:' || location.hostname === 'localhost')) {
    alert('Microphone requires HTTPS (or run on localhost).');
    started = false;
    return;
  }

  setStatus('Starting…');
  ws = new WebSocket(wsURL());
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => setStatus('Connected. Listening…');
  ws.onclose = () => setStatus('Disconnected.');
  ws.onerror = () => setStatus('WebSocket error');

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      updateUI(msg);
    } catch {}
  };

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
      sampleRate: SR_OUT   // browsers may ignore; we resample anyway
    }
  });

  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const inSR = audioCtx.sampleRate; // e.g. 44100 or 48000
  source = audioCtx.createMediaStreamSource(stream);

  const BUFFER_SIZE = 2048;
  processor = audioCtx.createScriptProcessor(BUFFER_SIZE, 1, 1);

  // simple streaming resampler into 16k
  let acc = new Float32Array(0);
  const ratio = inSR / SR_OUT;
  const samplesPerChunk = Math.round(SR_OUT * (CHUNK_MS / 1000));

  processor.onaudioprocess = (e) => {
    const x = e.inputBuffer.getChannelData(0);

    // downsample from inSR -> 16k using linear interpolation
    const outLen = Math.floor(x.length / ratio);
    const y = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const t = i * ratio;
      const i0 = Math.floor(t);
      const i1 = Math.min(i0 + 1, x.length - 1);
      const frac = t - i0;
      y[i] = x[i0] * (1 - frac) + x[i1] * frac;
    }

    // append to accumulator
    const tmp = new Float32Array(acc.length + y.length);
    tmp.set(acc, 0); tmp.set(y, acc.length);
    acc = tmp;

    // send in ~50 ms chunks
    while (acc.length >= samplesPerChunk) {
      const chunk = acc.slice(0, samplesPerChunk);
      acc = acc.slice(samplesPerChunk);
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(chunk.buffer);
        } catch { /* ignore */ }
      }
    }
  };

  source.connect(processor);
  processor.connect(audioCtx.destination); // required on some browsers
}

startBtn.addEventListener('click', start);
