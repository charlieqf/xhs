# D4-D8 启动 + 5 天烧号探边界协议

**写于**: 2026-05-14
**前置**: D1-D3 已完成（参见 `anti-detection-7day-acute-boundary-experiment.md` 日记段）。19921371193 累计 32 条，0 任何 stop-condition 触发。
**目标**: 5 天累计 2000 条评论 / 探账号烧前的安全边界 / 验证 19921371193 是否落在历史账号 A（几千条警告）或 B（几百条封禁）画像

---

## 实验目的的关键转向（vs 原 7 天计划）

D1-D3 跑完后，用户引入两条新先验：

1. **历史账号 A/B 数据**（无导流话术情况下）：
   - 账号 A：几千条评论后被警告
   - 账号 B：几百条评论后被封禁
   - 两者**都没用"私我/私信我"导流话术** — 意味着累积量和发帖节奏本身就是触发风控的主要因素，话术变体不是必要条件

2. **业务需求无上限，多多益善** — 2000 条/5 天目标是为了"主动落入 B 区间下界 + 穿过 A 区间下界，看挂的特征"，**不是产能 KPI**

**含义**：
- 原 D4-D7 γ 梯度（软/中/硬导流变体，280 条）废弃 — 因为底层"累积量轴"会先把账号挂掉，分离不出"话术变体的边际效应"
- 新计划：**全程纯交流话术（neutral persona，零后续邀约），通过量频梯度逼近边界**
- 用户接受账号烧掉，目的是拿到边界数据 + 验证仿人工/质量改造的有效性

---

## 5 天渐进梯度（累计 2000 条）

| 天 | 自然日 | 目标量 | 累计（含历史 32）| active_windows | min_interval | 单条周期 | 备注 |
|---|---|---|---|---|---|---|---|
| **D4-tonight** | 5-14 晚 | ~67 | 99 | 单段 [20:00-22:30] | 120s | ~120s | "提前启动" 段；D4 第 3 段配额 |
| **D4-main** | 5-15 全天 | 200 | 299 | 3 段 8h: [08:00-10:30][14:00-16:30][20:00-22:30] | 120s | ~120s | D4 主体；落入 B 下界 |
| **D5** | 5-16 | 300 | 599 | 3 段 9.5h: [07:30-11:00][13:30-16:30][19:30-22:30] | 100s | ~100s | B 中段 |
| **D6** | 5-17 | 400 | 999 | 3 段 12h: [07:00-11:30][13:00-17:00][19:00-22:30] | 90s | ~90s | 接近 A 下界 |
| **D7** | 5-18 | 500 | 1499 | 2 段 14h: [06:30-13:30][16:30-23:30] | 80s | ~80s | A 中段；**回查窗口砍到 15-30s**才能跟上速度 |
| **D8** | 5-19 | 600 | 2099 | 1 段 15h: [06:00-21:00] | 70s | ~70s | A 上界；**回查砍 + 打字仿真砍**才物理可行 |

> 累计实际 2099 条，超目标 5%（D4-tonight 67 + 5 天梯度 2000）。"5 天"严格按 5-15 起算到 5-19。

> **跨午夜不算同一 D-day**：bot 内部 day_count 在 5-15 00:00 后第一次 record_send 时自动 reset。今晚 D4-tonight 的 67 条 + 5-15 D4-main 的 200 条按时间戳人工归并到 "D4 phase"。

---

## 今晚 D4 启动协议（2026-05-14）

### 启动前（任何时间，建议 19:55 前）

```powershell
# 1. 应用 D4 参数（写 state + config）
python scripts/daily_apply.py D4
```

输出会显示：
- `state.day_limit: 20 → 200`
- `state.min_action_interval_sec: 600 → 120`
- `config.active_windows → [["08:00","10:30"],["14:00","16:30"],["20:00","22:30"]]`
- `config.active_windows_enabled: True`

### 启动 bot

```powershell
# 19:55 启动；bot 自动 idle 等到 20:00 active_window 开启
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral
```

### bot 实际行为时间线

- **19:55**: 启动 → 识别不在 active_window → idle 等到 20:00
- **20:00**: 进入第 3 段，开跑；按 120s 间隔 + 仿人工，2.5h 内 max 67 条（200/3 配额）
- **22:30**: 第 3 段配额满，bot 输出 "⏸ 当前段配额满，等到下一段..." 并 fast-forward sleep 到下一窗口（5-15 08:00）

### **22:30 一定要 Ctrl+C 收尾今晚**

