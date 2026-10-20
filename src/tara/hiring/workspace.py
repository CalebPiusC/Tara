"""On-disk workspace: inbox (CVs the owner drops in), candidates, interviews, documents. JSON, atomic writes, 0700/0600."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class WorkspaceError(ValueError):
    pass


def check_id(value: str) -> str:
    if not _ID.match(value or ""):
        raise WorkspaceError("invalid id (use lowercase letters, digits and dashes)")
    return value


class Workspace:
    def __init__(self, base: str | Path):
        self.base = Path(base).expanduser().resolve()
        self.inbox = self.base / "inbox"
        self.candidates = self.base / "candidates"
        self.interviews = self.base / "interviews"

    def ensure(self) -> None:
        for d in (self.base, self.inbox, self.candidates, self.interviews):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        self.ensure()
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkspaceError("not found") from exc
        except (OSError, ValueError) as exc:
            raise WorkspaceError("stored record is unreadable") from exc
        if not isinstance(data, dict):
            raise WorkspaceError("stored record is malformed")
        return data

    def save_candidate(self, candidate_id: str, data: dict[str, Any]) -> None:
        self._write(self.candidates / f"{check_id(candidate_id)}.json", data)

    def load_candidate(self, candidate_id: str) -> dict[str, Any]:
        return self._read(self.candidates / f"{check_id(candidate_id)}.json")

    def list_candidates(self) -> list[str]:
        return sorted(p.stem for p in self.candidates.glob("*.json")) if self.candidates.exists() else []

    def save_interview(self, interview_id: str, data: dict[str, Any]) -> None:
        self._write(self.interviews / f"{check_id(interview_id)}.json", data)

    def load_interview(self, interview_id: str) -> dict[str, Any]:
        return self._read(self.interviews / f"{check_id(interview_id)}.json")

    def list_interviews(self) -> list[str]:
        return sorted(p.stem for p in self.interviews.glob("*.json")) if self.interviews.exists() else []
