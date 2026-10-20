"""A word list of names and jargon, so mishearings are corrected and Tara knows how things are spelled.

Terms come from a plain text file (one per line, `#` comments), the participants' names, and a voice tool. They are used in
three places: the live instructions ("these names and terms may come up"), a fuzzy correction applied to stored
transcripts and summaries (so "Test flow" or "Testflo" is saved as "Testflow"), and the end-of-meeting summary prompt.
The correction is conservative: only close matches, never short words, never an exact word that is a term already.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from pathlib import Path

MAX_TERMS = 300
_TERM_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&'/\-]{1,59}$")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


class Vocabulary:
    def __init__(self, path: str | Path | None = None, terms: Iterable[str] = ()):
        self.path = Path(path) if path else None
        self._terms: list[str] = []
        for t in terms:
            self._insert(t)
        if self.path is not None and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    self._insert(line)

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(self._terms)

    def _insert(self, term: str) -> bool:
        term = " ".join(term.split())
        if not _TERM_OK.match(term) or len(self._terms) >= MAX_TERMS:
            return False
        if term.lower() in {t.lower() for t in self._terms}:
            return False
        self._terms.append(term)
        return True

    def add(self, term: str) -> bool:
        """Add and persist. Returns False for duplicates or unusable terms (never raises on those)."""
        if not self._insert(term):
            return False
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(" ".join(term.split()) + "\n")
        return True

    def instruction(self) -> str:
        if not self._terms:
            return ""
        return ("Names and terms that may come up, with the correct spelling (if you hear something close to one of these, "
                "it is probably that): " + ", ".join(self._terms[:120]) + ".")

    def correct(self, text: str) -> str:
        """Replace close misspellings of known terms. Whole words only; keeps the original when unsure."""
        if not self._terms or not text:
            return text
        singles = {t.lower(): t for t in self._terms if " " not in t}
        phrases = [t for t in self._terms if " " in t]
        out = text
        for term in phrases:                                  # "test flow" -> "Testflow" style joins and phrase typos
            squashed = term.replace(" ", "")
            out = re.sub(rf"\b{re.escape(squashed)}\b", term, out, flags=re.I)
        for term in singles.values():                         # "test flow" for the single-word term "Testflow"
            parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", term).split()
            if len(parts) == 1 and len(term) >= 7:
                for cut in range(3, len(term) - 2):
                    out = re.sub(rf"\b{re.escape(term[:cut])}\s{re.escape(term[cut:])}\b", term, out, flags=re.I)

        def fix(m: re.Match[str]) -> str:
            w = m.group(0)
            lw = w.lower()
            if lw in singles or len(w) < 5:
                return singles.get(lw, w) if lw in singles else w
            best = difflib.get_close_matches(lw, list(singles), n=1, cutoff=0.84)
            return singles[best[0]] if best else w

        return _WORD.sub(fix, out)
