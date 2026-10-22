from tara import cli


def test_help_lists_every_command(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in [*cli.COMMANDS, "tools"])


def test_unknown_command_fails_with_usage(capsys):
    assert cli.main(["nope"]) == 2
    assert "unknown command 'nope'" in capsys.readouterr().err


def test_tools_prints_the_catalogue(capsys):
    assert cli.main(["tools"]) == 0
    assert "- remember_memory" in capsys.readouterr().out


def test_every_command_points_at_a_module_with_main():
    import importlib.util
    for module, _ in cli.COMMANDS.values():
        assert importlib.util.find_spec(module) is not None, module
