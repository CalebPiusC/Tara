"""The tool catalogue must match the ToolDefs that actually exist in the source, both ways."""
import ast
from pathlib import Path

import tara
from tara.tools.catalog import CATALOG, all_tool_names, group_of, render

SRC = Path(tara.__file__).parent


def _tooldef_names() -> dict[str, set[str]]:
    """tool name -> modules that build a ToolDef with that literal name (legacy excluded)."""
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC.parent).with_suffix("")
        if rel.parts[1] == "legacy":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "ToolDef":
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.setdefault(first.value, set()).add(".".join(rel.parts))
    return found


def test_catalogue_lists_every_tool_in_the_source():
    assert set(_tooldef_names()) == set(all_tool_names())


def test_each_tool_is_built_in_the_module_its_group_names():
    for name, modules in _tooldef_names().items():
        group = group_of(name)
        assert group is not None and group.module in modules, (name, modules)


def test_tool_names_and_group_keys_are_unique():
    names = [n for g in CATALOG for n in g.tools]
    assert len(names) == len(set(names))
    assert len({g.key for g in CATALOG}) == len(CATALOG)


def test_render_mentions_every_tool():
    text = render()
    assert all(f"- {n}" in text for n in all_tool_names())
    assert f"{len(all_tool_names())} tools in {len(CATALOG)} groups." in text
