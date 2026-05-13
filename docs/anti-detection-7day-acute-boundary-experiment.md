# 单账号 7 天急性安全边界实验

实验设计日期：2026-05-13
实验账号：1 个（号码不入档）
关联文档：`docs/anti-detection-review-2026-05-09.md`、`docs/anti-detection-phase1-testing-2026-05-13.md`、`docs/anti-detection-phase2-p0-1-results-2026-05-13.md`

## 目标 & 边界

**测什么**：在选定的"日量 × 间隔"参数轨迹上，找出 19921371193 这个单一账号被 XHS 在**几小时内**触发警告/封禁的**急性上限**。同时记录**前导信号**（回查不可见率上升 / 评论秒删）出现到硬警告的时间差。

**测不到什么**（坦然接受，不在 1 周窗口内勉强追求）：
- chronic 安全水位（需要 ≥14 天稳定观察才有意义）
- 同质化识别的 trigger 阈值（XHS 需要多天聚类）
- 跨账号封禁连带效应（需 N≥2）
- 任何形式的"这个 profile 永远安全"的结论（censored experiment 本质）

**chronic 安全水位估算**：通常 ≈ 急性上限的 50-70%。1 周后会从急性数据外推一个估计值，但不应作为生产决策的唯一依据——需要等 N≥2 账号长期观察验证。

## 7 天日程表

| 天 | day_limit | min_interval | 目标 |
|---|---|---|---|
| **D1** | 3 | 2400s | 仪表板自检 + 校准。任何异常停下排查 |
| **D2** | 5 | 1800s | 当前生产配置基线，期望全过 |
| **D3** | 8 | 1200s | 60% 加压。观察 invisible 率是否抬头 |
| **D4** | 12 | 900s | 接近社区共识上限（5-10 条/天），前导信号最可能浮现 |
| **D5** | 18 | 600s | 超出共识，高概率触发警告 |
| **D6** | 25（若 D5 撑过）/ 硬停（若 D5 已挂） | 600s 或停 | 找急性顶 |
| **D7** | 35（若仍撑过）/ 复盘整理（若已挂） | 600s 或停 | 用尽账号或宣告未挂 |

**实验核心思想**：1 周资源紧张时，**用账号的剩余命换数据精度**。目标不是保号，是主动让账号失败以定位失败点。

## 停止条件（任意一条触发即立即停 + 尸检）

| 信号 | 含义 | 动作 |
|---|---|---|
| URL 重定向 (error_code=300013 / website-login/error) | 已有反检测在抓——warning 阶梯自动启动 | bot 自动停（已实现）；当日日志归档 |
| 最近 10 次回查 invisible 率 > 30% | 软封前导信号 | bot 自动停（**仪表板需要实现这条**）|
| 连续 3 条 invisible | 已触发 warning 阶梯（已实现 P0-3）| bot 自动停 + 冻结 |
| 关键 cookie 变化（web_session 长度变化、xsecappid 消失）| 登录态被风控调整 | **手动观察**——bot 不一定能感知；每日跑 probe 脚本 |
| 你手动 Ctrl+C | 任何主观不对的情形 | 优先权高于所有自动逻辑 |

**触发后必做的尸检**：
1. 立刻拷贝 state 文件、当日 responses 文件、bot stdout 到 `prod/account_state_log/<date>/manual_autopsy/`
2. 用 `test/probe_search_input.py` 抓一份 cookie / DOM 快照
3. 截图当前 explore 页与 user profile 页（手动）
4. 不要立刻重试发评论——给账号至少 24h 冷静期再观察

## 必备仪表板（D1 启动前**必须**完成）

| 项 | 实现位置 | 不做会怎样 |
|---|---|---|
| **每日 state snapshot 归档** | `scripts/snapshot_state.py` 独立脚本，每天 23:55 跑一次（手动或 Task Scheduler）；归档到 `prod/account_state_log/<account>/<date>/` | state 每天被覆盖，跨天对比丢数据 |
| **invisible 率自动 alarm** | `account_state.py` 加 visibility 滑动窗（last 10）+ rate 计算；`bot_lite.py` 在 rate > 30% 时大字提示 + 自动退出 | 你得人肉盯 stdout，后台跑出事不停 |

## 每日操作流程（SOP）

### 早晨（启动前）
1. 检查昨天的 snapshot 是否成功归档（`prod/account_state_log/<acc>/<昨天日期>/` 存在 state.json）
2. 切换今日参数：编辑 state 文件或跑命令调 `day_limit` / `min_interval_sec`
   - 例（D3）：`python -c "import sys; sys.path.insert(0, 'scripts'); import account_state; acc='19921371193'; s=account_state.load(acc); s['day_limit']=8; s['min_action_interval_sec']=1200; account_state.save(acc, s)"`
3. 启动 bot：`python prod\bot_lite.py`

