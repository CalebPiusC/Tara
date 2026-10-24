/* Tara audio bridge, injected into every page before it runs (Playwright add_init_script).
 *
 * Playwright cannot move audio, so the page does it:
 *  - REMOTE AUDIO OUT: RTCPeerConnection is wrapped; every incoming audio track is tapped with WebAudio, mixed, decimated
 *    from 48 kHz to 16 kHz mono PCM16 and handed to Python through window.__taraAudioIn(base64).
 *  - SPEECH IN: navigator.mediaDevices.getUserMedia is replaced; the "microphone" is a MediaStreamDestination that
 *    Python fills with window.__taraPlay(base64 pcm16, sampleRate). window.__taraStop() cancels everything queued.
 * No camera is used (a black frame is returned when video is requested). Nothing is recorded or stored here.
 *
 * Meeting audio is tapped on three routes, all mixed into one stream and each metered separately for the run log:
 *   rtc       incoming WebRTC audio tracks (the standard route)
 *   webaudio  anything the page itself plays through its own AudioContext (if the page decodes audio itself, this is where it shows)
 *   element   media elements the page plays that are not already covered (for example MediaStream generators)
 * The webaudio and element routes are muted while Tara is speaking, so a page that monitors the microphone can never feed her own voice back.
 *
 * PER-PARTICIPANT CHANNELS (window.__taraChannels === true, set by Python): Meet gives every remote participant its own media
 * element with a live MediaStream, so the audio is already separated by person. Each such element becomes its own channel
 * (index, 16 kHz PCM, per-frame peak) through window.__taraChannelAudio, and the speaking glow is reported separately through
 * window.__taraGlow / window.__taraSelf. The name is NOT attached here: the two signals are correlated in Python
 * (tara.meet.binding), so a name is only used when the evidence earns it. The mixed tap keeps working until the first channel
 * frame arrives, then stops for good (one-way handover: nobody is ever heard twice).
 * Ported from Vexa's gmeet-capture / gmeet-speakers (Apache-2.0); see docs/google-meet/SPEAKER_BINDING.md.
 *
 * DEVICES MODE (window.__taraMode === "devices", set by Python before this script): the page is pointed at two real virtual audio cables
 * instead (docs/google-meet/GOOGLE_MEET_BOT.md). The page's speaker is forced to the meeting cable and its microphone to the voice cable; Python
 * moves the audio with the sound card API, so none of the taps below are used.
 */
