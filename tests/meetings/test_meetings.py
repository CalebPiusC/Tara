import asyncio
import json

import pytest

from tara.control.state import ControlState
from tara.core.errors import PermissionDenied
from tara.core.settings import WorkSettings
from tara.meetings.agenda import Agenda, MeetingClock
from tara.meetings.brief import opening_brief
from tara.meetings.items import OpenItems, add_action_tool, complete_item_tool, list_open_items_tool, park_item_tool
from tara.meetings.types import RULES, MeetingType, parse_type
from tara.meetings.vocab import Vocabulary
from tara.memory import InMemoryMemoryStore, MemoryScope, MemoryService, MemoryWriteGate
from tara.secretary.recorder import MeetingRecorder
from tara.tools.executor import ToolExecutor
from tara.voice.memory import InMemoryMeetingMemory, Scope, WorkingMemory
from tara.work import WorkServices
from tests.helpers import ROLES, cfg
from tests.secretary.test_recorder import FakeText, say
from tests.voice.test_voice_engine import NAMES

run = asyncio.run
SC = Scope("t", "m")


def scope(section="join"):
    return MemoryScope("t1", "u1", meeting_id=section)


# ---- 8. vocabulary ------------------------------------------------------------------------------------------------
def test_vocabulary_corrects_close_misspellings_and_leaves_real_words(tmp_path):
    v = Vocabulary(tmp_path / "v.txt", ["Testflow", "Obafemi Awolowo University", "Adeyemi"])
    assert v.correct("we talked about Test flow and Testflo") == "we talked about Testflow and Testflow"
    assert v.correct("Obafemiawolowouniversity was founded") == "Obafemi Awolowo University was founded"
    assert v.correct("hello Adeyem and Adeyemi") == "hello Adeyemi and Adeyemi"
    assert v.correct("the tester said it works") == "the tester said it works"          # a real word stays


def test_vocabulary_persists_dedupes_and_rejects_junk(tmp_path):
    path = tmp_path / "v.txt"
    v = Vocabulary(path)
    assert v.add("Kubernetes") and not v.add("kubernetes") and not v.add("<script>") and not v.add("x")
    assert "Kubernetes" in Vocabulary(path).terms and "Kubernetes" in v.instruction()
    path.write_text("# names\nSam Ade  # a person\n\nTestflow\n")
    assert Vocabulary(path).terms == ("Sam Ade", "Testflow")


def test_recorder_stores_corrected_transcripts():
    async def go():
        store = InMemoryMemoryStore()
        svc, wm = MemoryService(store), WorkingMemory()
        rec = MeetingRecorder(svc, scope(), session_id="s", working=wm, vocab=Vocabulary(None, ["Testflow"]))
        say(wm, ("You", "Testflo is the product."))
        await rec.flush()
        assert "Testflow is the product." in (await store.list_recent(scope(), 5))[0].text
    run(go())


# ---- 9. meeting types -----------------------------------------------------------------------------------------------
def test_meeting_types_are_forgiving_and_each_has_its_own_rules():
    assert parse_type("1:1") is MeetingType.ONE_ON_ONE and parse_type("Stand up") is MeetingType.STANDUP
    assert parse_type("client") is MeetingType.CLIENT_CALL and parse_type("") is MeetingType.GENERAL
    with pytest.raises(ValueError):
        parse_type("board game night")
    assert set(RULES) == set(MeetingType) and all(r.behaviour and r.minutes and r.digest_hint for r in RULES.values())
    assert "blocker" in RULES[MeetingType.STANDUP].behaviour.lower() and "commit" in RULES[MeetingType.CLIENT_CALL].behaviour.lower()


# ---- 4. open items --------------------------------------------------------------------------------------------------
async def _items():
    svc = MemoryService(InMemoryMemoryStore())
    gate = MemoryWriteGate()
    return svc, gate, OpenItems(svc, scope(), "run-1")


def test_actions_need_a_request_carry_over_and_can_be_completed():
    async def go():
        svc, gate, t = await _items()
        await svc.start_session(scope(), "run-1")
        add = add_action_tool(t, gate).handler
        with pytest.raises(PermissionDenied):
            await add("Send the budget")
        gate.observe("Add an action item for Sam to send the budget by Friday.")
        out = await add("Send the budget", "Sam", "Friday")
        assert out["status"] == "added"
        park = park_item_tool(t, gate).handler
        with pytest.raises(PermissionDenied):
            await park("pricing for later")
        gate.observe("Park that, let's talk about pricing later.")
        assert (await park("Pricing model"))["status"] == "parked"
        await svc.end_session(scope(), "run-1")
        # a later meeting of the same section sees both as still open, with where they came from
        await svc.start_session(scope(), "run-2")
        t2 = OpenItems(svc, scope(), "run-2")
        listing = await list_open_items_tool(t2).handler()
        assert listing["count"] == 2
        action = next(r for r in listing["items"] if r["type"] == "action")
        assert action["owner"] == "Sam" and action["deadline"] == "Friday" and action["from_meeting"] == "1"
        assert (await list_open_items_tool(t2).handler("parked"))["count"] == 1
        done = complete_item_tool(t2, gate).handler
        gate2 = MemoryWriteGate()
        with pytest.raises(PermissionDenied):
            await complete_item_tool(t2, gate2).handler("budget")
        gate.observe("the budget one is done now")
        res = await done("budget")
        assert res["status"] == "done" and res["still_open"] == 1
        assert (await list_open_items_tool(t2).handler())["count"] == 1
        assert (await done("nothing like it"))["status"] == "not_found"
    run(go())


