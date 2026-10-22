"""Public GitHub profile reader (official REST API, fixed host, read-only).

Unauthenticated limit is 60 requests/hour per IP; a token raises it to 5,000/hour (docs, accessed 2026-10-21). The token
comes from configuration and is only ever sent to api.github.com. Only PUBLIC data is read; private repositories are
never requested. The /readme endpoint and Accept header were not in the docs we fetched: treated as unverified until a
live run.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.errors import ToolError
from ..tools.utility.contracts import HttpClient, HttpResponse
from ..tools.utility.http import clean_text, parse_json

BASE = "https://api.github.com"
_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_URL_USER = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/?(?:[?#].*)?$")


def parse_username(value: str) -> str:
    """Accept a handle or a github.com profile URL. Anything else is rejected, so this can never become a URL fetcher."""
    v = value.strip()
    m = _URL_USER.match(v)
    if m:
        v = m.group(1)
    if not _USER.match(v) or v.endswith("-") or "--" in v:
        raise ToolError("bad_github_user", "That does not look like a GitHub username")
    return v


class GitHubReader:
    def __init__(self, http: HttpClient, token: str = "", *, timeout_s: float = 8.0, max_repos: int = 6):
        self.http, self._token, self.timeout_s, self.max_repos = http, token, timeout_s, max_repos
        self.rate_remaining: int | None = None
        self.token_rejected = False

    def __repr__(self) -> str:
        return f"GitHubReader(token={'set' if self._token else 'none'})"

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _get(self, path: str, params: dict[str, Any] | None = None, accept: str = "application/vnd.github+json",
                   max_bytes: int = 400_000) -> HttpResponse:
        r = await self.http.get(BASE + path, params=params, headers=self._headers(accept), timeout_s=self.timeout_s,
                                max_bytes=max_bytes)
        if r.status == 401 and self._token:
            # A bad or expired token must not block public data: retry without it and remember to say so.
            self._token = ""
            self.token_rejected = True
            r = await self.http.get(BASE + path, params=params, headers=self._headers(accept), timeout_s=self.timeout_s,
                                    max_bytes=max_bytes)
        rem = r.headers.get("x-ratelimit-remaining")
        if rem is not None and rem.isdigit():
            self.rate_remaining = int(rem)
        if r.status == 404:
            raise ToolError("github_not_found", "GitHub has no public user or repository by that name")
        if r.status in (403, 429):
            raise ToolError("github_rate_limited", "GitHub is limiting requests; try again later or set a GitHub token",
                            retryable=True)
        if r.status == 401:
            raise ToolError("github_auth", "The GitHub token was rejected")
        if r.status >= 400:
            raise ToolError("github_error", "GitHub returned an error", retryable=r.status >= 500, details={"status": r.status})
        return r

    async def profile(self, username: str) -> dict[str, Any]:
        user = parse_username(username)
        info = parse_json((await self._get(f"/users/{user}")).body)
        repos_raw = parse_json((await self._get(f"/users/{user}/repos", {"type": "owner", "sort": "pushed", "per_page": 30})).body)
        if not isinstance(info, dict) or not isinstance(repos_raw, list):
            raise ToolError("bad_response", "Unexpected GitHub response", retryable=True)
        own = [r for r in repos_raw if isinstance(r, dict) and not r.get("fork") and not r.get("private")]
        own.sort(key=lambda r: (int(r.get("stargazers_count") or 0), str(r.get("pushed_at") or "")), reverse=True)
        projects: list[dict[str, Any]] = []
        for r in own[: self.max_repos]:
            name = str(r.get("name") or "")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
                continue
            langs: list[str] = []
            try:
                lang_raw = parse_json((await self._get(f"/repos/{user}/{name}/languages")).body)
                if isinstance(lang_raw, dict):
                    langs = [k for k, _ in sorted(lang_raw.items(), key=lambda kv: -int(kv[1]))][:5]
            except ToolError as exc:
                if exc.code == "github_rate_limited":
                    raise
            projects.append({"name": name, "description": clean_text(str(r.get("description") or ""), 200),
                             "url": str(r.get("html_url") or ""), "languages": langs or ([str(r["language"])] if r.get("language") else []),
                             "stars": int(r.get("stargazers_count") or 0), "pushed_at": str(r.get("pushed_at") or "")[:10]})
        return {"username": user, "url": f"https://github.com/{user}", "bio": clean_text(str(info.get("bio") or ""), 300),
                "public_repos": int(info.get("public_repos") or 0), "followers": int(info.get("followers") or 0),
                "account_created": str(info.get("created_at") or "")[:10], "projects": projects,
                "rate_remaining": self.rate_remaining}
