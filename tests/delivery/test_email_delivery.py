import asyncio
import json
import urllib.error

import pytest

from tara.delivery.email import DryRunSender, EmailError, EmailService, EmailSettings, ResendSender, mask, parse_recipients
from tests.work.test_work_integration import acall, authorize, make_work


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, body=b'{"id": "abc123"}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_recipients_are_validated_masked_and_deduplicated():
    assert parse_recipients("Me@Example.com, me@example.com; you@x.io") == ("me@example.com", "you@x.io")
    with pytest.raises(ValueError):
        parse_recipients("not-an-address")
    assert mask("feranmi@gmail.com") == "f***@gmail.com"
    assert EmailSettings(to=("a@b.co",), api_key="k").configured and not EmailSettings(to=("a@b.co",)).configured
    assert EmailSettings(to=("a@b.co",), dry_run=True).configured and not EmailSettings(api_key="k").configured


def test_resend_request_has_bearer_key_base64_attachment_and_only_allow_listed_recipients(tmp_path):
    seen = {}

    def opener(req, timeout):
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data)
        return FakeResponse()
    f = tmp_path / "minutes.docx"
    f.write_bytes(b"PK-fake-docx")
    svc = EmailService(EmailSettings("re_secret", "Tara <onboarding@resend.dev>", ("owner@x.io",)), tmp_path / "audit.jsonl",
                       sender=ResendSender("re_secret", "Tara <onboarding@resend.dev>", opener=opener))
    res = run(svc.send_file(str(f), "Minutes: Test", "attached"))
    assert res["ok"] and res["outcome"] == "sent" and res["to"] == ["o***@x.io"]
    assert seen["body"]["to"] == ["owner@x.io"] and seen["body"]["attachments"][0]["filename"] == "minutes.docx"
    assert seen["headers"]["Authorization"] == "Bearer re_secret"
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "sent" in audit and "re_secret" not in audit and "owner@x.io" not in audit      # no key, no full address in the log


def test_failures_are_reported_without_secrets_and_never_raise(tmp_path):
    def bad(req, timeout):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    f = tmp_path / "m.docx"
    f.write_bytes(b"x")
    svc = EmailService(EmailSettings("re_secret", "a <b@c.de>", ("owner@x.io",)), tmp_path / "audit.jsonl",
                       sender=ResendSender("re_secret", "a <b@c.de>", opener=bad))
    res = run(svc.send_file(str(f), "s", "t"))
    assert not res["ok"] and "key was rejected" in res["error"] and "re_secret" not in json.dumps(res)
    missing = run(svc.send_file(str(tmp_path / "nope.docx"), "s", "t"))
    assert not missing["ok"] and "audit" not in missing
    assert "failed" in (tmp_path / "audit.jsonl").read_text()
    off = EmailService(EmailSettings(), tmp_path / "a2.jsonl")
    assert not run(off.send_file(str(f), "s", "t"))["ok"]
    with pytest.raises(EmailError):
        run(ResendSender("k", "f", opener=lambda r, timeout: (_ for _ in ()).throw(TimeoutError())).send(("a@b.co",), "s", "t", []))


def test_dry_run_writes_the_message_to_disk_instead_of_sending(tmp_path):
    f = tmp_path / "m.docx"
    f.write_bytes(b"x")
    svc = EmailService(EmailSettings(to=("owner@x.io",), dry_run=True), tmp_path / "work" / "email-audit.jsonl")
    res = run(svc.send_file(str(f), "Minutes", "body"))
    assert res["ok"] and res["outcome"] == "dry_run" and isinstance(svc.sender, DryRunSender)
    assert list((tmp_path / "work" / "email-dry-run").glob("*.json"))


def _with_email(tmp_path, **kw):
    work, ex = make_work(tmp_path, memory=None)
    return work, ex


