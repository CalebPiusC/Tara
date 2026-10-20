from __future__ import annotations

from .model import Document


def render_markdown(doc: Document) -> str:
    out = [f"# {doc.title}", ""]
    if doc.subtitle:
        out += [f"_{doc.subtitle}_", ""]
    if doc.created:
        out += [f"Created: {doc.created}", ""]
    if doc.confidential:
        out += ["**CONFIDENTIAL**", ""]
    for b in doc.blocks:
        if b.kind == "heading":
            out += ["#" * b.level + " " + b.text, ""]
        elif b.kind == "paragraph":
            out += [b.text, ""]
        elif b.kind == "bullets":
            out += [f"- {i}" for i in b.items] + [""]
        elif b.kind == "numbered":
            out += [f"{n}. {i}" for n, i in enumerate(b.items, 1)] + [""]
        elif b.kind == "code":
            out += ["```", b.text, "```", ""]
        elif b.kind == "rule":
            out += ["---", ""]
        elif b.kind == "table":
            out += ["| " + " | ".join(b.headers) + " |", "|" + "---|" * len(b.headers)]
            out += ["| " + " | ".join(r) + " |" for r in b.rows] + [""]
    return "\n".join(out).rstrip() + "\n"
