"""Playwright-backed implementation of the worker's page facade and launcher.

Playwright is an optional extra (`pip install -e '.[meet]'`). It drives the **installed Google Chrome** (channel "chrome"), so no
browser is downloaded. The bot signs in once by hand into a dedicated profile folder (`./dev.sh meet login`); this code never sees
or stores a password. Audio is handled by the in-page bridge, not by Playwright.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import JoinError
from . import selectors as sel
from .bridge import AudioBridge
from .worker import Launcher, MeetPage

CHROME_ARGS = (
    "--autoplay-policy=no-user-gesture-required",     # the audio bridge must start without a click
    "--mute-audio",                                   # the bot machine must not play the meeting out loud; WebAudio taps are unaffected
    "--no-first-run", "--no-default-browser-check",
)
# Devices mode sends Chrome's sound to a virtual cable, and `--mute-audio` would silence that cable too, so it is left out.
CHROME_ARGS_DEVICES = tuple(a for a in CHROME_ARGS if a != "--mute-audio")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path
    channel: str = "chrome"
    headless: bool = False
    viewport: tuple[int, int] = (1280, 800)
    audio_mode: str = "page"                 # "page" (in-page taps) or "devices" (virtual audio cables)
    extra_args: tuple[str, ...] = ()


_ICON_NAME = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")          # Material icon ligatures such as "frame_person" or "keep_outline"


def real_names(raw: list[str]) -> list[str]:
    """Keep what looks like a person's name: drop icon-font text, blanks and duplicates (order preserved)."""
    seen: dict[str, None] = {}
    for n in raw:
        n = " ".join(str(n).split())
        if n and not _ICON_NAME.match(n) and len(n) <= 80:
            seen.setdefault(n, None)
    return list(seen)


class PlaywrightPage:
    def __init__(self, page: Any):
        self._p = page

    async def goto(self, url: str) -> None:
        await self._p.goto(url, wait_until="domcontentloaded", timeout=45_000)

    async def text(self) -> str:
        try:
            t = await self._p.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 20000)")
        except Exception:  # noqa: BLE001 - a page that is navigating has no text yet
            return ""
        return str(t).lower()

    async def visible(self, selector: str) -> bool:
        try:
            return bool(await self._p.locator(selector).first.is_visible())
        except Exception:  # noqa: BLE001
            return False

    async def click(self, selector: str) -> bool:
        try:
            await self._p.locator(selector).first.click(timeout=3000)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def click_text(self, labels: tuple[str, ...]) -> str:
        for label in labels:
            loc = self._p.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
            try:
                if await loc.is_visible():
                    await loc.click(timeout=3000)
                    return label
            except Exception:  # noqa: BLE001
                continue
        return ""

    async def fill_name(self, name: str) -> bool:
        loc = self._p.locator(sel.NAME_INPUT).first
        try:
            if await loc.is_visible() and not (await loc.input_value()).strip():
                await loc.fill(name)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    async def participants(self) -> list[str]:
        script = ("(s) => [...document.querySelectorAll(s)].map(e => (e.getAttribute('aria-label') || e.innerText || '')"
                  ".split('\\n')[0].trim()).filter(Boolean).slice(0, 60)")
        try:
            return real_names([str(x) for x in await self._p.evaluate(script, sel.PARTICIPANT_ITEMS)])
        except Exception:  # noqa: BLE001
            return []

    async def speaking(self) -> list[str]:
        """Names whose entry in the People list shows the speaking indicator (an animated icon). The markup is Meet's own and may change;
        the signals used are an animation running inside the entry, or a label that says the person is speaking. UNVERIFIED on a
        real call until the run log shows `speaking:` lines."""
        script = """(s) => [...document.querySelectorAll(s)].map((e) => {
          const name = (e.getAttribute('aria-label') || e.innerText || '').split('\\n')[0].trim();
          let on = false;
          for (const d of e.querySelectorAll('*')) {
            const cs = getComputedStyle(d);
            if (cs.animationName && cs.animationName !== 'none' && cs.animationPlayState !== 'paused') { on = true; break; }
            const cls = typeof d.className === 'string' ? d.className : '';
            const lab = ((d.getAttribute('aria-label') || '') + ' ' + cls).toLowerCase();
            if (/(^|[^a-z])(is )?(speaking|talking)([^a-z]|$)/.test(lab) && !/not speaking/.test(lab)) { on = true; break; }
          }
          return [name, on];
        }).filter((x) => x[0] && x[1]).map((x) => x[0]).slice(0, 10)"""
        try:
            return real_names([str(x) for x in await self._p.evaluate(script, sel.PARTICIPANT_ITEMS)])
        except Exception:  # noqa: BLE001
            return []

    def is_closed(self) -> bool:
        return bool(self._p.is_closed())


def playwright_launcher(cfg: BrowserConfig, bridge: AudioBridge, origin: str = "https://meet.google.com",
                        setup: Callable[[Any], Awaitable[None]] | None = None) -> Launcher:
    """A launcher that starts Chrome with the bot's profile, installs the audio bridge, and returns the page and a closer."""

    async def launch() -> tuple[MeetPage, Callable[[], Awaitable[None]]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise JoinError("Playwright is not installed; run `pip install -e '.[meet]'`") from exc
        cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        pw = await async_playwright().start()
        try:
            ctx = await pw.chromium.launch_persistent_context(
                str(cfg.profile_dir), channel=cfg.channel or None, headless=cfg.headless, args=list(CHROME_ARGS_DEVICES if cfg.audio_mode == "devices" else CHROME_ARGS) + list(cfg.extra_args),
                viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
                permissions=["microphone", "camera"],
                # Playwright starts Chrome with a mock keychain by default, which cannot decrypt the cookies a normal Chrome
                # window saved when you signed the bot in. Use the real keychain so that sign-in is readable (macOS may ask once).
                ignore_default_args=["--use-mock-keychain", "--password-store=basic"])
        except Exception as exc:
            await pw.stop()
            raise JoinError(f"could not start Chrome ({type(exc).__name__}); is Google Chrome installed and the profile folder free?") from exc
        try:
            await ctx.grant_permissions(["microphone", "camera"], origin=origin)
            await bridge.attach(ctx)
            if setup is not None:
                await setup(ctx)                       # tests route a local stand-in for Meet here; production passes nothing
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        except Exception:
            await ctx.close()
            await pw.stop()
            raise

        async def close() -> None:
            try:
                await ctx.close()
            finally:
                await pw.stop()

        wrapped = PlaywrightPage(page)
        wrapped.raw = page  # type: ignore[attr-defined]  # the adapter drives the audio bridge on the real page
        return wrapped, close

    return launch


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