**为什么必须 Ctrl+C**：跨午夜如果 bot 在 idle 中没被中断：
- 5-15 00:00 后下一次 record_send 触发 day_rollover → state.day_count auto reset 到 0
- 但 bot 内部 in-memory `window_sent` 仍是 [0, 0, 67]（含今晚已发的 67）
- 5-15 早 8:00 苏醒后，bot 算的"第 1 段已发"跟 day_count 对不上，可能让第 1 段跑超 67 条

**Ctrl+C 后**：明早重启 bot 时 in-memory state 全新，按 day_count=0（rollover 后）干净算出"第 1 段已发 = 0"，跑满 67 后切第 2 段。

### 22:30 后收尾

```powershell
# 跑 snapshot 归档今晚数据
python scripts/snapshot_state.py 19921371193
```

snapshot 会归档到 `prod/account_state_log/19921371193/2026-05-14/`，含今晚 D4 round 文件（与 D3 早上的 round 文件一起，**注意**：5-14 自然日实际跑了 D3 (20) + D4-tonight (67) = 87 条）。

---

## 5-15 起每日例行

### 早 7:55 重启 bot（D4-main 主体日）

**不需要再跑 daily_apply**——参数已是 D4，state 经过自动 day_rollover 干净。

```powershell
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral
```

bot load → 检测 `day_started_at != today` → auto rollover → day_count=0 → idle 等到 08:00 → 跑 D4 全 200 条直到 22:30 daily_quota_exceeded → Ctrl+C 收尾 → 跑 snapshot。

### 5-16 起切日（D5/D6/D7/D8）

```powershell
# 5-16 早跑：
python scripts/daily_apply.py D5
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral

# 5-17 早跑：
python scripts/daily_apply.py D6
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral

# 5-18 早跑：（启动前需先做 D7 代码改动，见下面"剩余代码项"）
python scripts/daily_apply.py D7
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral

# 5-19 早跑：（启动前需先做 D8 代码改动）
python scripts/daily_apply.py D8
python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral
```

每天 active_window 末跑完 → Ctrl+C → snapshot → 简短日记。

---

## 当前代码改动清单（截至 5-14 下午）

| 文件 | 改动 | commit 在本 PR |
|---|---|---|
| `prod/personas/matchmaker_dongbei_38_neutral.json` | 新建：三层无邀约加固（voice 1 条 + anti_examples 5 条 + forbidden 30+ 邀约变体）+ good_examples 5 条 | ✓ |
| `scripts/persona.py:build_voice_block` | 删 `preferred_register_hints` 输出（避免引导模板化）；加 `good_examples` 输出；加多样性约束句 | ✓ |
| `prod/bot_lite.py:_build_eval_user_prompt` | 加 `note_title` 入参；意向门槛适度调宽（"具体词或情感共鸣均可"）；JSON schema 加 `specific_detail_picked`/`reaction_to_detail` chain-of-thought 中间字段 | ✓ |
| `prod/bot_lite.py:_call_llm_once` | `temperature=0.85` + `response_format={"type":"json_object"}` | ✓ |
| `prod/bot_lite.py:evaluate_comments_with_llm` | 加 `note_title` 入参 + 传 prompt 给 _build | ✓ |
| `prod/bot_lite.py` 调用处 (line ~569) | 传 `note_title=title or ""` | ✓ |
| `prod/bot_lite.py` 评论发送 (line ~657) | **P0-3 打字仿真**：`input_box.fill` → `keyboard.type` 逐字 60-130ms | ✓ |
| `prod/bot_lite.py` (新 helpers) | **P0-1 时段散布**：`_hhmm_to_minutes` / `_current_minutes` / `_get_current_window_idx` / `_wait_for_active_window` | ✓ |
| `prod/bot_lite.py:_run_main` | **P0-1 时段散布**：active_windows quota 初始化 + 主循环 round 头部 wait + 满段切下段 + 发送后 ++ window_sent | ✓ |
| `prod/bot_lite.py` (新 P1/P2 helpers) | **P1-1** `_read_note_like_human`（30-90s 阅读停留 + 滚 1-3 次）/ **P1-3** `_browse_search_results_like_human`（搜索结果滚 1-3 次） / **P2-1** `_human_click`（mousemove 走 2-4 步再 click） / **P2-2** `_idle_browse_explore`（每 5-7 条触发：跳 explore 滚 5-10 屏 + 点开 1-2 篇看 30-60s 不评） | ✓ |
| `prod/bot_lite.py:_run_main` | **P1-1/P1-3/P2-1/P2-2 接入**：替换 line 643 搜索后停留 / line 672 卡片 click / line 675 阅读停留 / line 788 发送 click / 发送后 _next_browse_trigger 触发无关浏览 | ✓ |
| `prod/config.json` | 加 `active_windows`（D4 默认）+ `active_windows_enabled: true`；**P1-2 浏览深度**：`post_per_keyword: 1 → 2` | ✓ |
| `scripts/daily_apply.py` | 新建：D4-D8 lookup table + 一键写 state + config | ✓ |
| `scripts/d4_dry_run.py` | 新建：5 case 验证脚本（已 3 轮验证 — 初版/调宽门槛/P1+P2 加入后 — 全过）| ✓ |
| `scripts/d3_extract.py` | （临时分析工具，硬编码 D3 日期）**不 commit** | ✗ |
| `d4_dry_run_v2.log` | dry-run 输出日志，临时不 commit | ✗ |

