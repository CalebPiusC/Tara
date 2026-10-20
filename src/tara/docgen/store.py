"""Where generated documents are written. The model never chooses a path or filename."""
from __future__ import annotations

import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .model import DocStyle, Document, DocumentError
from .render_docx import render_docx
from .render_md import render_markdown
from .render_pdf import render_pdf

FORMATS = ("docx", "pdf", "md")
MAX_BYTES = 15_000_000
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, limit: int = 48) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:limit] or "document"


@dataclass(frozen=True)
class SavedDocument:
    path: str
    name: str
    format: str
    size: int
    title: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class DocumentStore:
    def __init__(self, base_dir: str | Path, style: DocStyle | None = None):
        self.base = Path(base_dir).expanduser().resolve() / "documents"
        self.style = style or DocStyle()

    def save(self, doc: Document, fmt: str = "docx") -> SavedDocument:
        fmt = fmt.lower().strip()
        if fmt not in FORMATS:
            raise DocumentError(f"unsupported format {fmt!r}; choose from {list(FORMATS)}")
        warnings: list[str] = []
        if fmt == "docx":
            data = render_docx(doc, self.style)
        elif fmt == "pdf":
            res = render_pdf(doc, self.style)
            data, warnings = res.data, res.warnings
        else:
            data = render_markdown(doc).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise DocumentError("generated document is too large")
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        name = f"{slugify(doc.title)}-{stamp}-{secrets.token_hex(3)}.{fmt}"
        target = self.base / name
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".tmp-", suffix=f".{fmt}")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return SavedDocument(str(target), name, fmt, len(data), doc.title, tuple(warnings))
