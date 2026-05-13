"""诊断 9222 上的 Chrome 实例：哪些 tab、登录到了哪个号、是哪个 user-data-dir。

挂到正在跑的 Chrome（端口 9222），报告：
  - 浏览器进程信息（user-data-dir 是哪个 profile，确认是不是 19921371193）
  - 所有 context、所有 tab 的 URL / title
  - 关键登录 cookie（web_session、a1、webId）是否存在
  - 在 explore 页上：是否还有 login-modal、搜索框 placeholder 是什么、用户头像是否出现

跑法：
    python test/probe_search_input.py
"""

from __future__ import annotations

import json
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[错误] 未安装 playwright。pip install playwright")
    sys.exit(1)

import requests


LOGIN_COOKIES = {"web_session", "a1", "webId", "xsecappid"}


def probe_browser_meta() -> None:
    """从 CDP HTTP API 拿浏览器进程级信息（不通过 Playwright）。"""
    print("=== 9222 浏览器进程元信息 ===")
    try:
        r = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
        info = r.json()
        for k, v in info.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  [错误] /json/version 失败: {e}")

    print("\n=== 9222 上所有 target（tab + 后台页）===")
    try:
        r = requests.get("http://127.0.0.1:9222/json", timeout=3)
        targets = r.json()
        for t in targets:
            print(
                f"  [{t.get('type')}] {t.get('title','')[:60]}  ←  {t.get('url','')[:100]}"
            )
    except Exception as e:
        print(f"  [错误] /json 失败: {e}")


def probe_context(context, idx: int) -> None:
    print(f"\n=== context #{idx} 的所有 tab ===")
    pages = list(context.pages)
    print(f"  共 {len(pages)} 个 tab")
    for i, p in enumerate(pages):
        try:
            print(f"  [{i}] url={p.url}")
            print(f"      title={p.title()}")
        except Exception as e:
            print(f"  [{i}] 读取失败: {e}")

    print(f"\n=== context #{idx} 的 xiaohongshu cookie ===")
    try:
        cookies = context.cookies(["https://www.xiaohongshu.com"])
        names = sorted({c["name"] for c in cookies})
        print(f"  共 {len(cookies)} 条 cookie")
        print(f"  cookie 名称: {names}")
        present = LOGIN_COOKIES & set(names)
        missing = LOGIN_COOKIES - set(names)
        print(f"  关键登录 cookie 命中: {sorted(present)}")
        print(f"  关键登录 cookie 缺失: {sorted(missing)}")
        if "web_session" in {c["name"] for c in cookies}:
            ws = next(c for c in cookies if c["name"] == "web_session")
            print(f"  web_session 长度: {len(ws.get('value',''))} (有值=已登录的强证据)")
    except Exception as e:
        print(f"  [错误] 读 cookie 失败: {e}")


def probe_explore_page(context) -> None:
    """在 context 中找 explore tab；没有就新开一个。报告登录状态。"""
    print(f"\n=== explore 页登录状态检查 ===")
    explore_page = None
    for p in context.pages:
        if "xiaohongshu.com" in p.url and ("explore" in p.url or p.url.endswith(".com/")):
            explore_page = p
            break
    if explore_page is None:
        print("  没找到 xhs tab，新开一个并 goto explore...")
        explore_page = context.new_page()
        explore_page.goto("https://www.xiaohongshu.com/explore", timeout=30000)
        explore_page.wait_for_load_state("domcontentloaded")

    explore_page.wait_for_timeout(2000)
    state = explore_page.evaluate("""
        () => {
            const result = {
                url: location.href,
                title: document.title,
                searchInput: null,
                loginModalPresent: false,
                userAvatarPresent: false,
            };
            const si = document.querySelector('#search-input');
            if (si) {
                result.searchInput = {
                    placeholder: si.placeholder,
                    visible: si.getBoundingClientRect().width > 0,
                };
            }
            // login modal 判定（之前探针看到 class 包含 login-modal）
            const lm = document.querySelector('.login-modal, .reds-modal-open');
            if (lm) {
                const cs = getComputedStyle(lm);
                result.loginModalPresent = cs.display !== 'none' && cs.visibility !== 'hidden';
                result.loginModalClass = lm.className;
            }
            // 头像 / 用户菜单（已登录的标志）
            const avatar = document.querySelector('.user .avatar, .header .user, .reds-avatar');
            if (avatar) {
                result.userAvatarPresent = avatar.getBoundingClientRect().width > 0;
            }
            return result;
        }
    """)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def main() -> int:
    probe_browser_meta()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print(f"\n[错误] Playwright 连不上 9222: {e}")
            return 1

        contexts = list(browser.contexts)
        print(f"\n=== Playwright 角度的 context 总数: {len(contexts)} ===")
        for i, ctx in enumerate(contexts):
            probe_context(ctx, i)

        # 在第一个 context 上做登录态探针
        if contexts:
            probe_explore_page(contexts[0])

        return 0


if __name__ == "__main__":
    sys.exit(main())
