"""Real Chrome, local pages only. Proves the audio bridge, the worker and the adapter against a stand-in for Meet (tests/meet_fixture.py).

Skipped when Playwright or Google Chrome is missing. These do NOT prove anything about Google's real pages; see docs/GOOGLE_MEET_BOT.md.
"""
import array
import asyncio
import math
import os

import pytest
from meet_fixture import URL, serve
from test_voice_engine import mk, running

from tara.contracts import MeetingEventKind, MeetingTarget, PCMChunk
from tara.errors import JoinError
from tara.meet.adapter import MeetAdapter
from tara.meet.browser import BrowserConfig, playwright_launcher
from tara.meet.controller import MeetController
from tara.meet.worker import State, WorkerConfig
from tara.voice.events import InputTranscript, ResponseAudio, TurnComplete

pytest.importorskip("playwright")
CHROME = os.environ.get("TARA_CHROME_PATH") or "/Applications/Google Chrome.app"
pytestmark = [pytest.mark.browser, pytest.mark.skipif(not os.path.exists(CHROME) and os.name != "posix", reason="Chrome not found")]
if not os.path.exists("/Applications/Google Chrome.app") and not os.path.exists("/usr/bin/google-chrome"):
    pytestmark.append(pytest.mark.skip(reason="Google Chrome is not installed"))

run = asyncio.run


def sine(freq=600, ms=800, rate=24000, amp=0.5) -> bytes:
    n = int(rate * ms / 1000)
    return array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))).tobytes()


def rms16(pcm: bytes) -> float:
    a = array.array("h")
    a.frombytes(pcm)
    return math.sqrt(sum(x * x for x in a) / max(1, len(a))) / 32768


def make_adapter(tmp_path, *, admit=False, session_id="s1", **kw):
    statuses: list[str] = []
    ctrl = MeetController(tmp_path / "locks")

    def factory(bridge):
        return playwright_launcher(BrowserConfig(tmp_path / "profile", "chrome", headless=True), bridge,
                                   setup=lambda ctx: serve(ctx, admit=admit))

    ad = MeetAdapter(ctrl, factory, session_id=session_id, interview_code="alex", url=URL, candidate_stream_id="a",
                     worker_cfg=WorkerConfig(poll_s=0.1, admit_timeout_s=20, join_timeout_s=20, reconnect_backoff_s=0.2),
                     on_status=statuses.append, **kw)
    return ad, ctrl, statuses


async def collect(ad, bucket: list):
    async for ev in ad.events():
        bucket.append(ev)