def test_the_email_tool_needs_a_clear_confirmation_after_a_person_spoke(tmp_path):
    async def go():
        from tara.work import WorkServices  # noqa: F401
        work, ex = make_work(tmp_path)
        assert work.email is None and "email_summary" not in [d.name for d in work.tool_defs]      # off until configured
    run(go())

    async def configured():
        import dataclasses

        from tara.control.state import ControlState
        from tara.core.config import ToolSpec
        from tara.core.settings import WorkSettings
        from tara.hiring.github import GitHubReader  # noqa: F401
        from tara.tools.executor import ToolExecutor
        from tara.voice.memory import InMemoryMeetingMemory, WorkingMemory
        from tara.work import WorkServices
        from tests.helpers import ROLES, cfg
        from tests.work import test_work_integration as twi
        working = WorkingMemory()
        mem = InMemoryMeetingMemory()
        work = WorkServices(WorkSettings(enabled=True, dir=str(tmp_path / "w"), email_to="owner@x.io", email_dry_run=True, job_timeout_s=20),
                            session_id="s1", scope=twi.SC, cfg=cfg(), roles=ROLES, names=twi.NAMES, meeting_memory=mem, working=working,
                            control=ControlState(), min_conf=0.8, llm=twi.llm_for({}), fetcher=None, github=twi.GitHubReader(twi.gh_http()),
                            candidate_speakers=frozenset({"a"}))
        ex = ToolExecutor({}, ())
        for d in work.tool_defs:
            ex.register_and_grant(d, ToolSpec(d.name, d.permission or "work", False))
        assert dataclasses and "email_summary" in [d.name for d in work.tool_defs]
        working.add_turn("You", "we decided to ship on friday")
        from tara.memory.models import MemoryScope  # noqa: F401
        from tara.voice.memory import MemoryRecord
        mem.add(twi.SC, MemoryRecord("decision", "ship on friday", ("e1",)))
        mem.promote_latest(twi.SC, confirmed_by="r")
        denied = await acall(ex, "email_summary")
        assert not denied.ok                                                         # nobody authorised yet
        authorize(work)
        first = await acall(ex, "email_summary")
        assert first.ok and first.output["status"] == "confirm_needed" and first.output["to"] == ["o***@x.io"]
        instant = await acall(ex, "email_summary", confirm=True)
        assert not instant.ok and "confirm" in json.dumps(instant.error).lower()      # the model cannot confirm for the person
        working.add_turn("You", "yes please send it")
        authorize(work)
        sent = await acall(ex, "email_summary", confirm=True)
        assert sent.ok and sent.output["status"] == "saved_dry_run" and sent.output["to"] == ["o***@x.io"]
        again = await acall(ex, "email_summary", confirm=True)
        assert not again.ok                                                          # one confirmation, one send
        assert list((tmp_path / "w" / "email-dry-run").glob("*.docx"))
    run(configured())


def test_email_design_escapes_meeting_text_includes_the_logo_and_sends_html_with_a_cid_attachment(tmp_path):
    from tara.delivery.template import LOGO_CID, build_minutes_email, load_logo
    facts = {"Earlier summaries": ["We agreed to ship <b>Friday</b>."], "Confirmed decisions": ["None recorded"],
             "Recorded decisions": ["Ship on Friday"], "Recorded actions": ["Send the budget (owner: Sam; deadline: Friday)"],
             "Attendees": "Sam, Alex"}
    html, text = build_minutes_email("Team <sync>", facts, "minutes.docx", logo=True)
    assert "<b>Friday</b>" not in html and "&lt;b&gt;Friday&lt;/b&gt;" in html and "Team &lt;sync&gt;" in html   # untrusted text is escaped
    assert f"cid:{LOGO_CID}" in html and "Ship on Friday" in html and "owner: Sam" in html and "None recorded" not in html
    assert "SUMMARY" in text and "- Ship on Friday" in text
    assert "cid:" not in build_minutes_email("T", {}, "m.docx", logo=False)[0]
    logo = load_logo()
    assert logo is None or logo[:4] == b"\x89PNG"
    seen = {}

    def opener(req, timeout):
        seen["body"] = json.loads(req.data)
        return FakeResponse()
    f = tmp_path / "m.docx"
    f.write_bytes(b"x")
    svc = EmailService(EmailSettings("k", "Tara <hello@appmd.dev>", ("o@x.io",)), tmp_path / "a.jsonl",
                       sender=ResendSender("k", "Tara <hello@appmd.dev>", opener=opener))
    run(svc.send_file(str(f), "s", text, html, [(LOGO_CID, "tara.png", b"\x89PNGxx")]))
    atts = seen["body"]["attachments"]
    assert seen["body"]["html"] == html and seen["body"]["from"] == "Tara <hello@appmd.dev>"
    assert [a for a in atts if a.get("content_id") == LOGO_CID] and [a for a in atts if a["filename"] == "m.docx"]
    dry = EmailService(EmailSettings(to=("o@x.io",), dry_run=True), tmp_path / "w" / "a.jsonl")
    run(dry.send_file(str(f), "s", text, html, [(LOGO_CID, "tara.png", b"\x89PNGxx")]))
    page = next((tmp_path / "w" / "email-dry-run").glob("*.html")).read_text()
    assert "cid:" not in page and ".png" in page                                    # the dry-run page opens in a browser with its logo
