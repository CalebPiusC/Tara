"""Email the meeting minutes to the owner. Outward-facing, so the rules are strict and enforced here, not by the model:

- recipients come only from the owner-configured allow-list (`TARA_EMAIL_TO`); nothing said in a meeting can add one;
- the secret (`TARA_RESEND_API_KEY`) lives only in the environment and is never logged;
- every attempt is written to an audit log (time, masked recipients, file name, outcome, never the content);
- `dry_run` writes the message to a local folder instead of sending anything.

The provider is Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email): POST /emails with a Bearer key,
`attachments` carrying base64 `content` and a `filename`. Without a verified domain Resend only lets the sender
`onboarding@resend.dev` deliver to the account owner's own address, which is exactly this use.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

API_URL = "https://api.resend.com/emails"
ADDRESS = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


class EmailError(Exception):
    """A send failed. The message is safe to say aloud and contains no secrets."""


@dataclass(frozen=True)
class EmailSettings:
    api_key: str = field(default="", repr=False)
    sender: str = "Tara <onboarding@resend.dev>"
    to: tuple[str, ...] = ()                  # the allow-list: the only addresses mail can go to
    auto: bool = False                        # send the minutes when the meeting ends
    dry_run: bool = False                     # write to disk instead of sending

    @property
    def configured(self) -> bool:
        return bool(self.to) and (bool(self.api_key) or self.dry_run)


def parse_recipients(raw: str) -> tuple[str, ...]:
    out = [a.strip() for a in re.split(r"[,;\s]+", raw or "") if a.strip()]
    bad = [a for a in out if not ADDRESS.match(a)]
    if bad:
        raise ValueError("TARA_EMAIL_TO has an address that does not look like an email address")
    return tuple(dict.fromkeys(a.lower() for a in out))


def mask(address: str) -> str:
    """a***@gmail.com: enough to confirm aloud, not enough to leak the whole address into a transcript."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if local else address


class EmailSender(Protocol):
    async def send(self, to: tuple[str, ...], subject: str, text: str, attachments: list[tuple[str, bytes]], html: str = "",
                   inline: list[tuple[str, str, bytes]] | None = None) -> str: ...


class ResendSender:
    def __init__(self, api_key: str, sender: str, timeout_s: float = 20.0,
                 opener: Callable[..., Any] | None = None):
        self._key, self._from, self._timeout = api_key, sender, timeout_s
        self._open = opener or urllib.request.urlopen

    async def send(self, to: tuple[str, ...], subject: str, text: str, attachments: list[tuple[str, bytes]], html: str = "",
                   inline: list[tuple[str, str, bytes]] | None = None) -> str:
        files = [{"filename": n, "content": base64.b64encode(b).decode("ascii")} for n, b in attachments]
        # inline images are referenced from the html as cid:<content_id> (Resend: attachments with content_id)
        files += [{"filename": n, "content": base64.b64encode(b).decode("ascii"), "content_id": cid} for cid, n, b in (inline or [])]
        body: dict[str, Any] = {"from": self._from, "to": list(to), "subject": subject, "text": text, "attachments": files}
        if html:
            body["html"] = html
        return await asyncio.to_thread(self._post, body)

    def _post(self, body: dict[str, Any]) -> str:
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "User-Agent": "tara-meeting-bot/1"})
        try:
            with self._open(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
                return str(data.get("id", ""))
        except urllib.error.HTTPError as exc:
            hint = {401: "the email key was rejected", 403: "the sender address is not allowed for this account",
                    422: "the email service refused the message", 429: "the email service is rate limiting"}.get(exc.code, f"error {exc.code}")
            raise EmailError(f"the email service said: {hint}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise EmailError("could not reach the email service") from None


class DryRunSender:
    """Writes the would-be message to a folder (no attachment bytes are printed anywhere)."""

    def __init__(self, folder: Path):
        self.folder = folder

    async def send(self, to: tuple[str, ...], subject: str, text: str, attachments: list[tuple[str, bytes]], html: str = "",
                   inline: list[tuple[str, str, bytes]] | None = None) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (self.folder / f"{stamp}.json").write_text(json.dumps({"to": list(to), "subject": subject, "text": text,
                                                              "attachments": [n for n, _ in attachments]}, indent=2), encoding="utf-8")
        page = html
        for cid, _name, data in inline or []:
            (self.folder / f"{stamp}-{cid}.png").write_bytes(data)
            page = page.replace(f"cid:{cid}", f"{stamp}-{cid}.png")
        if page:
            (self.folder / f"{stamp}.html").write_text(page, encoding="utf-8")        # open it in a browser to see the design
        for name, data in attachments:
            (self.folder / f"{stamp}-{Path(name).name}").write_bytes(data)
        return f"dry-run-{stamp}"


class EmailService:
    def __init__(self, cfg: EmailSettings, audit_path: Path, sender: EmailSender | None = None):
        self.cfg, self.audit_path = cfg, audit_path
        self.sender: EmailSender = sender or (
            ResendSender(cfg.api_key, cfg.sender) if cfg.api_key and not cfg.dry_run else DryRunSender(audit_path.parent / "email-dry-run"))

    @property
    def configured(self) -> bool:
        return self.cfg.configured

    def recipients_masked(self) -> list[str]:
        return [mask(a) for a in self.cfg.to]

    async def send_file(self, path: str, subject: str, text: str, html: str = "",
                        inline: list[tuple[str, str, bytes]] | None = None) -> dict[str, object]:
        """Mail one file to the allow-list. Never raises for delivery problems: returns {"ok": False, "error": ...}."""
        name = Path(path).name
        outcome, detail = "failed", ""
        try:
            if not self.configured:
                raise EmailError("email is not set up")
            data = Path(path).read_bytes()
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise EmailError("the document is too large to email")
            ref = await self.sender.send(self.cfg.to, subject[:200], text[:4000], [(name, data)], html[:200_000], inline)
            outcome = "dry_run" if isinstance(self.sender, DryRunSender) else "sent"
            return {"ok": True, "outcome": outcome, "to": self.recipients_masked(), "file": name, "id": ref}
        except EmailError as exc:
            detail = str(exc)
        except OSError:
            detail = "the document could not be read"
        finally:
            self._audit(outcome, name, detail)
        return {"ok": False, "error": detail, "to": self.recipients_masked(), "file": name}

    def _audit(self, outcome: str, name: str, detail: str) -> None:
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "to": self.recipients_masked(), "file": name,
                                     "outcome": outcome, "detail": detail}) + "\n")
        except OSError:
            pass
