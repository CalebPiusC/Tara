"""Async end-to-end tests of MeetingEngine with fakes + LocalMeetingAdapter.
They prove orchestration/policy only; no real audio models or Meet."""
import asyncio

import pytest

from tara.adapters.local import LocalMeetingAdapter
from tara.core import events as E
from tara.core.config import Mode
from tara.core.contracts import MeetingEvent, Passage, PCMChunk
from tara.core.contracts import MeetingEventKind as MK
from tara.core.errors import ProviderTimeout
from tara.core.knowledge import InMemoryKnowledge
from tara.floor.audio import EnergyVad, Segmenter, silence, tone
from tara.floor.classify import RuleIntentClassifier
from tara.floor.identity import LabelMap, ParticipantRegistry, SpeakerAttributor
from tara.floor.participation import Attribution, Utterance
from tara.floor.runtime import State
from tara.legacy.conversation import CLARIFY_SPEAKER, UNKNOWN_LINE, ConversationEngine
from tara.legacy.engine import EngineParams, MeetingEngine
from tara.testing.fakes import FailingLLM, FakeLLMProvider, FakeStt, FakeTts
from tests.helpers import ROLES, TH, cfg, group_cfg

NAMES = {"a": "Candidate A", "b": "Candidate B", "r": "Recruiter"}


def build(cfg_=None, llm=None, tts=None, adapter=None, stt=None, kb=None, params=None, slow=False):
    cfg_ = cfg_ or group_cfg()
    reg = ParticipantRegistry()
    for k, v in NAMES.items():
        reg.join(k, v)
    llm = llm or FakeLLMProvider(lambda r, a: {"say": "Here is a reasonably long answer for you.", "cites": []})
    adapter = adapter or LocalMeetingAdapter(realtime_factor=1.0)
    eng = MeetingEngine(
        cfg=cfg_, roles=ROLES, names=NAMES, adapter=adapter, stt=stt or FakeStt(),
        tts=tts or FakeTts(chars_per_second=8 if slow else 400, chunk_ms=100), classifier=RuleIntentClassifier(),
        conversation=ConversationEngine(llm, cfg_, kb), thresholds=TH, registry=reg,
        attributor=SpeakerAttributor(reg, LabelMap()),
        segmenter_factory=lambda sid: Segmenter(EnergyVad(), stream_id=sid, hangover_ms=200, min_speech_ms=60),
        params=params or EngineParams())
    return eng, adapter, llm


def U(eid, pid, text, conf=0.98, overlap=False):
    return Utterance(eid, text, Attribution(pid, conf, overlap))


async def started(eng):
    await eng.start()
    return eng


async def settle(eng, timeout=3.0):
    t = eng._task
    if t:
        await asyncio.wait_for(asyncio.shield(t), timeout)
        await asyncio.sleep(0)


def run(coro):
    return asyncio.run(coro)


def test_direct_address_is_spoken_to_completion_with_event_order():
    async def go():
        eng, ad, llm = build(llm=FakeLLMProvider(lambda r, a: {"say": "Short.", "cites": []}), cfg_=cfg(),)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, can you explain the deployment process?"))
        await settle(eng)
        assert eng.spoken[0]["outcome"] == "completed" and eng.rt.state is State.LISTENING
        names = eng.bus.names()
        order = [n for n in names if n in (E.PARTICIPATION, E.PROPOSAL_CREATED, E.PROPOSAL_APPROVED, E.SPEECH_STARTED, E.SPEECH_DELIVERED)]
        assert order == [E.PARTICIPATION, E.PROPOSAL_CREATED, E.PROPOSAL_APPROVED, E.SPEECH_STARTED, E.SPEECH_DELIVERED]
    run(go())


def test_humans_talking_to_each_other_get_silence():
    async def go():
        eng, ad, llm = build(cfg_=cfg())
        await eng.start()
        await eng.handle_utterance(U("1", "a", "I think we should migrate on friday"))
        await eng.handle_utterance(U("2", "b", "friday is risky, what about the redis cache"))
        assert eng.spoken == [] and llm.calls == [] and eng.rt.state is State.LISTENING
    run(go())


