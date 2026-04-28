"""
生产版 - 小红书评论自动回复机器人 (纯 Playwright DOM 模拟版)

基于关键词搜索笔记，通过纯模拟人工操作以规避搜素风控。
使用 Playwright 操作现有的 CDP 浏览器实例。
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

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("api_key", "")

# 检查依赖
try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[错误] 未安装 playwright。请运行: pip install playwright")
    sys.exit(1)


# ============================================================
#  配置与缓存加载
# ============================================================

def load_config() -> dict:
    with open(os.path.join(script_dir, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def load_keywords() -> dict:
    with open(os.path.join(script_dir, "keywords.json"), "r", encoding="utf-8") as f:
        return json.load(f)

CACHE_FILE = os.path.join(script_dir, "processed_cache_lite.json")

def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_cache(cache: dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ============================================================
#  关键词生成 (同 comment_bot 原逻辑)
# ============================================================

_used_keywords_history: set[str] = set()

def generate_keywords(config: dict, keywords_data: dict) -> list[str]:
    special_templates = keywords_data.get("special_keywords", [])
    general_pool = keywords_data.get("general_keywords", [])
    cities = keywords_data.get("city", [])
    platforms = keywords_data.get("platform", [])
    sports = keywords_data.get("sports", [])

    city_count = config.get("city_count", 3)
    platform_count = config.get("platform_count", 3)
    sports_count = config.get("sports_count", 3)
    keywords_count = config.get("keywords_count", 10)

    expanded: list[str] = []
    for template in special_templates:
        if "{city}" in template:
            for city in random.sample(cities, min(city_count, len(cities))):
                expanded.append(template.replace("{city}", city))
        elif "{platform}" in template:
            for platform in random.sample(platforms, min(platform_count, len(platforms))):
                expanded.append(template.replace("{platform}", platform))
        elif "{sports}" in template:
            for sport in random.sample(sports, min(sports_count, len(sports))):
                expanded.append(template.replace("{sports}", sport))
        else:
            expanded.append(template)

    general_sampled = random.sample(general_pool, min(keywords_count, len(general_pool)))
    all_keywords = expanded + general_sampled
    random.shuffle(all_keywords)

    unique: list[str] = []
    for kw in all_keywords:
        if kw not in _used_keywords_history:
            unique.append(kw)

    _used_keywords_history.update(unique)
    return unique

def generate_keywords_with_llm(config: dict, keywords_data: dict, batch_size: int = 20) -> list[str]:
    if not OPENROUTER_API_KEY:
        return []
    
    recent_used = list(_used_keywords_history)[-60:]
    prompt = (
        f"你是一个小红书营销关键词生成专家。我们的业务是相亲交友/脱单服务。\n"
        f"请生成 {batch_size} 个全新的小红书搜索关键词（已用词请回避：{recent_used}）。\n"
        f"关键词短小精干，口语化，必须与相亲、脱单、恋爱相关。\n"
        f'严格返回JSON：{{"keywords": ["词1", "词2"]}}'
    )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {"role": "system", "content": "You are a keyword generator. Output only JSON."},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        content_text = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content_text, re.DOTALL)
        result = json.loads(match.group(0)) if match else json.loads(content_text)
        fresh = [k.strip() for k in result.get("keywords", []) if k.strip() and k.strip() not in _used_keywords_history]
        _used_keywords_history.update(fresh)
        return fresh
    except Exception as e:
        print(f"  -> [错误] LLM 关键词生成失败: {e}")
        return []

def get_next_keyword_batch(config: dict, keywords_data: dict, round_number: int) -> list[str]:
    if round_number == 1:
        return generate_keywords(config, keywords_data)
    llm_keywords = generate_keywords_with_llm(config, keywords_data, config.get("llm_keyword_batch_size", 20))
    if llm_keywords:
        random.shuffle(llm_keywords)
        return llm_keywords
    print("  -> [回退] LLM 失败，使用静态词...")
    _used_keywords_history.clear()
    return generate_keywords(config, keywords_data)


# ============================================================
#  LLM 评论分析
# ============================================================

SERVICE_DESC = "为单身人群提供真诚靠谱的脱单交友服务，精准匹配同频对象，拓展社交圈，告别无效相亲与低效尬聊。"

def evaluate_comments_with_llm(comments: list[dict]) -> dict | None:
    if not OPENROUTER_API_KEY:
        return None

    comments_text = ""
    for i, c in enumerate(comments):
        comments_text += f"[{i + 1}] 用户: {c['user']}, 评论: {c['content']}\n"

    prompt = f"""
