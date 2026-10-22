"""The look of the minutes email: a clean card with Tara's picture, the headline points, and the full document attached.

Everything that came from a meeting is HTML-escaped (a transcript is untrusted). Layout is tables with inline styles because mail
clients ignore most modern CSS. The logo is attached as an inline image and referenced by `cid:` (Resend supports this); when
Pillow or the picture is missing the email simply has no logo."""
from __future__ import annotations

import io
from html import escape
from pathlib import Path

LOGO_CID = "tara-logo"
ACCENT = "#1F4E79"
IMAGE = Path(__file__).resolve().parents[3] / "image" / "image.png"
SKIP = {"none recorded", "none", "not recorded"}


def load_logo(path: Path = IMAGE, size: int = 120) -> bytes | None:
    """A small PNG of Tara for the email header, or None."""
    try:
        from PIL import Image
        with Image.open(path) as opened:
            im = opened.convert("RGBA")
            im.thumbnail((size, size))
            out = io.BytesIO()
            im.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - the logo is decoration
        return None


def _clean(items: object, limit: int = 6) -> list[str]:
    seq = items if isinstance(items, list) else [items] if items else []
    out = [" ".join(str(x).split())[:300] for x in seq if str(x).strip().lower() not in SKIP]
    return out[:limit]


def build_minutes_email(title: str, facts: dict[str, object], doc_name: str, *, logo: bool, complete: bool = True) -> tuple[str, str]:
    """(html, plain text) for the minutes email. `facts` is the same dict the document is written from."""
    summary = _clean(facts.get("Earlier summaries"), 1)
    decisions = _clean(facts.get("Confirmed decisions"), 5) + _clean(facts.get("Recorded decisions"), 5)
    actions = _clean(facts.get("Confirmed action items"), 5) + _clean(facts.get("Recorded actions"), 5)
    people = _clean([str(facts.get("Attendees", ""))], 1)
    sections = [("Summary", summary), ("Decisions", decisions[:6]), ("Action items", actions[:6])]
    lede = "The minutes of your meeting are attached." if complete else "The minutes so far are attached."

    def block(heading: str, lines: list[str]) -> str:
        if not lines:
            return ""
        items = "".join(f'<li style="margin:0 0 6px 0;">{escape(x)}</li>' for x in lines)
        return (f'<h3 style="margin:22px 0 8px 0;font:600 13px Arial,sans-serif;letter-spacing:.06em;text-transform:uppercase;'
                f'color:{ACCENT};">{escape(heading)}</h3><ul style="margin:0;padding-left:18px;font:15px/1.5 Arial,sans-serif;color:#222;">{items}</ul>')

    head_logo = (f'<td width="64" valign="middle"><img src="cid:{LOGO_CID}" width="56" height="56" alt="Tara" '
                 f'style="display:block;border-radius:12px;"></td>') if logo else ""
    body = "".join(block(h, ls) for h, ls in sections if ls) or (
        '<p style="margin:18px 0 0 0;font:15px/1.5 Arial,sans-serif;color:#555;">Nothing was recorded as a decision or action yet. '
        'The attached document has everything that was kept.</p>')
    who = (f'<p style="margin:20px 0 0 0;font:13px Arial,sans-serif;color:#666;">In the meeting: {escape(people[0])}</p>' if people else "")
    html = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f3f5f8;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f8;padding:24px 12px;"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;'
        'border-radius:14px;overflow:hidden;border:1px solid #e3e8ef;">'
        f'<tr><td style="background:{ACCENT};padding:18px 22px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr>{head_logo}'
        '<td valign="middle" style="padding-left:12px;"><div style="font:700 20px Arial,sans-serif;color:#ffffff;">Tara</div>'
        '<div style="font:13px Arial,sans-serif;color:#cfdced;">Your AI meeting participant</div></td></tr></table></td></tr>'
        '<tr><td style="padding:26px 24px 8px 24px;">'
        f'<h1 style="margin:0 0 8px 0;font:700 22px/1.3 Arial,sans-serif;color:#111;">{escape(title)}</h1>'
        f'<p style="margin:0;font:15px/1.5 Arial,sans-serif;color:#444;">{lede} Open <b>{escape(doc_name)}</b> for the full notes.</p>'
        f'{body}{who}</td></tr>'
        '<tr><td style="padding:22px 24px 26px 24px;"><div style="border-top:1px solid #e3e8ef;padding-top:14px;font:12px/1.5 Arial,sans-serif;color:#8a94a3;">'
        'Sent by Tara. Hey Tara, I\'m here to help. This email contains only what was said and kept in the meeting; '
        'check decisions and owners before you rely on them.</div></td></tr></table></td></tr></table></body></html>')
    text_parts = [f"{title}", "", lede, f"Open {doc_name} for the full notes."]
    for h, ls in sections:
        if ls:
            text_parts += ["", h.upper()] + [f"- {x}" for x in ls]
    if people:
        text_parts += ["", f"In the meeting: {people[0]}"]
    text_parts += ["", "Sent by Tara, your AI meeting participant."]
    return html, "\n".join(text_parts)