def test_takeover_mid_speech_stops_audio_and_blocks_more():
    async def go():
        eng, ad, llm = build(cfg_=group_cfg(), slow=True)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, please explain the interview format."))
        await asyncio.sleep(0.35)                      # playing now (paced in real time)
        assert eng.rt.state is State.SPEAKING
        await eng.handle_utterance(U("2", "r", "Thanks Tara, I'll take it from here."))
        rec = eng.spoken[0]
        assert rec["outcome"] == "interrupted" and 0 < rec["delivered_ms"] < 4000
        assert eng.rt.takeover_active and eng.rt.state is State.YIELDING_CONTROL
        await eng.handle_utterance(U("3", "r", "Tara, one more thing"))
        assert len(eng.spoken) == 1
        eng.release_takeover()
        assert eng.rt.state is State.LISTENING
        assert E.TAKEOVER_STARTED in eng.bus.names() and E.SPEECH_INTERRUPTED in eng.bus.names()
    run(go())


def test_barge_in_by_candidate_cancels_playback_and_truncates_history():
    async def go():
        eng, ad, llm = build(cfg_=group_cfg(), slow=True)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, explain the format please."))
        await asyncio.sleep(0.4)
        await eng.handle_utterance(U("2", "a", "sorry, quick thought on that", 0.98))
        assert eng.spoken[0]["outcome"] == "interrupted" and not eng.rt.takeover_active
        assert eng.rt.state is State.LISTENING and eng.rt.epoch >= 1
        assert any(l.speaker == "Tara" and l.text.endswith("[interrupted]") for l in eng.lines)
        assert not any(l.speaker == "Tara" and "answer for you." in l.text for l in eng.lines)   # unheard part not recorded
    run(go())


def test_slow_generation_made_stale_by_conversation_moving_on():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "late"}, latency_s=0.3))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what about redis?"))
        assert eng.rt.state is State.GENERATING
        await eng.handle_utterance(U("2", "a", "anyway, lets talk about the budget"))
        await asyncio.sleep(0.5)
        assert eng.spoken == [] and eng.rt.state is State.LISTENING
        assert E.PROPOSAL_REJECTED in eng.bus.names()
    run(go())


def test_proposal_older_than_max_age_is_dropped():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "late"}, latency_s=0.2),
                             params=EngineParams(max_proposal_age_ms=50))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what about redis?"))
        await settle(eng)
        assert eng.spoken == [] and eng.rt.state is State.LISTENING
        rej = [e for e in eng.bus.history if e.name == E.PROPOSAL_REJECTED]
        assert rej and rej[-1].data["reason"] == "stale_age"
    run(go())


def test_llm_failure_means_silence_not_improvisation():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FailingLLM(ProviderTimeout("down")))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is our rollback plan?"))
        await settle(eng)
        assert eng.spoken == [] and eng.rt.state is State.LISTENING
        assert ad.sent == {}
    run(go())


def test_malformed_model_output_means_silence():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"nope": 1}))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is our rollback plan?"))
        await settle(eng)
        assert eng.spoken == [] and eng.rt.state is State.LISTENING
    run(go())


def test_tts_failure_is_reported_and_runtime_recovers():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), tts=FakeTts(fail=True))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is our rollback plan?"))
        await settle(eng)
        assert eng.spoken[0]["outcome"] == "failed" and eng.rt.state is State.LISTENING
        assert E.SPEECH_FAILED in eng.bus.names()
    run(go())


def test_unreliable_identity_in_interview_asks_instead_of_answering_and_skips_llm():
    async def go():
        eng, ad, llm = build(cfg_=group_cfg())
        await eng.start()
        await eng.handle_utterance(U("1", "a", "Tara, is my answer okay?", conf=0.4))
        await settle(eng)
        assert llm.calls == [] and eng.rt.state is State.LISTENING
        assert eng.spoken[0]["text"] == CLARIFY_SPEAKER
    run(go())


def test_pitch_unknown_feature_admits_limit_without_llm():
    async def go():
        c = cfg(mode=Mode.PITCH, approved_knowledge_ids=("kb",))
        kb = InMemoryKnowledge([Passage("kb1", "Testflow runs regression tests in parallel.")])
        eng, ad, llm = build(cfg_=c, kb=kb)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, do you support quantum billing?"))
        await settle(eng)
        assert eng.spoken[0]["text"] == UNKNOWN_LINE and llm.calls == []
        assert eng.log.unanswered == ["Tara, do you support quantum billing?"]
    run(go())