def test_completing_an_ambiguous_item_asks_which():
    async def go():
        svc, gate, t = await _items()
        gate.observe("add an action item")
        await t.add("action", "Send the budget to finance")
        await t.add("action", "Send the budget to the client")
        gate.observe("mark it done")
        out = await complete_item_tool(t, gate).handler("send budget")
        assert out["status"] == "ambiguous" and len(out["options"]) == 2
    run(go())


def test_digest_actions_carry_owner_and_deadline_and_stay_open():
    async def go():
        store = InMemoryMemoryStore()
        svc, wm = MemoryService(store), WorkingMemory()
        reply = json.dumps({"summary": "S.", "facts": [{"text": "Sam sends the budget.", "kind": "action", "owner": "Sam",
                                                        "deadline": "Friday", "quote": "Sam will send the budget"}]})
        rec = MeetingRecorder(svc, scope(), session_id="s1", working=wm, llm=FakeText(reply))
        say(wm, ("You", "Right, Sam will send the budget by Friday, agreed."))
        await rec.finish()
        rows = await OpenItems(svc, scope(), "s2").describe(await OpenItems(svc, scope(), "s2").open_items())
        assert rows and rows[0]["owner"] == "Sam" and rows[0]["deadline"] == "Friday" and "unconfirmed" in rows[0]["note"]
    run(go())


# ---- 5. opening brief -----------------------------------------------------------------------------------------------
def test_opening_brief_says_what_was_left_open_and_is_silent_for_a_new_section():
    async def go():
        svc, gate, t = await _items()
        first = await svc.start_session(scope(), "run-1")
        assert await opening_brief(svc, scope(), first, t) == ""                       # nothing before it
        gate.observe("add an action item")
        await t.add("action", "Send the budget", "Sam", "Friday")
        await svc.remember(scope(), "We agreed to launch the beta.", source="s", kind="summary", session_id="run-1")
        await svc.end_session(scope(), "run-1")
        second = await svc.start_session(scope(), "run-2")
        text = await opening_brief(svc, scope(), second, OpenItems(svc, scope(), "run-2"))
        assert "meeting 1" in text and "launch the beta" in text and "Send the budget" in text and "Sam" in text
        assert "Do not mention anything not listed" in text
        empty = MemoryService(InMemoryMemoryStore())
        a = await empty.start_session(scope("x"), "r1")
        await empty.start_session(scope("x"), "r2")
        assert await opening_brief(empty, scope("x"), await empty.start_session(scope("x"), "r3"), OpenItems(empty, scope("x"), "r3")) == ""
        assert a.number == 1
    run(go())


# ---- 6. agenda and time-keeping -------------------------------------------------------------------------------------
def test_agenda_tracks_items_and_gives_each_time_nudge_once():
    now = [0.0]
    ag = Agenda(clock=lambda: now[0])
    ag.set(["Budget"], 30)
    clock = MeetingClock(ag)
    assert clock.tick() == []
    now[0] = 26 * 60
    first = clock.tick()
    assert len(first) == 1 and "five minutes" in first[0] and "Budget" in first[0]
    assert clock.tick() == []                                                      # the same nudge is not repeated
    now[0] = 29.5 * 60
    assert "One minute" in clock.tick()[0]
    now[0] = 31 * 60
    assert "planned end" in clock.tick()[0]
    assert clock.tick() == []                                                      # nothing repeats


def test_agenda_items_can_be_marked_by_number_or_words():
    ag = Agenda(clock=lambda: 0.0)
    ag.set(["Budget", "Hiring", "AOB"], 30)
    assert ag.status()["current"] == "Budget" and ag.items[0].minutes == 10
    assert ag.mark("budget").state == "done" and ag.status()["current"] == "Hiring" and ag.status()["covered"] == 1
    assert ag.mark("nothing at all") is None and ag.mark("3", "skipped").state == "skipped"
    assert ag.status()["remaining"] == 1


def test_an_item_running_long_gets_one_nudge():
    now = [0.0]
    ag = Agenda(clock=lambda: now[0])
    ag.set(["Budget", "Hiring"], 40)                        # 20 minutes planned for each item
    clock = MeetingClock(ag)
    now[0] = 24 * 60                                        # late, but not yet 1.5 times the plan
    assert clock.tick() == []
    now[0] = 31 * 60                                        # 31 minutes on a 20 minute item, 9 minutes of the meeting left
    texts = clock.tick()
    assert len(texts) == 1 and "spent about 31 minutes on Budget" in texts[0]
    assert clock.tick() == []
    with pytest.raises(ValueError):
        ag.set([" ", ""])


