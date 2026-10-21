"""Xiaohongshu link ingestion — paste a note URL, get analysable content.

Wraps the team's crawler (`olay/xhs/XhsExtractor/Crawler.py`) behind a stable
service interface. The crawler itself is used **as-is**; we only:

1. normalise whatever the user pasted into the ``/explore/<id>`` form the
   crawler requires (handles ``xhslink.com`` short links and the
   ``/discovery/item/`` variant),
2. run it out-of-process (:mod:`scripts.xhs_fetch`) with a timeout, so its
   ``exit(1)`` and its Chromium session can never take the API down,
3. normalise the result into the same shape the rest of the pipeline already
   consumes.

What a link can and cannot give us — kept explicit so nothing is invented:

    title + content  -> S (文案真实性)
    cover image(s)   -> R (图片真实性); the first image is analysed
    comments         -> U (评论区真实性)      best-effort
    author id        -> profile URL          best-effort
    profile history  -> T (账号可信度)       best-effort: recent note cards;
                                                fewer than 3 still means 暂不评分

Anything the crawler did not return is reported as absent rather than guessed.
An explicit ``account_url`` can override author-homepage discovery when the note
page does not expose a stable user id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field

from app.core.config import BACKEND_ROOT, Settings

logger = logging.getLogger(__name__)

RESULT_MARKER = "__XHS_RESULT__"
RUNNER_PATH = BACKEND_ROOT / "scripts" / "xhs_fetch.py"

# /explore/<id> and /discovery/item/<id> both carry the note id.
_NOTE_ID_IN_PATH = re.compile(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)")
_PROFILE_ID_IN_PATH = re.compile(r"/user/profile/([0-9a-zA-Z]+)")
# Bare ids appear in share text and in short-link redirect targets.
_BARE_NOTE_ID = re.compile(r"\b([0-9a-f]{24})\b")
# A full /explore URL, query string included. Kept verbatim when present: note
# detail access depends on the `xsec_token` that share links carry, so stripping
# the query would silently break the crawl.
_EXPLORE_URL = re.compile(r"https?://[^\s，,、）)]*xiaohongshu\.com/explore/[0-9a-zA-Z]+(?:\?[^\s，,、）)]*)?")
_PROFILE_URL = re.compile(r"https?://[^\s，,、）)]*xiaohongshu\.com/user/profile/[0-9a-zA-Z]+(?:\?[^\s，,、）)]*)?")
_XSEC_TOKEN = re.compile(r"xsec_token=([^&\s，,、）)]+)")
_SHORT_LINK_HOSTS = ("xhslink.com", "xhslink.cn")
_CANONICAL_TEMPLATE = "https://www.xiaohongshu.com/explore/{note_id}"
_CANONICAL_PROFILE_TEMPLATE = "https://www.xiaohongshu.com/user/profile/{user_id}"

# Only the cover is analysed today: ``ContentSample`` holds one image and the
# engine's image role takes one path. The rest are counted, not silently dropped.
MAX_IMAGE_CANDIDATES = 6


class XhsError(RuntimeError):
    """A crawl could not produce content. Carries a user-facing ``reason``."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(slots=True)
class XhsAccount:
    """Author homepage snapshot used by the T dimension."""

    profile_url: str
    handle: str | None = None
    profile_text: str | None = None
    follower_count: int | None = None
    total_post_count: int | None = None
    posts: list[dict] = field(default_factory=list)
    warning: str | None = None


@dataclass(slots=True)
class XhsNote:
    """Normalised crawl result, ready to be written into a ``ContentSample``."""

    url: str
    title: str = ""
    content: str = ""
    comments: list[dict] = field(default_factory=list)
    author_handle: str | None = None
    account: XhsAccount | None = None
    image_paths: list[str] = field(default_factory=list)
    warning: str | None = None

    @property
    def text(self) -> str:
        """Title + body joined, which is what the S dimension should read."""
        parts = [self.title.strip(), self.content.strip()]
        return "\n\n".join(part for part in parts if part)

    @property
    def image_count(self) -> int:
        return len(self.image_paths)


def extract_note_id(raw: str | None) -> str | None:
    """Pull the note id out of any share-text/URL form. ``None`` if absent."""
    if not raw:
        return None
    matched = _NOTE_ID_IN_PATH.search(raw)
    if matched:
        return matched.group(1)
    bare = _BARE_NOTE_ID.search(raw)
    return bare.group(1) if bare else None


def extract_user_id(raw: str | None) -> str | None:
    """Pull the author user id from a Xiaohongshu profile URL."""
    if not raw:
        return None
    matched = _PROFILE_ID_IN_PATH.search(raw)
    return matched.group(1) if matched else None