def test_observe_mode_never_speaks_even_if_addressed():
    async def go():
        from tara.core.config import SpeakingPolicy
        eng, ad, llm = build(cfg_=cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, thoughts?"))
        assert eng.spoken == [] and llm.calls == [] and eng.rt.state is State.LISTENING
    run(go())


def test_duplicate_adapter_and_utterance_events_answered_once():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "Ok."}))
        await eng.start()
        u = U("1", "r", "Tara, what is the plan?")
        await eng.handle_utterance(u); await eng.handle_utterance(u)
        await settle(eng)
        assert len(eng.spoken) == 1 and len(llm.calls) == 1
    run(go())


def test_reconnect_during_playback_does_not_assume_delivery():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), slow=True)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan for release?"))
        await asyncio.sleep(0.3)
        await eng.handle_event(MeetingEvent(MK.RECONNECTED, 0, "m1"))
        await eng.handle_event(MeetingEvent(MK.RECONNECTED, 0, "m1"))     # duplicate ignored
        assert eng.spoken[0]["outcome"] == "interrupted" and eng.rt.state is State.LISTENING
        assert eng.rt.epoch == 1          # duplicate reconnect did not bump twice
        assert ad.completed.get(eng.spoken[0]["id"]) is False
    run(go())


def test_participant_events_update_registry():
    async def go():
        eng, ad, _ = build(cfg_=cfg())
        await eng.start()
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "m1", {"id": "z", "name": "Zed"}))
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_RENAMED, 0, "m2", {"id": "z", "name": "Zedd"}))
        assert eng.registry.get("z").display_name == "Zedd"
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_LEFT, 0, "m3", {"id": "z"}))
        assert "z" not in eng.registry.present_ids()
    run(go())


def test_join_failure_propagates():
    async def go():
        from tara.core.errors import JoinError
        eng, ad, _ = build(adapter=LocalMeetingAdapter(fail_join=True))
        with pytest.raises(JoinError):
            await eng.start()
    run(go())


def test_audio_path_end_to_end_segment_stt_attribution_response():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), stt=FakeStt(queue=["Tara, what is the rollback plan?"]),
                             llm=FakeLLMProvider(lambda r, a: {"say": "Roll back via the previous tag."}))
        eng.registry.join("r", "Recruiter")
        await eng.start()
        pcm = silence(300) + tone(700) + silence(600)
        for i in range(0, len(pcm), 640):
            await eng.handle_event(MeetingEvent(MK.AUDIO, i // 32, f"a{i}", {"chunk": PCMChunk(pcm[i:i + 640], 16000, i // 32, "r")}))
        await eng.drain()
        await settle(eng)
        assert eng.spoken and eng.spoken[0]["outcome"] == "completed"
        att = [e for e in eng.bus.history if e.name == E.ATTRIBUTION][0]
        assert att.data["participant"] == "r"                      # per-participant stream => attributed
        assert E.VAD_STARTED in eng.bus.names() and E.VAD_ENDED in eng.bus.names()
    run(go())


def test_tara_own_audio_stream_and_echo_transcript_are_ignored():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "Roll back via the previous tag."}),
                             stt=FakeStt(queue=["Roll back via the previous tag."]))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the rollback plan?"))
        await settle(eng)
        n = len(eng.spoken)
        await eng.ingest_audio(PCMChunk(tone(500), 16000, 0, "tara"))     # her own track: dropped
        assert not eng._segmenters
        pcm = silence(200) + tone(600) + silence(500)
        for i in range(0, len(pcm), 640):
            await eng.ingest_audio(PCMChunk(pcm[i:i + 640], 16000, i // 32, "mix"))
        await eng.drain()
        assert len(eng.lines) == 2 and len(eng.spoken) == n           # echo transcript dropped
    run(go())


def test_stt_empty_after_barge_in_does_not_wedge_runtime():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), slow=True, stt=FakeStt(queue=[""]))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan for release?"))
        await asyncio.sleep(0.3)
        pcm = tone(400) + silence(500)
        for i in range(0, len(pcm), 640):
            await eng.ingest_audio(PCMChunk(pcm[i:i + 640], 16000, i // 32, "mix"))
        await eng.drain()
        assert eng.rt.state is State.LISTENING and eng.spoken[0]["outcome"] == "interrupted"
    run(go())


def test_shutdown_stops_everything():
    async def go():
        eng, ad, _ = build(cfg_=cfg(), slow=True)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan for release?"))
        await asyncio.sleep(0.2)
        await eng.shutdown()
        assert eng.rt.state is State.STOPPED and not ad.joined
        assert eng._task is None
    run(go())
