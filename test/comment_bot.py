import sys
import os
import json
import requests
import re
import time
import random

# Setup paths to import from scripts
script_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(script_dir)
sys.path.insert(0, os.path.join(base_dir, "scripts"))

# 自动解析根目录下的 .env 文件加载环境变量
env_path = os.path.join(base_dir, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip().strip("'\"")

from cdp_publish import XiaohongshuPublisher

# 从环境变量中读取 API Key（支持 .env 中的 api_key 字段）
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("api_key", "YOUR_OPENROUTER_API_KEY")


# ============================================================
#  LLM 评论分析
# ============================================================

def evaluate_comments_with_llm(comments):
    """
    发送前10条评论给 OpenRouter 的 gemini-3-flash-preview 模型，
    判断哪条评论最具有相亲意向，并生成一条回复。
    """
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "YOUR_OPENROUTER_API_KEY":
        print("  -> [警告] 请设置 OPENROUTER_API_KEY 环境变量，或在代码中硬编码。")
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    # 提取评论文本并排版
    comments_text = ""
    for i, c in enumerate(comments):
        user = c.get("userInfo", {}).get("nickname", "Unknown")
        content = c.get("content", "").replace('\n', ' ')
        comments_text += f"[{i+1}] 用户: {user}, 评论: {content}\n"

    prompt = f"""
请分析以下10条来自小红书帖子的真实评论。
你的任务是：
1. 分析并判断哪一条评论的用户表现出了最强烈的相亲/交友/脱单意向（可能有找对象的潜在需求）。
2. 针对那个最有意向的评论，生成一条有针对性的回复。
3. 你的回复核心目的是：自然地抛出橄榄枝，介绍我们是做相亲/交友服务的，并表达"如果有需要的话可以随时了解/联系我们"。
4. 语气要求：像一个真实的小红书用户，友好、真诚、不惹人反感、稍微带一点网感。

评论列表：
{comments_text}

请严格！必须！按照以下JSON格式返回结果（不要返回除了JSON以外的其他多余解释或Markdown代码块标记）：
{{
  "selected_index": 整数类型的数字,代表你选中的评论编号(1-10),
  "reason": "字符串, 说明为什么选择这条评论",
  "generated_reply": "字符串, 你针对该用户生成的专属回复文本"
}}
"""

    payload = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {"role": "system", "content": "You are a highly emotionally intelligent and helpful matchmaking service assistant."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        req = requests.post(url, headers=headers, json=payload, timeout=30)
        req.raise_for_status()
        data = req.json()
        content_text = data['choices'][0]['message']['content']
        
        # 尝试提取其中的 JSON 对象
        match = re.search(r'\{.*\}', content_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        
        return json.loads(content_text)
    except json.JSONDecodeError:
        print("  -> [错误] 模型返回的结果不是有效的 JSON:", content_text)
        return None
    except Exception as e:
        print(f"  -> [错误] 调用 OpenRouter 接口失败: {e}")
        if 'req' in locals() and hasattr(req, 'text'):
            print(f"  -> [响应内容]: {req.text}")
        return None


# ============================================================
#  缓存工具
# ============================================================

CACHE_FILE = os.path.join(script_dir, "processed_feeds.json")

def load_cache() -> dict:
    """加载已处理的 feed 缓存。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def save_cache(cache: dict):
    """将缓存写入磁盘。"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ============================================================
#  DOM 交互辅助函数（纯 SPA 内操作，不做任何 URL 硬跳转）
# ============================================================

def _scroll_search_page(publisher, pixels: int = 600):
    """在搜索结果页向下滚动指定像素，触发懒加载。"""
    publisher._evaluate(f"window.scrollBy(0, {pixels});")
    publisher._sleep(1.2, minimum_seconds=0.8)


def _find_card_in_dom(publisher, feed_id: str) -> bool:
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


def click_note_card(publisher, feed_id: str, feed_index: int = 0) -> bool:
    """
    在搜索结果页中，通过 CDP 鼠标点击指定 feed_id 的笔记卡片封面。
    增强版：预滚动 + 多轮滚动查找 + 多种 DOM 选择器 + 点击重试。

    Args:
        feed_index: 当前笔记在列表中的序号（0-based），用于估算预滚动距离。
    """
    # 阶段 0：如果卡片不在 DOM 中，根据序号预滚动到大致位置
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

    # 阶段 2：滚动到卡片可视区域（使用 instant 避免动画延迟）
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
                print(f"  -> [调试] CDP 点击卡片失败（重试 {retry + 1}/2）: {e}")
                publisher._sleep(1.0, minimum_seconds=0.5)
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
                print(f"  -> [调试] CDP 点击卡片失败: {e}")
                return False


def wait_for_detail_state(publisher, feed_id: str, timeout: float = 10.0) -> bool:
    """
    等待笔记详情的数据加载到 window.__INITIAL_STATE__.note.noteDetailMap 中。
    """
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


def extract_comments_from_dom(publisher) -> list[dict]:
    """
    直接从当前页面/浮层的 DOM 中提取评论。
    不依赖 __INITIAL_STATE__，而是读取渲染出来的 .comment-item 元素。
    """
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

                // 跳过作者置顶评论（通常包含"作者"标签）
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
            # 过滤掉作者自己的评论
            return [c for c in comments if not c.get("is_author")]
        except json.JSONDecodeError:
            pass
    return []


def close_detail_overlay(publisher):
    """
    关闭笔记详情浮层，回到搜索结果列表。
    优先用 history.back()，可靠性最高。
    """
    publisher._evaluate("window.history.back();")
    publisher._sleep(1.5, minimum_seconds=0.5)


# ============================================================
#  主流程
# ============================================================

def main():
    print("=== 初始化小红书 CDP 爬虫 ===")
    publisher = XiaohongshuPublisher()
    
    print("正在连接到 Chrome 浏览器...")
    try:
        publisher.connect()
    except Exception as e:
        print(f"错误: 连接 CDP 失败: {e}")
        print("提示: 请确保你已经手动启动了 Chrome，并且开启了 --remote-debugging-port=9222")
        sys.exit(1)
        
    print("检查小红书网页版主页的登录状态...")
    if not publisher.check_home_login(wait_seconds=5.0):
        print("错误: 你似乎没有在网页端登录小红书，请先登录。")
        sys.exit(1)
        
    keyword = "相亲帖"
    print(f"\n正在搜索与 '{keyword}' 相关的笔记...")
    try:
        search_results = publisher.search_feeds(keyword=keyword)
    except Exception as e:
        print(f"错误: 搜索笔记失败: {e}")
        sys.exit(1)
        
    feeds = search_results.get("feeds", [])
    if not feeds:
        print("未找到任何笔记，退出。")
        sys.exit(0)
        
    top_10_feeds = feeds[:10]
    print(f"成功找到 {len(feeds)} 篇相关笔记，即将分析前 {len(top_10_feeds)} 篇...\n")
    
    all_responses = []
    cache = load_cache()
    print(f"已加载缓存，共 {len(cache)} 条已处理记录。\n")

    for idx, feed in enumerate(top_10_feeds, start=1):
        feed_id = feed.get("id")
        xsec_token = feed.get("xsecToken") or feed.get("noteCard", {}).get("user", {}).get("xsecToken")
        title = feed.get("noteCard", {}).get("displayTitle", "未命名笔记")
        
        print(f"[{idx}/10] 分析笔记 -> {title} (ID: {feed_id})")

        # 检查缓存，跳过已处理的笔记
        if feed_id and feed_id in cache:
            print(f"  -> [跳过] 该笔记已在 {cache[feed_id].get('processed_at', '未知时间')} 处理过，不再重复。")
            continue
        
        # 从封面数据中直接过滤出评论数，跳过评论过少的笔记
        comment_count_str = str(feed.get("noteCard", {}).get("interactInfo", {}).get("commentCount", "0"))
        comment_count = 0
        try:
            if 'w' in comment_count_str.lower():
                 comment_count = int(float(comment_count_str.lower().replace('w', '')) * 10000)
            elif 'k' in comment_count_str.lower():
                 comment_count = int(float(comment_count_str.lower().replace('k', '')) * 1000)
            elif '+' in comment_count_str:
                 comment_count = int(comment_count_str.replace('+', ''))
            elif comment_count_str.isdigit():
                 comment_count = int(comment_count_str)
        except Exception:
            pass
            
        if comment_count < 10:
            print(f"  -> [跳过] 这篇笔记显示的评论数较少 ({comment_count} 条 < 10)，跳过！")
            continue
            
        print(f"  -> 显示评论数: {comment_count}。正在点击笔记卡片进入详情...")
        
        if not feed_id:
            print("  -> [跳过] 无法获取有效的 feed_id。")
            continue

        try:
            # ======== 核心：纯 DOM 点击 + 浮层提取 ========

            # 1. 在搜索结果页，通过 CDP 鼠标事件点击笔记卡片封面
            if not click_note_card(publisher, feed_id, feed_index=idx - 1):
                print("  -> [跳过] 笔记卡片点击失败。执行向下翻页恢复页面状态...")
                _scroll_search_page(publisher, pixels=800)
                continue

            # 2. 等待 SPA 路由加载笔记详情数据
            publisher._sleep(2.0, minimum_seconds=1.0)
            if not wait_for_detail_state(publisher, feed_id, timeout=8.0):
                print("  -> [跳过] 笔记详情加载超时，可能被风控或内容不可见。")
                close_detail_overlay(publisher)
                continue

            # 3. 从浮层 DOM 中直接提取评论（而不是走 __INITIAL_STATE__）
            actual_comments = extract_comments_from_dom(publisher)

            if len(actual_comments) == 0:
                print("  -> [跳过] 浮层中未提取到有效评论（可能是评论区尚未加载或被折叠）。")
                close_detail_overlay(publisher)
                continue

            actual_comments = actual_comments[:10]
            print(f"  -> 成功从浮层中提取到 {len(actual_comments)} 条真实有效评论。正在呼叫大模型分析...")
            
            # 4. 呼叫 LLM 分析评论
            llm_result = evaluate_comments_with_llm(actual_comments)
            if llm_result:
                selected_idx = llm_result.get("selected_index", 1) - 1
                
                if 0 <= selected_idx < len(actual_comments):
                    target_comment = actual_comments[selected_idx]
                    user_name = target_comment.get("userInfo", {}).get("nickname", "未知用户")
                    target_comment_id = target_comment.get("id")
                    reply_content = llm_result.get('generated_reply')
                    
                    print(f"\n  🎯 [AI 智能决策结果 - 发现潜在客户]:")
                    print(f"      [目标用户]: {user_name}")
                    print(f"      [原始评论]: {target_comment.get('content')}")
                    print(f"      [选择原因]: {llm_result.get('reason')}")
                    print(f"      [高情商回复]: {reply_content}\n")
                    
                    # 5. 直接在当前浮层里自动回复（skip_navigation=True，不再重新加载页面）
                    print("  -> 正在自动向该用户发送智能回复...")
                    try:
                        publisher.respond_comment(
                            feed_id=feed_id,
                            xsec_token=xsec_token or "",
                            content=reply_content,
                            comment_id=target_comment_id,
                            skip_navigation=True
                        )
                        print("  -> ✅ 回复发送成功！")
                        reply_status = "Success"
                    except Exception as e:
                        print(f"  -> ❌ 发送回复失败: {e}")
                        reply_status = f"Failed: {e}"
                    
                    # 保存到结果集
                    all_responses.append({
                        "note_title": title,
                        "note_id": feed_id,
                        "target_user": user_name,
                        "original_comment": target_comment.get('content'),
                        "ai_reason": llm_result.get('reason'),
                        "generated_reply": reply_content,
                        "send_status": reply_status
                    })

                    # 写入缓存
                    cache[feed_id] = {
                        "title": title,
                        "target_user": user_name,
                        "send_status": reply_status,
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    save_cache(cache)
                else:
                    print(f"  -> [忽略] 大模型返回了无效的越界索引: {selected_idx + 1}。")

            # 6. 关闭浮层，回到搜索结果列表
            print("  -> 关闭详情浮层，返回搜索结果页...")
            close_detail_overlay(publisher)

            # 随机等待 1~3 秒，模拟人类浏览节奏
            human_delay = random.uniform(1.0, 3.0)
            print(f"  -> 模拟人类浏览，随机等待 {human_delay:.1f}s...\n")
            time.sleep(human_delay)
            
        except Exception as e:
            print(f"  -> [错误] 爬取或解析发生异常: {e}")
            # 出错时也确保返回搜索页
            try:
                close_detail_overlay(publisher)
            except Exception:
                pass

    # 将核对信息写入 JSON 文件
    out_file = os.path.join(script_dir, "comment_response.json")
    print(f"\n正在将本次分析的 🎯 {len(all_responses)} 条回复结果保存至 {out_file}...")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