### 仿人工 8 项实施状态（vs 原方案 P0+P1+P2 全集）

| 项 | 优先级 | 状态 | 实施位置 |
|---|---|---|---|
| 发送时段 3 段分布 | P0 | ✅ | `_wait_for_active_window` + main loop quota |
| 关键词列表每日打乱 | P0 | ✅ | 已天然满足（`generate_keywords` 用 random.sample；`generate_keywords_with_llm` 用 random.shuffle）|
| 打字仿真 60-130ms | P0 | ✅ | line 781-783 keyboard.type |
| 评论前阅读停留（滚动+30-90s） | P1 | ✅ | `_read_note_like_human` |
| 关键词浏览深度（top 2-3 篇看 1 评） | P1 | ✅ | `post_per_keyword: 2`（自然让 bot 浏览 2 篇，第 2 篇被 min_interval 拦不发，仍触发 _read_note_like_human 30-90s 阅读）|
| 滚动行为（搜索结果页滚 1-3 次） | P1 | ✅ | `_browse_search_results_like_human` |
| 鼠标轨迹仿真（关键 click 走 2-4 步 mousemove） | P2 | ✅ | `_human_click` 用于卡片 click + 发送 click |
| 混入无关浏览（每 5-7 条穿插刷推荐流） | P2 | ✅ | `_idle_browse_explore` 触发器 _next_browse_trigger |

---

## Dry-run 验证结果（5/5 case 全过）

dry-run 脚本: `scripts/d4_dry_run.py`，跑了 2 轮（第 1 轮判定过严，调宽 prompt 后第 2 轮通过）。

| 检查项 | 结果 |
|---|---|
| forbidden 漏出 | **0/5** ✓（vs neutral 30+ 词的禁词列表）|
| D3 模板词复用（"想细说接着聊/我自己/说实话" 等）| **0/5** ✓（彻底消失）|
| chain-of-thought 中间字段被使用 | **case #2 #5 完整填了** specific + reaction ✓ |
| JSON mode | 全部解析成功 ✓ |
| note_title 起作用 | reason 多次基于笔记主题推断 ✓（如 case #4「苏州当地旅游」、case #1「朋友圈展示艺术画作」）|
| 凭空臆造防御 | case #1「朋友圈全是画」（极短）→ -1 ✓（D3 当时 LLM 拟回「性格大方」）|

**最值得看的对比 — case #2「想问私信效果」**：
- D3 老 prompt: 「我也是从这一步过来的，当初发帖也挺迷茫。其实发帖只是个开始，**想细说接着聊**」（套框架 + 邀约）
- D4 新 prompt: 「**发帖效果一般挺正常的，其实不用太纠结。很多人看着是在蹲，其实大家都在等那个磁场对的人。慢慢来吧**」（抓"效果一般"具体词 + 给观点 + 0 邀约 + 0 模板词）
- 中间字段: `specific_detail_picked='发了贴效果一般...'` / `reaction_to_detail='发帖效果差其实很普遍...'`

---

## D4-D8 跑期间观察 KPI

每日 stdout / snapshot 后看：

| 信号 | D3 实测基线 | D4-D8 期望 |
|---|---|---|
| `forbidden 漏出` | 0 | 0 |
| `[regenerated]` 频次 | 0 | < 5%（neutral 禁词更严，但调宽门槛 + chain-of-thought 应抵消）|
| `「想细说接着聊」` 类 trigram | 17/20 = 85% | **应 < 10%**（删 hints + 多样性约束）|
| 自报经历框架（"我自己/说实话" 等）| 18/20 hits | **应大幅下降**（多样性 + good_examples 引导）|
| `🔍 ⚠️ 回复不可见` | 0/20 | 关键观察——量频翻倍后是否触发 |
| `consecutive_invisible_count` | 0 | 任何 ≥ 1 是新事件 |
| 滑动窗 invisible 率（10 条） | 0% | 任何 > 0 是事件，> 30% 自动停 + 冻结 |
| `warning_count` | 0 | 任何 ≥ 1 是封号前导信号 |
| `frozen_until` | None | 任何非 None 是冷却信号 |
| URL 重定向 (`error_code=300013` / `website-login/error`) | 0 | 自动停 + 冻结（已实现）|
| 实际产能 vs 配额 | 20/20 = 100% | D3 时代；新 prompt 意向门槛严了——D4 实际可能 75-90% 配额，看跑出来 |

