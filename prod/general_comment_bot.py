"""
生产版 - 小红书评论自动回复机器人（通用版）

与 comment_bot.py 的区别：
- 服务相关配置（关键词、占位符池、服务描述、LLM 提示语等）全部抽离到
  prod/profiles/<profile>.json 配置文件中。
- 通过命令行指定 profile 名称即可切换不同业务领域。

用法:
    python prod/general_comment_bot.py <profile_name>

示例:
    python prod/general_comment_bot.py drone           # 无人机服务
    python prod/general_comment_bot.py medical_beauty  # 医美服务
    python prod/general_comment_bot.py dating          # 婚恋服务
"""

import argparse
import sys
import os
import json
import requests
import re
import time
import random

# ============================================================
#  路径与环境初始化
# ============================================================

script_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(script_dir)
sys.path.insert(0, os.path.join(base_dir, "scripts"))

# 解析 .env 文件
env_path = os.path.join(base_dir, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip().strip("'\"")

from cdp_publish import XiaohongshuPublisher

# 反检测模块（账号配额 + 风控信号）
import account_state
import risk_control
from account_manager import get_default_account
from run_lock import single_instance, SingleInstanceError

OPENROUTER_API_KEY = (
    os.environ.get("OPENROUTER_API_KEY")
    or os.environ.get("api_key", "")
)

PROFILES_DIR = os.path.join(script_dir, "profiles")
COMMENT_RESPONSES_DIR = os.path.join(script_dir, "comment_responses")

# ============================================================
#  Profile 加载
# ============================================================

def load_profile(profile_name: str) -> dict:
    """加载 prod/profiles/<profile_name>.json 配置文件。"""
    profile_path = os.path.join(PROFILES_DIR, f"{profile_name}.json")
    if not os.path.exists(profile_path):
        available = []
        if os.path.isdir(PROFILES_DIR):
            available = [
                os.path.splitext(f)[0]
                for f in os.listdir(PROFILES_DIR)
                if f.endswith(".json")
            ]
        raise FileNotFoundError(
            f"找不到 profile 文件: {profile_path}\n"
            f"可用 profile: {available}"
        )
    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


def render_prompt(template: str, values: dict[str, object]) -> str:
    """使用 profile 中的字符串模板渲染提示词。"""
    return re.sub(
        r"\$\{([A-Za-z_]\w*)\}",
        lambda match: str(values.get(match.group(1), match.group(0))),
        template,
    )


def get_prompt(profile: dict, name: str) -> str:
    """从 profile.llm_prompts 中读取提示词，保留旧字段兼容。"""
    prompts = profile.get("llm_prompts", {})
    if isinstance(prompts, dict) and prompts.get(name):
        return str(prompts[name])

    legacy_fields = {
        "comment_system": "llm_system_role",
    }
    legacy_field = legacy_fields.get(name)
    if legacy_field and profile.get(legacy_field):
        return str(profile[legacy_field])

    raise ValueError(f"profile 缺少 llm_prompts.{name} 配置")


def get_target_provinces(profile: dict) -> list[str]:
    """读取并标准化 profile 中限制处理的目标省份。"""
    raw = profile.get("target_provinces", [])
    if isinstance(raw, str):
        raw = [item.strip() for item in re.split(r"[,，、\s]+", raw)]
    if not isinstance(raw, list):
        return []

    provinces: list[str] = []
    seen: set[str] = set()
    for item in raw:
        province = str(item).strip()
        if province and province not in seen:
            provinces.append(province)
            seen.add(province)
    return provinces


def filter_comments_by_target_provinces(
    comments: list[dict],
    target_provinces: list[str],
) -> list[dict]:
    """有 target_provinces 配置时，只保留对应省份的评论。"""
    if not target_provinces:
        return comments

    allowed = set(target_provinces)
    return [
        comment
        for comment in comments
        if str(comment.get("province", "")).strip() in allowed
    ]


# ============================================================
#  关键词生成
# ============================================================

# 全局历史关键词集合，跨轮次去重
_used_keywords_history: set[str] = set()


def generate_keywords(profile: dict) -> list[str]:
    """
    根据 profile 展开 special_keywords（支持任意占位符）
    并从 general_keywords 中随机抽取，合并后打乱顺序返回。
    跳过已使用过的关键词。
    """
    keywords_data = profile.get("keywords", {})
    config = profile.get("config", {})

    special_templates: list[str] = keywords_data.get("special_keywords", [])
    general_pool: list[str] = keywords_data.get("general_keywords", [])
    placeholders: dict[str, list[str]] = keywords_data.get("placeholders", {})
    placeholder_counts: dict[str, int] = keywords_data.get("placeholder_counts", {})

    keywords_count = config.get("keywords_count", 10)
    default_placeholder_count = config.get("placeholder_count", 3)

    # --- A: 展开 special_keywords（支持多占位符） ---
    expanded: list[str] = []
    placeholder_pattern = re.compile(r"\{(\w+)\}")

    for template in special_templates:
        names = placeholder_pattern.findall(template)
        if not names:
            expanded.append(template)
            continue

        # 目前支持模板中最多一个占位符（与原实现一致）
        primary = names[0]
        pool = placeholders.get(primary, [])
        if not pool:
            continue

        count = placeholder_counts.get(primary, default_placeholder_count)
        sampled = random.sample(pool, min(count, len(pool)))
        for value in sampled:
            expanded.append(template.replace("{" + primary + "}", value))

    # --- B: 从 general_keywords 随机抽取 ---
    general_sampled = random.sample(
        general_pool, min(keywords_count, len(general_pool))
    )

    # --- C: 合并并打乱 ---
    all_keywords = expanded + general_sampled
    random.shuffle(all_keywords)

    # 去重（保持顺序，跳过历史已用关键词）
    seen: set[str] = set()
    unique: list[str] = []
    for kw in all_keywords:
        if kw not in seen and kw not in _used_keywords_history:
            seen.add(kw)
            unique.append(kw)

    _used_keywords_history.update(unique)
    return unique


def generate_keywords_with_llm(
    profile: dict,
    batch_size: int = 20,
) -> list[str]:
    """调用 LLM 根据 profile 描述的业务生成一批全新的搜索关键词。"""
    if not OPENROUTER_API_KEY:
        print("  -> [警告] 未设置 API Key，无法使用 LLM 生成关键词。")
        return []

    keywords_data = profile.get("keywords", {})
    all_existing = keywords_data.get("general_keywords", [])
    sample_size = min(15, len(all_existing))
    sample_keywords = random.sample(all_existing, sample_size) if all_existing else []

    recent_used = list(_used_keywords_history)[-60:]

    business_name = profile.get("service_name", "")
    business_topic = profile.get("llm_keyword_topic", business_name)
    intent_terms = profile.get("llm_intent_terms", business_name)

    prompt = render_prompt(
        get_prompt(profile, "keyword_user"),
        {
            "service_name": business_name,
            "business_topic": business_topic,
            "intent_terms": intent_terms,
            "batch_size": batch_size,
            "sample_keywords_json": json.dumps(sample_keywords, ensure_ascii=False),
            "recent_used_json": json.dumps(recent_used, ensure_ascii=False),
        },
    )
    system_prompt = get_prompt(profile, "keyword_system")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": profile.get("llm_model", "google/gemini-3-flash-preview"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    content_text = ""
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content_text = data["choices"][0]["message"]["content"]

        match = re.search(r"\{.*\}", content_text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            result = json.loads(content_text)

        raw_keywords = result.get("keywords", [])

        fresh: list[str] = []
        for kw in raw_keywords:
            kw = kw.strip()
            if kw and kw not in _used_keywords_history:
                fresh.append(kw)
                _used_keywords_history.add(kw)

        print(f"  -> [LLM] 成功生成 {len(fresh)} 个新关键词。")
        return fresh

    except json.JSONDecodeError:
        print(f"  -> [错误] LLM 关键词生成返回非 JSON: {content_text[:200]}")
        return []
    except Exception as e:
        print(f"  -> [错误] LLM 关键词生成失败: {e}")
        return []


def get_next_keyword_batch(profile: dict, round_number: int) -> tuple[list[str], str]:
    """
    智能获取下一批关键词：
    - 所有轮次：优先用 LLM 生成新词
    - LLM 失败或未返回关键词时，回退到 profile 中的静态关键词

    返回：
    - keywords: 本轮关键词
    - source: 实际来源，用于日志展示
    """
    _ = round_number  # 保留参数便于日志和后续按轮次扩展策略
    config = profile.get("config", {})
    batch_size = config.get("llm_keyword_batch_size", 20)
    llm_keywords = generate_keywords_with_llm(profile, batch_size)

    if llm_keywords:
        random.shuffle(llm_keywords)
        return llm_keywords, "LLM 智能生成"

    print("  -> [回退] LLM 生成失败，清除历史重新使用静态关键词...")
    _used_keywords_history.clear()
    return generate_keywords(profile), "静态关键词库（LLM 回退）"


# ============================================================
#  LLM 评论分析
# ============================================================

def evaluate_comments_with_llm(
    profile: dict,
    comments: list[dict],
) -> dict | None:
    """
    将评论发送给 LLM 分析，判断哪条最有业务意向。
    无意向时返回 selected_index = -1。
    """
    if not OPENROUTER_API_KEY:
        print("  -> [警告] 未设置 OPENROUTER_API_KEY / api_key，跳过 LLM 分析。")
        return None

    service_desc = profile.get("service_desc", "")
    intent_terms = profile.get("llm_intent_terms", "相关服务")
    reply_style = profile.get(
        "llm_reply_style",
        "像真实小红书用户，友好、真诚、有网感",
    )
    reply_max_chars = profile.get("llm_reply_max_chars", 50)
    system_prompt = render_prompt(
        get_prompt(profile, "comment_system"),
        {
            "service_desc": service_desc,
            "intent_terms": intent_terms,
            "reply_style": reply_style,
            "reply_max_chars": reply_max_chars,
        },
    )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    comments_text = ""
    for i, c in enumerate(comments):
        user = c.get("userInfo", {}).get("nickname", "Unknown")
        province = c.get("province") or "未知"
        content = c.get("content", "").replace("\n", " ")
        comments_text += (
            f"[{i + 1}] 用户: {user}, 省份: {province}, 评论: {content}\n"
        )

    prompt = render_prompt(
        get_prompt(profile, "comment_user"),
        {
            "service_desc": service_desc,
            "intent_terms": intent_terms,
            "reply_style": reply_style,
            "reply_max_chars": reply_max_chars,
            "comments_text": comments_text,
            "comments_count": len(comments),
        },
    )

    payload = {
        "model": profile.get("llm_model", "google/gemini-3-flash-preview"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    content_text = ""
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content_text = data["choices"][0]["message"]["content"]

        match = re.search(r"\{.*\}", content_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))

        return json.loads(content_text)
    except json.JSONDecodeError:
        print(f"  -> [错误] LLM 返回非 JSON: {content_text[:200]}")
        return None
    except Exception as e:
        print(f"  -> [错误] 调用 OpenRouter 失败: {e}")
        if "resp" in locals() and hasattr(resp, "text"):
            print(f"  -> [响应]: {resp.text[:300]}")
        return None


# ============================================================
#  缓存工具 - 基于 profile 独立文件
# ============================================================

def get_cache_file(profile_name: str) -> str:
    return os.path.join(script_dir, f"processed_cache_{profile_name}.json")


def load_cache(profile_name: str) -> dict:
    path = get_cache_file(profile_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cache(profile_name: str, cache: dict):
    with open(get_cache_file(profile_name), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ============================================================
#  DOM 交互辅助函数
# ============================================================

def _scroll_search_page(publisher: XiaohongshuPublisher, pixels: int = 600):
    """在搜索结果页向下滚动指定像素，触发懒加载。"""
    publisher._evaluate(f"""
        (() => {{
            const delta = {pixels};
            const isScrollable = (el) => {{
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const overflowY = style.overflowY || "";
                const canScroll = /(auto|scroll|overlay)/.test(overflowY);
                return canScroll && el.scrollHeight > el.clientHeight + 40;
            }};

            const candidates = [
                document.scrollingElement,
                document.documentElement,
                document.body,
                ...document.querySelectorAll("main, [class*='search'], [class*='feeds'], [class*='waterfall'], [class*='container'], div")
            ].filter((el, index, arr) => el && arr.indexOf(el) === index);

            const target = candidates
                .filter(isScrollable)
                .sort((a, b) => {{
                    const aRange = a.scrollHeight - a.clientHeight;
                    const bRange = b.scrollHeight - b.clientHeight;
                    return bRange - aRange;
                }})[0] || document.scrollingElement || document.documentElement || document.body;

            target.scrollBy({{ top: delta, behavior: "instant" }});
            window.dispatchEvent(new WheelEvent("wheel", {{ deltaY: delta, bubbles: true }}));
            return true;
        }})()
    """)
    publisher._sleep(1.2, minimum_seconds=0.8)


def _scroll_feeds_container_area(
    publisher: XiaohongshuPublisher,
    pixels: int,
) -> bool:
    """小幅滚动承载 feeds-container 的搜索结果区域。"""
    result = publisher._evaluate(f"""
        (() => {{
            const delta = {pixels};
            const feeds = document.querySelector(
                ".feeds-container, [class*='feeds-container'], #exploreFeeds"
            );
            if (!(feeds instanceof HTMLElement)) {{
                return {{ ok: false, reason: "feeds_not_found" }};
            }}

            const isScrollable = (el) => {{
                if (!el) return false;
                return el.scrollHeight > el.clientHeight + 40;
            }};

            const candidates = [];
            let node = feeds;
            while (node && node instanceof HTMLElement) {{
                candidates.push(node);
                node = node.parentElement;
            }}
            candidates.push(
                document.scrollingElement,
                document.documentElement,
                document.body
            );

            const target = candidates.find(isScrollable);
            const beforeWindow = window.scrollY || window.pageYOffset || 0;
            if (target && typeof target.scrollBy === "function") {{
                const before = target.scrollTop || 0;
                target.scrollBy({{ top: delta, behavior: "instant" }});
                target.dispatchEvent(new WheelEvent("wheel", {{
                    deltaY: delta,
                    bubbles: true,
                    cancelable: true,
                }}));
                const after = target.scrollTop || 0;
                const afterWindow = window.scrollY || window.pageYOffset || 0;
                return {{
                    ok: after !== before || afterWindow !== beforeWindow,
                    reason: "element_scroll",
                    before,
                    after,
                    beforeWindow,
                    afterWindow,
                }};
            }}

            window.scrollBy({{ top: delta, behavior: "instant" }});
            window.dispatchEvent(new WheelEvent("wheel", {{
                deltaY: delta,
                bubbles: true,
                cancelable: true,
            }}));
            const afterWindow = window.scrollY || window.pageYOffset || 0;
            return {{
                ok: afterWindow !== beforeWindow,
                reason: "window_scroll",
                beforeWindow,
                afterWindow,
            }};
        }})()
    """)
    publisher._sleep(0.8, minimum_seconds=0.35)
    return bool(result.get("ok")) if isinstance(result, dict) else bool(result)


def _extract_search_feeds_from_dom(publisher: XiaohongshuPublisher) -> list[dict]:
    """从当前搜索结果 DOM 中提取已渲染的笔记卡片。"""
    raw = publisher._evaluate("""
        (() => {
            const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
            const noteIdFromHref = (href) => {
                try {
                    const url = new URL(href, window.location.origin);
                    const match = url.pathname.match(/^\\/explore\\/([^/?#]+)/);
                    return match ? {
                        id: match[1],
                        xsecToken: url.searchParams.get("xsec_token") || "",
                    } : null;
                } catch (_) {
                    return null;
                }
            };
            const output = [];
            const seen = new Set();
            const cards = Array.from(
                document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
            );

            for (const card of cards) {
                if (!(card instanceof HTMLElement)) continue;
                const rawIndex = card.getAttribute("data-index");
                const domIndex = rawIndex && /^\\d+$/.test(rawIndex)
                    ? Number(rawIndex)
                    : null;
                const links = Array.from(card.querySelectorAll(
                    'a[href^="/explore/"], a[href*="/explore/"]'
                ));
                const parsed = links
                    .map((link) => noteIdFromHref(link.getAttribute("href") || link.href))
                    .find(Boolean);
                if (!parsed || !parsed.id || seen.has(parsed.id)) continue;
                seen.add(parsed.id);

                const titleEl = card.querySelector(
                    ".title, [class*='title'], [class*='desc'], [class*='footer']"
                );
                const title = normalize(
                    titleEl ? titleEl.textContent : card.textContent
                ) || "未命名笔记";
                const commentEl = card.querySelector(
                    "[class*='comment'] .count, [class*='chat'] .count, [class*='comment']"
                );
                const commentCount = normalize(commentEl ? commentEl.textContent : "");

                output.push({
                    id: parsed.id,
                    xsecToken: parsed.xsecToken,
                    _domIndex: domIndex,
                    _commentCountUnknown: !commentCount,
                    noteCard: {
                        displayTitle: title,
                        interactInfo: { commentCount: commentCount || "0" },
                        user: { xsecToken: parsed.xsecToken },
                    },
                });
            }
            return JSON.stringify(output);
        })()
    """)
    if raw and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _merge_feeds(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """按 ID 合并笔记列表，并补齐 DOM 定位信息。"""
    merged: list[dict] = []
    by_id: dict[str, dict] = {}
    for feed in existing + incoming:
        feed_id = str(feed.get("id", "")).strip()
        if not feed_id:
            continue

        if feed_id not in by_id:
            copied = dict(feed)
            by_id[feed_id] = copied
            merged.append(copied)
            continue

        target = by_id[feed_id]
        if target.get("_domIndex") is None and feed.get("_domIndex") is not None:
            target["_domIndex"] = feed.get("_domIndex")
        if not target.get("xsecToken") and feed.get("xsecToken"):
            target["xsecToken"] = feed.get("xsecToken")

        target_card = target.get("noteCard", {})
        incoming_card = feed.get("noteCard", {})
        if isinstance(target_card, dict) and isinstance(incoming_card, dict):
            if not target_card.get("displayTitle") and incoming_card.get("displayTitle"):
                target_card["displayTitle"] = incoming_card.get("displayTitle")
            target_info = target_card.get("interactInfo", {})
            incoming_info = incoming_card.get("interactInfo", {})
            if isinstance(target_info, dict) and isinstance(incoming_info, dict):
                current_count = str(target_info.get("commentCount", "")).strip()
                incoming_count = str(incoming_info.get("commentCount", "")).strip()
                if current_count in ("", "0") and incoming_count not in ("", "0"):
                    target_info["commentCount"] = incoming_count
                    target["_commentCountUnknown"] = False
    return merged


def _sort_feeds_by_dom_index(feeds: list[dict]) -> list[dict]:
    """按真实 data-index 排序；没有 data-index 的记录保持在后面。"""
    indexed = list(enumerate(feeds))

    def sort_key(item: tuple[int, dict]) -> tuple[int, int]:
        original_index, feed = item
        dom_index = feed.get("_domIndex")
        if isinstance(dom_index, int):
            return (0, dom_index)
        return (1, original_index)

    return [feed for _, feed in sorted(indexed, key=sort_key)]


def _reset_search_results_scroll(publisher: XiaohongshuPublisher):
    """回到搜索结果页顶部，方便从第一篇开始逐条处理。"""
    publisher._evaluate("""
        (() => {
            const roots = [
                document.scrollingElement,
                document.documentElement,
                document.body,
                ...document.querySelectorAll(
                    "main, [class*='search'], [class*='feeds'], [class*='waterfall'], [class*='container']"
                )
            ].filter(Boolean);
            for (const root of roots) {
                if (root.scrollHeight > root.clientHeight + 40) {
                    root.scrollTo({ top: 0, behavior: "instant" });
                }
            }
            return true;
        })()
    """)
    publisher._sleep(1.0, minimum_seconds=0.5)


def load_more_search_feeds(
    publisher: XiaohongshuPublisher,
    feeds: list[dict],
    target_count: int,
    max_scrolls: int = 12,
) -> tuple[list[dict], int]:
    """滚动搜索结果页，尽量加载到 target_count 篇笔记后回到顶部。"""
    if target_count <= len(feeds):
        feeds = _sort_feeds_by_dom_index(
            _merge_feeds(feeds, _extract_search_feeds_from_dom(publisher))
        )
        return feeds, 0

    merged = _merge_feeds(feeds, _extract_search_feeds_from_dom(publisher))
    if len(merged) >= target_count:
        _reset_search_results_scroll(publisher)
        return _sort_feeds_by_dom_index(merged), 0

    print(
        f"  -> 当前仅 {len(merged)} 篇，目标 {target_count} 篇，"
        f"开始滚动加载更多搜索结果..."
    )
    stagnant_rounds = 0
    scroll_count = 0
    for _ in range(max_scrolls):
        before = len(merged)
        _scroll_search_page(publisher, pixels=random.randint(900, 1300))
        scroll_count += 1
        dom_feeds = _extract_search_feeds_from_dom(publisher)
        merged = _merge_feeds(merged, dom_feeds)

        if len(merged) >= target_count:
            break
        if len(merged) == before:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        if stagnant_rounds >= 3:
            break

    if len(merged) > len(feeds):
        print(f"  -> 加载后可处理笔记增至 {len(merged)} 篇。")
    else:
        print("  -> 滚动后没有加载到更多笔记。")
    print(f"  -> 搜索结果补加载滚动 {scroll_count} 次，回到页首后开始处理。")
    _reset_search_results_scroll(publisher)
    return _sort_feeds_by_dom_index(merged), scroll_count


def _card_rect_js(feed_id: str) -> str:
    """返回定位指定笔记卡片点击区域的 JS 片段。"""
    return f"""
        (() => {{
            const feedId = "{feed_id}";
            const noteIdFromHref = (href) => {{
                try {{
                    const url = new URL(href, window.location.origin);
                    const match = url.pathname.match(/^\\/explore\\/([^/?#]+)/);
                    return match ? match[1] : "";
                }} catch (_) {{
                    return "";
                }}
            }};
            const visibleRect = (el) => {{
                if (!(el instanceof HTMLElement)) return null;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return null;
                if (rect.bottom <= 0 || rect.top >= window.innerHeight) return null;
                if (rect.right <= 0 || rect.left >= window.innerWidth) return null;
                return rect;
            }};

            for (const card of document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")) {{
                const links = Array.from(card.querySelectorAll('a[href^="/explore/"], a[href*="/explore/"]'));
                if (!links.some((link) => noteIdFromHref(link.getAttribute("href") || link.href) === feedId)) {{
                    continue;
                }}

                const cover = links.find((link) => {{
                    if (!(link instanceof HTMLElement)) return false;
                    if (!link.classList.contains("cover")) return false;
                    return noteIdFromHref(link.getAttribute("href") || link.href) === feedId;
                }});
                const rect = visibleRect(cover) || visibleRect(card);
                if (rect) {{
                    return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                }}
            }}
            return null;
        }})()
    """


def _card_rect_by_index_js(feed_index: int) -> str:
    """返回当前搜索结果 DOM 中指定序号卡片的点击区域。"""
    return f"""
        (() => {{
            const targetIndex = {feed_index};
            const visibleRect = (el) => {{
                if (!(el instanceof HTMLElement)) return null;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return null;
                if (rect.bottom <= 0 || rect.top >= window.innerHeight) return null;
                if (rect.right <= 0 || rect.left >= window.innerWidth) return null;
                return rect;
            }};
            const cards = Array.from(
                document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
            );
            const card = cards.find((node) => (
                node instanceof HTMLElement &&
                node.getAttribute("data-index") === String(targetIndex)
            )) || cards[targetIndex];
            if (!(card instanceof HTMLElement)) return null;

            const cover = Array.from(card.querySelectorAll("a.cover, a[href*='/explore/']"))
                .find((node) => visibleRect(node));
            const rect = visibleRect(cover) || visibleRect(card);
            if (rect) {{
                return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
            }}
            return null;
        }})()
    """


def _card_visibility_by_index(
    publisher: XiaohongshuPublisher,
    feed_index: int,
) -> dict:
    """检查目标 data-index 是否已渲染，以及是否在当前视口可点击。"""
    result = publisher._evaluate(f"""
        (() => {{
            const targetIndex = {feed_index};
            const visibleRect = (el) => {{
                if (!(el instanceof HTMLElement)) return null;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return null;
                if (rect.bottom <= 0 || rect.top >= window.innerHeight) return null;
                if (rect.right <= 0 || rect.left >= window.innerWidth) return null;
                const clickX = rect.x + rect.width / 2;
                const clickY = rect.y + rect.height / 2;
                return {{
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    clickX,
                    clickY,
                    centerInViewport: (
                        clickX >= 0 &&
                        clickX <= window.innerWidth &&
                        clickY >= 0 &&
                        clickY <= window.innerHeight
                    ),
                }};
            }};
            const cards = Array.from(
                document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
            );
            const card = cards.find((node) => (
                node instanceof HTMLElement &&
                node.getAttribute("data-index") === String(targetIndex)
            ));
            if (!(card instanceof HTMLElement)) {{
                return {{
                    exists: false,
                    clickable: false,
                    rect: null,
                    viewport: {{
                        width: window.innerWidth,
                        height: window.innerHeight,
                    }},
                }};
            }}

            const cover = Array.from(card.querySelectorAll("a.cover, a[href*='/explore/']"))
                .find((node) => visibleRect(node));
            const rect = visibleRect(cover) || visibleRect(card);
            return {{
                exists: true,
                clickable: !!(rect && rect.centerInViewport),
                rect,
                viewport: {{
                    width: window.innerWidth,
                    height: window.innerHeight,
                }},
            }};
        }})()
    """)
    return result if isinstance(result, dict) else {
        "exists": False,
        "clickable": False,
        "rect": None,
        "viewport": None,
    }


def _format_card_visibility(state: dict) -> str:
    """格式化卡片定位状态，便于日志排查坐标问题。"""
    rect = state.get("rect")
    viewport = state.get("viewport")
    rect_text = "None"
    viewport_text = "None"
    if isinstance(rect, dict):
        rect_text = (
            f"x={rect.get('x')}, y={rect.get('y')}, "
            f"w={rect.get('width')}, h={rect.get('height')}, "
            f"clickX={rect.get('clickX')}, clickY={rect.get('clickY')}, "
            f"centerInViewport={rect.get('centerInViewport')}"
        )
    if isinstance(viewport, dict):
        viewport_text = (
            f"w={viewport.get('width')}, h={viewport.get('height')}"
        )
    return (
        f"exists={bool(state.get('exists'))}, "
        f"clickable={bool(state.get('clickable'))}, "
        f"rect=({rect_text}), viewport=({viewport_text})"
    )


def _center_card_by_index_in_dom(
    publisher: XiaohongshuPublisher,
    feed_index: int,
) -> bool:
    """把当前搜索结果 DOM 中指定序号卡片滚到视口中间。"""
    return bool(publisher._evaluate(f"""
        (() => {{
            const cards = Array.from(
                document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
            );
            const targetIndex = {feed_index};
            const card = cards.find((node) => (
                node instanceof HTMLElement &&
                node.getAttribute("data-index") === String(targetIndex)
            )) || cards[targetIndex];
            if (!(card instanceof HTMLElement)) return false;
            const cover = card.querySelector("a.cover, a[href*='/explore/']");
            const target = cover instanceof HTMLElement ? cover : card;
            target.scrollIntoView({{
                behavior: "instant",
                block: "center",
                inline: "center"
            }});
            return true;
        }})()
    """))


def _visible_search_data_index_range(
    publisher: XiaohongshuPublisher,
) -> tuple[int | None, int | None]:
    """返回当前搜索结果 DOM 中 note-item 的 data-index 范围。"""
    result = publisher._evaluate("""
        (() => {
            const indexes = Array.from(
                document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
            )
                .map((node) => node instanceof HTMLElement ? node.getAttribute("data-index") : "")
                .filter((value) => value && /^\\d+$/.test(value))
                .map((value) => Number(value));
            if (!indexes.length) return null;
            return { min: Math.min(...indexes), max: Math.max(...indexes) };
        })()
    """)
    if not isinstance(result, dict):
        return None, None
    min_index = result.get("min")
    max_index = result.get("max")
    if not isinstance(min_index, int) or not isinstance(max_index, int):
        return None, None
    return min_index, max_index


def _bring_search_card_index_into_dom(
    publisher: XiaohongshuPublisher,
    feed_index: int,
    replay_scrolls: int = 0,
) -> bool:
    """从页首重放补加载滚动次数，直到目标 data-index 出现在 DOM 中。"""
    def centered_and_clickable() -> bool:
        before = _card_visibility_by_index(publisher, feed_index)
        if before.get("clickable"):
            return True
        if not _center_card_by_index_in_dom(publisher, feed_index):
            return False
        publisher._sleep(0.8, minimum_seconds=0.4)
        after = _card_visibility_by_index(publisher, feed_index)
        return bool(after.get("clickable"))

    def nudge_until_clickable(max_nudges: int = 3) -> bool:
        for _ in range(max_nudges):
            state = _card_visibility_by_index(publisher, feed_index)
            if state.get("clickable"):
                return True
            rect = state.get("rect")
            viewport = state.get("viewport")
            if not isinstance(rect, dict) or not isinstance(viewport, dict):
                return False

            click_y = float(rect.get("clickY") or 0)
            viewport_h = float(viewport.get("height") or 0)
            if viewport_h <= 0:
                return False

            if click_y > viewport_h:
                delta = min(max(click_y - viewport_h + 80, 160), 420)
                print(
                    f"    -> [调试] 卡片中心 y={click_y:.1f} 超出视口 "
                    f"h={viewport_h:.1f}，下滚 {delta:.0f}px 后再点击。"
                )
                if not _scroll_feeds_container_area(publisher, pixels=int(delta)):
                    return False
            elif click_y < 0:
                delta = min(max(abs(click_y) + 80, 160), 420)
                print(
                    f"    -> [调试] 卡片中心 y={click_y:.1f} 在视口上方，"
                    f"上滚 {delta:.0f}px 后再点击。"
                )
                if not _scroll_feeds_container_area(publisher, pixels=-int(delta)):
                    return False
            else:
                return False
        return bool(_card_visibility_by_index(publisher, feed_index).get("clickable"))

    if replay_scrolls <= 0:
        return False

    print(
        f"    -> [调试] 从页首重放 {replay_scrolls} 次补加载滚动，"
        f"定位 data-index={feed_index}。"
    )
    _reset_search_results_scroll(publisher)
    if centered_and_clickable() or nudge_until_clickable():
        return True

    for step in range(replay_scrolls):
        did_scroll = _scroll_feeds_container_area(publisher, pixels=950)
        visible_state = _card_visibility_by_index(publisher, feed_index)
        if centered_and_clickable() or nudge_until_clickable():
            return True
        min_index, max_index = _visible_search_data_index_range(publisher)
        print(
            f"    -> [调试] 重放滚动 {step + 1}/{replay_scrolls} 后，"
            f"当前窗口 data-index={min_index}-{max_index}，"
            f"目标状态: {_format_card_visibility(visible_state)}，"
            f"滚动{'成功' if did_scroll else '未生效'}。"
        )
        if not did_scroll and min_index is None and max_index is None:
            return False

    return False


def _find_card_in_dom(publisher: XiaohongshuPublisher, feed_id: str) -> bool:
    """检查 feed_id 对应的可点击卡片是否在当前可见 DOM 中。"""
    return bool(publisher._evaluate(_card_rect_js(feed_id)))


def _center_card_in_dom(publisher: XiaohongshuPublisher, feed_id: str) -> bool:
    """把目标笔记卡片滚到视口中间。"""
    return bool(publisher._evaluate(f"""
        (() => {{
            const feedId = "{feed_id}";
            const noteIdFromHref = (href) => {{
                try {{
                    const url = new URL(href, window.location.origin);
                    const match = url.pathname.match(/^\\/explore\\/([^/?#]+)/);
                    return match ? match[1] : "";
                }} catch (_) {{
                    return "";
                }}
            }};
            for (const card of document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")) {{
                const links = Array.from(card.querySelectorAll('a[href^="/explore/"], a[href*="/explore/"]'));
                if (!links.some((link) => noteIdFromHref(link.getAttribute("href") || link.href) === feedId)) {{
                    continue;
                }}

                const cover = links.find((link) => (
                    link instanceof HTMLElement &&
                    link.classList.contains("cover") &&
                    noteIdFromHref(link.getAttribute("href") || link.href) === feedId
                ));
                const target = cover || card;
                if (target instanceof HTMLElement) {{
                    target.scrollIntoView({{ behavior: "instant", block: "center", inline: "center" }});
                    return true;
                }}
            }}
            return false;
        }})()
    """))


def _seek_card_by_scrolling(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    max_steps: int = 24,
) -> bool:
    """通过真实滚动搜索结果容器，让虚拟列表把目标卡片渲染出来。"""
    if _find_card_in_dom(publisher, feed_id):
        return True
    if _center_card_in_dom(publisher, feed_id):
        publisher._sleep(1.0, minimum_seconds=0.5)
        if _find_card_in_dom(publisher, feed_id):
            return True

    for step in range(max_steps):
        _scroll_search_page(publisher, pixels=420 + (step % 3) * 120)
        if _find_card_in_dom(publisher, feed_id):
            return True

    publisher._evaluate("""
        (() => {
            const roots = [
                document.scrollingElement,
                document.documentElement,
                document.body,
                ...document.querySelectorAll("main, [class*='search'], [class*='feeds'], [class*='waterfall'], [class*='container'], div")
            ];
            for (const root of roots) {
                if (root && root.scrollHeight > root.clientHeight + 40) {
                    root.scrollTo({ top: 0, behavior: "instant" });
                }
            }
            return true;
        })()
    """)
    publisher._sleep(1.2, minimum_seconds=0.6)

    for step in range(max_steps):
        if _find_card_in_dom(publisher, feed_id):
            return True
        _scroll_search_page(publisher, pixels=420 + (step % 3) * 120)
    visible_ids = publisher._evaluate("""
        (() => {
            const noteIdFromHref = (href) => {
                try {
                    const url = new URL(href, window.location.origin);
                    const match = url.pathname.match(/^\\/explore\\/([^/?#]+)/);
                    return match ? match[1] : "";
                } catch (_) {
                    return "";
                }
            };
            const ids = [];
            const seen = new Set();
            for (const card of document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")) {
                const rect = card.getBoundingClientRect();
                if (rect.bottom <= 0 || rect.top >= window.innerHeight) continue;
                const link = Array.from(card.querySelectorAll('a[href^="/explore/"], a[href*="/explore/"]'))
                    .find((node) => noteIdFromHref(node.getAttribute("href") || node.href));
                const id = link ? noteIdFromHref(link.getAttribute("href") || link.href) : "";
                if (!id || seen.has(id)) continue;
                seen.add(id);
                ids.push(id);
                if (ids.length >= 12) break;
            }
            return ids;
        })()
    """)
    print(f"    -> [调试] 未定位到目标卡片，可见笔记ID: {visible_ids}")
    return False


def click_note_card(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    feed_index: int = 0,
    replay_scrolls: int = 0,
    force_replay: bool = False,
) -> bool:
    """通过 CDP 鼠标点击已加载搜索结果中的笔记卡片封面。"""
    def nudge_index_click_center_into_view(max_nudges: int = 3) -> bool:
        for _ in range(max_nudges):
            state = _card_visibility_by_index(publisher, feed_index)
            if state.get("clickable"):
                return True
            rect = state.get("rect")
            viewport = state.get("viewport")
            if not isinstance(rect, dict) or not isinstance(viewport, dict):
                return False

            click_y = float(rect.get("clickY") or 0)
            viewport_h = float(viewport.get("height") or 0)
            if viewport_h <= 0:
                return False

            if click_y > viewport_h:
                delta = min(max(click_y - viewport_h + 80, 160), 420)
                print(
                    f"    -> [调试] 点击中心 y={click_y:.1f} 超出视口 "
                    f"h={viewport_h:.1f}，下滚 {delta:.0f}px 后再点击。"
                )
                if not _scroll_feeds_container_area(publisher, pixels=int(delta)):
                    return False
            elif click_y < 0:
                delta = min(max(abs(click_y) + 80, 160), 420)
                print(
                    f"    -> [调试] 点击中心 y={click_y:.1f} 在视口上方，"
                    f"上滚 {delta:.0f}px 后再点击。"
                )
                if not _scroll_feeds_container_area(publisher, pixels=-int(delta)):
                    return False
            else:
                return False
        return bool(_card_visibility_by_index(publisher, feed_index).get("clickable"))

    if feed_index >= 0:
        initial_visibility = _card_visibility_by_index(publisher, feed_index)
        print(
            f"    -> [调试] 点击前 data-index={feed_index} "
            f"卡片状态: {_format_card_visibility(initial_visibility)}"
        )
        if not initial_visibility.get("clickable") and initial_visibility.get("exists"):
            nudge_index_click_center_into_view()
        if (
            not force_replay
            and _card_visibility_by_index(publisher, feed_index).get("clickable")
        ):
            for retry in range(3):
                try:
                    publisher._click_element_by_cdp(
                        "note card cover by index",
                        _card_rect_by_index_js(feed_index),
                    )
                    return True
                except Exception as e:
                    if retry < 2:
                        print(
                            f"    -> [调试] 按序号点击卡片失败"
                            f"（重试 {retry + 1}/2）: {e}"
                        )
                        publisher._sleep(0.8, minimum_seconds=0.4)
                    else:
                        print(f"    -> [调试] 按序号点击卡片失败: {e}")
        elif _bring_search_card_index_into_dom(
            publisher,
            feed_index,
            replay_scrolls=replay_scrolls,
        ):
            publisher._sleep(0.8, minimum_seconds=0.4)
            if publisher._evaluate(_card_rect_by_index_js(feed_index)):
                try:
                    publisher._click_element_by_cdp(
                        "note card cover by centered index",
                        _card_rect_by_index_js(feed_index),
                    )
                    return True
                except Exception as e:
                    print(f"    -> [调试] 按序号居中后点击失败: {e}")
        print("    -> [调试] 已加载范围内未定位到该序号卡片。")
        return False

    # 只保留无序号调用的旧 ID 定位兜底。主流程逐条处理时不使用，
    # 避免在已凑够 post_per_keyword 后继续滚动加载搜索结果。
    if not _seek_card_by_scrolling(publisher, feed_id):
        return False

    scroll_ok = _center_card_in_dom(publisher, feed_id)
    if not scroll_ok:
        return False
    publisher._sleep(1.5, minimum_seconds=0.8)

    for retry in range(3):
        try:
            publisher._click_element_by_cdp("note card cover", _card_rect_js(feed_id))
            return True
        except Exception as e:
            if retry < 2:
                print(f"    -> [调试] CDP 点击卡片失败（重试 {retry + 1}/2）: {e}")
                publisher._sleep(1.0, minimum_seconds=0.5)
                _center_card_in_dom(publisher, feed_id)
                publisher._sleep(1.0, minimum_seconds=0.5)
            else:
                print(f"    -> [调试] CDP 点击卡片失败: {e}")
                return False


def wait_for_detail_state(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    timeout: float = 10.0,
) -> bool:
    """等待笔记详情数据加载，兼容搜索页内详情浮层。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = publisher._evaluate(f"""
            (() => {{
                const feedId = "{feed_id}";
                const state = window.__INITIAL_STATE__;
                if (state && state.note && state.note.noteDetailMap) {{
                    const map = state.note.noteDetailMap;
                    if (map[feedId] || Object.keys(map).length > 0) {{
                        return true;
                    }}
                }}

                if (window.location.href.includes(feedId)) {{
                    return true;
                }}

                const selectors = [
                    ".note-detail-mask",
                    ".note-detail",
                    "[class*='note-detail']",
                    ".comments-container",
                    "[class*='comments-container']",
                    ".comment-item",
                    ".parent-comment",
                    "[class*='comment-item']",
                    "[class*='parent-comment']",
                ];
                for (const selector of selectors) {{
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (!(node instanceof HTMLElement)) continue;
                        const rect = node.getBoundingClientRect();
                        if (rect.width > 20 && rect.height > 20) {{
                            return true;
                        }}
                    }}
                }}
                return false;
            }})()
        """)
        if ready:
            return True
        publisher._sleep(0.5, minimum_seconds=0.2)
    return False


def _ensure_comments_visible(publisher: XiaohongshuPublisher):
    """把详情页右侧滚动区域滚到评论区起始位置。"""
    publisher._evaluate("""
        (() => {
            const visible = (el) => {
                if (!(el instanceof HTMLElement)) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 20
                    && rect.height > 20
                    && rect.bottom > 0
                    && rect.top < window.innerHeight
                    && rect.right > 0
                    && rect.left < window.innerWidth;
            };
            const scroller = Array.from(document.querySelectorAll(
                ".note-scroller, [class*='note-scroller']"
            )).find(visible);
            const root = Array.from(document.querySelectorAll(
                ".comments-container, [class*='comments-container']"
            )).find(visible) || document.querySelector(
                ".comments-container, [class*='comments-container']"
            );
            if (scroller instanceof HTMLElement && root instanceof HTMLElement) {
                const scrollerRect = scroller.getBoundingClientRect();
                const rootRect = root.getBoundingClientRect();
                const targetTop = scroller.scrollTop
                    + rootRect.top
                    - scrollerRect.top
                    - 24;
                scroller.scrollTo({
                    top: Math.max(targetTop, 0),
                    behavior: "instant"
                });
            } else if (root instanceof HTMLElement) {
                root.scrollIntoView({ behavior: "instant", block: "center" });
            }
        })()
    """)
    publisher._sleep(1.0, minimum_seconds=0.4)


def extract_comments_from_dom(
    publisher: XiaohongshuPublisher,
    ensure_visible: bool = True,
) -> list[dict]:
    """从当前页面浮层/详情页 DOM 中提取评论列表（排除作者评论）。"""
    if ensure_visible:
        _ensure_comments_visible(publisher)

    raw = publisher._evaluate("""
        (() => {
            const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
            const results = [];
            const seen = new Set();
            const itemSelectors = [
                '.comment-item',
                '.parent-comment',
                "[class*='comment-item']",
                "[class*='parent-comment']",
            ].join(',');
            const items = document.querySelectorAll(itemSelectors);
            for (const item of items) {
                if (!(item instanceof HTMLElement)) continue;
                const rect = item.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 12) continue;

                const rawId = (
                    item.id ||
                    item.getAttribute('data-comment-id') ||
                    item.getAttribute('comment-id') ||
                    item.dataset.commentId ||
                    ''
                );
                const id = rawId.replace(/^comment-/, '');

                const nameEl = item.querySelector(
                    'a.name, .name, [class*="name"], [class*="author"]'
                );
                const textEl = item.querySelector(
                    '.note-text, [class*="note-text"], [class*="content"], [class*="text"]'
                );
                const content = normalize(textEl ? textEl.textContent : item.textContent);
                if (!content) continue;
                if (content.length < 2 || content.length > 500) continue;

                const locationEl = item.querySelector(
                    '.date .location, .location, [class*="location"]'
                );
                const province = normalize(locationEl ? locationEl.textContent : '');

                const key = id || content.slice(0, 80);
                if (seen.has(key)) continue;
                seen.add(key);

                const tagEl = item.querySelector('.tag');
                const isAuthor = tagEl && normalize(tagEl.textContent) === '作者';

                results.push({
                    id: id,
                    content: content,
                    province: province,
                    is_author: !!isAuthor,
                    userInfo: { nickname: nameEl ? nameEl.textContent.trim() : '' }
                });
            }
            return JSON.stringify(results);
        })()
    """)
    if raw and isinstance(raw, str):
        try:
            comments = json.loads(raw)
            return [c for c in comments if not c.get("is_author")]
        except json.JSONDecodeError:
            pass
    return []


def _comment_key(comment: dict) -> str:
    """生成跨滚动去重用的评论 key。"""
    comment_id = str(comment.get("id", "")).strip()
    if comment_id:
        return f"id:{comment_id}"
    content = str(comment.get("content", "")).strip()
    nickname = str(comment.get("userInfo", {}).get("nickname", "")).strip()
    province = str(comment.get("province", "")).strip()
    return f"text:{nickname}:{province}:{content[:80]}"


def _scroll_comments_area(
    publisher: XiaohongshuPublisher,
    direction: str = "down",
    pixels: int = 900,
) -> bool:
    """滚动详情页右侧 note-scroller 区域，触发更多评论加载。"""
    sign = -1 if direction == "up" else 1
    did_scroll = bool(publisher._evaluate(f"""
        (() => {{
            const delta = {pixels * sign};
            const visible = (el) => {{
                if (!(el instanceof HTMLElement)) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 20
                    && rect.height > 20
                    && rect.bottom > 0
                    && rect.top < window.innerHeight
                    && rect.right > 0
                    && rect.left < window.innerWidth;
            }};
            const target = Array.from(document.querySelectorAll(
                ".note-scroller, [class*='note-scroller']"
            )).find((el) => {{
                if (!visible(el)) return false;
                return el.scrollHeight > el.clientHeight + 20;
            }});
            if (!(target instanceof HTMLElement)) {{
                return false;
            }}

            const commentsRoot = target.querySelector(
                ".comments-container, [class*='comments-container']"
            );
            if (!(commentsRoot instanceof HTMLElement)) {{
                return false;
            }}

            const before = target.scrollTop;
            target.scrollBy({{ top: delta, behavior: "instant" }});
            target.dispatchEvent(new WheelEvent("wheel", {{
                deltaY: delta,
                bubbles: true,
                cancelable: true,
            }}));
            return target.scrollTop !== before;
        }})()
    """))
    if did_scroll:
        publisher._sleep(1.0, minimum_seconds=0.5)
    else:
        publisher._sleep(0.4, minimum_seconds=0.2)
    return did_scroll


def collect_comments_from_dom(
    publisher: XiaohongshuPublisher,
    target_count: int = 100,
    max_scrolls: int = 18,
) -> list[dict]:
    """滚动评论区并累积提取评论，最多返回前 target_count 条。"""
    collected: list[dict] = []
    seen: set[str] = set()
    stagnant_rounds = 0

    for step in range(max_scrolls + 1):
        current = extract_comments_from_dom(publisher, ensure_visible=(step == 0))
        before = len(collected)
        for comment in current:
            key = _comment_key(comment)
            if key in seen:
                continue
            seen.add(key)
            collected.append(comment)
            if len(collected) >= target_count:
                return collected[:target_count]

        if len(collected) == before:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

        if step >= max_scrolls or stagnant_rounds >= 4:
            break
        if not _scroll_comments_area(publisher, direction="down"):
            break

    return collected[:target_count]


def ensure_comment_visible(
    publisher: XiaohongshuPublisher,
    target_comment: dict,
    max_scrolls: int = 18,
) -> bool:
    """回复前把已选中的评论滚回当前 DOM 可见区域。"""
    comment_id = str(target_comment.get("id", "")).strip()
    content = str(target_comment.get("content", "")).strip()
    snippet = content[:60]
    id_literal = json.dumps(comment_id, ensure_ascii=False)
    snippet_literal = json.dumps(snippet, ensure_ascii=False)

    find_js = f"""
        (() => {{
            const targetId = {id_literal};
            const snippet = {snippet_literal};
            const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
            const items = document.querySelectorAll(
                '.comment-item, .parent-comment, [class*="comment-item"], [class*="parent-comment"]'
            );
            for (const item of items) {{
                if (!(item instanceof HTMLElement)) continue;
                const rawId = (
                    item.id ||
                    item.getAttribute('data-comment-id') ||
                    item.getAttribute('comment-id') ||
                    item.dataset.commentId ||
                    ''
                ).replace(/^comment-/, '');
                const text = normalize(item.textContent);
                const matched = targetId
                    ? rawId === targetId
                    : (snippet && text.includes(snippet));
                if (matched) {{
                    item.scrollIntoView({{
                        behavior: "instant",
                        block: "center",
                        inline: "nearest"
                    }});
                    return true;
                }}
            }}
            return false;
        }})()
    """

    if publisher._evaluate(find_js):
        publisher._sleep(0.5, minimum_seconds=0.2)
        return True

    for direction in ("up", "down"):
        for _ in range(max_scrolls):
            if not _scroll_comments_area(publisher, direction=direction):
                break
            if publisher._evaluate(find_js):
                publisher._sleep(0.5, minimum_seconds=0.2)
                return True
    return False


def close_detail_overlay(publisher: XiaohongshuPublisher):
    """关闭笔记详情浮层或从详情页返回，回到搜索结果列表。"""
    publisher._evaluate("window.history.back();")
    publisher._sleep(1.5, minimum_seconds=0.5)


# ============================================================
#  评论数解析
# ============================================================

def parse_comment_count(count_str: str) -> int:
    """解析小红书的评论数显示文本（如 '1.2w'、'3k'、'100+'）为整数。"""
    count_str = str(count_str).strip().lower()
    try:
        if "w" in count_str:
            return int(float(count_str.replace("w", "")) * 10000)
        elif "k" in count_str:
            return int(float(count_str.replace("k", "")) * 1000)
        elif "+" in count_str:
            return int(count_str.replace("+", ""))
        elif count_str.isdigit():
            return int(count_str)
    except (ValueError, TypeError):
        pass
    return 0


# ============================================================
#  结果保存
# ============================================================

def _save_results(
    profile_name: str,
    all_responses: list[dict],
    total_replies: int,
    total_skipped: int,
    round_number: int,
    is_final: bool = False,
):
    """保存当前累计的回复结果到 JSON 文件。"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(COMMENT_RESPONSES_DIR, exist_ok=True)
    result_file = os.path.join(
        COMMENT_RESPONSES_DIR,
        f"comment_responses_{profile_name}_{timestamp}.json",
    )

    label = "最终" if is_final else f"第{round_number}轮"
    print(f"\n{'=' * 60}")
    print(f"  {label}结果保存")
    print(f"  累计成功回复: {total_replies} 条")
    print(f"  累计跳过: {total_skipped} 条")
    print(f"  结果文件: {result_file}")
    print(f"{'=' * 60}")

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)


# ============================================================
#  主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="小红书评论自动回复机器人（通用版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python prod/general_comment_bot.py drone\n"
            "  python prod/general_comment_bot.py medical_beauty\n"
        ),
    )
    parser.add_argument(
        "profile",
        help="profile 名称（对应 prod/profiles/<profile>.json）",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="账号名（对应 prod/account_state/<name>.json + Chrome profile）；"
        "省略则用 account_manager 的默认账号",
    )
    args = parser.parse_args()
    profile_name = args.profile
    account = args.account or get_default_account()

    # --- 加载 profile ---
    try:
        profile = load_profile(profile_name)
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    service_name = profile.get("service_name", profile_name)
    target_provinces = get_target_provinces(profile)

    print("=" * 60)
    print(f"  小红书评论自动回复机器人 (通用版 · 无限模式)")
    print(f"  当前业务: {service_name} [profile={profile_name}]")
    print(f"  账号: {account}")
    if target_provinces:
        print(f"  目标省份: {', '.join(target_provinces)}")
    print("  按 Ctrl+C 可随时安全停止")
    print("=" * 60)

    # 起步即检查账号 state，已永久退役直接退出
    state = account_state.load(account)
    if state.get("frozen_until") == account_state.PERMANENT_FREEZE_ISO:
        print(f"⛔ 账号 {account} 已永久退役，bot 拒绝启动。")
        sys.exit(1)
    allowed, reason = account_state.can_send(account)
    if not allowed:
        print(f"⚠️ 账号 {account} 当前不可发送：{reason}（bot 仍会启动并等待间隔/冻结到期）")

    # 按账号粒度的 single-instance 锁；防止两个 bot 同时操作同一账号导致双发
    _lock_ctx = single_instance(f"xhs_account_{account}")
    try:
        _lock_ctx.__enter__()
    except SingleInstanceError as e:
        print(f"⛔ {e}")
        sys.exit(1)
    import atexit
    atexit.register(_lock_ctx.__exit__, None, None, None)

    # --- 读取配置 ---
    config = profile.get("config", {})
    min_comment_count = config.get("min_comment_count", 5)
    analyze_comment_count = config.get("analyze_comment_count", 10)
    post_per_keyword = config.get("post_per_keyword", 10)
    keyword_delay_min = config.get("keyword_delay_min", 8)
    keyword_delay_max = config.get("keyword_delay_max", 15)
    post_delay_min = config.get("post_delay_min", 2)
    post_delay_max = config.get("post_delay_max", 5)
    round_delay_min = config.get("round_delay_min", 15)
    round_delay_max = config.get("round_delay_max", 30)

    # --- 初始化 CDP ---
    print("\n[初始化] 正在连接 Chrome 浏览器...")
    publisher = XiaohongshuPublisher()
    try:
        publisher.connect()
    except Exception as e:
        print(f"[错误] 连接 CDP 失败: {e}")
        print("[提示] 请确保已启动 Chrome 并开启 --remote-debugging-port=9222")
        sys.exit(1)

    print("[初始化] 检查小红书登录状态...")
    if not publisher.check_home_login(wait_seconds=5.0):
        print("[错误] 未登录小红书网页版，请先登录。")
        sys.exit(1)
    print("[初始化] 登录状态正常 ✓\n")

    # --- 加载缓存 ---
    cache = load_cache(profile_name)
    print(f"[缓存] 已加载 {len(cache)} 条历史处理记录 (profile={profile_name})。\n")

    all_responses: list[dict] = []
    total_replies = 0
    total_skipped = 0
    round_number = 0

    try:
        while True:
            round_number += 1

            print(f"\n{'━' * 60}")
            print(f"  🔄 第 {round_number} 轮关键词生成")
            print(f"{'━' * 60}")

            keywords, source = get_next_keyword_batch(profile, round_number)

            if not keywords:
                print("  -> [警告] 未生成任何关键词，等待后重试...")
                time.sleep(random.uniform(10, 20))
                continue

            print(f"\n[关键词] 来源: {source}，共 {len(keywords)} 个：")
            for i, kw in enumerate(keywords, 1):
                print(f"  {i:3d}. {kw}")

            for kw_idx, keyword in enumerate(keywords, start=1):
                print(f"\n{'─' * 50}")
                print(f"[第{round_number}轮 {kw_idx}/{len(keywords)}] 🔍 搜索关键词: 「{keyword}」")
                print(f"{'─' * 50}")

                try:
                    search_results = publisher.search_feeds(keyword=keyword)
                except Exception as e:
                    err_msg = str(e).lower()
                    if any(k in err_msg for k in ("closed", "keepalive", "ping", "websocket", "1011")):
                        print(f"  -> [警告] WebSocket 连接断开 ({e})，尝试重连...")
                        try:
                            publisher._ensure_connected()
                            search_results = publisher.search_feeds(keyword=keyword)
                        except Exception as retry_e:
                            print(f"  -> [错误] 重连后搜索仍失败: {retry_e}")
                            continue
                    else:
                        print(f"  -> [错误] 搜索失败: {e}")
                        continue

                feeds = search_results.get("feeds", [])
                if not feeds:
                    print("  -> 未找到任何笔记，跳过。")
                    continue

                search_load_scrolls = 0
                if len(feeds) < post_per_keyword:
                    feeds, search_load_scrolls = load_more_search_feeds(
                        publisher,
                        feeds,
                        target_count=post_per_keyword,
                    )
                else:
                    feeds = _sort_feeds_by_dom_index(
                        _merge_feeds(
                            feeds,
                            _extract_search_feeds_from_dom(publisher),
                        )
                    )

                top_feeds = feeds[:post_per_keyword]
                print(f"  -> 找到 {len(feeds)} 篇笔记，处理前 {len(top_feeds)} 篇。\n")

                for feed_idx, feed in enumerate(top_feeds, start=1):
                    feed_id = feed.get("id")
                    xsec_token = (
                        feed.get("xsecToken")
                        or feed.get("noteCard", {}).get("user", {}).get("xsecToken")
                    )
                    title = feed.get("noteCard", {}).get("displayTitle", "未命名笔记")

                    print(f"  [{feed_idx}/{len(top_feeds)}] 📝 {title} (ID: {feed_id})")

                    if feed_id and feed_id in cache:
                        processed_at = cache[feed_id].get("processed_at", "未知")
                        print(f"    -> [跳过] 已于 {processed_at} 处理过。")
                        total_skipped += 1
                        continue

                    comment_count_str = str(
                        feed.get("noteCard", {})
                        .get("interactInfo", {})
                        .get("commentCount", "0")
                    )
                    comment_count = parse_comment_count(comment_count_str)
                    comment_count_unknown = bool(feed.get("_commentCountUnknown"))

                    if (
                        not target_provinces
                        and not comment_count_unknown
                        and comment_count < min_comment_count
                    ):
                        print(
                            f"    -> [跳过] 评论数不足 "
                            f"({comment_count} < {min_comment_count})。"
                        )
                        total_skipped += 1
                        continue

                    if not feed_id:
                        print("    -> [跳过] 无法获取 feed_id。")
                        total_skipped += 1
                        continue

                    print(
                        f"    -> 评论数: {'未知，打开详情后校验' if comment_count_unknown else comment_count}。"
                        f"正在打开笔记详情..."
                    )
                    feed_dom_index = (
                        feed.get("_domIndex")
                        if isinstance(feed.get("_domIndex"), int)
                        else feed_idx - 1
                    )

                    try:
                        if not click_note_card(
                            publisher,
                            feed_id,
                            feed_index=feed_dom_index,
                            replay_scrolls=search_load_scrolls,
                        ):
                            print("    -> [跳过] 已加载搜索结果范围内未定位到该笔记卡片。")
                            total_skipped += 1
                            continue

                        publisher._sleep(2.0, minimum_seconds=1.0)
                        if not wait_for_detail_state(publisher, feed_id, timeout=10.0):
                            print(
                                "    -> [警告] 详情加载超时，"
                                "从页首重放补加载滚动后重试打开..."
                            )
                            close_detail_overlay(publisher)
                            if not click_note_card(
                                publisher,
                                feed_id,
                                feed_index=feed_dom_index,
                                replay_scrolls=search_load_scrolls,
                                force_replay=True,
                            ):
                                print("    -> [跳过] 重放滚动后仍未定位到该笔记卡片。")
                                total_skipped += 1
                                continue
                            publisher._sleep(2.0, minimum_seconds=1.0)
                            if not wait_for_detail_state(publisher, feed_id, timeout=10.0):
                                print("    -> [跳过] 重试后详情仍加载超时。")
                                close_detail_overlay(publisher)
                                total_skipped += 1
                                continue

                        if target_provinces:
                            print("    -> 已配置目标省份，滚动加载前 100 条评论用于筛选...")
                            actual_comments = collect_comments_from_dom(
                                publisher,
                                target_count=100,
                            )
                        else:
                            actual_comments = extract_comments_from_dom(publisher)

                        candidate_comments = filter_comments_by_target_provinces(
                            actual_comments,
                            target_provinces,
                        )
                        if target_provinces:
                            skipped_by_province = (
                                len(actual_comments) - len(candidate_comments)
                            )
                            print(
                                f"    -> 省份过滤: 保留 {len(candidate_comments)} 条 "
                                f"({', '.join(target_provinces)})，"
                                f"忽略 {skipped_by_province} 条其他省份评论。"
                            )

                        if target_provinces:
                            if not candidate_comments:
                                print(
                                    f"    -> [跳过] 前 {len(actual_comments)} 条评论中"
                                    f"没有目标省份评论。"
                                )
                                close_detail_overlay(publisher)
                                total_skipped += 1
                                continue
                        elif len(candidate_comments) < min_comment_count:
                            print(
                                f"    -> [跳过] 可分析评论不足 "
                                f"({len(candidate_comments)} < {min_comment_count})。"
                            )
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        comments_to_analyze = (
                            candidate_comments
                            if target_provinces
                            else candidate_comments[:analyze_comment_count]
                        )
                        print(
                            f"    -> 提取到 {len(actual_comments)} 条评论，"
                            f"候选 {len(candidate_comments)} 条，"
                            f"取前 {len(comments_to_analyze)} 条送 LLM 分析..."
                        )

                        llm_result = evaluate_comments_with_llm(profile, comments_to_analyze)

                        if not llm_result:
                            print("    -> [跳过] LLM 分析失败。")
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        selected_idx = llm_result.get("selected_index", -1)

                        if selected_idx == -1:
                            print(
                                f"    -> [跳过] LLM 判定无业务意向。"
                                f"原因: {llm_result.get('reason', '未提供')}"
                            )
                            cache[feed_id] = {
                                "title": title,
                                "status": "no_intent",
                                "reason": llm_result.get("reason", ""),
                                "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            }
                            save_cache(profile_name, cache)
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        real_idx = selected_idx - 1
                        if not (0 <= real_idx < len(comments_to_analyze)):
                            print(
                                f"    -> [跳过] LLM 返回越界索引: {selected_idx}。"
                            )
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        target_comment = comments_to_analyze[real_idx]
                        user_name = target_comment.get("userInfo", {}).get(
                            "nickname", "未知用户"
                        )
                        target_comment_province = target_comment.get("province", "")
                        target_comment_id = target_comment.get("id")
                        reply_content = llm_result.get("generated_reply", "")

                        if not reply_content:
                            print("    -> [跳过] LLM 未生成回复内容。")
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        print(f"\n    🎯 发现潜在客户:")
                        print(f"       用户: {user_name}")
                        if target_comment_province:
                            print(f"       省份: {target_comment_province}")
                        print(f"       评论: {target_comment.get('content')}")
                        print(f"       原因: {llm_result.get('reason')}")
                        print(f"       回复: {reply_content}\n")

                        if target_provinces:
                            print("    -> 正在定位目标评论，确保回复按钮可见...")
                            if not ensure_comment_visible(publisher, target_comment):
                                print("    -> [跳过] 未能把目标评论滚动到可见区域。")
                                close_detail_overlay(publisher)
                                total_skipped += 1
                                continue

                        # 反检测：发送前先看 URL 是否已被风控重定向、账号是否可发
                        try:
                            current_url = publisher._evaluate("window.location.href")
                        except Exception:
                            current_url = None
                        rc_signal = risk_control.check_and_record(account, current_url)
                        if rc_signal is not None:
                            kind, count, frozen_until = rc_signal
                            print(f"    ⚠️ [风控] {kind}: 第 {count} 次警告，冻结至 {frozen_until}")
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        allowed, reason = account_state.can_send(account)
                        if not allowed:
                            cur_state = account_state.load(account)
                            if cur_state.get("frozen_until") == account_state.PERMANENT_FREEZE_ISO:
                                print(f"    ⛔ 账号已永久退役（{reason}），bot 退出。")
                                close_detail_overlay(publisher)
                                return
                            print(f"    ⏸ 跳过发送：{reason}")
                            close_detail_overlay(publisher)
                            total_skipped += 1
                            continue

                        print("    -> 正在发送回复...")
                        if not xsec_token:
                            print("    -> [提示] 当前笔记缺少 xsec_token，将在已打开详情页内直接回复。")
                        try:
                            publisher.respond_comment(
                                feed_id=feed_id,
                                xsec_token=xsec_token or "",
                                content=reply_content,
                                comment_id=target_comment_id,
                                skip_navigation=True,
                            )
                            print("    -> ✅ 回复发送成功！")
                            account_state.record_send(account)
                            reply_status = "success"
                            total_replies += 1

                            # 发送后再 check 一次 URL，捕捉 publish 之后的 风控 弹窗/重定向
                            try:
                                post_url = publisher._evaluate("window.location.href")
                            except Exception:
                                post_url = None
                            post_signal = risk_control.check_and_record(account, post_url)
                            if post_signal is not None:
                                kind, count, frozen_until = post_signal
                                print(f"    ⚠️ [风控] 发送后 {kind}: 第 {count} 次警告，冻结至 {frozen_until}")

                            try:
                                publisher._like_note()
                            except Exception as like_err:
                                print(f"    -> [警告] 点赞失败（不影响回复）: {like_err}")

                        except Exception as e:
                            print(f"    -> ❌ 回复发送失败: {e}")
                            reply_status = f"failed: {e}"

                        response_record = {
                            "profile": profile_name,
                            "keyword": keyword,
                            "note_title": title,
                            "note_id": feed_id,
                            "target_user": user_name,
                            "target_comment_province": target_comment_province,
                            "target_comment_id": target_comment_id,
                            "original_comment": target_comment.get("content"),
                            "ai_reason": llm_result.get("reason"),
                            "generated_reply": reply_content,
                            "send_status": reply_status,
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        all_responses.append(response_record)

                        cache[feed_id] = {
                            "title": title,
                            "target_user": user_name,
                            "target_comment_province": target_comment_province,
                            "comment_id": target_comment_id,
                            "status": reply_status,
                            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        save_cache(profile_name, cache)

                        print("    -> 关闭详情浮层，返回搜索结果...")
                        close_detail_overlay(publisher)

                    except Exception as e:
                        print(f"    -> [错误] 处理异常: {e}")
                        try:
                            close_detail_overlay(publisher)
                        except Exception:
                            pass

                    delay = random.uniform(post_delay_min, post_delay_max)
                    print(f"    -> 模拟浏览，等待 {delay:.1f}s...\n")
                    time.sleep(delay)

                if kw_idx < len(keywords):
                    delay = random.uniform(keyword_delay_min, keyword_delay_max)
                    print(f"\n  ⏳ 切换关键词前等待 {delay:.1f}s...")
                    time.sleep(delay)

            if all_responses:
                _save_results(profile_name, all_responses, total_replies, total_skipped, round_number)

            delay = random.uniform(round_delay_min, round_delay_max)
            print(f"\n  🔁 第 {round_number} 轮结束 (回复 {total_replies} / 跳过 {total_skipped})")
            print(f"  ⏳ 休息 {delay:.0f}s 后开始第 {round_number + 1} 轮...")
            print(f"  💡 已使用 {len(_used_keywords_history)} 个不同关键词\n")
            time.sleep(delay)

    except KeyboardInterrupt:
        print(f"\n\n{'=' * 60}")
        print(f"  ⛔ 用户中断（Ctrl+C），正在安全退出...")
        print(f"{'=' * 60}")

    if all_responses:
        _save_results(
            profile_name, all_responses, total_replies, total_skipped,
            round_number, is_final=True,
        )
    else:
        print("\n  没有回复记录需要保存。")

    print(f"\n  👋 运行共 {round_number} 轮，累计回复 {total_replies} 条。再见！")


if __name__ == "__main__":
    main()