def canonical_note_url(note_id: str, xsec_token: str | None = None) -> str:
    """Build the ``/explore/<id>`` URL the crawler requires.

    ``xsec_token`` is re-attached when we have one: it is what authorises note
    detail access, so dropping it makes Xiaohongshu redirect to the feed.
    """
    url = _CANONICAL_TEMPLATE.format(note_id=note_id)
    if xsec_token:
        url = f"{url}?xsec_token={xsec_token}&xsec_source=pc_share"
    return url


def canonical_profile_url(user_id: str, xsec_token: str | None = None) -> str:
    url = _CANONICAL_PROFILE_TEMPLATE.format(user_id=user_id)
    if xsec_token:
        url = f"{url}?xsec_token={xsec_token}&xsec_source=pc_share"
    return url


class XhsService:
    """Fetch a note by URL. Stateless; safe to call concurrently."""

    def __init__(
        self,
        *,
        enabled: bool,
        timeout_seconds: int,
        max_comments: int,
        user_data_path: str | None = None,
        headless: bool = False,
        local_port: int | None = None,
        python_executable: str | None = None,
        runner_path: pathlib.Path | None = None,
    ) -> None:
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._max_comments = max_comments
        self._user_data_path = user_data_path
        self._headless = headless
        self._local_port = local_port
        self._python = python_executable or sys.executable
        self._runner = runner_path or RUNNER_PATH

    @classmethod
    def from_settings(cls, settings: Settings) -> "XhsService":
        return cls(
            enabled=settings.xhs_enabled,
            timeout_seconds=settings.xhs_timeout_seconds,
            max_comments=settings.xhs_max_comments,
            user_data_path=(
                str(settings.xhs_user_data_path_absolute)
                if settings.xhs_user_data_path_absolute
                else None
            ),
            headless=settings.xhs_headless,
            local_port=settings.xhs_local_port,
        )

    @property
    def available(self) -> bool:
        return self._enabled and self._runner.is_file()

    def unavailable_reason(self) -> str:
        if not self._enabled:
            return "链接抓取未启用（设置 XHS_ENABLED=true 并安装 requirements-xhs.txt）"
        if not self._runner.is_file():
            return f"抓取运行器缺失：{self._runner}"
        return "链接抓取不可用"

    # -- URL handling --------------------------------------------------------

    def normalize(self, raw: str | None) -> str:
        """Return a crawler-compatible ``/explore/<id>`` URL.

        Nothing here touches the network beyond following a short-link redirect,
        so a bad paste is rejected fast — before we ever start a browser.
        """
        text = (raw or "").strip()
        if not text:
            raise XhsError("链接为空，请粘贴小红书笔记链接或分享文案")

        # 1) A full /explore URL is already exactly what the crawler needs — reuse
        #    it verbatim, query string and all (see `_EXPLORE_URL`).
        direct = _EXPLORE_URL.search(text)
        if direct:
            return direct.group(0)

        # 2) Short link: follow the redirect, then retry the same extraction.
        if any(host in text for host in _SHORT_LINK_HOSTS):
            text = self._follow_short_link(text)
            direct = _EXPLORE_URL.search(text)
            if direct:
                return direct.group(0)

        # 3) Anything else: rebuild the canonical form from whatever id we can find.
        note_id = extract_note_id(text)
        if note_id:
            return canonical_note_url(note_id, extract_xsec_token(text))

        raise XhsError(
            "无法识别的链接，请粘贴小红书笔记链接",
            detail=f"got={text[:200]}",
        )

    def normalize_account(self, raw: str | None) -> str:
        """Normalise a Xiaohongshu author homepage URL."""
        text = (raw or "").strip()
        if not text:
            raise XhsError("账号主页链接为空")

        direct = _PROFILE_URL.search(text)
        if direct:
            return direct.group(0)

        if any(host in text for host in _SHORT_LINK_HOSTS):
            text = self._follow_short_link(text)
            direct = _PROFILE_URL.search(text)
            if direct:
                return direct.group(0)

        user_id = extract_user_id(text)
        if user_id:
            return canonical_profile_url(user_id, extract_xsec_token(text))

        raise XhsError(
            "无法识别的账号主页链接，请粘贴小红书用户主页链接",
            detail=f"got={text[:200]}",
        )

    @staticmethod
    def _follow_short_link(text: str) -> str:
        """Follow an ``xhslink.com`` redirect and return the final URL + body.

        Best-effort: XHS short links sometimes resolve through JS, in which case
        the body regex in :func:`extract_note_id` is the fallback.
        """
        url = _first_url(text)
        if not url:
            return text
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TruLens/0.1)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed user input, short link
                body = response.read(4096).decode("utf-8", errors="ignore")
                return f"{response.geturl()} {body}"
        except Exception as exc:  # noqa: BLE001 - resolve failure must not crash
            logger.warning("short link resolve failed: %s", exc)
            return text

    # -- crawl ---------------------------------------------------------------

    async def fetch(self, raw_url: str, account_url: str | None = None) -> XhsNote:
        """Crawl one note. Blocking work runs off the event loop."""
        if not self.available:
            raise XhsError(self.unavailable_reason())

        url = self.normalize(raw_url)
        normalized_account_url = self.normalize_account(account_url) if account_url else None
        payload = await asyncio.to_thread(self._run_runner, url, normalized_account_url)
        author_payload = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        account = _normalize_account_payload(payload.get("account"))
        account_error = str(payload.get("account_error") or "").strip() or None
        if account is not None and account_error:
            account.warning = account_error

        note = XhsNote(
            url=url,
            title=str(payload.get("title") or ""),
            content=str(payload.get("content") or ""),
            comments=_normalize_comments(payload.get("comments")),
            author_handle=author_payload.get("handle") or (account.handle if account else None),
            account=account,
            image_paths=[str(path) for path in (payload.get("images") or [])][:MAX_IMAGE_CANDIDATES],
            warning=payload.get("warning") or account_error,
        )
        if not note.text.strip() and not note.image_paths:
            raise XhsError("抓取成功但未取到正文或图片")
        return note

    def _run_runner(self, url: str, account_url: str | None = None) -> dict:
        """Execute ``scripts/xhs_fetch.py`` in its own process and parse the result."""
        env = dict(os.environ)
        env["XHS_MAX_COMMENTS"] = str(self._max_comments)
        env["PYTHONIOENCODING"] = "utf-8"
        if self._user_data_path:
            # Persistent profile: log in once, reuse the session on every crawl.
            env["XHS_USER_DATA_PATH"] = self._user_data_path
        if self._local_port:
            env["XHS_LOCAL_PORT"] = str(self._local_port)
        env["XHS_HEADLESS"] = "true" if self._headless else "false"

        try:
            argv = [self._python, str(self._runner), url]
            if account_url:
                argv.append(account_url)
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                env=env,
                cwd=str(self._runner.parent),
            )
        except subprocess.TimeoutExpired as exc:
            raise XhsError(
                "链接抓取超时",
                detail=f"超过 {self._timeout}s（浏览器可能停在登录/验证页面）",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise XhsError("链接抓取无法启动", detail=f"{type(exc).__name__}: {exc}") from exc

        payload = _parse_marker(completed.stdout)
        if payload is None:
            tail = (completed.stderr or completed.stdout or "").strip()[-400:]
            raise XhsError("链接抓取没有返回结果", detail=tail or f"exit={completed.returncode}")
        if not payload.get("ok"):
            raise XhsError(
                str(payload.get("error") or "链接抓取失败"),
                detail=str(payload.get("diagnosis") or payload.get("trace") or "")[:400] or None,
            )
        return payload


# -- helpers -----------------------------------------------------------------


def _first_url(text: str) -> str | None:
    matched = re.search(r"https?://[^\s，,、）)]+", text)
    return matched.group(0) if matched else None


def extract_xsec_token(text: str | None) -> str | None:
    """Pull the ``xsec_token`` out of a share link / share text, if present.

    Note detail is gated on this token: without it Xiaohongshu redirects a
    logged-out session straight to the feed, so it must survive normalisation.
    """
    if not text:
        return None
    matched = _XSEC_TOKEN.search(text)
    return matched.group(1) if matched else None


def _parse_marker(stdout: str | None) -> dict | None:
    """The runner prints one ``__XHS_RESULT__{json}`` line; ignore its noise."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER) :])
            except json.JSONDecodeError:
                return None
    return None


def _normalize_comments(raw: object) -> list[dict]:
    """Coerce the runner's comment list into the pipeline's comment shape."""
    if not isinstance(raw, list):
        return []
    comments: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        comments.append(
            {
                "index": index,
                "content": content,
                "author": item.get("author") or None,
                "likes": 0,
                "created_at": None,
            }
        )
    return comments


def _normalize_account_payload(raw: object) -> XhsAccount | None:
    if not isinstance(raw, dict):
        return None
    profile_url = str(raw.get("profile_url") or "").strip()
    posts: list[dict] = []
    for item in raw.get("posts") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        posts.append(
            {
                "text": text,
                "published_at": item.get("published_at"),
                "like_count": item.get("like_count") or 0,
                "source_note_id": item.get("source_note_id"),
            }
        )
    return XhsAccount(
        profile_url=profile_url,
        handle=str(raw.get("handle") or "").strip() or None,
        profile_text=str(raw.get("profile_text") or "").strip() or None,
        follower_count=_as_optional_int(raw.get("follower_count")),
        total_post_count=_as_optional_int(raw.get("total_post_count")),
        posts=posts,
    )


def _as_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