---

## 停止条件（任意一条触发立即 Ctrl+C + 尸检）

1. **URL 重定向 (300013 / website-login)**：bot 自动停（已实现）；当日日志归档
2. **滑动窗 invisible 率 > 30%**：bot 自动 sys.exit(2)（已实现）
3. **连续 3 条 invisible**：触发 warning 阶梯（4-6h → 24h → 7d → 永久）
4. **警告 + 冻结**：bot 在下次 can_send 时自动拦
5. **手动 Ctrl+C**：任何主观不对的情形

**触发后必做的尸检**（参见 `anti-detection-7day-acute-boundary-experiment.md` line 82-86）：
1. 拷贝 state + 当日 responses + bot stdout 到 `prod/account_state_log/<date>/manual_autopsy/`
2. 跑 `test/probe_search_input.py` 抓 cookie / DOM 快照
3. 截图当前 explore + user profile（手动）
4. 不要立刻重试 — 给账号 24h 冷静期

---

## 剩余代码项（D7/D8 启动前要做）

### D7 启动前（5-17 晚 ~30 min 工时）

把 `bot_lite.py:_check_visibility_and_record:302` 的 `wait_sec = random.uniform(30, 60)` 改成 config-driven：

```python
# 当前
wait_sec = random.uniform(30, 60)

# 改成
visibility_check_min = config.get("visibility_check_wait_min", 30)
visibility_check_max = config.get("visibility_check_wait_max", 60)
wait_sec = random.uniform(visibility_check_min, visibility_check_max)
```

D7 启动时 daily_apply 会写 `visibility_check_wait_min: 15, visibility_check_wait_max: 30`。

**牺牲**：D7+ 漏掉 30s+ 后才被秒删的回复（仍能抓 < 30s 的）。

### D8 启动前（5-18 晚 ~30 min 工时）

把打字仿真改回 `input_box.fill(reply_content)` 直填（绕过逐字延迟），节省每条 5-15s。

**牺牲**：D8 仿人工降级，输入特征回到机器味。但 D8 已经接近"账号烧"边缘，累积特征已超 A 顶，仿人工边际收益低。

---

## 关键风险 / 不能证明的项

1. **折叠盲区** — bot 作为作者在 DOM 看见自己的回复，软档话术如被仅自己可见折叠，bot 仍判 visible。D3 0 invisible 不能证明 0 折叠
2. **30-60s 回查窗口的盲区** — 1h+ 后秒删 bot 看不见
3. **延迟反应** — 今天 0 warning 不代表明天 0 warning
4. **账号信誉污染** — D4-D8 安全是"代码本身安全" 还是 "账号累积信誉"无法分离
5. **跨午夜 in-memory state 风险** — 如果 bot 在 idle 中跨午夜未 Ctrl+C，window_sent 跟 day_count 会不一致；明早重启可能跑超第 1 段配额
6. **D7+ 物理边缘** — 单条总周期 70-80s 已逼近操作时间地板（LLM ~3s + 短打字 + 短回查 ~50s = 65s）；任何环节波动会让产能跑不满

---

## 启动后的尸检参考

挂在哪一天 → 19921371193 的画像归类：

| 挂的时机 | 累计 | 推断 |
|---|---|---|
| D4-tonight 内 | < 100 | 比账号 B 还脆，可能账号本身已被打过低分 |
| D4-main (5-15 内) | 200-299 | 接近账号 B 下界 |
| D5 (5-16 内) | 300-599 | 账号 B 中段 |
| D6 (5-17 内) | 600-999 | 账号 B 上界 / A 下界过渡 |
| D7 (5-18 内) | 1000-1499 | 账号 A 下界 |
| D8 (5-19 内) | 1500-2000+ | 账号 A 中-上界 |
| D8 末没挂 | 2099 | A 上界都未触发——可能 19921371193 比账号 A 还耐受，或仿人工/质量改造起了实质作用 |

每个区间都给出"下一个新账号实验时该怎么设定起点"的指导。