(() => {
  if (window.__taraBridge) return;
  const MODE = window.__taraMode === "devices" ? "devices" : "page";
  const CHANNELS = MODE !== "devices" && window.__taraChannels === true;   // one channel per remote participant (see the header)
  const DEV = window.__taraDevices || {};
  const S = { ctx: null, dest: null, mixer: null, proc: null, next: 0, firstStart: null, sources: new Set(),
              pcs: new Set(), holdUntil: 0, tapList: [], routes: {}, ownTrackIds: new Set(), rtcTrackIds: new Set(), wireLevel: 0, wirePeak: 0, taps: 0, remoteTracks: 0, inFrames: 0, inBytes: 0, outBytes: 0, lastInRms: 0, peakRms: 0, gumCalls: 0, errors: [] };

  const note = (msg) => { S.events.push({ t: Math.round(performance.now()), msg: String(msg).slice(0, 200) }); if (S.events.length > 60) S.events.shift(); };
  S.events = [];

  function ctx() {
    if (!S.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      S.ctx = new AC({ sampleRate: 48000, latencyHint: "interactive" });
      S.dest = S.ctx.createMediaStreamDestination();
      S.ctx.onstatechange = () => note('audio context ' + S.ctx.state);
      note('audio context created, state ' + S.ctx.state);
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
    if (CHANNELS && CH.active) return;                            // per-participant audio has taken over; never send both
    const inp = e.inputBuffer.getChannelData(0);
    const n = Math.floor(inp.length / 3);                         // 48 kHz -> 16 kHz by averaging three samples
    const out = new Int16Array(n); let sum = 0;
    for (let i = 0; i < n; i++) {
      const v = (inp[3 * i] + inp[3 * i + 1] + inp[3 * i + 2]) / 3;
      sum += v * v;
      const c = Math.max(-1, Math.min(1, v));
      out[i] = c < 0 ? c * 0x8000 : c * 0x7fff;
    }
    S.lastInRms = Math.sqrt(sum / n); if (S.lastInRms > S.peakRms) S.peakRms = S.lastInRms; S.inFrames++; S.inBytes += out.byteLength;
    if (typeof window.__taraAudioIn === "function") { try { window.__taraAudioIn(b64(out.buffer)); } catch (_) {} }
  }

  // One tapped audio source: a stream into our mixer through its own gain (for the echo guard) and meter (for the run log).
  function addRoute(name, stream, guard) {
    const c = ctx();
    const src = c.createMediaStreamSource(stream);
    const g = c.createGain(); g.gain.value = 1;
    src.connect(g); g.connect(S.mixer);
    const an = c.createAnalyser(); an.fftSize = 1024; g.connect(an);
    const r = S.routes[name] || (S.routes[name] = { gains: [], meters: [], peak: 0, level: 0, guard: !!guard, sources: 0 });
    r.gains.push(g); r.meters.push(an); r.sources++;
    return src;
  }

  function tap(track, ownStream) {
    if (MODE === "devices") { note('track received (devices mode: audio goes through the cable)'); return; }
    try {
      // Prefer the stream Meet itself plays (when it carries just this track); otherwise wrap the track.
      const stream = ownStream && ownStream.getAudioTracks().length === 1 ? ownStream : new MediaStream([track]);
      const a = new Audio(); a.__taraOwn = true; a.srcObject = stream;
      a.muted = false; a.volume = 1;                                 // output is silenced by the browser flag; a muted element may not pull audio
      a.play().catch(() => {});                                      // a remote stream needs a playing sink to flow
      const src = addRoute("rtc", stream, false);
      S.rtcTrackIds.add(track.id);
      S.tapList.push({ track, el: a });
      note('tap ' + S.tapList.length + ': muted=' + track.muted + ' state=' + track.readyState + ' ownStream=' + (stream === ownStream));
      track.addEventListener('unmute', () => note('track ' + S.tapList.findIndex((x) => x.track === track) + ' unmuted (media started)'));
      track.addEventListener('mute', () => note('track ' + S.tapList.findIndex((x) => x.track === track) + ' muted (media stopped)'));
      S.taps++; S.remoteTracks++;
      track.addEventListener("ended", () => { try { src.disconnect(); } catch (_) {} S.taps = Math.max(0, S.taps - 1); });
    } catch (e) { S.errors.push("tap: " + String(e)); }
  }

  // ---- route: audio the page plays through its own AudioContext ----
  const nativeConnect = AudioNode.prototype.connect;
  const pageTaps = new WeakMap();
  const routedCtx = new WeakSet();
  AudioNode.prototype.connect = function (target, ...rest) {
    const r = nativeConnect.call(this, target, ...rest);
    try {
      if (MODE === "devices") {
        if (this.context && target instanceof AudioDestinationNode && typeof this.context.setSinkId === "function" && !routedCtx.has(this.context)) {
          routedCtx.add(this.context); this.context.setSinkId("default").catch((e) => note('context sink failed ' + e));
        }
        return r;
      }
      if (this.context && !(this.context instanceof OfflineAudioContext) && target instanceof AudioDestinationNode && (!S.ctx || this.context !== S.ctx)) {
        ctx();
        let dst = pageTaps.get(this.context);
        if (!dst) {
          dst = this.context.createMediaStreamDestination();
          pageTaps.set(this.context, dst);
          addRoute("webaudio", dst.stream, true);
          note('tapping the page audio context (rate ' + this.context.sampleRate + ')');
        }
        nativeConnect.call(this, dst);                                // fan out: the page still plays normally
      }
    } catch (e) { S.errors.push("webaudio tap: " + String(e)); }
    return r;
  };

  // ---- route: media elements the page plays ----
  const nativePlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...a) {
    try { if (MODE === "devices") routeElement(this); else if (!this.__taraSeen && !this.__taraOwn) { this.__taraSeen = true; watchElement(this); } } catch (_) {}
    return nativePlay.apply(this, a);
  };
  function routeElement(el) {
    if (el.__taraRouted || typeof el.setSinkId !== "function") return;
    el.__taraRouted = true;
    el.setSinkId("default").catch((e) => note('element sink failed ' + e));
  }
  if (MODE === "devices") setInterval(() => { document.querySelectorAll("audio,video").forEach(routeElement); }, 1000);

  function watchElement(el) {
    note('element play: ' + el.tagName + ' srcObject=' + !!el.srcObject + ' url=' + (el.currentSrc ? 'yes' : 'no') + ' muted=' + el.muted + ' volume=' + el.volume);
    el.addEventListener('playing', () => {
      try {
        if (el.muted || el.volume === 0 || !el.captureStream) return;                              // self-views and previews are muted
        const so = el.srcObject;
        if (so && so.getTracks().some((t) => S.ownTrackIds.has(t.id) || S.rtcTrackIds.has(t.id))) return;   // our mic, or already tapped
        const cs = el.captureStream(); const at = cs.getAudioTracks();
        if (at.length) { addRoute("element", new MediaStream(at), true); note('capturing element audio (' + at.length + ' track)'); }
      } catch (e) { S.errors.push('element capture: ' + String(e)); }
    }, { once: true });
  }

  // ---- notes on how the page does audio (for the run log) ----
  const watchCtor = (name) => {
    try {
      const O = window[name];
      if (typeof O === "function") window[name] = new Proxy(O, { construct(t, args, nt) { note('new ' + name + (typeof args[0] === 'string' ? ' ' + args[0].slice(0, 60) : '')); return Reflect.construct(t, args, nt); } });
    } catch (_) {}
  };
  ["Worker", "SharedWorker", "AudioDecoder", "AudioWorkletNode", "MediaStreamTrackGenerator", "RTCRtpScriptTransform", "MediaSource"].forEach(watchCtor);
  try {
    if (window.AudioWorklet) { const am = AudioWorklet.prototype.addModule; AudioWorklet.prototype.addModule = function (u, o) { note('AudioWorklet.addModule ' + String(u).slice(0, 60)); return am.call(this, u, o); }; }
    if (window.RTCRtpReceiver && RTCRtpReceiver.prototype.createEncodedStreams) {
      const ce = RTCRtpReceiver.prototype.createEncodedStreams; RTCRtpReceiver.prototype.createEncodedStreams = function () { note('receiver.createEncodedStreams'); return ce.call(this); };
    }
  } catch (_) {}

  const Orig = window.RTCPeerConnection;
  if (Orig) {
    const Wrapped = function (...args) {
      const pc = new Orig(...args);
      note('RTCPeerConnection created' + (args[0] && args[0].__taraIgnore ? ' (ignored)' : ''));
      if (!(args[0] && args[0].__taraIgnore)) {                      // tests mark their own measuring connections; Meet never does
        S.pcs.add(pc);
        pc.addEventListener("track", (ev) => { if (ev.track && ev.track.kind === "audio") tap(ev.track, ev.streams && ev.streams[0]); });
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
    if (window.__taraPicture) {                                                  // Tara's picture with its caption, redrawn so the frame keeps flowing
      const img = new Image(); let ready = false;
      img.onload = () => { ready = true; };
      img.src = window.__taraPicture;
      setInterval(() => { if (ready) g.drawImage(img, 0, 0, 640, 360); else g.fillRect(0, 0, 1, 1); }, 500);
      return c.captureStream(2).getVideoTracks()[0];
    }
    setInterval(() => { g.fillRect(0, 0, 1, 1); }, 500);
    return c.captureStream(2).getVideoTracks()[0];
  }

  // The device list below includes a fake speaker. Meet routes its playback to the output it lists; setSinkId on a device that does not
  // exist fails, and when the call fails the remote audio never starts flowing (the tap would hear silence). Accept our fake id.
  const FAKE_SINK = "tara-spk";
  const dev = { mic: null, spk: null, loaded: null };
  async function realDevices() {
    if (!dev.loaded) {
      dev.loaded = origEnumerate().then((list) => {
        if (!list.some((d) => d.label)) { dev.loaded = null; return list; }           // labels appear only once permission is granted
        const has = (d, kind, want) => d.kind === kind && d.label.toLowerCase().includes(String(want || "~~").toLowerCase());
        dev.mic = list.find((d) => has(d, "audioinput", DEV.mic)) || null;
        dev.spk = list.find((d) => has(d, "audiooutput", DEV.speaker)) || null;
        note("devices: microphone=" + (dev.mic ? dev.mic.label : "NOT FOUND") + " speaker=" + (dev.spk ? dev.spk.label : "NOT FOUND"));
        return list;
      });
    }
    return dev.loaded;
  }
  const acceptFakeSink = (proto) => {
    if (proto && typeof proto.setSinkId === "function") {
      const orig = proto.setSinkId;
      proto.setSinkId = function (id) {
        note('setSinkId ' + id);
        if (MODE === "devices") {                                      // whatever the page asks for, its sound goes to the meeting cable
          return realDevices().then(() => (dev.spk ? orig.call(this, dev.spk.deviceId)
            : Promise.reject(new DOMException("the meeting cable was not found: " + DEV.speaker, "NotFoundError"))));
        }
        return id === FAKE_SINK ? Promise.resolve() : orig.call(this, id);
      };
    }
  };
  acceptFakeSink(window.HTMLMediaElement && window.HTMLMediaElement.prototype);
  acceptFakeSink(window.AudioContext && window.AudioContext.prototype);

  const md = navigator.mediaDevices;
  const origEnumerate = md && md.enumerateDevices ? md.enumerateDevices.bind(md) : async () => [];
  if (md) {
    const origGum = md.getUserMedia ? md.getUserMedia.bind(md) : null;
    const fake = (id, kind, label) => ({ deviceId: id, kind, label, groupId: "tara", toJSON() { return { deviceId: id, kind, label, groupId: "tara" }; } });
    if (MODE === "devices") {
      md.getUserMedia = async (constraints) => {
        note('getUserMedia ' + JSON.stringify(constraints));
        const out = new MediaStream();
        if (constraints && constraints.audio) {
          await realDevices();
          if (!dev.mic) throw new DOMException("the voice cable was not found: " + DEV.mic, "NotFoundError");
          const base = typeof constraints.audio === "object" ? constraints.audio : {};
          const s = await origGum({ audio: Object.assign({}, base, { deviceId: { exact: dev.mic.deviceId }, echoCancellation: false, noiseSuppression: false, autoGainControl: false }) });
          s.getAudioTracks().forEach((t) => out.addTrack(t));
        }
        if (constraints && constraints.video) out.addTrack(blackTrack());
        if (!out.getTracks().length) throw new DOMException("no media requested", "NotFoundError");
        S.gumCalls++;
        return out;
      };
      md.enumerateDevices = async () => {
        await realDevices();
        const list = []; if (dev.mic) list.push(dev.mic); if (dev.spk) list.push(dev.spk);
        list.push(fake("tara-cam", "videoinput", "Tara camera"));
        return list;
      };
    } else {
      md.getUserMedia = async (constraints) => {
        note('getUserMedia ' + JSON.stringify(constraints));
        const out = new MediaStream();
        if (constraints && constraints.audio) { ctx(); S.dest.stream.getAudioTracks().forEach((t) => { const c = t.clone(); S.ownTrackIds.add(c.id); out.addTrack(c); }); }
        if (constraints && constraints.video) out.addTrack(blackTrack());
        if (!out.getTracks().length) { if (origGum) return origGum(constraints); throw new DOMException("no media requested", "NotFoundError"); }
        S.gumCalls++;
        return out;
      };
      md.enumerateDevices = async () => [fake("tara-mic", "audioinput", "Tara microphone"), fake("tara-spk", "audiooutput", "Tara speaker"),
                                         fake("tara-cam", "videoinput", "Tara camera")];
    }
  }

  // The level Meet's own packets report (RTP audio-level header extension). It does not depend on our tap or on playout,
  // so it says whether anyone is audible on the wire even when the tap hears nothing.
  const setGuard = (on) => { for (const name of Object.keys(S.routes)) { const r = S.routes[name]; if (r.guard) r.gains.forEach((g) => { g.gain.value = on ? 0 : 1; }); } };
  const meterBuf = new Float32Array(1024);
  setInterval(() => {
    let lvl = 0;
    for (const pc of S.pcs) {
      try {
        for (const r of pc.getReceivers()) {
          if (!r.track || r.track.kind !== "audio") continue;
          for (const x of r.getSynchronizationSources()) lvl = Math.max(lvl, x.audioLevel || 0);
          for (const x of r.getContributingSources()) lvl = Math.max(lvl, x.audioLevel || 0);
        }
      } catch (_) {}
    }
    S.wireLevel = lvl; if (lvl > S.wirePeak) S.wirePeak = lvl;
    // Echo guard and per-route meters. While Tara speaks, routes that could carry her own voice are silenced.
    setGuard(!!S.ctx && S.ctx.currentTime < S.holdUntil);        // her voice, plus a short tail while it drains through the page's own graph
    for (const name of Object.keys(S.routes)) {
      const r = S.routes[name];
      let m = 0;
      for (const an of r.meters) { try { an.getFloatTimeDomainData(meterBuf); let sum = 0; for (let i = 0; i < meterBuf.length; i++) sum += meterBuf[i] * meterBuf[i]; m = Math.max(m, Math.sqrt(sum / meterBuf.length)); } catch (_) {} }
      r.level = m; if (m > r.peak) r.peak = m;
    }
  }, 100);

  // ---- per-participant channels: one PCM stream per remote media element (identity is earned in Python, not here) ----
  const CH = { byStream: new Map(), next: 1, frames: 0, active: false, lit: new Set(), self: null, reported: new Set(),
               errors: [], timer: null, scanTimer: null, ctxs: new Set(), poll: 250, rescanMs: 750 };

  const chNote = (msg) => { CH.errors.push(String(msg).slice(0, 160)); if (CH.errors.length > 20) CH.errors.shift(); note('channels: ' + msg); };

  function chPeak(f) { let m = 0; for (let i = 0; i < f.length; i++) { const a = f[i] < 0 ? -f[i] : f[i]; if (a > m) m = a; } return m; }

  // 16 kHz mono PCM16 out of an arbitrary-rate Float32 frame (only used when the context refuses 16 kHz).
  function down16(f, rate) {
    const ratio = rate / 16000, n = Math.max(1, Math.floor(f.length / ratio)), out = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const lo = Math.floor(i * ratio), hi = Math.min(f.length, Math.floor((i + 1) * ratio));
      let sum = 0, k = 0;
      for (let j = lo; j < hi; j++) { sum += f[j]; k++; }
      const v = Math.max(-1, Math.min(1, k ? sum / k : 0));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return out;
  }

  function chEmit(index, frame, rate) {
    if (!frame || !frame.length) return;
    const pcm = rate === 16000 ? (() => { const o = new Int16Array(frame.length);
      for (let i = 0; i < frame.length; i++) { const v = Math.max(-1, Math.min(1, frame[i])); o[i] = v < 0 ? v * 0x8000 : v * 0x7fff; }
      return o; })() : down16(frame, rate);
    if (!pcm.length) return;
    CH.frames++;
    if (!CH.active) { CH.active = true; note('per-participant audio active: mixed tap stops here'); }
    if (typeof window.__taraChannelAudio === "function") {
      try { window.__taraChannelAudio(index, b64(pcm.buffer), chPeak(frame), Date.now()); } catch (e) { chNote('emit: ' + String(e)); }
    }
  }

  const WORKLET = `class TaraPcmChannel extends AudioWorkletProcessor {
    constructor() { super(); this._buf = new Float32Array(4096); this._n = 0; }
    process(inputs) {
      const ch = inputs[0] && inputs[0][0];
      if (!ch) return true;
      for (let i = 0; i < ch.length; i++) {
        this._buf[this._n++] = ch[i];
        if (this._n === 4096) { this.port.postMessage(this._buf); this._buf = new Float32Array(4096); this._n = 0; }
      }
      return true;
    }
  }
  registerProcessor('tara-pcm-channel', TaraPcmChannel);`;

  function chWorklet(c, src, index) {
    // An AudioWorklet runs on the audio thread, so a busy Meet page cannot starve it into duplicated or dropped frames (the
    // stutter a ScriptProcessor produces under load). Falls back to ScriptProcessor when the worklet cannot be loaded.
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    return c.audioWorklet.addModule(url).then(() => {
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(c, 'tara-pcm-channel', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
                                                                 channelCount: 1, channelCountMode: 'explicit' });
      node.port.onmessage = (e) => chEmit(index, e.data, c.sampleRate);
      return node;
    }).catch((e) => { try { URL.revokeObjectURL(url); } catch (_) {} chNote('worklet: ' + String(e)); return chScript(c, src, index); });
  }

  function chScript(c, src, index) {
    const node = c.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = (e) => chEmit(index, e.inputBuffer.getChannelData(0), c.sampleRate);
    return node;
  }

  function chConnect(el) {
    let stream;
    try { stream = el.srcObject; } catch (_) { return false; }
    if (!(stream instanceof MediaStream) || !stream.getAudioTracks().length) return false;
    if (el.__taraOwn || CH.byStream.has(stream.id)) return false;                     // our own sink, or already captured
    if (stream.getAudioTracks().some((t) => S.ownTrackIds.has(t.id))) return false;   // Tara's own microphone: never a participant
    const index = CH.next++;
    try {
      let c;
      try { c = new AudioContext({ sampleRate: 16000 }); } catch (_) { c = new AudioContext(); }
      if (c.state === "suspended") c.resume().catch(() => {});
      const src = c.createMediaStreamSource(stream);
      const sink = c.createGain(); sink.gain.value = 0;                               // keeps the graph pulled without any output
      const wire = (node) => { src.connect(node); node.connect(sink); sink.connect(c.destination); };
      if (c.audioWorklet) chWorklet(c, src, index).then(wire); else wire(chScript(c, src, index));
      CH.byStream.set(stream.id, { index, ctx: c, el });
      CH.ctxs.add(c);
      const track = stream.getAudioTracks()[0];
      track.addEventListener('ended', () => { try { c.close(); } catch (_) {} CH.byStream.delete(stream.id); CH.ctxs.delete(c); });
      note(`channel ${index} connected (track ${track.id.substring(0, 8)})`);
      return true;
    } catch (e) { chNote(`channel ${index}: ` + String(e)); return false; }
  }

  function chScan() {
    if (!CHANNELS || !document.querySelectorAll) return;
    for (const el of document.querySelectorAll("audio,video")) { try { chConnect(el); } catch (_) {} }
  }

  // ---- the glow: who Meet renders as speaking (raw signal only; it never names a channel by itself) ----
  const TILE_SELECTORS = ['div[data-participant-id]', '[data-participant-id]'];
  const SPEAKING_CLASSES = ['Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli',
                            'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator'];
  const JUNK_NAME = /^Google Participant \(|spaces\/|devices\//;
  const JUNK_PHRASES = ['let participants', 'send messages', 'turn on captions'];

  function tileName(el) {
    const nt = el.querySelector('span.notranslate');
    let t = (nt && nt.textContent ? nt.textContent.trim() : '') || '';
    if (!t) { const labeled = el.querySelector('[data-self-name]'); t = (labeled && labeled.getAttribute('data-self-name') || '').trim(); }
    if (!t || t.length < 2 || t.length > 50) return null;
    if (JUNK_NAME.test(t)) return null;
    const lower = t.toLowerCase();
    if (JUNK_PHRASES.some((p) => lower.includes(p))) return null;
    return t;
  }

  function tileSpeaking(el) {
    for (const cls of SPEAKING_CLASSES) {
      try { if (el.classList.contains(cls) || el.querySelector('.' + CSS.escape(cls))) return true; } catch (_) {}
    }
    return false;
  }

  function glowScan() {
    const lit = new Set();
    const tiles = new Set();
    for (const sel of TILE_SELECTORS) for (const el of document.querySelectorAll(sel)) tiles.add(el);
    for (const el of tiles) {
      const name = tileName(el);
      if (!name) continue;
      const self = el.hasAttribute('data-self-name') || !!el.querySelector('[data-self-name]');
      if (self) {                                                                // the bot's own tile: pinned, never a channel
        if (CH.self !== name) { CH.self = name; if (!CH.reported.has(name)) { CH.reported.add(name);
          if (typeof window.__taraSelf === "function") { try { window.__taraSelf(name); } catch (_) {} } } }
        continue;
      }
      if (tileSpeaking(el)) lit.add(name);
    }
    for (const name of lit) if (!CH.lit.has(name) && typeof window.__taraGlow === "function") { try { window.__taraGlow(name, false, Date.now()); } catch (_) {} }
    for (const name of [...CH.lit]) if (!lit.has(name) && typeof window.__taraGlow === "function") { try { window.__taraGlow(name, true, Date.now()); } catch (_) {} }
    CH.lit = lit;
  }

  if (CHANNELS) {
    setTimeout(chScan, 500);
    CH.scanTimer = setInterval(chScan, CH.rescanMs);
    CH.timer = setInterval(glowScan, CH.poll);
  }

  // Diagnostics for the run log and the tests: how many channels, how many frames, who is lit, and whether a name was reported.
  window.__taraBind = () => ({
    enabled: CHANNELS, channels: CH.byStream.size, contexts: CH.ctxs.size, frames: CH.frames, active: CH.active,
    lit: [...CH.lit], self: CH.self, errors: CH.errors.slice(-3),
    streams: [...CH.byStream.values()].map((v) => ({ index: v.index, state: v.ctx.state })),
  });

  window.__taraPlay = (data, rate) => {
    const c = ctx(); setGuard(true);                                 // silence the routes that could carry her own voice, immediately
    const bin = atob(data); const n = bin.length >> 1; const f = new Float32Array(n);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v & 0x8000) v -= 0x10000; f[i] = v / 32768; }
    const buf = c.createBuffer(1, n, rate); buf.copyToChannel(f, 0);
    const src = c.createBufferSource(); src.buffer = buf; src.connect(S.dest);
    const now = c.currentTime; const start = Math.max(now + 0.03, S.next);
    if (S.firstStart === null || S.next <= now) S.firstStart = start;
    src.start(start); S.next = start + buf.duration; S.holdUntil = S.next + 0.3; S.sources.add(src); src.onended = () => S.sources.delete(src);
    S.outBytes += n * 2;
    return Math.round((S.next - now) * 1000);
  };

  window.__taraStop = () => {
    let played = 0;
    if (S.ctx && S.firstStart !== null) played = Math.max(0, Math.min(S.ctx.currentTime, S.next) - S.firstStart);
    S.sources.forEach((s) => { try { s.stop(); } catch (_) {} });
    S.sources.clear(); S.next = 0; S.firstStart = null; if (S.ctx) S.holdUntil = S.ctx.currentTime + 0.3;
    return Math.round(played * 1000);
  };

  window.__taraStatus = () => ({
    mode: MODE, devices: { mic: dev.mic ? dev.mic.label : null, speaker: dev.spk ? dev.spk.label : null },
    ready: !!S.ctx, state: S.ctx ? S.ctx.state : "none",
    queuedMs: S.ctx ? Math.max(0, Math.round((S.next - S.ctx.currentTime) * 1000)) : 0,
    inFrames: S.inFrames, inBytes: S.inBytes, inRms: S.lastInRms, peakRms: S.peakRms, wireLevel: S.wireLevel, wirePeak: S.wirePeak,
    tapInfo: S.tapList.slice(0, 6).map((t) => ({ muted: t.track.muted, state: t.track.readyState, enabled: t.track.enabled, paused: t.el.paused })), outBytes: S.outBytes,
    routes: Object.fromEntries(Object.entries(S.routes).map(([k, r]) => [k, { peak: r.peak, level: r.level, sources: r.sources }])),
    remoteTracks: S.remoteTracks, taps: S.taps, gumCalls: S.gumCalls, errors: S.errors.slice(-3),
  });

  // Ground truth from WebRTC itself, independent of the WebAudio tap: is audio arriving, and is it loud?
  window.__taraStats = async () => {
    const out = { connections: S.pcs.size, inPackets: 0, inBytes: 0, inEnergy: 0, inLevel: 0, outPackets: 0, outBytes: 0, tracks: 0, mutedTracks: 0 };
    for (const pc of S.pcs) {
      try {
        pc.getReceivers().forEach((r) => { if (r.track && r.track.kind === "audio") { out.tracks++; if (r.track.muted) out.mutedTracks++; } });
        const rep = await pc.getStats();
        rep.forEach((st) => {
          if (st.type === "inbound-rtp" && st.kind === "audio") {
            out.inPackets += st.packetsReceived || 0; out.inBytes += st.bytesReceived || 0;
            out.inEnergy += st.totalAudioEnergy || 0; out.inLevel = Math.max(out.inLevel, st.audioLevel || 0);
          }
          if (st.type === "outbound-rtp" && st.kind === "audio") { out.outPackets += st.packetsSent || 0; out.outBytes += st.bytesSent || 0; }
        });
      } catch (_) {}
    }
    return out;
  };

  window.__taraPcs = () => S.pcs;                                  // for diagnostics and tests only

  // Everything the bot can learn about incoming audio, for the run log: what Meet did, and WebRTC's own counters per stream.
  window.__taraDebug = async () => {
    const inbound = [];
    for (const pc of S.pcs) {
      try {
        const rep = await pc.getStats();
        rep.forEach((st) => {
          if (st.type === "inbound-rtp" && st.kind === "audio") {
            inbound.push({ ssrc: st.ssrc, packets: st.packetsReceived, lost: st.packetsLost, jitter: st.jitter, samples: st.totalSamplesReceived,
                           concealed: st.concealedSamples, silentConcealed: st.silentConcealedSamples, level: st.audioLevel, energy: st.totalAudioEnergy,
                           decoded: st.framesDecoded, bytes: st.bytesReceived });
          }
        });
      } catch (_) {}
    }
    return { events: S.events.splice(0, S.events.length), inbound, hidden: document.hidden, visibility: document.visibilityState };
  };

  window.__taraBridge = true;
})();