async def until(cond, timeout=10.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def page_of(ad):
    return ad.worker.page.raw


async def fake(ad, expr):
    return await page_of(ad).evaluate(expr)


async def stream_of(gen):
    for c in gen:
        yield c


def test_audio_flows_both_ways_through_real_chrome(tmp_path):
    async def go():
        ad, ctrl, st = make_adapter(tmp_path)
        events: list = []
        await ad.join(MeetingTarget(URL))
        pump = asyncio.create_task(collect(ad, events))
        try:
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await until(lambda: any(e.kind is MeetingEventKind.PARTICIPANT_JOINED for e in events))
            joined = [e for e in events if e.kind is MeetingEventKind.PARTICIPANT_JOINED][0]
            assert joined.payload == {"id": "a", "name": "Alex Kim"}                # the only other person is the candidate
            # 1. meeting audio -> Tara: a 440 Hz tone from the other participant arrives as 16 kHz PCM with energy, labelled "a"
            await fake(ad, "window.__fake.setTone(true)")
            await until(lambda: any(e.kind is MeetingEventKind.AUDIO and rms16(e.payload["chunk"].data) > 0.05 for e in events), 15)
            assert await fake(ad, "window.__fake.sinkOk") is True and await fake(ad, "window.__fake.sinkError || null") is None   # the page could pick the speaker it was offered
            loud = next(e for e in events if e.kind is MeetingEventKind.AUDIO and rms16(e.payload["chunk"].data) > 0.05)
            assert loud.payload["chunk"].sample_rate == 16000 and loud.payload["chunk"].stream_id == "a"
            from tara.meet.bridge import AudioBridge
            wire = await AudioBridge.stats(page_of(ad))                              # WebRTC's own numbers agree with the tap: speech-sized packets
            assert wire["connections"] >= 1 and wire["inPackets"] > 0 and wire["inBytes"] > wire["inPackets"] * 20 and wire["tracks"] >= 1
            for _ in range(40):                                                      # the level sampler runs every 200 ms
                st = await AudioBridge.status(page_of(ad))
                if st["wirePeak"] > 0.2:
                    break
                await asyncio.sleep(0.1)
            dbg = await AudioBridge.debug(page_of(ad))                                # the run log's raw material
            assert any("tap 1" in e["msg"] for e in dbg["events"]) and any("getUserMedia" in e["msg"] for e in dbg["events"])
            assert dbg["inbound"] and dbg["inbound"][0]["packets"] > 0 and dbg["inbound"][0]["samples"] > 0
            assert st["wirePeak"] > 0.2 and st["peakRms"] > 0.2 and st["tapInfo"] and st["tapInfo"][0]["paused"] is False   # RTP level agrees with the tap
            await fake(ad, "window.__fake.setTone(false)")
            # 2. Tara -> meeting: her speech becomes the microphone the far side measures
            assert await fake(ad, "window.__fake.micRms") < 0.01
            chunks = [PCMChunk(sine(ms=200), 24000, 0) for _ in range(4)]
            task = asyncio.create_task(ad.send_audio("u1", stream_of(chunks)))
            await asyncio.sleep(0.35)
            assert await fake(ad, "window.__fake.micRms") > 0.05
            receipt = await task
            assert receipt.completed and 700 <= receipt.delivered_ms <= 900
            await asyncio.sleep(0.4)
            assert await fake(ad, "window.__fake.micRms") < 0.02                    # silent again after she finishes
            # 3. barge-in: stopping cancels what is queued and reports how much was really played
            long_chunks = [PCMChunk(sine(ms=500), 24000, 0) for _ in range(8)]
            task2 = asyncio.create_task(ad.send_audio("u2", stream_of(long_chunks)))
            await asyncio.sleep(0.6)
            played = await ad.stop_audio("u2")
            assert 150 <= played <= 1500
            r2 = await task2
            assert not r2.completed
            await asyncio.sleep(0.4)
            assert await fake(ad, "window.__fake.micRms") < 0.02
            assert (await ad.send_audio("u2", stream_of([PCMChunk(sine(), 24000, 0)]))).completed is False   # a stopped id stays stopped
        finally:
            await ad.leave()
            await pump
        assert not list((tmp_path / "locks").glob("*.lock"))                          # cleanup released the meeting lock
    run(go())


def test_waiting_room_then_admitted_people_come_and_go_and_leave_cleans_up(tmp_path):
    async def go():
        ad, ctrl, st = make_adapter(tmp_path, admit=True)
        events: list = []
        joining = asyncio.create_task(ad.join(MeetingTarget(URL)))
        await until(lambda: ad.worker is not None and ad.worker.state is State.WAITING_ROOM, 20)
        assert not joining.done()                                                     # it waits: it never gets itself in
        await fake(ad, "window.__fake.admit()")
        await joining
        pump = asyncio.create_task(collect(ad, events))
        await fake(ad, "window.__fake.addParticipant('Alex Kim')")
        await until(lambda: any(e.kind is MeetingEventKind.PARTICIPANT_JOINED for e in events))
        await fake(ad, "window.__fake.addParticipant('Sam')")
        await until(lambda: len([e for e in events if e.kind is MeetingEventKind.PARTICIPANT_JOINED]) == 2)
        await fake(ad, "window.__fake.setTone(true)")
        await until(lambda: any(e.kind is MeetingEventKind.AUDIO and e.payload["chunk"].stream_id == "mix" for e in events), 15)
        await fake(ad, "window.__fake.removeParticipant('Sam')")
        await until(lambda: any(e.kind is MeetingEventKind.PARTICIPANT_LEFT for e in events))
        assert any("waiting_room" in s for s in st) and any("cannot be told apart" in s for s in st)
        await ad.leave()
        await pump
        assert ad.worker.state in (State.ENDED,) and not list((tmp_path / "locks").glob("*.lock"))
        ids = [e.event_id for e in events]
        assert len(ids) == len(set(ids))                                              # no duplicate event ids
    run(go())


def test_not_admitted_is_reported_not_forced(tmp_path):
    async def go():
        ad, ctrl, st = make_adapter(tmp_path, admit=True)
        joining = asyncio.create_task(ad.join(MeetingTarget(URL)))
        await until(lambda: ad.worker is not None and ad.worker.state is State.WAITING_ROOM, 20)
        await fake(ad, "window.__fake.deny()")
        with pytest.raises(JoinError, match="rejected"):
            await joining
        assert ctrl.sessions["s1"].state == "rejected" and not list((tmp_path / "locks").glob("*.lock"))
    run(go())


def test_a_dead_browser_is_rejoined_and_audio_resumes(tmp_path):
    async def go():
        ad, ctrl, st = make_adapter(tmp_path)
        events: list = []
        await ad.join(MeetingTarget(URL))
        pump = asyncio.create_task(collect(ad, events))
        try:
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await until(lambda: any(e.kind is MeetingEventKind.PARTICIPANT_JOINED for e in events))
            await page_of(ad).close()                                                 # the browser page dies
            await until(lambda: any(e.kind is MeetingEventKind.RECONNECTED for e in events), 30)
            assert ad.worker.reconnects == 1 and ad.worker.state is State.IN_MEETING
            assert (await ad.send_audio("late", stream_of([]))).completed in (True, False)
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await fake(ad, "window.__fake.setTone(true)")
            n = len(events)
            await until(lambda: any(e.kind is MeetingEventKind.AUDIO and rms16(e.payload["chunk"].data) > 0.05 for e in events[n:]), 20)
        finally:
            await ad.leave()
            await pump
    run(go())


def test_the_real_voice_engine_hears_the_meeting_and_speaks_into_it(tmp_path):
    async def go():
        ad, ctrl, st = make_adapter(tmp_path)
        eng, (sess,), prov, _ = mk(adapter=ad, params=None)
        async with running(eng):
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await fake(ad, "window.__fake.setTone(true)")
            await until(lambda: len(sess.audio) > 3, 15)                              # meeting audio reached the voice session
            assert eng.metrics.counters["live_uplink_dropped"] == 0
            await fake(ad, "window.__fake.setTone(false)")                            # the candidate stops talking, so Tara may reply
            await asyncio.sleep(1.5)
            sess.push(InputTranscript("Tara, can you hear me?", True))
            sess.push(*[ResponseAudio(1, sine(ms=100, rate=24000)) for _ in range(8)])
            sess.push(TurnComplete(1))
            heard = []
            for _ in range(60):                                                       # while she speaks, the far side measures her voice
                heard.append(await fake(ad, "window.__fake.micRms"))
                if max(heard) > 0.05:
                    break
                await asyncio.sleep(0.05)
            assert max(heard) > 0.05
            await until(lambda: eng.playback.results, 10)                              # and her reply completes
        assert not list((tmp_path / "locks").glob("*.lock"))
    run(go())


def make_mode_adapter(tmp_path, audio):
    ctrl = MeetController(tmp_path / "locks")

    def factory(bridge):
        return playwright_launcher(BrowserConfig(tmp_path / "profile", "chrome", headless=True), bridge,
                                   setup=lambda ctx: serve(ctx, audio=audio))

    return MeetAdapter(ctrl, factory, session_id="m1", interview_code="", url=URL, candidate_stream_id="a",
                       worker_cfg=WorkerConfig(poll_s=0.1, admit_timeout_s=20, join_timeout_s=20), diag_s=0)


@pytest.mark.parametrize("mode", ["webaudio", "element"])
def test_audio_the_page_plays_itself_is_heard_too(tmp_path, mode):
    """Meet may decode and play audio on its own instead of through WebRTC playout; the bridge must still hear it."""
    async def go():
        from tara.meet.bridge import AudioBridge
        ad = make_mode_adapter(tmp_path, mode)
        events: list = []
        await ad.join(MeetingTarget(URL))
        pump = asyncio.create_task(collect(ad, events))
        try:
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await fake(ad, "window.__fake.setTone(true)")
            await until(lambda: any(e.kind is MeetingEventKind.AUDIO and rms16(e.payload["chunk"].data) > 0.05 for e in events), 15)
            for _ in range(30):
                st = await AudioBridge.status(page_of(ad))
                if st["routes"].get(mode, {}).get("peak", 0) > 0.2:
                    break
                await asyncio.sleep(0.1)
            assert st["routes"][mode]["peak"] > 0.2                                   # the log can say which route carried it
            await fake(ad, "window.__fake.setTone(false)")
        finally:
            await ad.leave()
            await pump
    run(go())


def test_taras_own_voice_never_comes_back_through_monitors_or_self_views(tmp_path):
    """The stand-in page monitors the microphone through its own audio context and shows a muted self-view, as Meet does."""
    async def go():
        ad = make_mode_adapter(tmp_path, "rtc")
        events: list = []
        await ad.join(MeetingTarget(URL))
        pump = asyncio.create_task(collect(ad, events))
        try:
            await fake(ad, "window.__fake.addParticipant('Alex Kim')")
            await asyncio.sleep(1.0)
            start = len(events)
            chunks = [PCMChunk(sine(ms=200), 24000, 0) for _ in range(5)]
            receipt = await ad.send_audio("self", stream_of(chunks))
            await asyncio.sleep(0.4)
            heard = [rms16(e.payload["chunk"].data) for e in events[start:] if e.kind is MeetingEventKind.AUDIO]
            assert receipt.completed and heard and max(heard) < 0.02                  # nothing of her own voice reached the inbound audio
            from tara.meet.bridge import AudioBridge
            routes = (await AudioBridge.status(page_of(ad)))["routes"]
            assert "element" not in routes or routes["element"]["peak"] < 0.02       # the muted self-view was never captured
        finally:
            await ad.leave()
            await pump
    run(go())


def test_devices_mode_hands_the_page_the_named_cable_and_taps_nothing(tmp_path):
    """Chrome's own fake audio device stands in for a cable here (real cables need the driver installed)."""
    async def go():
        from tara.meet.bridge import AudioBridge
        bridge = AudioBridge(lambda _b: None, "devices", {"mic": "Fake", "speaker": "Fake"})
        cfg = BrowserConfig(tmp_path / "profile", "chrome", True, audio_mode="devices",
                            extra_args=("--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"))
        page, close = await playwright_launcher(cfg, bridge, setup=lambda ctx: serve(ctx))()
        try:
            await page.goto(URL)
            raw = page.raw
            out = await raw.evaluate("""async () => {
                const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true }, video: true });
                const devs = await navigator.mediaDevices.enumerateDevices();
                return { audio: s.getAudioTracks().map(t => t.label), video: s.getVideoTracks().length,
                         kinds: devs.map(d => d.kind + ':' + d.label) , settings: s.getAudioTracks()[0].getSettings() };
            }""")
            st = await AudioBridge.status(raw)
            assert out["audio"] and "Fake" in out["audio"][0] and out["video"] == 1               # the real device, not the in-page one
            assert any(k.startswith("audioinput:") and "Fake" in k for k in out["kinds"]) and "videoinput:Tara camera" in out["kinds"]
            assert not any(k.startswith("audioinput:Tara") for k in out["kinds"])                    # the in-page fake microphone is gone
            assert out["settings"].get("echoCancellation") is False                                  # her voice is not processed
            assert st["mode"] == "devices" and st["devices"]["mic"] and st["routes"] == {}            # nothing is tapped in this mode
            assert (await raw.evaluate("window.__taraMode")) == "devices"
        finally:
            await close()
    run(go())
