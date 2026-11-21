"""Mobile call page served at ``/`` (single self-contained HTML document)."""

PAGE_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Call my PC</title>
<style>
  :root { color-scheme: dark; --bg:#0f1115; --fg:#e8eaed; --mut:#8b93a1; --ok:#2fb86a; --bad:#e5484d; --card:#181b21; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px system-ui,sans-serif;
         min-height:100dvh; display:flex; flex-direction:column; padding:16px; gap:12px; }
  h1 { font-size:18px; margin:0; font-weight:600; }
  input { width:100%; padding:12px; border-radius:10px; border:1px solid #2a2f38; background:var(--card); color:var(--fg); font-size:16px; }
  button { padding:18px; border:0; border-radius:14px; font-size:18px; font-weight:600; color:#fff; background:var(--ok); }
  button.hang { background:var(--bad); }
  #state { text-align:center; color:var(--mut); min-height:1.4em; }
  #log { flex:1; overflow:auto; display:flex; flex-direction:column; gap:8px; }
  .msg { padding:10px 12px; border-radius:12px; background:var(--card); max-width:90%; white-space:pre-wrap; }
  .me { align-self:flex-end; background:#1f3a5f; }
  .err { color:var(--bad); }
</style></head><body>
<h1>Call my PC</h1>
<input id="token" type="password" placeholder="Access token" autocomplete="off">
<button id="call">Call</button>
<div id="state">Idle</div>
<div id="log"></div>
<script>
(() => {
  const $ = id => document.getElementById(id);
  const tokenEl = $('token'), callBtn = $('call'), stateEl = $('state'), logEl = $('log');
  try { tokenEl.value = localStorage.getItem('pc_token') || ''; } catch (e) {}

  const TARGET_RATE = 16000, SPEECH_THR = 0.02, BARGE_THR = 0.06, BARGE_MS = 250;
  const SILENCE_MS = 900, MIN_SPEECH_MS = 300, PREROLL_MS = 600;
  const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
  let ctx, stream, proc, src, wake, hb, player, active = false, callGen = 0;
  let mode = 'listening';            // listening | thinking | speaking
  let speaking = false, speechMs = 0, silenceMs = 0, loudMs = 0, chunks = [], preroll = [], stopSent = false;

  const hdr = extra => Object.assign({ Authorization: 'Bearer ' + tokenEl.value.trim() }, extra || {});
  const setState = s => { stateEl.textContent = s; };
  const add = (text, cls) => { const d = document.createElement('div'); d.className = 'msg ' + (cls || ''); d.textContent = text; logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight; };
  async function call(method, path, body) {
    const r = await fetch(path, { method, headers: hdr(body ? { 'Content-Type': 'application/octet-stream' } : {}), body: method === 'POST' ? (body || '') : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status });
    return j;
  }
  const post = (path, body) => call('POST', path, body);
  const get = path => call('GET', path);
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function downsample(buf, inRate) {
    if (inRate === TARGET_RATE) return buf;
    const ratio = inRate / TARGET_RATE, n = Math.floor(buf.length / ratio), out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * ratio), b = Math.min(buf.length, Math.floor((i + 1) * ratio));
      let s = 0; for (let j = a; j < b; j++) s += buf[j];
      out[i] = s / Math.max(1, b - a);
    }
    return out;
  }
  function toPcm16(parts) {
    let len = 0; parts.forEach(p => len += p.length);
    const out = new Int16Array(len); let o = 0;
    parts.forEach(p => { for (let i = 0; i < p.length; i++) { const v = Math.max(-1, Math.min(1, p[i])); out[o++] = v < 0 ? v * 0x8000 : v * 0x7fff; } });
    return out.buffer;
  }
  function pushPreroll(data) {
    preroll.push(data); let t = 0, k = preroll.length;
    while (k > 0 && t < PREROLL_MS) t += preroll[--k].length / TARGET_RATE * 1000;
    preroll = preroll.slice(k);
  }

  function onAudio(e) {
    if (!active) return;
    const data = downsample(new Float32Array(e.inputBuffer.getChannelData(0)), ctx.sampleRate);
    let sum = 0; for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length), ms = data.length / TARGET_RATE * 1000;

    if (mode !== 'listening') {
      // Barge-in needs sustained loud audio so echo or a cough does not cut the agent off.
      loudMs = rms > BARGE_THR ? loudMs + ms : 0;
      pushPreroll(data);
      if (loudMs >= BARGE_MS) {
        loudMs = 0;
        if (mode === 'speaking') {
          player.pause(); post('/api/stop').catch(() => {});
          mode = 'listening'; speaking = true; chunks = preroll.slice(); preroll = []; speechMs = BARGE_MS; silenceMs = 0;
          setState('Hearing you…');
        } else if (!stopSent) {
          stopSent = true; post('/api/stop').catch(() => {}); setState('Stopping…');
        }
      }
      return;
    }
    if (!speaking) {
      pushPreroll(data);
      if (rms > SPEECH_THR) { speaking = true; chunks = preroll.slice(); preroll = []; speechMs = ms; silenceMs = 0; setState('Hearing you…'); }
    } else {
      chunks.push(data);
      if (rms > SPEECH_THR) { speechMs += ms; silenceMs = 0; } else { silenceMs += ms; }
      if (silenceMs >= SILENCE_MS || speechMs > 28000) {
        const parts = chunks; chunks = []; speaking = false; preroll = [];
        if (speechMs >= MIN_SPEECH_MS) send(parts); else setState('Listening…');
      }
    }
  }

  async function send(parts) {
    const gen = callGen;
    mode = 'thinking'; stopSent = false; loudMs = 0; setState('Working…');
    try {
      const { job } = await post('/api/utterance', toPcm16(parts));
      let since = 0, shown = false, fails = 0, j = null;
      while (active && gen === callGen) {
        try {
          j = await get('/api/job/' + job + '?since=' + since + '&wait=20');
          fails = 0;
        } catch (e) {
          if (e.status || ++fails > 6) throw e;   // real HTTP error, or the network is gone
          await sleep(1000); continue;
        }
        since = j.version;
        if (j.transcript && !shown) { shown = true; add(j.transcript, 'me'); }
        if (j.done) break;
      }
      if (!active || gen !== callGen || !j) return;
      if (j.error) add(j.error, 'err');
      if (j.reply) add(j.reply);
      if (j.audio_url) {
        const r = await fetch(j.audio_url, { headers: hdr() });
        if (r.ok && active && gen === callGen) {
          const url = URL.createObjectURL(await r.blob());
          player.src = url; mode = 'speaking'; loudMs = 0; setState('Speaking…');
          player.onended = () => {
            URL.revokeObjectURL(url);
            if (j.status === 'hangup') { endCall(false); return; }
            if (mode === 'speaking') { mode = 'listening'; setState('Listening…'); }
          };
          try { await player.play(); return; } catch (e) { add('Tap the page to enable sound.', 'err'); }
        }
      }
      if (j.status === 'hangup') { endCall(false); return; }
    } catch (e) {
      if (e.status === 409 && /No active call/.test(e.message)) { endCall(true); return; }
      if (e.status === 409) setState('Agent busy, try again');
      else add(String(e.message || e), 'err');
    }
    if (active) { mode = 'listening'; speaking = false; preroll = []; if (!/busy/.test(stateEl.textContent)) setState('Listening…'); }
  }

  async function startCall() {
    try { localStorage.setItem('pc_token', tokenEl.value.trim()); } catch (e) {}
    callBtn.disabled = true; setState('Connecting…');
    // Unlock audio output and the audio graph synchronously inside the tap (iOS/Safari).
    ctx = new (window.AudioContext || window.webkitAudioContext)(); ctx.resume();
    player = new Audio(); player.src = SILENT_WAV; player.play().catch(() => {});
    let started = false;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      await post('/api/call/start');
      started = true;
      callGen++;
      src = ctx.createMediaStreamSource(stream);
      proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = onAudio;
      const mute = ctx.createGain(); mute.gain.value = 0;
      src.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
      try { wake = await navigator.wakeLock.request('screen'); } catch (e) {}
      hb = setInterval(() => post('/api/call/heartbeat').catch(e => { if (e.status === 409) endCall(true); }), 5000);
      active = true; mode = 'listening'; speaking = false; preroll = []; chunks = []; loudMs = 0;
      callBtn.textContent = 'Hang up'; callBtn.className = 'hang'; callBtn.disabled = false;
      setState('Listening…'); add('Call connected. PC input is locked.');
    } catch (e) {
      add(e.status === 401 ? 'Wrong token' : e.status === 429 ? 'Too many attempts, try later' : String(e.message || e), 'err');
      cleanup(); setState('Idle'); callBtn.disabled = false;
      if (started) post('/api/call/end').catch(() => {});   // never leave the PC locked
    }
  }

  function cleanup() {
    active = false; clearInterval(hb);
    try { if (player) player.pause(); } catch (e) {}
    try { if (proc) proc.disconnect(); if (src) src.disconnect(); } catch (e) {}
    try { if (ctx) ctx.close(); } catch (e) {}
    try { if (stream) stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    try { if (wake) wake.release(); } catch (e) {}
    stream = null; callBtn.textContent = 'Call'; callBtn.className = '';
  }
  async function endCall(remoteAlreadyEnded) {
    if (!active) return;
    cleanup();
    if (!remoteAlreadyEnded) { try { await post('/api/call/end'); } catch (e) {} }
    setState('Call ended'); add('Call ended.');
  }

  callBtn.addEventListener('click', () => { active ? endCall(false) : startCall(); });
})();
</script></body></html>
"""
