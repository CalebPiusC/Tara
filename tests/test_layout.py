"""Package boundaries from AGENTS.md, checked on the import graph of src/tara."""
import ast
from pathlib import Path

import tara

SRC = Path(tara.__file__).parent


def _imports() -> dict[str, set[str]]:
    """module -> absolute names it imports (relative imports resolved)."""
    out: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
        is_pkg = parts[-1] == "__init__"
        mod = ".".join(parts[:-1] if is_pkg else parts)
        pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                base = pkg[: len(pkg) - node.level + 1] if node.level else []
                names.add(".".join(base + ([node.module] if node.module else [])))
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        out[mod] = names
    return out


def test_live_code_never_imports_legacy():
    offenders = {m: sorted(n for n in names if n.startswith("tara.legacy"))
                 for m, names in _imports().items()
                 if not m.startswith(("tara.legacy", "tara.testing"))}
    assert not {m: n for m, n in offenders.items() if n}


def test_only_the_gemini_adapter_imports_google_sdk_types():
    users = {m for m, names in _imports().items() if any(n == "google" or n.startswith("google.") for n in names)}
    assert users <= {"tara.voice.gemini_live"}, users
