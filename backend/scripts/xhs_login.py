"""One-off Xiaohongshu login — unlocks notes that require a session.

    python scripts/xhs_login.py

Opens Chrome with the persistent profile (``XHS_USER_DATA_PATH``) on the login
page and waits for you to sign in (scan the QR code with the phone app). When
you press Enter here, the session is already saved in that profile, and every
later crawl reuses it.

Why this is needed: many notes are gated — Xiaohongshu answers an anonymous
request with a redirect to ``/login?redirectPath=…`` instead of rendering the
note. The team's crawler can dismiss the *dismissible* login modal, but nothing
in the page can help once the whole navigation is a redirect, so the session has
to exist before the crawl starts.

Alternative for a demo: skip this and point ``XHS_LOCAL_PORT`` at your own
Chrome (started with ``--remote-debugging-port=9222``) — the crawl then reuses
the session you are already logged into there.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402

LOGIN_URL = "https://www.xiaohongshu.com/login"


def main() -> int:
    settings = get_settings()
    profile = settings.xhs_user_data_path
    if not profile:
        print("XHS_USER_DATA_PATH 未设置 —— 先在 backend/.env 里指定一个持久目录，例如：")
        print("  XHS_USER_DATA_PATH=./storage/chrome-profile")
        return 1

    profile = pathlib.Path(profile)
    profile.mkdir(parents=True, exist_ok=True)

    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError as exc:
        print(f"DrissionPage 未安装：{exc}")
        print("  先执行: .venv\\Scripts\\pip install -r requirements-xhs.txt")
        return 1

    options = ChromiumOptions()
    options.set_user_data_path(str(profile))
    options.set_timeouts(base=15)

    print("=" * 74)
    print("小红书登录（登录态会保存到下面这个目录，后续抓取直接复用）")
    print("=" * 74)
    print(f"  profile: {profile.resolve()}")
    print(f"  目标页 : {LOGIN_URL}")
    print("\n即将打开 Chrome，请用手机小红书 App 扫码登录；")
    print("登录成功后回到这个终端按 Enter 即可关闭浏览器。\n")

    page = ChromiumPage(options)
    page.get(LOGIN_URL)

    try:
        input("登录完成后按 Enter 关闭浏览器… ")
    except (EOFError, KeyboardInterrupt):
        pass

    # Give the session a moment to be flushed to disk before the browser dies.
    time.sleep(3)
    try:
        page.quit()
    except Exception:  # noqa: BLE001
        pass

    cookie_dir = profile / "Default"
    wrote = cookie_dir.is_dir()
    print("\n完成。")
    print(f"  登录态目录 {'已生成' if wrote else '未生成'}: {cookie_dir}")
    print("  现在可以重试抓取：")
    print('    .venv\\Scripts\\python scripts\\xhs_smoke_test.py "<笔记链接>" --analyze')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