你的任务是判断下方哪条小红书评论展现了最强的脱单/相亲意向。
没有意向时返回 selected_index: -1。
有意向时生成50字内的自然回复（像真实用户，引导私信，不使用emoji）。
服务："{SERVICE_DESC}"

评论：
{comments_text}

严格返回JSON，例如：{{"selected_index": 1, "reason": "...", "generated_reply": "..."}}
"""

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {"role": "system", "content": "You are a customer service bot. Output only JSON."},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        content_text = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content_text, re.DOTALL)
        return json.loads(match.group(0)) if match else json.loads(content_text)
    except Exception as e:
        print(f"  -> [错误] 调用 LLM 失败: {e}")
        return None


# ============================================================
#  主逻辑
# ============================================================

def _check_rate_limit(page) -> bool:
    """检测是否被限流，如果是则等待并恢复。返回 True 表示触发了限流。"""
    url = page.url
    if "error_code=300013" in url or "error_msg" in url:
        wait_time = random.uniform(60, 120)
        print(f"  ⚠️ [限流] 检测到访问频繁限制，等待 {wait_time:.0f} 秒后恢复...")
        time.sleep(wait_time)
        page.goto("https://www.xiaohongshu.com/explore", timeout=60000)
        time.sleep(random.uniform(5, 10))
        return True
    if "website-login/error" in url:
        wait_time = random.uniform(90, 180)
        print(f"  ⚠️ [风控] 被重定向到错误页，等待 {wait_time:.0f} 秒后恢复...")
        time.sleep(wait_time)
        page.goto("https://www.xiaohongshu.com/explore", timeout=60000)
        time.sleep(random.uniform(5, 10))
        return True
    return False

def _human_delay(min_s: float = 2.0, max_s: float = 5.0):
    """模拟人类操作间隔"""
    time.sleep(random.uniform(min_s, max_s))

def _random_scroll(page):
    """随机滚动页面，模拟浏览行为"""
    scroll_distance = random.randint(200, 600)
    page.mouse.wheel(0, scroll_distance)
    time.sleep(random.uniform(0.5, 1.5))

def _save_results(all_responses: list[dict], total_replies: int, round_number: int):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(script_dir, f"bot_lite_responses_{timestamp}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 第 {round_number} 轮结果已保存: {file_path}")

def main():
    print("=" * 60)
    print("  小红书评论自动回复机器人 (Lite Playwright 版)")
    print("  按 Ctrl+C 可随时安全停止")
    print("=" * 60)

    config = load_config()
    keywords_data = load_keywords()
    cache = load_cache()

    min_comment_count = config.get("min_comment_count", 5)
    analyze_comment_count = config.get("analyze_comment_count", 10)
    post_per_keyword = config.get("post_per_keyword", 10)

    with sync_playwright() as p:
        print("\n[初始化] 正在连接 Playwright 到 CDP 9222 端口...")
        try:
            resp = requests.get("http://127.0.0.1:9222/json/version", timeout=5)
            ws_url = resp.json().get("webSocketDebuggerUrl")
            browser = p.chromium.connect_over_cdp(ws_url)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            # 统一设置视口更像真人
            page.set_viewport_size({"width": 1280, "height": 800})
        except Exception as e:
            print(f"[错误] 连接到 Chrome (9222) 失败: {e}")
            print("请先执行 python scripts/chrome_launcher.py 启动浏览器。")
            sys.exit(1)

        print("[导航] 前往小红书探索页...")
        page.goto("https://www.xiaohongshu.com/explore", timeout=60000)
        time.sleep(3)

        all_responses = []
        total_replies = 0
        round_number = 0

        try:
            while True:
                round_number += 1
                keywords = get_next_keyword_batch(config, keywords_data, round_number)
                if not keywords:
                    time.sleep(10)
                    continue

                for kw_idx, keyword in enumerate(keywords, start=1):
                    print(f"\n{'─' * 50}\n[第{round_number}轮 {kw_idx}/{len(keywords)}] 🔍 搜索关键词: 「{keyword}」")
                    
                    # 检测限流
                    _check_rate_limit(page)
                    
                    # 回到探索页，确保我们在一致的状态且不易被阻断
                    if "explore" not in page.url and "search" not in page.url:
                        page.goto("https://www.xiaohongshu.com/explore")
                        _human_delay(3, 6)
                        
                    # 寻找搜索框并模拟输入
                    try:
                        search_input = page.locator("#search-input")
                        search_input.wait_for(state="visible", timeout=10000)
                        search_input.click()
                        _human_delay(0.5, 1.5)
                        search_input.fill("") 
                        # 模拟缓慢输入（更真实的逐字速度）
                        search_input.type(keyword, delay=random.randint(80, 200))
                        _human_delay(0.8, 2.0)
                        page.keyboard.press("Enter")
                    except PlaywrightTimeout:
                        print("  -> [跳过] 未能找到搜索框。重试刷新页面...")
                        page.goto("https://www.xiaohongshu.com/explore")
                        _human_delay(3, 6)
                        continue

                    # 等待搜索结果出现
                    try:
                        page.wait_for_selector("section.note-item, a.title", timeout=15000)
                    except PlaywrightTimeout:
                        print("  -> [跳过] 结果加载超时或没有结果。")
                        if _check_rate_limit(page):
                            continue
                        continue

                    # 给点时间让瀑布流渲染完毕，模拟浏览
                    _human_delay(3, 6)
                    _random_scroll(page)
                    cards = page.locator("section.note-item").all()
                    top_feeds = cards[:post_per_keyword]
                    print(f"  -> 共渲染了 {len(cards)} 篇笔记，将查看前 {len(top_feeds)} 篇。")

                    for feed_idx, card in enumerate(top_feeds, start=1):
                        unique_id = f"note-{time.time()}"  # 因为直接从 DOM 获取可能拿不到真实 feed_id，使用标题或随机
                        title = "未知标题"
                        
                        try:
                            # 尝试提取标题用于日志和缓存（小红书卡片里的标题）
                            title_loc = card.locator(".title, a.title span").first
                            if title_loc.count() > 0:
                                title = title_loc.text_content().strip()
                        except Exception:
                            pass
                            
                        # 用标题做基础排重（如果有的话）
                        cache_key = title if title and title != "未知标题" else str(feed_idx)
                        if cache_key in cache:
                            print(f"  [{feed_idx}/{len(top_feeds)}] 📝 [跳过] 笔记已处理过: {title}")
                            continue

                        print(f"  [{feed_idx}/{len(top_feeds)}] 📝 打开: {title}")

                        # 纯人工模拟：点击卡片进入详情浮层
                        _human_delay(1.5, 3.5)  # 阅读标题后再点击
                        try:
                            card.click()
                            # 详情页出现
                            page.wait_for_selector(".comment-item, .note-container, .note-detail-mask", timeout=10000)
                            _human_delay(2, 5)  # 模拟阅读笔记内容
                            if _check_rate_limit(page):
                                continue
                        except Exception as e:
                            print(f"    -> 点击卡片或等待详情页失败: {e}")
                            page.keyboard.press("Escape")
                            _human_delay(1, 2)
                            continue

                        # 抓取评论
                        comment_elements = page.locator(".comment-item").all()
                        if len(comment_elements) < min_comment_count:
                            print(f"    -> [跳过] 评论数不足 ({len(comment_elements)} < {min_comment_count})。")
                            page.keyboard.press("Escape")
                            time.sleep(1)
                            continue

                        # 解析前面 N 条评论的内容
                        comments_data = []
                        valid_elements = []  # 存下元素对象方便等会点击
                        for idx, el in enumerate(comment_elements[:analyze_comment_count]):
                            try:
                                author = el.locator(".name").first.text_content()
                                content = el.locator(".note-text, .content").first.text_content()
                                if author and content:
                                    comments_data.append({"user": author.strip(), "content": content.strip()})
                                    valid_elements.append(el)
                            except:
                                continue

                        if not comments_data:
                            page.keyboard.press("Escape")
                            time.sleep(1)
                            continue

                        # 让 LLM 分析
                        print(f"    -> 分析 {len(comments_data)} 条评论...")
                        llm_result = evaluate_comments_with_llm(comments_data)
                        if not llm_result:
                            page.keyboard.press("Escape")
                            time.sleep(1)
                            continue

                        selected_idx = llm_result.get("selected_index", -1) - 1
                        reason = llm_result.get("reason", "未提供")
                        
                        if selected_idx < 0 or selected_idx >= len(comments_data):
                            print(f"    -> [跳过] 无意向客户。({reason})")
                            cache[cache_key] = {"status": "no_intent"}
                            page.keyboard.press("Escape")
                            time.sleep(1)
                            continue

                        # 发现了意向客户，开始回复
                        target_comment = comments_data[selected_idx]
                        reply_content = llm_result.get("generated_reply", "")
                        target_element = valid_elements[selected_idx]

                        print(f"\n    🎯 发现潜在客户: {target_comment['user']}")
                        print(f"       评论: {target_comment['content']}")
                        print(f"       准备回复: {reply_content}\n")

                        try:
                            # 1. 悬停并点击回复按钮
                            target_element.hover()
                            time.sleep(1)
                            # 小红书的回复按钮可能叫 .reply, 或包含文字 “回复”
                            try:
                                reply_btn = target_element.locator("text='回复'").first
                                if reply_btn.count() == 0:
                                    reply_btn = target_element.locator(".reply").first
                                reply_btn.click()
                            except:
                                # 如果上述定位依然失败，尝试直接点击整个评论区域，通常也能唤起输入框
                                target_element.click()
                            time.sleep(1)

                            # 2. 填写输入框（基于真实 DOM）
                            input_box = page.locator("#content-textarea").first
                            input_box.fill(reply_content)
                            time.sleep(1)

                            # 3. 发送（基于真实 DOM）
                            send_btn = page.locator("button.btn.submit").first
                            send_btn.click()
                            print("    -> ✅ 回复操作已模拟完成！")
                            total_replies += 1

                            # 记录结果
                            all_responses.append({
                                "keyword": keyword,
                                "note_title": title,
                                "target_user": target_comment["user"],
                                "original": target_comment["content"],
                                "reply": reply_content,
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                            })
                            cache[cache_key] = {"status": "replied", "user": target_comment["user"]}
                            
                        except Exception as e:
                            print(f"    -> ❌ 模拟回复失败: {e}")

                        # 退出浮层
                        page.keyboard.press("Escape")
                        time.sleep(random.uniform(5, 12))
                        save_cache(cache)

                    print(f"  ⏳ 切换关键词暂歇...")
                    time.sleep(random.uniform(15, 30))

                _save_results(all_responses, total_replies, round_number)
                print(f"  🔁 轮次结束休息...")
                time.sleep(random.uniform(60, 120))

        except KeyboardInterrupt:
            print("\n⛔ 任务被手动停止。")
        finally:
            _save_results(all_responses, total_replies, round_number)

if __name__ == "__main__":
    main()
