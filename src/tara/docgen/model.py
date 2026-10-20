from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

MAX_BLOCKS = 600
MAX_TEXT = 20_000
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 12
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

BlockKind = Literal["heading", "paragraph", "bullets", "numbered", "table", "code", "rule"]


class DocumentError(ValueError):
    """The document content is not renderable (too large, malformed)."""


def clean(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 1                                   # heading level 1-3
    items: tuple[str, ...] = ()                      # bullets / numbered
    headers: tuple[str, ...] = ()                    # table
    rows: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.kind == "heading" and not 1 <= self.level <= 3:
            raise DocumentError("heading level must be 1 to 3")
        if len(self.text) > MAX_TEXT or any(len(i) > MAX_TEXT for i in self.items):
            raise DocumentError("a block is too long")
        if self.kind == "table":
            if not self.headers or len(self.headers) > MAX_TABLE_COLS or len(self.rows) > MAX_TABLE_ROWS:
                raise DocumentError("table has no headers or exceeds size limits")
            if any(len(r) != len(self.headers) for r in self.rows):
                raise DocumentError("table rows must match the header width")


@dataclass(frozen=True)
class DocStyle:
    """Look of the document. Fonts are names for Word (installed on the reader's machine) and font FILES for PDF."""

    font_body: str = "Calibri"
    font_heading: str = "Calibri"
    font_mono: str = "Courier New"
    size_body: float = 11.0
    size_h1: float = 20.0
    size_h2: float = 15.0
    size_h3: float = 12.5
    accent_rgb: tuple[int, int, int] = (31, 78, 121)
    margin_cm: float = 2.2
    page: Literal["A4", "Letter"] = "A4"
    pdf_font_regular: str = ""                       # path to a TTF for Unicode PDF text ("" = auto-detect, else Helvetica)
    pdf_font_bold: str = ""
    pdf_font_italic: str = ""

    def __post_init__(self) -> None:
        if not (6 <= self.size_body <= 20):
            raise DocumentError("body font size must be 6 to 20 pt")
        if not all(0 <= c <= 255 for c in self.accent_rgb):
            raise DocumentError("accent colour must be RGB 0-255")
        if not (0.5 <= self.margin_cm <= 6):
            raise DocumentError("margin must be 0.5 to 6 cm")


@dataclass(frozen=True)
class Document:
    title: str
    blocks: tuple[Block, ...]
    subtitle: str = ""
    author: str = "Tara"
    created: str = ""                                 # ISO timestamp supplied by the caller
    footer: str = ""
    confidential: bool = False
    meta: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DocumentError("a document needs a title")
        if len(self.blocks) > MAX_BLOCKS:
            raise DocumentError("document has too many blocks")


_H = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_BUL = re.compile(r"^\s*[-*•]\s+(.+)$")
_NUM = re.compile(r"^\s*\d{1,3}[.)]\s+(.+)$")
_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def _cells(line: str) -> tuple[str, ...]:
    m = _ROW.match(line)
    inner = m.group(1) if m else line
    return tuple(c.strip() for c in inner.split("|"))


def parse_markdown(md: str, *, title: str = "", **doc_kwargs: object) -> Document:
    """Parse a restricted Markdown subset: headings (#-###), paragraphs, bullets, numbered lists, pipe tables,
    fenced code, horizontal rules. Inline **bold** and *italic* are kept in the text for the renderers.
    Raw HTML is not interpreted: it stays as literal text."""
    lines = clean(md).split("\n")
    blocks: list[Block] = []
    para: list[str] = []
    bullets: list[str] = []
    numbered: list[str] = []
    found_title = title.strip()
    i = 0

    def flush() -> None:
        nonlocal para, bullets, numbered
        if para:
            blocks.append(Block("paragraph", " ".join(s.strip() for s in para).strip()))
        if bullets:
            blocks.append(Block("bullets", items=tuple(bullets)))
        if numbered:
            blocks.append(Block("numbered", items=tuple(numbered)))
        para, bullets, numbered = [], [], []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        h = _H.match(line)
        if h:
            flush()
            level, text = len(h.group(1)), h.group(2).strip()
            if level == 1 and not found_title:
                found_title = text
            else:
                blocks.append(Block("heading", text, level=min(3, max(1, level if found_title else level))))
            i += 1
            continue
        if _RULE.match(line):
            flush()
            blocks.append(Block("rule"))
            i += 1
            continue
        if _ROW.match(line) and i + 1 < len(lines) and _SEP.match(lines[i + 1]):
            flush()
            headers = _cells(line)
            i += 2
            rows: list[tuple[str, ...]] = []
            while i < len(lines) and _ROW.match(lines[i]):
                row = _cells(lines[i])
                row = (row + ("",) * len(headers))[: len(headers)]
                rows.append(row)
                i += 1
            blocks.append(Block("table", headers=headers, rows=tuple(rows)))
            continue
        b, n = _BUL.match(line), _NUM.match(line)
        if b:
            if para or numbered:
                flush()
            bullets.append(b.group(1).strip())
        elif n:
            if para or bullets:
                flush()
            numbered.append(n.group(1).strip())
        else:
            if bullets or numbered:
                flush()
            para.append(line)
        i += 1
    flush()
    if not found_title:
        found_title = "Untitled document"
    return Document(title=found_title, blocks=tuple(blocks), **doc_kwargs)  # type: ignore[arg-type]


_INLINE = re.compile(r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*|__[^_\n]+__)")


def inline_runs(text: str) -> list[tuple[str, bool, bool]]:
    """Split inline markup into (text, bold, italic) runs. Unmatched markers stay literal."""
    runs: list[tuple[str, bool, bool]] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False, False))
        tok = m.group(0)
        if tok.startswith("**"):
            runs.append((tok[2:-2], True, False))
        elif tok.startswith("__"):
            runs.append((tok[2:-2], False, True))
        else:
            runs.append((tok[1:-1], False, True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False, False))
    return runs or [("", False, False)]