### 白天（跑期间）
- 每隔 1-2 小时扫一眼 stdout，关注：
  - `🔍 ⚠️ 回复不可见` 出现频次
  - `⚠️ [风控] ...` 任何出现都是事件
  - `[regenerated]` 频次（如果持续 > 30% 说明 persona 仍在朝禁用词漂移）
- 任何感觉不对：Ctrl+C，按尸检流程处理

### 夜间（关停前）
1. Ctrl+C bot
2. 跑 snapshot：`python scripts\snapshot_state.py 19921371193`
3. 简要在本文档"日记"段写一行（见下）

## 当日日记模板（请在跑完每天后填）

```
### D1（2026-MM-DD）
- 配置: day_limit=3, min_interval=2400s
- 实发: ?? 条
- invisible 计数（连续/累计）: ? / ?
- warning 触发: 无 / 第 X 次
- 主观读感（回复质量、bot 行为）: ...
- 异常事件: ...
- 决定下一天是否继续按计划: ...
```

## 结果汇总模板（D7 跑完填）

```
## 实验输出

### 急性上限单点估计
- 在 day_limit=__ 触发警告 / 在 day_limit=__ 仍未触发
- chronic 安全水位**估算**: 急性 × 50-70% = __ ~ __ 条/天（**未经长期验证**）

### 前导信号到硬警告的时间差
- invisible 率首次 > 30% 时间: D__
- 硬警告时间: D__
- 时差: __h

### 内容 DNA 漂移观察
- D1 vs D7 回复 DNA 是否有变化（trigram 重叠率 / 禁用词命中率）

### 后续动作
- 下一阶段实验设计变更
- N=2 账号上线时间表
```

## 后续（D7 之后）

- 把急性边界数据回写到 `docs/anti-detection-review-2026-05-09.md` 的"3-5/天养号节奏"条目，把社区共识替换/补充为本项目实测
- 启动评审文档 Phase 2 P0-2 trigram 去重 / P1 多 persona binding——具体取决于 N=2 账号何时就位
- 如果账号撑过 7 天未挂：考虑延长观察到 14 天（chronic 信号），或转入 N=2 准备

## 实验日记

### D1（2026-05-13）
- 配置: day_limit=3, min_action_interval_sec=2400s
- 实发: 3 条（全部命中评论意向 → DOM 提交 → 30-60s 后回查可见）
  1. 「真心想找确实挺不容易…慢慢碰，总能遇上那份踏实」（关键词：分配对象 / 评论："05年"）
  2. 「我以前也觉得是环境不行…大家不是不想，是怕受伤」（关键词：脱单超能力 / 评论："这个时代就是不会有人想谈"）
  3. 「不被父母祝福的感情，结了婚也是受罪」（关键词：拒绝被迫相亲 / 评论：4 年男友被父母安排相亲）
- invisible 计数（连续/累计）: **0 / 0**
- visibility_window 终态: `[true, true, true, true]`（含早晨 1 条历史 + D1 三条）
- warning 触发: **无**
- regeneration: 1 次（关键词「机构」被 forbidden_phrases 后过滤，符合 P0-1 设计）
- 已知 bug 复现 2 次: `selected_index=0` 被错判为"无意向"（reason 明确"意向最强"）—— 已在 `anti_detection_status.md` 跟踪，不阻塞 D1，D7 后修
- 主观读感:
  - 3 条实际发出的回复**质量都不错**：基于评论者具体处境共情 + 观点延伸，无 persona 自报年龄、无"看我主页"导流、无编造个人经历
  - 但**没发出去的候选**里出现了 3 类需要警惕的漂移：
    1. "看看我主页" 引流话术（3 例）—— 比 DNA 销售词隐性的导流模式
    2. persona 字面值自报（"我也是 38 岁"、"我自己当初相亲也总卡在身高上"）—— LLM 蹭评论关键词往 persona 上贴
    3. 编造背景（对纯调侃评论"笑死我了"编了"想在本地找个踏实人"的心思）
  - 这些都被 min_interval=2400s 的高间隔意外拦了下来，**D2 把 min_interval 降到 1800s 后命中概率会上升**，需要重点观察
- 仪表板表现:
  - 回查链路全部 ✅，DOM evaluate 工作正常
  - 30-60s 回查窗口 OK，未出现"评论区 DOM 为空"跳过
  - 滑动窗 invisible 率始终 0%，远低于 30% 阈值（也意味着 alarm 路径未被实测触发——D3+ 加压时再观察）
- 异常事件:
  - `scripts/snapshot_state.py` 首次跑时 Windows cp1252 stdout 编码炸，归档成功但 print 失败——已修（reconfigure utf-8）
  - 5-12 当日 snapshot 缺失，无法补——D2 起每晚必须跑
- 决定下一天: **按计划继续 D2**（day_limit=5, min_interval_sec=1800）。第一件事：起 bot 前重点盯发出去的 5 条里有没有出现今天观察到的 3 类漂移信号。
