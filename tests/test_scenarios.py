"""Scripted simulations (policy logic only; no audio/models/Meet)."""
from helpers import ROLES, TH, group_cfg, cfg, utt
from tara.config import Role
from tara.participation import Intent, IntentKind as K
from tara.runtime import State
from tara.sim import FakeLLM, SimMeeting

ADDR = Intent(K.ADDRESSED)
H2H = Intent(K.HUMAN_TO_HUMAN)


def sim(c=None, **kw):
    return SimMeeting(c or group_cfg(), ROLES, TH, **kw)


def u(eid, pid, conf, text="x", overlap=False):
    return utt(pid, conf, overlap, eid, 0, text)


# --- group interview attribution ----------------------------------------
def test_candidate_evidence_never_crosses_candidates():
    m = sim()
    m.feed(1000, u("e1", "a", 0.97, "use idempotency key"), H2H)
    m.feed(5000, u("e2", "b", 0.96, "different approach", overlap=True), H2H)   # interruption overlap
    m.feed(6000, u("e3", None, 0.0, "add a cache"), H2H)                        # unknown speaker
    m.feed(7000, u("e4", "a", 0.55, "ambiguous"), H2H)                          # low confidence
    m.feed(8000, u("e5", "b", 0.95, "clean B answer"), H2H)
    assert [e.quote for e in m.evidence.report_for("a")] == ["use idempotency key"]
    assert [e.quote for e in m.evidence.report_for("b")] == ["clean B answer"]
    assert m.evidence.report_for("c") == []
    reasons = {why for _, why in m.evidence.quarantine}
    assert reasons == {"overlap", "unknown_speaker", "low_confidence"}
    # quarantined text never appears in anyone's evidence
    quotes = [e.quote for c in ("a", "b") for e in m.evidence.report_for(c)]
    assert "add a cache" not in quotes and "different approach" not in quotes


def test_tara_does_not_answer_when_candidate_identity_unreliable():
    m = sim()
    m.feed(1000, u("e1", "a", 0.4, "Tara, is that ok?"), ADDR)
    assert m.spoken == []
    assert m.decisions[0][1].reason == "speaker_identity_low"
    assert m.rt.state is State.LISTENING


# --- takeover mid-speech -------------------------------------------------
def test_takeover_mid_speech_stops_playback_and_blocks_further_speech():
    m = sim(llm=FakeLLM(latency_ms=500, speak_ms=6000))
    m.feed(1000, u("e1", "r", 0.99, "Tara, explain the format"), ADDR)
    m.advance(1600)
    assert m.rt.state is State.SPEAKING
    start = m.spoken[0].start_ms
    m.feed(3600, u("e2", "r", 0.99, "Thanks Tara, I'll take it from here"), Intent(K.TAKEOVER))
    s = m.spoken[0]
    assert s.outcome == "yielded" and s.delivered_ms == 3600 - start
    assert m.rt.state is State.YIELDING_CONTROL and m.rt.takeover_active
    m.feed(9000, u("e3", "r", 0.99, "Tara, one more thing"), ADDR)
    assert len(m.spoken) == 1                      # no new speech while taken over
    m.advance(20000)
    assert m.spoken[0].delivered_ms == 3600 - start  # playback never "completes" after takeover


def test_takeover_by_candidate_is_ignored():
    m = sim(llm=FakeLLM(latency_ms=500, speak_ms=6000))
    m.feed(1000, u("e1", "r", 0.99, "Tara, go"), ADDR)
    m.advance(2000)
    m.feed(3000, u("e2", "a", 0.99, "I'll take over"), Intent(K.TAKEOVER))
    assert not m.rt.takeover_active
    # candidate speech is still a barge-in, so Tara stops, but does not yield control
    assert m.spoken[0].outcome == "interrupted"


