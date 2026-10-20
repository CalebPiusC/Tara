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
</div>
<script>
const F = window.__fake = { cfg: { admit: false }, names: [], peopleOpen: false, micRms: 0, tone: null, remoteRms: 0, joined: false };
const $ = (id) => document.getElementById(id);
const show = (id) => { for (const x of ["prejoin","waiting","denied","ended","call"]) $(x).style.display = (x === id ? "block" : "none"); };
function renderPeople() { const ul = $("plist"); ul.style.display = F.peopleOpen ? "block" : "none";
  ul.innerHTML = ["Tara (You)"].concat(F.names).map(n => '<li role="listitem">' + n + '</li>').join(""); }
$("people").onclick = () => { F.peopleOpen = true; renderPeople(); };
F.addParticipant = (n) => { if (!F.names.includes(n)) F.names.push(n); renderPeople(); };
F.removeParticipant = (n) => { F.names = F.names.filter(x => x !== n); renderPeople(); };
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
  // The "other participant": a tone sent to the bot through a normal (wrapped) connection, like Meet's incoming audio
  const rin = new RTCPeerConnection({ iceServers: [] }), rsend = new RTCPeerConnection({ iceServers: [], __taraIgnore: true });
  rin.onicecandidate = (e) => e.candidate && rsend.addIceCandidate(e.candidate); rsend.onicecandidate = (e) => e.candidate && rin.addIceCandidate(e.candidate);
  rin.ontrack = (e) => { const el = new Audio(); el.srcObject = new MediaStream([e.track]); el.muted = true; el.play().catch(()=>{}); };
  const oc = new AudioContext(); const osc = oc.createOscillator(); osc.frequency.value = 440; const g = oc.createGain(); g.gain.value = 0;
  const dst = oc.createMediaStreamDestination(); osc.connect(g); g.connect(dst); osc.start(); F.tone = { gain: g };
  dst.stream.getTracks().forEach(t => rsend.addTrack(t, dst.stream));
  await rsend.setLocalDescription(await rsend.createOffer()); await rin.setRemoteDescription(rsend.localDescription);
  await rin.setLocalDescription(await rin.createAnswer()); await rsend.setRemoteDescription(rin.localDescription);
  F.joined = true;
}
$("join").onclick = async () => {
  F.nameUsed = $("nm").value; await startMedia();
  if (F.cfg.admit) { show("waiting"); } else { F.admit(); }
};
$("leave").onclick = () => { show("ended"); };
</script></body></html>"""


async def serve(ctx: Any, *, admit: bool = False) -> None:
    """Route the Meet URL to the stand-in for every page of this browser context."""
    page_html = HTML.replace("admit: false", f"admit: {'true' if admit else 'false'}")

    async def handler(route: Any) -> None:
        await route.fulfill(status=200, content_type="text/html", body=page_html)

    await ctx.route("https://meet.google.com/**", handler)
