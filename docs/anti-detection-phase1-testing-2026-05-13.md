# Phase 1 反检测测试纪要

测试日期：2026-05-13
测试范围：Phase 1 改动（`scripts/account_state.py`、`scripts/risk_control.py`、三个 bot 入口的集成挂接、`prod/config.json` 的 delay 收紧）
测试账号：1 个真实测试账号（号码不在文档中保留）

## 测试方法

按"无风险 → 低风险 → 真发"的顺序分四层：

| 层 | 方法 | 风险 | 验证什么 |
|---|---|---|---|
| Layer 1 | 单元测试 | 零 | `account_state` / `risk_control` 模块逻辑层 |
| Layer 2 | 双进程锁测试 | 零 | `single_instance` 同账号互斥 |
| Layer 3 | 注入 `frozen_until` 干跑 | 零（bot 进发送门口被拦） | 三 bot 入口的集成挂接、不误发、state 不污染 |
| Layer 4 | 临时调紧参数真发 2 条 | 低（消耗 2 条评论额度） | `record_send` / `min_interval` / `daily_quota` 真实运行 |

实际跑了 Layer 1、Layer 3、Layer 4。Layer 2 因 bot 启动行为隐式覆盖了锁路径，跳过单独测试。

## 测试产出（test/ 下新增 3 个文件）

- **`test/test_account_state.py`** — Layer 1 单测；8 用例覆盖：默认 state 初始化、`min_interval` 拦截、`daily_quota` 拦截、跨日 `day_count` 重置、`frozen_until` 拦截、warning 阶梯（4-6h / 24h / 7d / 永久）、URL 风控分类、`check_and_record` 联动写入。直接 `python test/test_account_state.py` 运行，不依赖 pytest。
- **`test/probe_search_input.py`** — 9222 上 Chrome 实例诊断探针；列出所有 tab、所有 cookie（含关键登录 cookie 命中/缺失）、explore 页登录态。修复 bot_lite "找不到搜索框" 时的核心调查工具。
- **`test/smoke_openrouter.py`** — OpenRouter API 健康检查；最小 chat completion 请求验证 key 可用 + 不在 quota 上限。用于排除 LLM 故障干扰反检测验证。

## 验证结论

| 验证项 | 状态 | 证据 |
|---|---|---|
| Layer 1 — 模块逻辑 | ✅ 8/8 通过 | `python test/test_account_state.py` 全 PASS |
| Layer 3 — 启动检查 `can_send` | ✅ | bot 启动 banner 后打印 frozen 警告 |
| Layer 3 — 主循环 `can_send` 拦截 | ✅ 11 次拦截 | `⏸ 跳过发送：frozen until ...` 在 11 个候选回复处出现，bot 走完整管线被拦在发送门口 |
| Layer 3 — `single_instance` 锁 | ✅ 隐式 | 多次启动 bot 无冲突，bot 正常退出锁正常释放 |
| Layer 3 — `record_send` 不误触 | ✅ | 跑完 state 文件 `day_count=0`、`last_action_at=null` |
| Layer 3 — `record_warning` 不误触 | ✅ | 跑完 `warning_count=0` |
| Layer 4 — `record_send` 真触发 | （跑完更新） | day_count 应递增到 2 |
| Layer 4 — `min_interval` 真拦截 | （跑完更新） | 临时设 180s，应在第 2 次发送前看到 `min_interval_not_met` |
| Layer 4 — `daily_quota` 真拦截 | （跑完更新） | 临时设 day_limit=2，达标后应看到 `daily_quota_exceeded (2/2)` |

## 测试期间暴露的 4 个文档外问题

测试不只是验证设计，更是把"假设的环境"和"真实的环境"对齐——这一轮跑出 4 个原审阅文档没覆盖的真问题。

### 问题 A：`.env` 缺 `KEY=` 前缀，所有 LLM 调用静默失败（已修）

**位置**：项目根 `.env`
**现象**：文件单行 raw API key（`sk-or-v1-...`），没有 `OPENROUTER_API_KEY=` 前缀。
**根因**：bot_lite.py:30 等处的 `.env` 解析器要求 `if line and not line.startswith("#") and "=" in line`，无 `=` 的行被静默跳过。
**影响**：`OPENROUTER_API_KEY` env var 永远是空的，`evaluate_comments_with_llm` / `get_keywords_with_llm` 一直挂；bot 看起来在跑但从不会"发现潜在客户"，没人会怀疑是 .env 格式问题。
**修复**：给那行加 `OPENROUTER_API_KEY=` 前缀。
**教训**：bot 入口应该在 LLM 第一次失败时打印更明确的诊断（"OPENROUTER_API_KEY 未设置 / 401 / quota exceeded" 分别区分），别一律 fallback 到静态词。否则下次出现同样问题排查成本一样高。

### 问题 B：`cdp_publish.py login` 只登 creator 子域，bot 需要 www 子域要二次扫码