def test_takeover_during_generation_discards_pending():
    m = sim(llm=FakeLLM(latency_ms=2000))
    m.feed(1000, u("e1", "r", 0.99, "Tara?"), ADDR)
    m.feed(1500, u("e2", "r", 0.99, "I'll take it"), Intent(K.TAKEOVER))
    m.advance(10000)
    assert m.spoken == []


def test_release_resumes_listening_then_tara_can_speak():
    m = sim(llm=FakeLLM(latency_ms=100, speak_ms=1000))
    m.feed(1000, u("e1", "r", 0.99, "I'll take it"), Intent(K.TAKEOVER))
    m.release_takeover()
    assert m.rt.state is State.LISTENING
    m.feed(2000, u("e2", "r", 0.99, "Tara, summarize"), ADDR)
    m.advance(4000)
    assert len(m.spoken) == 1 and m.spoken[0].outcome == "completed"


# --- barge-in ------------------------------------------------------------
def test_barge_in_truncates_and_unaddressed_interrupter_gets_silence():
    m = sim(llm=FakeLLM(latency_ms=100, speak_ms=8000))
    m.feed(1000, u("e1", "r", 0.99, "Tara, explain"), ADDR)
    m.advance(1200)
    m.feed(3200, u("e2", "a", 0.97, "wait, actually"), H2H)
    s = m.spoken[0]
    assert s.outcome == "interrupted" and s.delivered_ms == 3200 - 1100
    assert m.rt.state is State.LISTENING and len(m.spoken) == 1
    assert m.rt.epoch == 1


# --- stale responses -----------------------------------------------------
def test_slow_llm_response_dropped_as_stale_age():
    m = sim(llm=FakeLLM(latency_ms=4000), max_proposal_age_ms=3000)
    m.feed(1000, u("e1", "r", 0.99, "Tara, explain"), ADDR)
    m.advance(10000)
    assert m.spoken == [] and m.rejected == [("e1", "stale_age")]
    assert m.rt.state is State.LISTENING


def test_conversation_moves_on_makes_pending_stale_epoch():
    m = sim(llm=FakeLLM(latency_ms=2000))
    m.feed(1000, u("e1", "r", 0.99, "Tara, explain"), ADDR)
    m.feed(1500, u("e2", "a", 0.97, "unrelated chat"), H2H)
    m.advance(10000)
    assert m.spoken == [] and m.rejected == [("e1", "stale_epoch")]


def test_duplicate_event_triggers_single_response():
    m = sim(llm=FakeLLM(latency_ms=100, speak_ms=500))
    for _ in range(3):
        m.feed(1000, u("e1", "r", 0.99, "Tara, explain"), ADDR)
    m.advance(5000)
    assert len(m.spoken) == 1 and m.llm.calls == ["e1"]


def test_second_ask_with_new_event_id_is_answered_after_first_finishes():
    m = sim(llm=FakeLLM(latency_ms=100, speak_ms=500))
    m.feed(1000, u("e1", "r", 0.99, "Tara, a"), ADDR)
    m.feed(3000, u("e2", "r", 0.99, "Tara, b"), ADDR)
    m.advance(6000)
    assert [s.trigger for s in m.spoken] == ["e1", "e2"]


# --- silent / observe ------------------------------------------------------
def test_observe_mode_never_speaks():
    from tara.config import Mode, SpeakingPolicy
    c = cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT)
    m = SimMeeting(c, {"r": Role.ORGANIZER}, TH)
    m.feed(1000, utt("r", 0.99, False, "e1", 0, "Tara?"), ADDR)
    m.advance(10000)
    assert m.spoken == [] and m.llm.calls == []


def test_takeover_during_generation_leaves_runtime_consistent():
    m = sim(llm=FakeLLM(latency_ms=2000))
    m.feed(1000, u("e1", "r", 0.99, "Tara?"), ADDR)
    assert m.rt.state is State.GENERATING
    m.feed(1500, u("e2", "r", 0.99, "I'll take it"), Intent(K.TAKEOVER))
    assert m.rt.state is State.LISTENING and m.rt.takeover_active
