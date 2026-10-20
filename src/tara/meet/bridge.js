/* Tara audio bridge, injected into every page before it runs (Playwright add_init_script).
 *
 * Playwright cannot move audio, so the page does it:
 *  - REMOTE AUDIO OUT: RTCPeerConnection is wrapped; every incoming audio track is tapped with WebAudio, mixed, decimated
 *    from 48 kHz to 16 kHz mono PCM16 and handed to Python through window.__taraAudioIn(base64).
 *  - SPEECH IN: navigator.mediaDevices.getUserMedia is replaced; the "microphone" is a MediaStreamDestination that
 *    Python fills with window.__taraPlay(base64 pcm16, sampleRate). window.__taraStop() cancels everything queued.
 * No camera is used (a black frame is returned when video is requested). Nothing is recorded or stored here.
 */
(() => {
  if (window.__taraBridge) return;
  const S = { ctx: null, dest: null, mixer: null, proc: null, next: 0, firstStart: null, sources: new Set(),
              taps: 0, remoteTracks: 0, inFrames: 0, inBytes: 0, outBytes: 0, lastInRms: 0, gumCalls: 0, errors: [] };

  function ctx() {
    if (!S.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      S.ctx = new AC({ sampleRate: 48000, latencyHint: "interactive" });
      S.dest = S.ctx.createMediaStreamDestination();
      S.mixer = S.ctx.createGain();
      S.proc = S.ctx.createScriptProcessor(4096, 1, 1);          // deprecated but universal; one mixed mono stream
      S.proc.onaudioprocess = onIn;
      const sink = S.ctx.createGain();
      sink.gain.value = 0;                                          // keeps the graph running without any output
      S.mixer.connect(S.proc); S.proc.connect(sink); sink.connect(S.ctx.destination);
    }
    if (S.ctx.state === "suspended") S.ctx.resume().catch(() => {});
    return S.ctx;
  }

  function b64(buf) {
    const bytes = new Uint8Array(buf); let s = ""; const step = 0x8000;
    for (let i = 0; i < bytes.length; i += step) s += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    return btoa(s);
  }

  function onIn(e) {
    const inp = e.inputBuffer.getChannelData(0);
    const n = Math.floor(inp.length / 3);                         // 48 kHz -> 16 kHz by averaging three samples
    const out = new Int16Array(n); let sum = 0;
    for (let i = 0; i < n; i++) {
      const v = (inp[3 * i] + inp[3 * i + 1] + inp[3 * i + 2]) / 3;
      sum += v * v;
      const c = Math.max(-1, Math.min(1, v));
      out[i] = c < 0 ? c * 0x8000 : c * 0x7fff;
    }
    S.lastInRms = Math.sqrt(sum / n); S.inFrames++; S.inBytes += out.byteLength;
    if (typeof window.__taraAudioIn === "function") { try { window.__taraAudioIn(b64(out.buffer)); } catch (_) {} }
  }

  function tap(track) {
    try {
      const c = ctx();
      const stream = new MediaStream([track]);
      const a = new Audio(); a.srcObject = stream; a.muted = true; a.play().catch(() => {});   // a remote stream needs a sink to flow
      const src = c.createMediaStreamSource(stream);
      src.connect(S.mixer);
      S.taps++; S.remoteTracks++;
      track.addEventListener("ended", () => { try { src.disconnect(); } catch (_) {} S.taps = Math.max(0, S.taps - 1); });
    } catch (e) { S.errors.push("tap: " + String(e)); }
  }

  const Orig = window.RTCPeerConnection;
  if (Orig) {
    const Wrapped = function (...args) {
      const pc = new Orig(...args);
      if (!(args[0] && args[0].__taraIgnore)) {                      // tests mark their own measuring connections; Meet never does
        pc.addEventListener("track", (ev) => { if (ev.track && ev.track.kind === "audio") tap(ev.track); });
      }
      return pc;
    };
    Wrapped.prototype = Orig.prototype;
    Object.setPrototypeOf(Wrapped, Orig);
    window.RTCPeerConnection = Wrapped;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = Wrapped;
  }

  function blackTrack() {
    const c = document.createElement("canvas"); c.width = 640; c.height = 360;
    const g = c.getContext("2d"); g.fillStyle = "#000"; g.fillRect(0, 0, 640, 360);
    setInterval(() => { g.fillRect(0, 0, 1, 1); }, 500);
    return c.captureStream(2).getVideoTracks()[0];
  }

  const md = navigator.mediaDevices;
  if (md) {
    const origGum = md.getUserMedia ? md.getUserMedia.bind(md) : null;
    md.getUserMedia = async (constraints) => {
      const out = new MediaStream();
      if (constraints && constraints.audio) { ctx(); S.dest.stream.getAudioTracks().forEach((t) => out.addTrack(t.clone())); }
      if (constraints && constraints.video) out.addTrack(blackTrack());
      if (!out.getTracks().length) { if (origGum) return origGum(constraints); throw new DOMException("no media requested", "NotFoundError"); }
      S.gumCalls++;
      return out;
    };
    const fake = (id, kind, label) => ({ deviceId: id, kind, label, groupId: "tara", toJSON() { return { deviceId: id, kind, label, groupId: "tara" }; } });
    md.enumerateDevices = async () => [fake("tara-mic", "audioinput", "Tara microphone"), fake("tara-spk", "audiooutput", "Tara speaker"),
                                       fake("tara-cam", "videoinput", "Tara camera")];
  }

  window.__taraPlay = (data, rate) => {
    const c = ctx(); const bin = atob(data); const n = bin.length >> 1; const f = new Float32Array(n);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v & 0x8000) v -= 0x10000; f[i] = v / 32768; }
    const buf = c.createBuffer(1, n, rate); buf.copyToChannel(f, 0);
    const src = c.createBufferSource(); src.buffer = buf; src.connect(S.dest);
    const now = c.currentTime; const start = Math.max(now + 0.03, S.next);
    if (S.firstStart === null || S.next <= now) S.firstStart = start;
    src.start(start); S.next = start + buf.duration; S.sources.add(src); src.onended = () => S.sources.delete(src);
    S.outBytes += n * 2;
    return Math.round((S.next - now) * 1000);
  };

  window.__taraStop = () => {
    let played = 0;
    if (S.ctx && S.firstStart !== null) played = Math.max(0, Math.min(S.ctx.currentTime, S.next) - S.firstStart);
    S.sources.forEach((s) => { try { s.stop(); } catch (_) {} });
    S.sources.clear(); S.next = 0; S.firstStart = null;
    return Math.round(played * 1000);
  };

  window.__taraStatus = () => ({
    ready: !!S.ctx, state: S.ctx ? S.ctx.state : "none",
    queuedMs: S.ctx ? Math.max(0, Math.round((S.next - S.ctx.currentTime) * 1000)) : 0,
    inFrames: S.inFrames, inBytes: S.inBytes, inRms: S.lastInRms, outBytes: S.outBytes,
    remoteTracks: S.remoteTracks, taps: S.taps, gumCalls: S.gumCalls, errors: S.errors.slice(-3),
  });

  window.__taraBridge = true;
})();
