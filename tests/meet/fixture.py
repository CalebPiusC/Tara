"""A local stand-in for Google Meet, served in place of https://meet.google.com/abc-defg-hij inside a real Chrome.

It is NOT Google's page: it only has the same kinds of controls (buttons labelled by aria-label, the waiting-room text, a people
list) and real WebRTC audio plumbing, so the worker, the bridge and the adapter are tested for real in a real browser. Whether
Google's actual markup still matches `tara.meet.selectors` is separate and unverified.
"""
from __future__ import annotations

from typing import Any

URL = "https://meet.google.com/abc-defg-hij"

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Fake Meet</title></head><body style="font-family:sans-serif">
<div id="prejoin"><h1>Ready to join?</h1>
  <input id="nm" aria-label="Your name" placeholder="Your name" value="">
  <button id="cam" aria-label="Turn off camera (ctrl + e)">Camera</button>
  <button id="mic" aria-label="Turn off microphone (ctrl + d)">Mic</button>
  <button id="join" aria-label="Join now">Join now</button>
</div>
<div id="waiting" style="display:none"><h2>Asking to be let in...</h2><p>Someone will let you in soon</p></div>
<div id="denied" style="display:none"><h2>You can't join this video call</h2><p>Someone in the call denied your request to join</p></div>
<div id="ended" style="display:none"><h2>You left the meeting</h2></div>
<div id="call" style="display:none"><h2>In the call</h2>
  <button id="leave" aria-label="Leave call">Leave</button>
  <button id="people" aria-label="People">People</button>
  <ul id="plist" role="list" aria-label="Participants" style="display:none"></ul>
  <div id="tiles"></div>
  <div data-participant-id="self" data-self-name="Tara"><span class="notranslate">Tara</span></div>
</div>
<script>
const F = window.__fake = { cfg: { admit: false, audio: 'rtc' }, names: [], peopleOpen: false, micRms: 0, tone: null, remoteRms: 0, joined: false, speakers: {} };
const $ = (id) => document.getElementById(id);
const show = (id) => { for (const x of ["prejoin","waiting","denied","ended","call"]) $(x).style.display = (x === id ? "block" : "none"); };
function renderPeople() { const ul = $("plist"); ul.style.display = F.peopleOpen ? "block" : "none";
  ul.innerHTML = ["Tara (You)"].concat(F.names).map(n => '<li role="listitem">' + n + '</li>').join(""); }