**位置**：`scripts/cdp_publish.py` 的 `XHS_CREATOR_LOGIN_CHECK_URL = "https://creator.xiaohongshu.com"`，登录跳转 `creator.xiaohongshu.com/login`。
**现象**：扫码后 creator 子域显示已登录，但 bot_lite 跳到 `www.xiaohongshu.com/explore` 仍弹 login-modal。
**实测验证**（probe_search_input.py 输出）：cookies 共享给 www（`web_session` / `a1` / `webId` / `xsecappid` 4 个关键 cookie 全在），但 www 仍判定未登录。
**根因推断**：XHS 按子域分别签发 access-token——cookie 列表里有 `access-token-creator.xiaohongshu.com`，但没有 `access-token-www.xiaohongshu.com`。**cookie 共享 ≠ 子域已登录**。
**临时绕过**：扫码完成后，手动在同一个 9222 Chrome 上再访问 `www.xiaohongshu.com`，弹出 login-modal 时再扫一次。
**应做改造**（不在 Phase 1 范围）：`account_manager` 的登录命令应该串两子域，避免每个新账号都要手动扫两次。这个问题不修，矩阵账号扩展会很痛。

### 问题 C：bot_lite 跑 2-3 轮后 explore 偶发 timeout

**现象**：第 2-3 轮某个 keyword 时 `page.goto("xiaohongshu.com/explore")` 30s 超时崩溃。
**触发**：不固定，没有清晰复现条件。
**推测**：XHS 对 9222 调试端口流量做了某种节流，或网络偶发。
**优先级**：低。前 1-2 轮稳定，每轮都能发 1-2 条评论，对验证目标够用。
**绕过**：bot 是无限循环，重启即可。生产路径上需要加"goto timeout → 等待重启"的恢复逻辑。

### 问题 D：`evaluate_comments_with_llm` 的 system prompt 把 LLM 锁死在同一套话术（P0 实证）

**这是这一轮测试最重要的发现。** 评审文档 P0 #1 描述的"跨账号内容 DNA 同质化"在这一轮 Layer 3 干跑里**得到了实证**：LLM 生成的 11 条候选回复全部具有同一 DNA。

11 条候选回复中：
- 全部含 "私信我" / "私我" / "后台" 推私信
- 9/11 含 "同频" 或 "精准匹配"
- 7/11 含 "优质单身" 类表述
- 结构清一色 "共情对方 → 我有解决方案 → 联系我"

**意外发现**：`bot_lite.py` 不读 `prod/profiles/*.json`、也没有写死的 `SERVICE_DESC`，按评审文档原本的判断它应该比 `comment_bot.py` / `general_comment_bot.py` 干净。但 `evaluate_comments_with_llm` 的 system prompt 把 LLM 锁在同一套销售话术里——**实际效果跟 SERVICE_DESC 写死没区别**。

**评级提升**：评审文档原把 P0 #1 列为 "Phase 2 第二阶段"。基于这一轮观察，这条的紧迫性比设想的高一档：

- 按 day_limit=5 跑 14 天 = 单号 70 条评论几乎都是这个调调
- 矩阵扩账号后，所有号都用同一段 system prompt → trigram 重叠率会非常高
- 风控按文本相似度聚账号环这条线，基本是被这套 system prompt 喂出来的

**建议**：Phase 2 第一项就上 system prompt 多样化（不止是 SERVICE_DESC 同义池，更是把 system prompt 本身按 persona 拆分），不要等其它 Phase 2 子项。

## 给后人的几条操作经验

按踩坑顺序：

1. **新账号"已登录"判定靠不住，要双子域确认**——只看 creator 显示已登录会被骗，bot 实际操作的是 www；用 probe 脚本核对 explore 页的 `loginModalPresent` 和搜索框 placeholder 才是地面真相。
2. **bot_lite 找不到搜索框首选怀疑登录态**，不要先怀疑选择器漂移。`#search-input` 这种简单 ID 不容易变，被 modal 遮住的概率高得多。
3. **LLM 故障会假装成"反检测在工作"**——bot 跑完 state 干净不等于反检测拦下来了，可能是根本没走到发送步骤。Layer 3 必须配合 LLM 健康检查（用 `test/smoke_openrouter.py`）才有意义。
4. **state 文件是验证 bot 行为的最强证据**——`day_count` / `last_action_at` / `warning_count` 比 stdout log 可信得多。每次跑完 bot 都看一眼 state 文件，就知道它真的做了什么。
5. **不要轻易消耗测试账号额度**——Layer 4 用临时调紧参数（day_limit=2 / min_interval=180）能在 5-10 分钟内验证完，不必跑完一整天的 5 条 quota。验证完一定要把参数恢复到生产值。

## 后续

- [ ] Layer 4 跑完更新本文上面的表格
- [ ] 决定 Phase 2 是否提前启动（基于问题 D 的实证）
- [ ] 修问题 B（双子域登录），矩阵扩账号前必须做
- [ ] 加 LLM 故障的明确诊断输出（问题 A 的教训）
- [ ] bot_lite explore timeout 的恢复逻辑（问题 C，低优）
