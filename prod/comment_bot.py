"""
生产版 - 小红书评论自动回复机器人

基于关键词搜索笔记，通过 LLM 分析评论意向，自动回复最有相亲服务意向的评论。

用法:
    python prod/comment_bot.py
"""

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

OPENROUTER_API_KEY = (
    os.environ.get("OPENROUTER_API_KEY")
    or os.environ.get("api_key", "")
)


# ============================================================
#  配置加载
# ============================================================

def load_config() -> dict:
    """加载 prod/config.json 配置。"""
    config_path = os.path.join(script_dir, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_keywords() -> dict:
    """加载 prod/keywords.json 关键词数据。"""
    keywords_path = os.path.join(script_dir, "keywords.json")
    with open(keywords_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
#  关键词生成
# ============================================================

def generate_keywords(config: dict, keywords_data: dict) -> list[str]:
    """
    根据配置展开 special_keywords 并随机抽取 general_keywords，
    合并后打乱顺序返回。
    """
    special_templates = keywords_data.get("special_keywords", [])
    general_pool = keywords_data.get("general_keywords", [])
    cities = keywords_data.get("city", [])
    platforms = keywords_data.get("platform", [])
    sports = keywords_data.get("sports", [])

    city_count = config.get("city_count", 3)
    platform_count = config.get("platform_count", 3)
    sports_count = config.get("sports_count", 3)
    keywords_count = config.get("keywords_count", 10)

    # --- A: 展开 special_keywords ---
    expanded: list[str] = []

    for template in special_templates:
        if "{city}" in template:
            sampled = random.sample(cities, min(city_count, len(cities)))
            for city in sampled:
                expanded.append(template.replace("{city}", city))
        elif "{platform}" in template:
            sampled = random.sample(platforms, min(platform_count, len(platforms)))
            for platform in sampled:
                expanded.append(template.replace("{platform}", platform))
        elif "{sports}" in template:
            sampled = random.sample(sports, min(sports_count, len(sports)))
            for sport in sampled:
                expanded.append(template.replace("{sports}", sport))
        else:
            # 不含占位符的 special 关键词直接加入
            expanded.append(template)

    # --- B: 从 general_keywords 随机抽取 ---
    general_sampled = random.sample(
        general_pool, min(keywords_count, len(general_pool))
    )

    # --- C: 合并并打乱 ---
    all_keywords = expanded + general_sampled
    random.shuffle(all_keywords)

    # 去重（保持顺序）
    seen: set[str] = set()
    unique: list[str] = []
    for kw in all_keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique


# ============================================================
#  LLM 评论分析
# ============================================================

SERVICE_DESC = (
    "为单身人群提供真诚靠谱的脱单交友服务，"
    "精准匹配同频对象，拓展社交圈，"
    "告别无效相亲与低效尬聊。"
)


def evaluate_comments_with_llm(
    comments: list[dict],
) -> dict | None:
    """
    将评论发送给 LLM 分析，判断哪条最有相亲意向。
    无意向时返回 selected_index = -1。
    """
    if not OPENROUTER_API_KEY:
        print("  -> [警告] 未设置 OPENROUTER_API_KEY / api_key，跳过 LLM 分析。")
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    # 构建评论文本
    comments_text = ""
    for i, c in enumerate(comments):
        user = c.get("userInfo", {}).get("nickname", "Unknown")
        content = c.get("content", "").replace("\n", " ")
        comments_text += f"[{i + 1}] 用户: {user}, 评论: {content}\n"

    prompt = f"""
请分析以下来自小红书帖子的评论。
你的任务是：
1. 判断哪一条评论的用户表现出了最强烈的相亲/交友/脱单意向（有找对象的潜在需求）。
2. 如果没有任何评论有相亲/交友意向，请返回 selected_index 为 -1。
3. 如果有意向，针对最有意向的评论生成一条回复。
4. 回复核心目的：自然地邀请对方发私信了解我们的服务。
5. 服务介绍（可适当缩写）：「{SERVICE_DESC}」
6. 回复要求：
   - 像真实小红书用户，友好、真诚、有网感
   - 字数严格控制在50字以内
   - 不要使用 emoji 表情

评论列表：
{comments_text}

请严格按照以下JSON格式返回（不要返回除JSON以外的任何内容）：
{{
  "selected_index": 整数, 选中的评论编号(1-{len(comments)})，无意向时为 -1,
  "reason": "选择或跳过的理由",
  "generated_reply": "回复文本（无意向时为空字符串）"
}}
"""

    payload = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a highly emotionally intelligent matchmaking "
                    "service assistant. You analyze comments and generate "
                    "natural, concise Chinese replies."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content_text = data["choices"][0]["message"]["content"]

        # 尝试提取 JSON
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
#  缓存工具 - 在 prod/ 目录下持久化
# ============================================================

CACHE_FILE = os.path.join(script_dir, "processed_cache.json")


def load_cache() -> dict:
    """加载已处理记录缓存。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cache(cache: dict):
    """持久化缓存到磁盘。"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ============================================================
#  DOM 交互辅助函数
# ============================================================

def _scroll_search_page(publisher: XiaohongshuPublisher, pixels: int = 600):
    """在搜索结果页向下滚动指定像素，触发懒加载。"""
    publisher._evaluate(f"window.scrollBy(0, {pixels});")
    publisher._sleep(1.0, minimum_seconds=0.5)


def _find_card_in_dom(publisher: XiaohongshuPublisher, feed_id: str) -> bool:
    """检查 feed_id 对应的卡片链接是否存在于当前 DOM 中。"""
    return bool(publisher._evaluate(f"""
        (() => {{
            const feedId = "{feed_id}";
            const links = document.querySelectorAll('a');
            for (const link of links) {{
                if (link.href && link.href.includes(feedId)) return true;
            }}
            return false;
        }})()
    """))


def click_note_card(publisher: XiaohongshuPublisher, feed_id: str) -> bool:
    """
    在搜索结果页中，通过 CDP 鼠标点击指定 feed_id 的笔记卡片封面。
    会先尝试滚动查找（最多 5 次），解决懒加载导致卡片不在 DOM 中的问题。
    """
    # 阶段 1：如果卡片不在 DOM 中，逐步向下滚动搜索结果页触发懒加载
    max_scroll_attempts = 5
    if not _find_card_in_dom(publisher, feed_id):
        for attempt in range(max_scroll_attempts):
            _scroll_search_page(publisher, pixels=800)
            if _find_card_in_dom(publisher, feed_id):
                break
        else:
            # 滚动到底仍未找到
            return False

    # 阶段 2：滚动到卡片可视区域
    scroll_ok = publisher._evaluate(f"""
        (() => {{
            const feedId = "{feed_id}";
            const links = document.querySelectorAll('a');
            for (const link of links) {{
                if (link.href && link.href.includes(feedId)) {{
                    link.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    return true;
                }}
            }}
            return false;
        }})()
    """)
    if not scroll_ok:
        return False
    publisher._sleep(1.0, minimum_seconds=0.5)

    # 阶段 3：获取卡片矩形并点击
    rect_js = f"""
        (() => {{
            const feedId = "{feed_id}";
            const links = document.querySelectorAll('a');
            for (const link of links) {{
                if (!link.href || !link.href.includes(feedId)) continue;
                const rect = link.getBoundingClientRect();
                if (rect.width > 30 && rect.height > 30 &&
                    rect.top >= -50 && rect.bottom <= window.innerHeight + 100) {{
                    return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                }}
            }}
            return null;
        }})()
    """
    try:
        publisher._click_element_by_cdp("note card cover", rect_js)
        return True
    except Exception as e:
        print(f"    -> [调试] CDP 点击卡片失败: {e}")
        return False


def open_note_by_url(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    xsec_token: str,
) -> bool:
    """
    直接通过 URL 导航到笔记详情页（卡片点击失败时的后备方案）。
    返回是否成功加载详情页。
    """
    from feed_explorer import make_feed_detail_url

    if not xsec_token:
        print("    -> [调试] 无 xsec_token，无法构造详情 URL。")
        return False

    try:
        detail_url = make_feed_detail_url(feed_id, xsec_token)
    except Exception as e:
        print(f"    -> [调试] 构造详情 URL 失败: {e}")
        return False

    print(f"    -> 使用 URL 直接导航到笔记详情页...")
    publisher._navigate(detail_url)
    publisher._sleep(2.5, minimum_seconds=1.5)
    return True


def wait_for_detail_state(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    timeout: float = 10.0,
) -> bool:
    """等待笔记详情数据加载。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = publisher._evaluate(f"""
            (() => {{
                const state = window.__INITIAL_STATE__;
                if (state && state.note && state.note.noteDetailMap) {{
                    const map = state.note.noteDetailMap;
                    if (map["{feed_id}"] || Object.keys(map).length > 0) {{
                        return true;
                    }}
                }}
                return false;
            }})()
        """)
        if ready:
            return True
        publisher._sleep(0.5, minimum_seconds=0.2)
    return False


def extract_comments_from_dom(publisher: XiaohongshuPublisher) -> list[dict]:
    """从当前页面浮层/详情页 DOM 中提取评论列表（排除作者评论）。"""
    raw = publisher._evaluate("""
        (() => {
            const results = [];
            const seen = new Set();
            const items = document.querySelectorAll('.comment-item');
            for (const item of items) {
                const rawId = item.id || '';
                const id = rawId.replace(/^comment-/, '');
                if (!id || seen.has(id)) continue;
                seen.add(id);

                const nameEl = item.querySelector('a.name');
                const textEl = item.querySelector('.note-text');
                if (!textEl) continue;

                const content = (textEl.textContent || '').trim();
                if (!content) continue;

                const tagEl = item.querySelector('.tag');
                const isAuthor = tagEl && tagEl.textContent.trim() === '作者';

                results.push({
                    id: id,
                    content: content,
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
#  主流程
# ============================================================

def main():
    print("=" * 60)
    print("  小红书评论自动回复机器人 (生产版)")
    print("=" * 60)

    # --- 加载配置 ---
    config = load_config()
    keywords_data = load_keywords()

    min_comment_count = config.get("min_comment_count", 5)
    analyze_comment_count = config.get("analyze_comment_count", 10)
    post_per_keyword = config.get("post_per_keyword", 10)
    keyword_delay_min = config.get("keyword_delay_min", 8)
    keyword_delay_max = config.get("keyword_delay_max", 15)
    post_delay_min = config.get("post_delay_min", 2)
    post_delay_max = config.get("post_delay_max", 5)

    # --- 生成关键词 ---
    keywords = generate_keywords(config, keywords_data)
    print(f"\n[关键词] 共生成 {len(keywords)} 个搜索关键词：")
    for i, kw in enumerate(keywords, 1):
        print(f"  {i:3d}. {kw}")

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
    cache = load_cache()
    print(f"[缓存] 已加载 {len(cache)} 条历史处理记录。\n")

    # --- 遍历关键词 ---
    all_responses: list[dict] = []
    total_replies = 0
    total_skipped = 0

    for kw_idx, keyword in enumerate(keywords, start=1):
        print(f"\n{'─' * 50}")
        print(f"[{kw_idx}/{len(keywords)}] 🔍 搜索关键词: 「{keyword}」")
        print(f"{'─' * 50}")

        try:
            search_results = publisher.search_feeds(keyword=keyword)
        except Exception as e:
            # 连接断开时尝试重连一次再搜索
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

            # 检查缓存
            if feed_id and feed_id in cache:
                processed_at = cache[feed_id].get("processed_at", "未知")
                print(f"    -> [跳过] 已于 {processed_at} 处理过。")
                total_skipped += 1
                continue

            # 检查评论数门槛
            comment_count_str = str(
                feed.get("noteCard", {})
                .get("interactInfo", {})
                .get("commentCount", "0")
            )
            comment_count = parse_comment_count(comment_count_str)

            if comment_count < min_comment_count:
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
                f"    -> 评论数: {comment_count}。"
                f"正在打开笔记详情..."
            )

            # 记录是否通过直接 URL 导航（用于后续正确返回）
            opened_via_url = False

            try:
                # 1. 尝试点击笔记卡片（快速，保持在搜索页上下文）
                if not click_note_card(publisher, feed_id):
                    # 后备方案：直接 URL 导航到笔记详情页
                    print("    -> 卡片未在 DOM 中找到，使用 URL 直接导航...")
                    if not open_note_by_url(publisher, feed_id, xsec_token or ""):
                        print("    -> [跳过] 无法打开笔记详情。")
                        total_skipped += 1
                        continue
                    opened_via_url = True

                # 2. 等待详情加载
                publisher._sleep(2.0, minimum_seconds=1.0)
                if not wait_for_detail_state(publisher, feed_id, timeout=10.0):
                    print("    -> [跳过] 详情加载超时。")
                    close_detail_overlay(publisher)
                    # 如果是直接导航，需要额外等待搜索页恢复
                    if opened_via_url:
                        publisher._sleep(1.5, minimum_seconds=0.8)
                    total_skipped += 1
                    continue

                # 3. 提取 DOM 评论
                actual_comments = extract_comments_from_dom(publisher)

                if len(actual_comments) < min_comment_count:
                    print(
                        f"    -> [跳过] 实际可见评论不足 "
                        f"({len(actual_comments)} < {min_comment_count})。"
                    )
                    close_detail_overlay(publisher)
                    total_skipped += 1
                    continue

                # 取前 analyze_comment_count 条
                comments_to_analyze = actual_comments[:analyze_comment_count]
                print(
                    f"    -> 提取到 {len(actual_comments)} 条评论，"
                    f"取前 {len(comments_to_analyze)} 条送 LLM 分析..."
                )

                # 4. LLM 分析
                llm_result = evaluate_comments_with_llm(comments_to_analyze)

                if not llm_result:
                    print("    -> [跳过] LLM 分析失败。")
                    close_detail_overlay(publisher)
                    total_skipped += 1
                    continue

                selected_idx = llm_result.get("selected_index", -1)

                # 无意向 → 跳过
                if selected_idx == -1:
                    print(
                        f"    -> [跳过] LLM 判定无相亲意向。"
                        f"原因: {llm_result.get('reason', '未提供')}"
                    )
                    # 即使无意向也记入缓存，避免下次重复分析
                    cache[feed_id] = {
                        "title": title,
                        "status": "no_intent",
                        "reason": llm_result.get("reason", ""),
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    save_cache(cache)
                    close_detail_overlay(publisher)
                    total_skipped += 1
                    continue

                # 有意向 → 回复
                real_idx = selected_idx - 1  # LLM 返回 1-based
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
                target_comment_id = target_comment.get("id")
                reply_content = llm_result.get("generated_reply", "")

                if not reply_content:
                    print("    -> [跳过] LLM 未生成回复内容。")
                    close_detail_overlay(publisher)
                    total_skipped += 1
                    continue

                print(f"\n    🎯 发现潜在客户:")
                print(f"       用户: {user_name}")
                print(f"       评论: {target_comment.get('content')}")
                print(f"       原因: {llm_result.get('reason')}")
                print(f"       回复: {reply_content}\n")

                # 5. 自动回复
                print("    -> 正在发送回复...")
                try:
                    publisher.respond_comment(
                        feed_id=feed_id,
                        xsec_token=xsec_token or "",
                        content=reply_content,
                        comment_id=target_comment_id,
                        skip_navigation=True,
                    )
                    print("    -> ✅ 回复发送成功！")
                    reply_status = "success"
                    total_replies += 1

                    # 5b. 给笔记点赞，方便后续在「我的赞」中找到已回复的笔记
                    try:
                        publisher._like_note()
                    except Exception as like_err:
                        print(f"    -> [警告] 点赞失败（不影响回复）: {like_err}")

                except Exception as e:
                    print(f"    -> ❌ 回复发送失败: {e}")
                    reply_status = f"failed: {e}"

                # 保存结果
                response_record = {
                    "keyword": keyword,
                    "note_title": title,
                    "note_id": feed_id,
                    "target_user": user_name,
                    "target_comment_id": target_comment_id,
                    "original_comment": target_comment.get("content"),
                    "ai_reason": llm_result.get("reason"),
                    "generated_reply": reply_content,
                    "send_status": reply_status,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                all_responses.append(response_record)

                # 写入缓存
                cache[feed_id] = {
                    "title": title,
                    "target_user": user_name,
                    "comment_id": target_comment_id,
                    "status": reply_status,
                    "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                save_cache(cache)

                # 6. 关闭浮层
                print("    -> 关闭详情浮层，返回搜索结果...")
                close_detail_overlay(publisher)

            except Exception as e:
                print(f"    -> [错误] 处理异常: {e}")
                try:
                    close_detail_overlay(publisher)
                except Exception:
                    pass

            # 笔记间随机延迟
            delay = random.uniform(post_delay_min, post_delay_max)
            print(f"    -> 模拟浏览，等待 {delay:.1f}s...\n")
            time.sleep(delay)

        # 关键词间随机延迟
        if kw_idx < len(keywords):
            delay = random.uniform(keyword_delay_min, keyword_delay_max)
            print(f"\n  ⏳ 切换关键词前等待 {delay:.1f}s...")
            time.sleep(delay)

    # --- 保存结果 ---
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(script_dir, f"comment_responses_{timestamp}.json")
    print(f"\n{'=' * 60}")
    print(f"  运行结束")
    print(f"  成功回复: {total_replies} 条")
    print(f"  跳过: {total_skipped} 条")
    print(f"  结果保存: {result_file}")
    print(f"{'=' * 60}")

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