$("people").onclick = () => { F.peopleOpen = true; renderPeople(); };
F.addParticipant = (n) => { if (!F.names.includes(n)) F.names.push(n); renderPeople(); };
F.removeParticipant = (n) => { F.names = F.names.filter(x => x !== n); renderPeople(); };
// A participant as Meet renders one: their own media element carrying their own live MediaStream, plus a tile that glows while
// they speak. This is what per-participant capture taps and what the glow scanner reads; the name is never in the audio.
F.addSpeaker = (n) => {
  F.addParticipant(n);
  if (F.speakers[n]) return true;
  const oc = new AudioContext(); const osc = oc.createOscillator(); osc.frequency.value = 500 + 40 * Object.keys(F.speakers).length;
  const g = oc.createGain(); g.gain.value = 0; osc.connect(g); osc.start();
  const dst = oc.createMediaStreamDestination(); g.connect(dst);
  const el = new Audio(); el.srcObject = dst.stream; el.play().catch(() => {});
  const tile = document.createElement("div");
  tile.setAttribute("data-participant-id", "p-" + n.replace(/[^a-z0-9]+/gi, "-").toLowerCase());
  const span = document.createElement("span"); span.className = "notranslate"; span.textContent = n; tile.appendChild(span);
  tile.appendChild(el);                       // like Meet: the participant's media element lives inside their tile, in the DOM
  $("tiles").appendChild(tile);
  F.speakers[n] = { gain: g, el, tile, oc, dst };
  return true;
};
F.setSpeaker = (n, on) => { const s = F.speakers[n]; if (!s) return false; s.gain.gain.value = on ? 0.5 : 0; return true; };
F.setGlow = (n, on) => { const s = F.speakers[n]; if (!s) return false; s.tile.classList.toggle("speaking", !!on); return true; };
F.admit = () => { show("call"); renderPeople(); };
F.deny = () => show("denied");
F.dropCall = () => { $("call").style.display = "none"; };
F.setTone = (on) => { if (F.tone) F.tone.gain.gain.value = on ? 0.5 : 0; };
$("cam").onclick = () => { $("cam").setAttribute("aria-label", "Turn on camera (ctrl + e)"); };
async function startMedia() {
  // Tara's microphone, as Meet would use it: getUserMedia -> a track sent to the far side (measured, not played)
  const mic = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  const a = new RTCPeerConnection({ iceServers: [], __taraIgnore: true }), b = new RTCPeerConnection({ iceServers: [], __taraIgnore: true });
  a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate); b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
  mic.getTracks().forEach(t => a.addTrack(t, mic));
  const ac = new AudioContext(); const an = ac.createAnalyser(); an.fftSize = 2048; const buf = new Float32Array(2048);
  b.ontrack = (e) => { const s = new MediaStream([e.track]); const el = new Audio(); el.srcObject = s; el.muted = true; el.play().catch(()=>{});
    ac.createMediaStreamSource(s).connect(an); };
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
  setInterval(() => { an.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v; F.micRms = Math.sqrt(s / buf.length); }, 50);
  // The "other participant": audible tone, delivered the way the chosen mode says. rtc: a normal incoming WebRTC track (like Meet's
  // standard path). webaudio: the page decodes audio itself and plays it through its own AudioContext (no RTC playout at all).
  // element: the page plays a generated stream in a media element.
  const oc = new AudioContext(); const osc = oc.createOscillator(); osc.frequency.value = 440; const g = oc.createGain(); g.gain.value = 0;
  osc.connect(g); osc.start(); F.tone = { gain: g };
  if (F.cfg.audio === 'webaudio') {
    g.connect(oc.destination);
  } else if (F.cfg.audio === 'element') {
    const dst = oc.createMediaStreamDestination(); g.connect(dst);
    const el = new Audio(); el.srcObject = dst.stream; el.play().catch(() => {});
  } else {
    const rin = new RTCPeerConnection({ iceServers: [] }), rsend = new RTCPeerConnection({ iceServers: [], __taraIgnore: true });
    rin.onicecandidate = (e) => e.candidate && rsend.addIceCandidate(e.candidate); rsend.onicecandidate = (e) => e.candidate && rin.addIceCandidate(e.candidate);
    // Like Meet: send playback to the output device the page lists, and only start playing if that worked.
    rin.ontrack = async (e) => { const el = new Audio(); el.srcObject = new MediaStream([e.track]); el.muted = true;
      try { const d = (await navigator.mediaDevices.enumerateDevices()).find(x => x.kind === 'audiooutput');
            if (d) await el.setSinkId(d.deviceId); F.sinkOk = true; await el.play(); } catch (err) { F.sinkError = err.name; } };
    const dst = oc.createMediaStreamDestination(); g.connect(dst);
    dst.stream.getTracks().forEach(t => rsend.addTrack(t, dst.stream));
    await rsend.setLocalDescription(await rsend.createOffer()); await rin.setRemoteDescription(rsend.localDescription);
    await rin.setLocalDescription(await rin.createAnswer()); await rsend.setRemoteDescription(rin.localDescription);
  }
  // A self-view like Meet's: a muted preview of the bot's own microphone. It must never be tapped (that would be an echo of Tara).
  const self = document.createElement('video'); self.muted = true; self.srcObject = mic; self.play().catch(() => {}); F.selfView = self;
  // A page that monitors the microphone through its own audio context (a level meter wired to the destination at zero gain is common).
  const mctx = new AudioContext(); const msrc = mctx.createMediaStreamSource(mic); const mg = mctx.createGain(); mg.gain.value = 1; msrc.connect(mg); mg.connect(mctx.destination);
  F.joined = true;
}
$("join").onclick = async () => {
  F.nameUsed = $("nm").value; await startMedia();
  if (F.cfg.admit) { show("waiting"); } else { F.admit(); }
};
$("leave").onclick = () => { show("ended"); };
</script></body></html>"""


async def serve(ctx: Any, *, admit: bool = False, audio: str = "rtc") -> None:
    """Route the Meet URL to the stand-in for every page of this browser context."""
    page_html = HTML.replace("admit: false, audio: 'rtc'", f"admit: {'true' if admit else 'false'}, audio: '{audio}'")

    async def handler(route: Any) -> None:
        await route.fulfill(status=200, content_type="text/html", body=page_html)

    await ctx.route("https://meet.google.com/**", handler)
