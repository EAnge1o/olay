"""Fetch one Xiaohongshu note by URL, as a standalone subprocess.

    python scripts/xhs_fetch.py "<xiaohongshu url>"

Why a subprocess instead of importing the crawler into the API process:

* The team's crawler (`olay/xhs/XhsExtractor/Crawler.py`) calls ``exit(1)`` on an
  invalid URL and owns a real Chromium instance. Running it out-of-process keeps
  either of those from taking the API down.
* A browser session is not concurrency-safe, so isolating it per request is the
  simple correct thing.

We reuse *their* extraction as-is (title / content / images) and only ADD
best-effort comment + author collection in the same page session. Every extra
selector is wrapped so a DOM change degrades to "not available" instead of
failing the fetch — the pipeline already knows how to show "暂不评分".

Output contract: exactly one stdout line, prefixed with ``__XHS_RESULT__``, then
JSON. Everything else the crawler prints is ignored by the caller.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

RESULT_MARKER = "__XHS_RESULT__"

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
# olay/xhs/XhsExtractor — the sibling folder that holds the team's crawler.
XHS_SRC = BACKEND_ROOT.parent / "xhs" / "XhsExtractor"

# Signals that we hit the login wall instead of the note detail.
LOGIN_HINTS = ("登录", "扫码", "手机号", "验证码", "log in", "sign in")
# Xiaohongshu's risk control page ("安全限制" / website-login/error) — an
# automated, logged-out session gets bounced here instead of the note.
BLOCK_HINTS = ("安全限制", "账号异常", "website-login/error", "error_code=", "请稍后重试")

# Comment selectors seen in the XHS note detail DOM. Tried in order; the first
# batch that yields anything wins. Kept intentionally redundant: the markup is
# not stable and a miss must not be fatal.
COMMENT_ITEM_SELECTORS = (
    ".comments-container .comment-item",
    ".comment-list .comment-item",
    ".comments-el .comment-item",
    ".comment-item",
)
COMMENT_TEXT_SELECTORS = (".content .note-text", ".content span", ".note-text", ".content")
COMMENT_AUTHOR_SELECTORS = (".author .name", ".name", ".author-wrapper .name")
AUTHOR_NAME_SELECTORS = (".author-container .username", ".author-container .name", ".username", ".author-wrapper .name")


def _emit(payload: dict) -> None:
    sys.stdout.write(RESULT_MARKER + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _text_of(page, parent, selectors: tuple[str, ...]) -> str | None:
    """First non-empty text found among ``selectors``; ``None`` if all miss."""
    for selector in selectors:
        try:
            element = parent.ele(selector, timeout=0.4)
        except Exception:  # noqa: BLE001 - a missing node is expected, not an error
            continue
        if element is None:
            continue
        try:
            text = (element.text or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if text:
            return text
    return None


def _extract_comments(page, limit: int) -> list[dict]:
    """Best-effort comment collection. Returns [] on any mismatch."""
    try:
        # Comments lazy-load; scroll a bounded number of times to pull them in.
        for _ in range(12):
            try:
                page.scroll.to_bottom()
            except Exception:  # noqa: BLE001
                break
            try:
                if len(page.eles(".comment-item", timeout=0.5)) >= limit:
                    break
            except Exception:  # noqa: BLE001
                break

        items = []
        for selector in COMMENT_ITEM_SELECTORS:
            try:
                items = page.eles(selector, timeout=1.5)
            except Exception:  # noqa: BLE001
                items = []
            if items:
                break

        comments: list[dict] = []
        for node in items[:limit]:
            text = _text_of(page, node, COMMENT_TEXT_SELECTORS)
            if not text:
                continue
            comments.append({"content": text, "author": _text_of(page, node, COMMENT_AUTHOR_SELECTORS)})
        return comments
    except Exception:  # noqa: BLE001
        return []


def _extract_author(page) -> dict:
    """Best-effort note author (handle only — no profile crawling)."""
    try:
        return {"handle": _text_of(page, page, AUTHOR_NAME_SELECTORS)}
    except Exception:  # noqa: BLE001
        return {"handle": None}


def _salvage(folder: pathlib.Path) -> tuple[str | None, str | None]:
    """Read whatever the crawler managed to write before it threw."""
    title = content = None
    for name, target in (("标题.txt", "title"), ("内容.txt", "content")):
        path = folder / name
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if "title" == target:
                    title = value
                else:
                    content = value
        except Exception:  # noqa: BLE001
            continue
    return title, content


# -- embedded-state extraction -----------------------------------------------
# The detail DOM (`#detail-title` / `#detail-desc`) is NOT stable — Xiaohongshu
# changed it, so a note that renders perfectly still throws ElementNotFoundError.
# The note payload also exists in `window.__INITIAL_STATE__`, which survives
# markup changes — but that object is a **Vue proxy**, so it cannot be returned
# to Python (DrissionPage fails to JSON-encode it). We therefore walk it *inside
# the page* and hand back a small, plain JSON string.

INITIAL_STATE_JS = """
return (function () {
  var root = window.__INITIAL_STATE__;
  if (!root) return null;
  var seen = [];
  function isObj(v) { return v && typeof v === 'object'; }
  function walk(node, pred, depth) {
    if (!isObj(node) || depth > 14 || seen.indexOf(node) !== -1) return null;
    seen.push(node);
    try { if (pred(node)) return node; } catch (e) {}
    var vals = Array.isArray(node) ? node : Object.keys(node).map(function (k) { return node[k]; });
    for (var i = 0; i < vals.length; i++) {
      var hit = walk(vals[i], pred, depth + 1);
      if (hit) return hit;
    }
    return null;
  }
  function imageUrl(entry) {
    if (typeof entry === 'string') return entry.indexOf('http') === 0 ? entry : null;
    if (!isObj(entry)) return null;
    var keys = ['urlDefault', 'urlPre', 'url', 'urlTrace', 'urlWatermark'];
    for (var i = 0; i < keys.length; i++) {
      var v = entry[keys[i]];
      if (typeof v === 'string' && v.indexOf('http') === 0) return v;
    }
    var infos = entry.infoList;
    if (Array.isArray(infos)) {
      for (var j = 0; j < infos.length; j++) {
        var u = infos[j] && (infos[j].url || infos[j].urlDefault);
        if (typeof u === 'string' && u.indexOf('http') === 0) return u;
      }
    }
    return null;
  }
  var out = {
    title: null,
    desc: null,
    author: null,
    author_id: null,
    author_red_id: null,
    author_xsec_token: null,
    images: [],
    comments: []
  };

  var note = walk(root, function (n) {
    return Object.prototype.hasOwnProperty.call(n, 'desc') ||
           (Object.prototype.hasOwnProperty.call(n, 'title') &&
            (Object.prototype.hasOwnProperty.call(n, 'imageList') ||
             Object.prototype.hasOwnProperty.call(n, 'images')));
  }, 0);
  if (note) {
    if (typeof note.title === 'string') out.title = note.title;
    if (typeof note.desc === 'string') out.desc = note.desc;
    if (isObj(note.user)) {
      out.author = note.user.nickname || note.user.nickName || null;
      out.author_id = note.user.userId || note.user.user_id || note.user.id || null;
      out.author_red_id = note.user.redId || note.user.red_id || null;
      out.author_xsec_token = note.user.xsecToken || note.user.xsec_token || null;
    }
    var list = note.imageList || note.images || [];
    if (Array.isArray(list)) {
      for (var i = 0; i < list.length; i++) {
        var url = imageUrl(list[i]);
        if (url) out.images.push(url);
      }
    }
  }

  var holder = walk(root, function (n) {
    return Array.isArray(n.comments) && n.comments.length > 0;
  }, 0);
  if (holder) {
    for (var i = 0; i < holder.comments.length && out.comments.length < 80; i++) {
      var item = holder.comments[i];
      if (!isObj(item)) continue;
      var text = item.content || item.text;
      if (typeof text !== 'string' || !text.trim()) continue;
      var user = item.userInfo || item.user;
      out.comments.push({ content: text, author: isObj(user) ? user.nickname : null });
    }
  }
  return JSON.stringify(out);
})()
"""

PROFILE_STATE_JS = """
return (function () {
  var root = window.__INITIAL_STATE__;
  if (!root) return null;
  var seen = [];

  function isObj(v) {
    return v !== null && typeof v === 'object';
  }
  function find(node, pred, depth) {
    if (!isObj(node) || depth > 14 || seen.indexOf(node) !== -1) return null;
    seen.push(node);
    try {
      if (pred(node)) return node;
    } catch (e) {}
    var vals = Array.isArray(node) ? node : Object.keys(node).map(function (k) { return node[k]; });
    for (var i = 0; i < vals.length; i++) {
      var hit = find(vals[i], pred, depth + 1);
      if (hit) return hit;
    }
    return null;
  }
  function each(node, fn, depth) {
    if (!isObj(node) || depth > 14 || seen.indexOf(node) !== -1) return;
    seen.push(node);
    try { fn(node); } catch (e) {}
    var vals = Array.isArray(node) ? node : Object.keys(node).map(function (k) { return node[k]; });
    for (var i = 0; i < vals.length; i++) each(vals[i], fn, depth + 1);
  }

  var holder = find(root, function (n) {
    return (isObj(n.basicInfo) && (typeof n.basicInfo.nickname === 'string' || typeof n.basicInfo.nickName === 'string')) ||
           (isObj(n.userPageData) && isObj(n.userPageData.basicInfo)) ||
           (typeof n.nickname === 'string' && (n.redId || n.userId || n.desc));
  }, 0);

  var basic = {};
  var interactions = [];
  if (isObj(holder)) {
    if (isObj(holder.userPageData)) {
      basic = holder.userPageData.basicInfo || {};
      interactions = holder.userPageData.interactions || [];
    }
    if (isObj(holder.basicInfo)) basic = holder.basicInfo;
    if (Array.isArray(holder.interactions)) interactions = holder.interactions;
  }
  if (!Object.keys(basic).length) {
    seen = [];
    basic = find(root, function (n) {
      return typeof n.nickname === 'string' && (n.redId || n.userId || n.desc || n.noteCount);
    }, 0) || {};
  }
  if (!Array.isArray(interactions) || !interactions.length) {
    seen = [];
    var interactionHolder = find(root, function (n) {
      return Array.isArray(n.interactions) && n.interactions.length > 0;
    }, 0);
    if (interactionHolder) interactions = interactionHolder.interactions;
  }

  var notes = {};
  function addNote(wrapper) {
    if (!isObj(wrapper)) return;
    var card = wrapper.noteCard || wrapper.note_card || wrapper;
    var id = card.noteId || card.note_id || card.id || wrapper.id || null;
    var title = card.displayTitle || card.display_title || card.title || card.desc || null;
    if (!title && !id) return;
    var interact = card.interactInfo || card.interact_info || {};
    var published = card.time || card.publishTime || card.publish_time ||
                    card.lastUpdateTime || card.last_update_time || null;
    var likes = interact.likedCount || interact.liked_count || interact.likeCount || 0;
    var key = id || String(title);
    notes[key] = {
      id: id,
      title: title ? String(title) : null,
      published_at: published,
      like_count: likes
    };
  }

  seen = [];
  each(root, function (n) {
    if (isObj(n.noteCard)) addNote(n);
    if (isObj(n.note_card)) addNote(n);
    if ((n.displayTitle || n.display_title) && (n.interactInfo || n.interact_info || n.noteId || n.note_id)) {
      addNote(n);
    }
  }, 0);

  return JSON.stringify({
    basic_info: basic,
    interactions: interactions,
    notes: Object.keys(notes).map(function (key) { return notes[key]; })
  });
})()
"""
# Cap so a 20-image gallery does not stall the crawl for a demo.
MAX_IMAGES = 6
MAX_ACCOUNT_POSTS = 12

_PROFILE_ID_IN_PATH = re.compile(r"/user/profile/([0-9a-zA-Z]+)")


def _profile_url(user_id: str | None, xsec_token: str | None = None) -> str | None:
    if not user_id:
        return None
    url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    if xsec_token:
        url = f"{url}?xsec_token={xsec_token}&xsec_source=pc_share"
    return url


def _extract_initial_state(page) -> dict:
    """Read the note payload embedded in the page. Returns empty fields on miss."""
    result: dict = {
        "title": None,
        "content": None,
        "images": [],
        "author": None,
        "author_id": None,
        "author_red_id": None,
        "author_xsec_token": None,
        "comments": [],
    }
    if page is None:
        return result
    try:
        raw = page.run_js(INITIAL_STATE_JS)
    except Exception:  # noqa: BLE001 - a page that refuses JS just yields nothing
        return result
    if not isinstance(raw, str) or not raw.strip():
        return result
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result

    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()
    desc = payload.get("desc")
    if isinstance(desc, str) and desc.strip():
        result["content"] = desc.strip()
    images = payload.get("images")
    if isinstance(images, list):
        result["images"] = [url for url in images if isinstance(url, str)]
    author = payload.get("author")
    if isinstance(author, str) and author.strip():
        result["author"] = author.strip()
    for key in ("author_id", "author_red_id", "author_xsec_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    comments = payload.get("comments")
    if isinstance(comments, list):
        result["comments"] = [
            {"content": str(item.get("content", "")).strip(), "author": item.get("author")}
            for item in comments
            if isinstance(item, dict) and str(item.get("content", "")).strip()
        ]
    return result


def _as_count(value: object) -> int | None:
    """Parse Xiaohongshu counts such as ``1.2万`` / ``3,842`` / ``12``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("万") or text.lower().endswith("w"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    matched = re.search(r"\d+(?:\.\d+)?", text)
    if not matched:
        return None
    try:
        return max(0, int(float(matched.group(0)) * multiplier))
    except ValueError:
        return None


def _as_iso_time(value: object) -> str | None:
    """Normalise millisecond/second timestamps; leave human date strings as-is."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return _as_iso_time(int(text))
    return text


def _normalize_profile(
    payload: dict,
    *,
    profile_url: str,
    max_posts: int,
) -> dict:
    basic = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
    interactions = payload.get("interactions") if isinstance(payload.get("interactions"), list) else []

    counts: dict[str, int | None] = {}
    for item in interactions:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("name") or item.get("key") or "").lower()
        count = _as_count(item.get("count") or item.get("value"))
        if any(token in kind for token in ("fan", "fans", "粉丝", "follower")):
            counts["follower"] = count
        elif any(token in kind for token in ("note", "笔记")):
            counts["notes"] = count

    posts: list[dict] = []
    seen_titles: set[str] = set()
    for item in payload.get("notes") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        posts.append(
            {
                "text": title,
                "published_at": _as_iso_time(item.get("published_at")),
                "like_count": _as_count(item.get("like_count")) or 0,
                "source_note_id": item.get("id"),
            }
        )
        if len(posts) >= max_posts:
            break

    follower_count = (
        counts.get("follower")
        or _as_count(basic.get("fans") or basic.get("followerCount") or basic.get("follower_count"))
    )
    total_post_count = (
        counts.get("notes")
        or _as_count(basic.get("noteCount") or basic.get("note_count") or basic.get("notesCount"))
        or (len(posts) if posts else None)
    )
    handle = (
        str(basic.get("nickname") or basic.get("nickName") or basic.get("redId") or "").strip()
        or None
    )
    profile_text = str(basic.get("desc") or basic.get("description") or "").strip() or None
    return {
        "profile_url": profile_url,
        "handle": handle,
        "profile_text": profile_text,
        "follower_count": follower_count,
        "total_post_count": total_post_count,
        "posts": posts,
    }


def _extract_profile(
    page,
    profile_url: str,
    *,
    max_posts: int = MAX_ACCOUNT_POSTS,
) -> tuple[dict | None, str | None]:
    """Open an author homepage and merge note cards from its embedded state."""
    if page is None:
        return None, "profile_browser_unavailable"
    try:
        page.get(profile_url)
        time.sleep(2.0)
    except Exception as exc:  # noqa: BLE001
        return None, f"profile_navigation_failed: {type(exc).__name__}"

    merged: dict[str, dict] = {}
    basic: dict = {}
    interactions: list = []
    for attempt in range(3):
        try:
            raw = page.run_js(PROFILE_STATE_JS)
            payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
        except Exception:  # noqa: BLE001
            payload = None
        if isinstance(payload, dict):
            if isinstance(payload.get("basic_info"), dict) and payload["basic_info"]:
                basic = payload["basic_info"]
            if isinstance(payload.get("interactions"), list) and payload["interactions"]:
                interactions = payload["interactions"]
            for item in payload.get("notes") or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("id") or item.get("title") or "")
                if key:
                    merged[key] = item
        if len(merged) >= max_posts:
            break
        try:
            page.scroll.to_bottom()
            time.sleep(1.0)
        except Exception:  # noqa: BLE001
            break

    profile = _normalize_profile(
        {"basic_info": basic, "interactions": interactions, "notes": list(merged.values())},
        profile_url=profile_url,
        max_posts=max_posts,
    )
    if not profile.get("handle") and not profile.get("posts"):
        return None, "profile_state_unavailable"
    return profile, None


def _diagnose(page) -> dict:
    """Explain WHY a crawl produced nothing — so the user gets a next step.

    A missing ``#detail-title`` means one of: not logged in, the note is gone, or
    Xiaohongshu's risk control bounced us to a "安全限制" page. Reporting the
    actual page title/URL turns a cryptic ``ElementNotFoundError`` into something
    the caller can act on.
    """
    info: dict = {
        "page_title": None,
        "page_url": None,
        "login_wall": False,
        "risk_control": False,
    }
    if page is None:
        return info
    try:
        info["page_title"] = (page.title or "").strip() or None
    except Exception:  # noqa: BLE001
        pass
    try:
        info["page_url"] = page.url
    except Exception:  # noqa: BLE001
        pass
    haystack = ""
    try:
        haystack = f"{info.get('page_title') or ''} {info.get('page_url') or ''} {(page.html or '')[:20000]}"
    except Exception:  # noqa: BLE001
        haystack = f"{info.get('page_title') or ''} {info.get('page_url') or ''}"
    info["login_wall"] = any(hint in haystack for hint in LOGIN_HINTS)
    info["risk_control"] = any(hint in haystack for hint in BLOCK_HINTS)
    return info


_NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)")


def _note_id_of(url: str | None) -> str | None:
    """The note id we asked for, so we can tell whether the page stayed on it."""
    matched = _NOTE_ID_RE.search(url or "")
    return matched.group(1) if matched else None


def _safe_url(page) -> str | None:
    """Current URL, or ``None`` when the page/browser is already gone."""
    if page is None:
        return None
    try:
        return page.url
    except Exception:  # noqa: BLE001
        return None


def _quit(page) -> None:
    """Best-effort browser teardown; never raises."""
    if page is None:
        return
    try:
        page.quit()
    except Exception:  # noqa: BLE001
        pass


def _failure_reason(crawl_error: str | None, diagnosis: dict, requested_id: str | None = None) -> str:
    """Turn the diagnosis into an actionable, user-facing reason."""
    if diagnosis.get("risk_control"):
        return (
            "小红书返回了安全限制（风控拦截：账号异常/需登录）。"
            "请在打开的浏览器窗口中完成登录后重试。"
        )
    if diagnosis.get("login_wall"):
        return f"需要先登录小红书（当前页面：{diagnosis.get('page_title')}）"
    if crawl_error and "PageDisconnected" in crawl_error:
        return (
            "浏览器窗口被关闭或连接断开，抓取中断——"
            "请勿手动关闭自动弹出的 Chrome 窗口，然后重试。"
        )
    # Landed somewhere other than the note we asked for: the usual cause is an
    # expired/invalid share token, which Xiaohongshu answers with a redirect to
    # the home feed (`/explore`) — not a DOM or login problem.
    page_url = str(diagnosis.get("page_url") or "")
    if requested_id and page_url and requested_id not in page_url:
        return (
            "链接已失效或被重定向回小红书首页（分享链接里的 xsec_token 会过期）——"
            "请在 App 里重新复制一条新的分享链接再试。"
        )
    if diagnosis.get("page_title"):
        # The URL is the note's, but the rendered page is the site's own shell:
        # `location` ends up on /login?redirectPath=<the note>. Verified with a
        # plain HTTP request (no browser) that this is server-side gating, not a
        # DOM/selector problem — and it hits previously-working notes too, so the
        # trigger is the *client* (burst traffic / automation), not the note.
        if "你的生活兴趣社区" in str(diagnosis["page_title"]):
            return (
                "小红书把本次请求拦截到了登录页（/login?redirectPath=这条笔记）——属于服务端风控："
                "笔记详情需要有效会话，且短时间内的频繁/自动化访问会被判定为异常。"
                "建议：① 隔几分钟再试（风控窗口很短）；② 设置 XHS_LOCAL_PORT 复用你自己"
                "已登录的 Chrome 会话（最稳）；③ 或跑 scripts/xhs_login.py 登录一次后复用该 profile。"
            )
        # Otherwise: the page loaded with a title, but neither the crawler's
        # selectors nor the embedded state yielded content.
        return (
            f"页面已加载但未能取到笔记内容（页面标题：{diagnosis['page_title']}）——"
            "可能是小红书前端结构变化、该笔记不可访问，或正文/图片均为空。"
        )
    if crawl_error:
        return f"{crawl_error} :: {diagnosis}"
    return "抓取完成但页面结构未匹配（笔记可能已删除或需要登录）"


def _crawler_class(base_cls, browser_options):
    """Subclass the team's ``Crawler`` to inject browser options.

    Their ``__init__`` builds ``ChromiumPage()`` with default options, which means
    a throwaway profile (and therefore a logged-out session). Re-running their
    ``__init__`` body with our options is the smallest change that keeps their
    extraction code — including ``run()`` and every private selector — untouched.
    """

    class _ConfiguredCrawler(base_cls):
        def __init__(self, url: str, should_download_img: bool, tear_down: bool):
            from DrissionPage import ChromiumPage  # noqa: PLC0415

            self.url = url
            self.should_download_img = should_download_img
            self.tear_down = tear_down
            self.browser = ChromiumPage(browser_options)
            task_id = datetime.now().strftime("%Y年%m月%d日%H时%M分%S秒")
            self.folder = f"./output/{task_id}"

    return _ConfiguredCrawler


def _build_browser_options():
    """Persistent profile + optional attach-to-existing-browser + headless."""
    from DrissionPage import ChromiumOptions  # noqa: PLC0415

    options = ChromiumOptions()

    # Preferred for demos: attach to a Chrome the user already logged into.
    port = os.environ.get("XHS_LOCAL_PORT", "").strip()
    if port:
        options.set_local_port(int(port))

    profile = os.environ.get("XHS_USER_DATA_PATH")
    if profile:
        pathlib.Path(profile).mkdir(parents=True, exist_ok=True)
        options.set_user_data_path(profile)

    if os.environ.get("XHS_HEADLESS", "").lower() in {"1", "true", "yes"}:
        options.headless(True)

    # The note detail can take a moment on a cold profile.
    options.set_timeouts(base=12)
    return options


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"ok": False, "error": "missing_url"})
        return 2
    url = sys.argv[1]
    account_hint = sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip() else None

    if str(XHS_SRC) not in sys.path:
        sys.path.insert(0, str(XHS_SRC))

    # The crawler writes to ./output/<timestamp>, so move off the repo into a
    # throwaway directory before anything runs.
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="xhs-"))
    os.chdir(workdir)

    try:
        from Crawler import Crawler  # noqa: PLC0415 - path is set up above
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": f"crawler_unavailable: {type(exc).__name__}: {exc}"})
        return 1

    crawler = None
    page = None
    crawl_error: str | None = None
    try:
        crawler_class = _crawler_class(Crawler, _build_browser_options())
        crawler = crawler_class(url, True, False)  # tear_down=False keeps the session alive
        page = crawler.browser
        crawler.run()
    except SystemExit as exc:
        # Their crawler calls exit(1) when the URL lacks the /explore/ marker.
        _emit({"ok": False, "error": f"unsupported_url (crawler exited {exc.code})"})
        return 1
    except Exception as exc:  # noqa: BLE001 - salvage whatever landed on disk
        crawl_error = f"{type(exc).__name__}: {str(exc)[:400]}"

    requested_id = _note_id_of(url)
    page_url = _safe_url(page)

    # Hard gate: if the browser is not on the note we asked for, everything below
    # would describe the feed instead. `page.title` in particular always has a
    # value (e.g. "小红书 - 你的生活兴趣社区"), so without this check a redirect
    # used to be reported as a *success* with the feed title as the note text.
    if requested_id and page_url and requested_id not in page_url:
        diagnosis = _diagnose(page)
        reason = _failure_reason(
            crawl_error or "redirected_away_from_note", diagnosis, requested_id
        )
        _quit(page)
        _emit({"ok": False, "error": reason, "diagnosis": diagnosis})
        return 1

    folder = pathlib.Path(getattr(crawler, "folder", "") or ".").resolve()
    title, content = _salvage(folder)

    image_dir = folder / "图片"
    images: list[str] = []
    if image_dir.is_dir():
        images = sorted(str(path.resolve()) for path in image_dir.iterdir() if path.is_file())

    max_comments = int(os.environ.get("XHS_MAX_COMMENTS", "80"))
    comments: list[dict] = []
    author: dict = {"handle": None}
    if page is not None:
        try:
            comments = _extract_comments(page, max_comments)
            author = _extract_author(page)
        except Exception:  # noqa: BLE001
            pass

    # The DOM selectors above are version-fragile (Xiaohongshu changed them, so a
    # note that renders fine still throws). The embedded state is the primary
    # source; anything it supplies wins over a missing DOM extraction.
    state = _extract_initial_state(page)
    if not title:
        # ONLY the embedded state may supply the title. `document.title` is not a
        # valid fallback: when the note fails to render (login gate / expired
        # token) the SPA keeps the note URL but shows the feed, so its title is
        # "小红书 - 你的生活兴趣社区" — feeding that to the analyser as the note's
        # text is exactly how a broken crawl used to masquerade as a success.
        title = state["title"]
    if not content:
        content = state["content"]
    if not comments and state["comments"]:
        comments = state["comments"][:max_comments]
    if not author.get("handle") and state["author"]:
        author["handle"] = state["author"]
    if state.get("author_id"):
        author["user_id"] = state["author_id"]
    if state.get("author_red_id"):
        author["red_id"] = state["author_red_id"]
    author["profile_url"] = account_hint or _profile_url(
        state.get("author_id"),
        state.get("author_xsec_token"),
    )

    # State images are CDN URLs; download them the same way the crawler does.
    if not images and state["images"] and page is not None:
        image_dir.mkdir(parents=True, exist_ok=True)
        for url in state["images"][:MAX_IMAGES]:
            try:
                page.download(url, str(image_dir), suffix="webp")
            except Exception:  # noqa: BLE001 - a failed image must not fail the crawl
                continue
        images = sorted(str(path.resolve()) for path in image_dir.iterdir() if path.is_file())

    if not (title or content or images):
        diagnosis = _diagnose(page)
        reason = _failure_reason(crawl_error, diagnosis, _note_id_of(url))
        try:
            if page is not None:
                page.quit()
        except Exception:  # noqa: BLE001
            pass
        _emit({"ok": False, "error": reason, "diagnosis": diagnosis})
        return 1

    account: dict | None = None
    account_error: str | None = None
    profile_url = author.get("profile_url")
    if profile_url and page is not None:
        account, account_error = _extract_profile(page, str(profile_url))
        if account and not author.get("handle"):
            author["handle"] = account.get("handle")

    try:
        if page is not None:
            page.quit()
    except Exception:  # noqa: BLE001
        pass

    _emit(
        {
            "ok": True,
            "url": url,
            "title": title,
            "content": content,
            "images": images,
            "comments": comments,
            "author": author,
            "account": account,
            "account_error": account_error,
            "warning": crawl_error,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - the caller must always get a verdict
        _emit({"ok": False, "error": f"unhandled: {type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1200:]})
        raise SystemExit(1) from exc