def test_clock_speaks_through_the_given_callback():
    async def go():
        now = [0.0]
        ag = Agenda(clock=lambda: now[0])
        ag.set(["A"], 10)
        spoken: list[str] = []

        async def speak(t):
            spoken.append(t)

        clock = MeetingClock(ag, speak, poll_s=0.01)
        now[0] = 6 * 60 + 1                                  # 4 minutes left
        task = asyncio.create_task(clock.run())
        await asyncio.sleep(0.1)
        task.cancel()
        assert spoken and "five minutes" in spoken[0]
    run(go())


# ---- tools through the real executor ----------------------------------------------------------------------------------
def _work(tmp_path, svc=None, **kw):
    svc = svc or MemoryService(InMemoryMemoryStore())
    gate = MemoryWriteGate()
    spoken: list[str] = []
    kinds: list[object] = []

    async def speak(t):
        spoken.append(t)

    work = WorkServices(
        WorkSettings(enabled=True, dir=str(tmp_path / "work"), **kw), session_id="run-1", scope=SC, cfg=cfg(), roles=ROLES, names=NAMES,
        meeting_memory=InMemoryMeetingMemory(), working=WorkingMemory(), control=ControlState(), min_conf=0.8,
        memory_service=svc, memory_scope=scope(), memory_gate=gate, speak=speak, on_kind=kinds.append)
    ex = ToolExecutor({}, ())
    from tara.core.config import ToolSpec
    for d in work.tool_defs:
        ex.register_and_grant(d, ToolSpec(d.name, d.permission or "work", False))
    return work, ex, gate, spoken, kinds


async def acall(ex, name, **args):
    from tara.core.contracts import ToolCall
    return await ex.execute(ToolCall(name, args), f"c-{name}")


def test_agenda_type_vocab_and_recap_tools_work_end_to_end(tmp_path):
    async def go():
        work, ex, gate, spoken, kinds = _work(tmp_path, meeting_type="standup", vocab_terms="Testflow, Zenith")
        assert "stand-up" in work.instructions() and "Testflow" in work.instructions() and "recap_meeting" in work.instructions()
        r = await acall(ex, "set_agenda", items="Budget; Hiring; Wrap up", minutes=30)
        assert r.ok and r.output["current"] == "Budget"
        assert (await acall(ex, "agenda_mark", item="budget")).output["current"] == "Hiring"
        assert (await acall(ex, "agenda_status")).output["covered"] == 1
        set_type = await acall(ex, "set_meeting_type", meeting_type="1:1")
        assert set_type.ok and set_type.output["meeting_type"] == "one_on_one" and kinds == ["casual"]
        assert not (await acall(ex, "set_meeting_type", meeting_type="chess club")).ok
        denied = await acall(ex, "add_vocabulary", term="Kubernetes")
        assert not denied.ok
        gate.observe("Remember that Kubernetes is spelled like this.")
        assert (await acall(ex, "add_vocabulary", term="Kubernetes")).output["status"] == "added"
        work.working.add_turn("You", "We discussed Testflo pricing.")
        recap = (await acall(ex, "recap_meeting")).output
        assert "Testflow pricing" in recap["transcript"] and recap["covers"] == "the most recent turns"
        stored = [i.text for i in await work.items.service.store.list_recent(scope(), 10)]
        assert any(t.startswith("Agenda:") for t in stored)
        work._clock_task and work._clock_task.cancel()
    run(go())


def test_open_item_tools_are_registered_only_with_memory(tmp_path):
    async def go():
        work, ex, *_ = _work(tmp_path)
        names = {d["name"] for d in ex.catalog()}
        assert {"add_action", "park_item", "list_open_items", "complete_item"} <= names
        no_mem = WorkServices(WorkSettings(enabled=True, dir=str(tmp_path / "w2")), session_id="s", scope=SC, cfg=cfg(), roles=ROLES,
                              names=NAMES, meeting_memory=InMemoryMeetingMemory(), working=WorkingMemory(),
                              control=ControlState(), min_conf=0.8)
        assert "add_action" not in {d.name for d in no_mem.tool_defs}
    run(go())


def test_opening_prompt_respects_the_setting(tmp_path):
    async def go():
        svc = MemoryService(InMemoryMemoryStore())
        await svc.start_session(scope(), "run-0")
        await svc.remember(scope(), "Last time we planned the launch.", source="s", kind="summary", session_id="run-0")
        meeting = await svc.start_session(scope(), "run-1")
        work, *_ = _work(tmp_path, svc=svc)
        assert "planned the launch" in await work.opening_prompt(meeting)
        quiet, *_ = _work(tmp_path, svc=svc, opening_brief=False)
        assert await quiet.opening_prompt(meeting) == ""
    run(go())
