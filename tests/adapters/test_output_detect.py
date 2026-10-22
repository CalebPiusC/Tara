"""Output auto-detection: classification rules, profiler parsing, probe fallbacks, and the demo flags.
The parsing sample is the real `system_profiler` shape from the dev machine (built-in speakers only); the
'Headphones' strings are assumptions that real wired headphones have not confirmed."""
import subprocess
import sys

import pytest

from tara.adapters import output_detect as od
from tara.adapters.output_detect import OutputInfo, classify_output
from tara.voice.demo import _parser

SAMPLE = """Audio:

    Devices:

        MacBook Pro Microphone:

          Default Input Device: Yes
          Input Channels: 1
          Transport: Built-in
          Input Source: MacBook Pro Microphone

        MacBook Pro Speakers:

          Default Output Device: Yes
          Default System Output Device: Yes
          Output Channels: 2
          Transport: Built-in
          Output Source: MacBook Pro Speakers

        Other Display:

          Output Channels: 2
          Transport: DisplayPort
          Output Source: Other Display
"""

AIRPODS = SAMPLE.replace("MacBook Pro Speakers", "Feranmi's AirPods Pro").replace("Transport: Built-in\n          Output Source: Feranmi's AirPods Pro", "Transport: Bluetooth\n          Output Source: Feranmi's AirPods Pro")


def test_parses_the_default_output_block_not_the_microphone_or_other_devices():
    info = od._parse_profiler(SAMPLE)
    assert info == OutputInfo("MacBook Pro Speakers", "MacBook Pro Speakers", "Built-in")
    assert od._parse_profiler("Audio:\n\n    Devices:\n") is None
    air = od._parse_profiler(AIRPODS)
    assert air is not None and air.transport == "Bluetooth" and "AirPods" in air.name


def test_built_in_speakers_are_speakers_and_confident():
    g = classify_output(od._parse_profiler(SAMPLE))
    assert g.kind == "speaker" and g.confident


def test_headphone_like_devices_are_headphones():
    for name in ("Feranmi's AirPods Pro", "WH-1000XM5 Headphones", "Jabra Evolve Headset", "Galaxy Buds2", "USB EarPods"):
        g = classify_output(OutputInfo(name=name))
        assert g.kind == "headphones" and g.confident, name
    assert classify_output(OutputInfo(name="MacBook Pro Speakers", source="Headphones")).kind == "headphones"   # assumed macOS wording
    assert classify_output(OutputInfo(name="External Headphones")).kind == "headphones"


def test_unknown_or_empty_devices_fall_back_to_the_safe_choice():
    for info in (OutputInfo(name="USB Audio Device"), OutputInfo(name="Aggregate Device"), OutputInfo()):
        g = classify_output(info)
        assert g.kind == "speaker" and not g.confident, info
    assert classify_output(OutputInfo(name="Studio Display Speakers")).kind == "speaker"
    assert classify_output(OutputInfo(name="HomePod")).kind == "speaker"          # bluetooth speakers are not headphones


def test_headphone_word_in_the_source_beats_a_speaker_like_device_name():
    assert classify_output(OutputInfo(name="MacBook Pro Speakers", source="Headphones")).confident
    assert classify_output(OutputInfo(name="Headphones Dock", source="Speakers")).kind == "speaker"


def test_probe_never_raises_and_falls_back(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("system_profiler", 1)
    monkeypatch.setattr(od.subprocess, "run", boom)
    monkeypatch.setattr(od.sys, "platform", "darwin")
    info = od.probe_output(0.01)                                  # falls back to sounddevice or None
    assert info is None or isinstance(info, OutputInfo)
    monkeypatch.setitem(sys.modules, "sounddevice", None)         # import fails too
    assert od.probe_output(0.01) is None
    g = od.detect_output()
    assert g.kind == "speaker" and not g.confident


def test_probe_uses_profiler_output_when_available(monkeypatch):
    class R:
        stdout = AIRPODS
    monkeypatch.setattr(od.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(od.sys, "platform", "darwin")
    g = od.detect_output()
    assert g.kind == "headphones" and "AirPods" in g.reason


def test_demo_flags_default_to_auto_and_keep_the_old_aliases():
    p = _parser()
    assert p.parse_args([]).output == "auto"
    assert p.parse_args(["--speaker"]).output == "speaker"
    assert p.parse_args(["--headphones"]).output == "headphones"
    assert p.parse_args(["--output", "speaker"]).output == "speaker"
    with pytest.raises(SystemExit):
        p.parse_args(["--output", "megaphone"])
