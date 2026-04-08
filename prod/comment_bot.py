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

# 全局历史关键词集合，跨轮次去重
_used_keywords_history: set[str] = set()


def generate_keywords(config: dict, keywords_data: dict) -> list[str]:
    """
    根据配置展开 special_keywords 并随机抽取 general_keywords，
    合并后打乱顺序返回。跳过已使用过的关键词。
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

    # 去重（保持顺序，跳过历史已用关键词）
    seen: set[str] = set()
    unique: list[str] = []
    for kw in all_keywords:
        if kw not in seen and kw not in _used_keywords_history:
            seen.add(kw)
            unique.append(kw)

    # 记录本轮关键词到历史
    _used_keywords_history.update(unique)

    return unique


def generate_keywords_with_llm(
    config: dict,
    keywords_data: dict,
    batch_size: int = 20,
) -> list[str]:
    """
    调用 LLM 根据现有关键词库的风格，生成一批全新的搜索关键词。
    这些关键词会避开历史已使用过的词，确保每轮搜索都能覆盖新内容。
    """
    if not OPENROUTER_API_KEY:
        print("  -> [警告] 未设置 API Key，无法使用 LLM 生成关键词。")
        return []

    # 从现有关键词库中随机抽取样本作为参考
    all_existing = keywords_data.get("general_keywords", [])
    sample_size = min(15, len(all_existing))
    sample_keywords = random.sample(all_existing, sample_size) if all_existing else []

    # 准备已用关键词列表（取最近的部分避免 prompt 过长）
    recent_used = list(_used_keywords_history)[-60:]

    prompt = (
        f"你是一个小红书营销关键词生成专家。我们的业务是相亲交友/脱单服务。\n"
        f"\n"
        f"请基于以下参考关键词的风格和主题，生成 {batch_size} 个全新的小红书搜索关键词。\n"
        f"\n"
        f"参考关键词样本：\n"
        f"{json.dumps(sample_keywords, ensure_ascii=False)}\n"
        f"\n"
        f"以下关键词已经使用过，请不要重复：\n"
        f"{json.dumps(recent_used, ensure_ascii=False)}\n"
        f"\n"
        f"生成要求：\n"
        f"1. 关键词必须是小红书用户真实会搜索的内容\n"
        f"2. 覆盖多个角度：情感痛点、场景需求、年龄段、职业、城市、兴趣社交等\n"
        f"3. 包含长尾关键词（3-8个字）和短关键词（2-4个字）混合\n"
        f"4. 可以包含口语化、情绪化的表达（如'好想脱单啊'、'单身久了会怎样'）\n"
        f"5. 可以蹭热点话题、节日、季节相关（如'春天脱单计划'、'520前脱单'）\n"
        f"6. 每个关键词都要跟 相亲/交友/脱单/恋爱/婚恋 相关\n"
        f"7. 不要带编号和引号\n"
        f"\n"
        f'请严格按以下JSON格式返回（不要返回除JSON以外的任何内容）：\n'
        f'{{\n'
        f'  "keywords": ["关键词1", "关键词2", ...]\n'
        f'}}'
    )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Chinese social media keyword research expert. "
                    "Generate diverse, realistic search keywords for Xiaohongshu."
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

        match = re.search(r"\{.*\}", content_text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            result = json.loads(content_text)

        raw_keywords = result.get("keywords", [])

        # 过滤已用关键词
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


def get_next_keyword_batch(
    config: dict,
    keywords_data: dict,
    round_number: int,
) -> list[str]:
    """
    智能获取下一批关键词：
    - 第1轮：使用 keywords.json 中的静态关键词
    - 第2轮开始：优先用 LLM 生成新词，不够时回退到静态关键词（重置历史允许复用）
    """
    if round_number == 1:
        # 第一轮：使用静态关键词
        return generate_keywords(config, keywords_data)

    # 后续轮次：LLM 生成
    batch_size = config.get("llm_keyword_batch_size", 20)
    llm_keywords = generate_keywords_with_llm(config, keywords_data, batch_size)

    if llm_keywords:
        random.shuffle(llm_keywords)
        return llm_keywords

    # LLM 失败时回退：清除历史，重新使用静态关键词
    print("  -> [回退] LLM 生成失败，清除历史重新使用静态关键词...")
    _used_keywords_history.clear()
    return generate_keywords(config, keywords_data)


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
    publisher._sleep(1.2, minimum_seconds=0.8)


def _find_card_in_dom(publisher: XiaohongshuPublisher, feed_id: str) -> bool:
    """
    检查 feed_id 对应的**可点击卡片**是否存在于当前 DOM 中。
    只匹配真正可点击的元素，避免 textContent 误匹配隐藏 JSON 等。
    """
    return bool(publisher._evaluate(f"""
        (() => {{
            const feedId = "{feed_id}";
            // 1. 检查 <a> 标签 href 包含 feedId（最可靠）
            const links = document.querySelectorAll('a[href*="' + feedId + '"]');
            for (const link of links) {{
                const rect = link.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) return true;
            }}
            // 2. 检查带 data 属性的元素
            const dataEls = document.querySelectorAll(
                '[data-note-id="' + feedId + '"], ' +
                '[data-feed-id="' + feedId + '"], ' +
                '[note-id="' + feedId + '"], ' +
                '[feed-id="' + feedId + '"]'
            );
            for (const el of dataEls) {{
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) return true;
            }}
            return false;
        }})()
    """))


def _get_card_element_js(feed_id: str) -> str:
    """
    返回一段 JS 表达式，用于查找并返回目标卡片的可点击 <a> 元素。
    兼容多种 DOM 结构，优先返回可见且尺寸合理的元素。
    """
    return f"""
        (() => {{
            const feedId = "{feed_id}";
            const links = document.querySelectorAll('a');
            for (const link of links) {{
                if (!link.href || !link.href.includes(feedId)) continue;
                const rect = link.getBoundingClientRect();
                // 元素必须可见且尺寸合理
                if (rect.width > 20 && rect.height > 20) {{
                    return link;
                }}
            }}
            return null;
        }})()
    """


def click_note_card(
    publisher: XiaohongshuPublisher,
    feed_id: str,
    feed_index: int = 0,
) -> bool:
    """
    在搜索结果页中，通过 CDP 鼠标点击指定 feed_id 的笔记卡片封面。
    增强版：预滚动 + 多轮滚动查找 + 多种 DOM 选择器 + 点击重试。

    Args:
        feed_index: 当前笔记在列表中的序号（0-based），用于估算预滚动距离。
    """
    # 阶段 0：如果卡片不在 DOM 中，根据序号预滚动到大致位置
    #         每张卡片大约占 280px 高度（双列布局，每行2张，约560px/2）
    if not _find_card_in_dom(publisher, feed_id) and feed_index > 0:
        estimated_y = (feed_index // 2) * 280
        publisher._evaluate(
            f"window.scrollTo({{ top: {estimated_y}, behavior: 'instant' }});"
        )
        publisher._sleep(1.5, minimum_seconds=0.8)

    # 阶段 1：如果卡片仍不在 DOM 中，逐步向下滚动触发懒加载
    max_scroll_attempts = 10
    if not _find_card_in_dom(publisher, feed_id):
        for attempt in range(max_scroll_attempts):
            _scroll_search_page(publisher, pixels=500)
            if _find_card_in_dom(publisher, feed_id):
                break
        else:
            # 向下搜索失败，滚回顶部做一次全量向下搜索
            publisher._evaluate("window.scrollTo({ top: 0, behavior: 'instant' });")
            publisher._sleep(1.5, minimum_seconds=0.8)
            for attempt in range(max_scroll_attempts):
                if _find_card_in_dom(publisher, feed_id):
                    break
                _scroll_search_page(publisher, pixels=500)
            else:
                return False

    # 阶段 2：滚动到卡片可视区域（使用 instant 避免 smooth 动画延迟）
    scroll_ok = publisher._evaluate(f"""
        (() => {{
            const feedId = "{feed_id}";
            // 优先通过 href 精确匹配 <a> 标签
            const links = document.querySelectorAll('a[href*="' + feedId + '"]');
            for (const link of links) {{
                link.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                return true;
            }}
            // fallback: data 属性匹配
            const dataEls = document.querySelectorAll(
                '[data-note-id="' + feedId + '"], ' +
                '[data-feed-id="' + feedId + '"]'
            );
            for (const el of dataEls) {{
                el.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                return true;
            }}
            return false;
        }})()
    """)
    if not scroll_ok:
        return False
    publisher._sleep(1.5, minimum_seconds=0.8)

    # 阶段 3：获取卡片矩形并点击（带重试）
    for retry in range(3):
        rect_js = f"""
            (() => {{
                const feedId = "{feed_id}";
                
                // 优先检查 <a> 标签
                const links = document.querySelectorAll('a');
                for (const link of links) {{
                    if (!link.href || !link.href.includes(feedId)) continue;
                    const rect = link.getBoundingClientRect();
                    // 放宽视口限制：只要元素部分可见即可
                    if (rect.width > 20 && rect.height > 20 &&
                        rect.bottom > 0 && rect.top < window.innerHeight) {{
                        return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                    }}
                }}
                
                // fallback: 检查 data-* 属性
                const dataEls = document.querySelectorAll(
                    '[data-note-id="' + feedId + '"], ' +
                    '[data-feed-id="' + feedId + '"], ' +
                    '[note-id="' + feedId + '"], ' +
                    '[feed-id="' + feedId + '"]'
                );
                for (const el of dataEls) {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 20 && rect.height > 20 &&
                        rect.bottom > 0 && rect.top < window.innerHeight) {{
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
            if retry < 2:
                print(f"    -> [调试] CDP 点击卡片失败（重试 {retry + 1}/2）: {e}")
                publisher._sleep(1.0, minimum_seconds=0.5)
                # 重新滚动确保卡片在视口中
                publisher._evaluate(f"""
                    (() => {{
                        const feedId = "{feed_id}";
                        const links = document.querySelectorAll('a');
                        for (const link of links) {{
                            if (link.href && link.href.includes(feedId)) {{
                                link.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                                return true;
                            }}
                        }}
                        return false;
                    }})()
                """)
                publisher._sleep(1.0, minimum_seconds=0.5)
            else:
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

def _save_results(all_responses: list[dict], total_replies: int, total_skipped: int, round_number: int, is_final: bool = False):
    """保存当前累计的回复结果到 JSON 文件。"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(script_dir, f"comment_responses_{timestamp}.json")

    label = "最终" if is_final else f"第{round_number}轮"
    print(f"\n{'=' * 60}")
    print(f"  {label}结果保存")
    print(f"  累计成功回复: {total_replies} 条")
    print(f"  累计跳过: {total_skipped} 条")
    print(f"  结果文件: {result_file}")
    print(f"{'=' * 60}")

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 60)
    print("  小红书评论自动回复机器人 (生产版 · 无限模式)")
    print("  按 Ctrl+C 可随时安全停止")
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
    cache = load_cache()
    print(f"[缓存] 已加载 {len(cache)} 条历史处理记录。\n")

    # --- 无限循环 ---
    all_responses: list[dict] = []
    total_replies = 0
    total_skipped = 0
    round_number = 0

    try:
        while True:
            round_number += 1

            # --- 生成本轮关键词 ---
            print(f"\n{'━' * 60}")
            print(f"  🔄 第 {round_number} 轮关键词生成")
            print(f"{'━' * 60}")

            keywords = get_next_keyword_batch(config, keywords_data, round_number)

            if not keywords:
                print("  -> [警告] 未生成任何关键词，等待后重试...")
                time.sleep(random.uniform(10, 20))
                continue

            source = "静态关键词库" if round_number == 1 else "LLM 智能生成"
            print(f"\n[关键词] 来源: {source}，共 {len(keywords)} 个：")
            for i, kw in enumerate(keywords, 1):
                print(f"  {i:3d}. {kw}")

            # --- 遍历关键词 ---
            for kw_idx, keyword in enumerate(keywords, start=1):
                print(f"\n{'─' * 50}")
                print(f"[第{round_number}轮 {kw_idx}/{len(keywords)}] 🔍 搜索关键词: 「{keyword}」")
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
                        if not click_note_card(publisher, feed_id, feed_index=feed_idx - 1):
                            print("    -> 笔记卡片初次点击失败。尝试向下翻页并重试...")
                            _scroll_search_page(publisher, pixels=800)
                            if not click_note_card(publisher, feed_id, feed_index=0):
                                print("    -> [跳过] 重试点击依然失败。")
                                total_skipped += 1
                                continue

                        # 2. 等待详情加载
                        publisher._sleep(2.0, minimum_seconds=1.0)
                        if not wait_for_detail_state(publisher, feed_id, timeout=10.0):
                            print("    -> [跳过] 详情加载超时。")
                            close_detail_overlay(publisher)
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

            # --- 一轮结束，保存阶段性结果 ---
            if all_responses:
                _save_results(all_responses, total_replies, total_skipped, round_number)

            # --- 轮次间长休息 ---
            delay = random.uniform(round_delay_min, round_delay_max)
            print(f"\n  🔁 第 {round_number} 轮结束 (回复 {total_replies} / 跳过 {total_skipped})")
            print(f"  ⏳ 休息 {delay:.0f}s 后开始第 {round_number + 1} 轮...")
            print(f"  💡 已使用 {len(_used_keywords_history)} 个不同关键词\n")
            time.sleep(delay)

    except KeyboardInterrupt:
        print(f"\n\n{'=' * 60}")
        print(f"  ⛔ 用户中断（Ctrl+C），正在安全退出...")
        print(f"{'=' * 60}")

    # --- 最终保存结果 ---
    if all_responses:
        _save_results(all_responses, total_replies, total_skipped, round_number, is_final=True)
    else:
        print("\n  没有回复记录需要保存。")

    print(f"\n  👋 运行共 {round_number} 轮，累计回复 {total_replies} 条。再见！")


if __name__ == "__main__":
    main()
