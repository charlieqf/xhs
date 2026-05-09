# XHS 评论自动化反检测审阅报告

审阅日期：2026-05-09
审阅范围：`prod/comment_bot.py`、`prod/general_comment_bot.py`、`prod/bot_lite.py`、`prod/comment_count.py`、`prod/profiles/*.json`、`scripts/chrome_launcher.py`、`scripts/cdp_publish.py`、`scripts/account_manager.py`
关注点：风控触发面 / 反检测姿态 / 拟人化行为 / 多账号矩阵安全

## 审阅结论

整体架构方向是对的——CDP attach 真实 Chrome + 持久化 user-data-dir + 一账号一 profile，已经避开了 Playwright launch 模式下 90% 的指纹问题。但当前实现里有几处**会直接缩短账号存活时间的设计缺陷**：

1. **跨账号内容 DNA 高度同质化**——profile 里写死的 service 介绍 + intent terms + 同一 LLM/同一 system prompt，是 2026 年 XHS 风控的头号封号轴线。
2. **没有任何账号级速率上限**——`while True:` 循环 + `random.uniform` 延时，单号每天可发 200+ 条评论，远超人类基线；也没有"账号收警告就冻结"的状态机。
3. **没有软封反馈环**——评论失败/被屏蔽/零曝光都不会触发账号侧的暂停，dead account 会被持续锤击直到硬封。
4. **CDP 协议层残留风险面较窄**——本仓库未启用 `Runtime.enable` / `Console.enable`，仅启用 `Page.enable` / `Network.enable` / `DOM.enable`，避开了业界讨论较多的两条协议层泄漏；attach 模式固有的 `debugger;` 检测无解，作为已知 trade-off 接受。
5. **点击前置动作缺失，延时分布有统计学指纹**——点击已派发 `mousePressed` + `mouseReleased`（不是"鬼点"），但缺少 `mouseMoved` 前置轨迹、缺少 press/release 之间的人类停顿、缺少坐标随机偏移；`random.uniform` 延时分布与真人对数正态分布差异明显。

## 关键背景：2026 年 XHS 风控环境变化

在给出问题清单前，先记录几条**会改变结论的环境事实**，避免按 2024 年的认知制定方案：

- **检测窗口从 ~14 天压缩到 ~1 天**。`xiaohongshu-mcp` issue #674 多个运营者报告 2 小时一次自动回复，第二天直接封号；TimYuJian 给出的封禁阶梯：未实名 → 冻结 → 实名解冻 → 自动发文即警告 → 7 天 → 永封。
- **官方 H1 2025 战报**：封号 1000 万+，部署 50+ 模型，"同质化识别"召回率 +72%，处理低质 AI 笔记 60 万+（chinadaily / qbitai 报道 RedOne 模型）。
- **只浏览不发文也会被警告/封号**（issue #674：CPU-beng、jsadu826）。
- **"仅自己可见"绕不过**（issue #680）。
- **签名机制 ~月度轮换**：`x-s` / `x-t` / `x-s-common` / `xsec_token` 持续变更；新增 `sec_poison_id`、`websectiga`、`acw_tc`、`x-b3-traceid` 等参数（RedCrack / xhshow 项目）。
- **`puppeteer-extra-plugin-stealth` 单独已不够用**——能盖住指纹，盖不住行为节律（issue #674）。

## 做得好的部分

- **CDP attach + 持久 profile**（`scripts/chrome_launcher.py:111-175`）：直接复用真实 Chrome 进程，避免 `--enable-automation` 痕迹。MediaCrawler 维护者 2026 年也已切到这个路线（issue #865）。
- **多账号 profile 隔离**（`scripts/account_manager.py` + `chrome_launcher.py:79-98`）：cookie / localStorage / IndexedDB 都按账号隔离，比 cookie 文件管理稳。
- **关键词随机洗牌**（`prod/comment_bot.py:114`）+ `post_per_keyword` 抽样：避免按固定顺序遍历的可预测模式。
- **`bot_lite.py` 的两档软封感知**（`prod/bot_lite.py:213-230`）：识别 `error_code=300013` 后冷却 60-120s，识别 `website-login/error` 重定向后冷却 90-180s。"硬重定向 → 长冷却"的思路是对的，是当前 codebase 唯一的软封反应路径，需要泛化到所有 bot 入口。
- **CDP 协议启用面偏小**：`scripts/cdp_publish.py` 仅启用 `Page.enable` / `Network.enable` / `DOM.enable`（cdp_publish.py:608, 866-867, 967, 1103, 1143, 1546, 3337-3338, 3486），未启用业界讨论较多的 `Runtime.enable` / `Console.enable`，避开了 Patchright 修补的两条主要协议层泄漏。
- **点击事件已发完整 DOM 事件**：`_click_element_by_cdp`（`scripts/cdp_publish.py:4123-4155`）通过 `Input.dispatchMouseEvent` 派发 `mousePressed` + `mouseReleased`，DOM 端能正常收到 `mousedown` / `mouseup` / `click`，不是协议层级的"鬼点"。

## 主要问题

### 1. 跨账号回复内容 DNA 同质化（P0）

- **严重级别：极高**
- **位置**：项目里有两条同质化路径，修复方式不同，需分别处理。
  - **路径 A — `prod/comment_bot.py`（相亲业务）**：`prod/comment_bot.py:261-265` 写死 `SERVICE_DESC = "为单身人群提供真诚靠谱的脱单交友服务……"`；`evaluate_comments_with_llm`（`prod/comment_bot.py:268-350`）用同一字符串拼 system prompt。**所有跑这个 bot 的账号共用同一段写死字符串，LLM 也是固定 OpenRouter 同模型。** 此路径不读 `prod/profiles/`。
  - **路径 B — `prod/general_comment_bot.py`（profile 驱动）**：`prod/general_comment_bot.py:52, 60, 221-222, 323-324` 加载 `prod/profiles/*.json`。`prod/profiles/medical_beauty.json:2-14` 把 service 介绍、intent terms、tone 写死，**所有挂同一 profile 的账号共享语料 DNA**。
- **现象**：
  - 路径 A：`SERVICE_DESC` 固定 → 出现在每条 LLM prompt 中 → 跨账号回复语义指纹极强；同一 LLM、同一 system prompt，温度即使拉高，分布仍是同一个。
  - 路径 B：profile JSON 是矩阵账号共用模板；intent 命中规则也是固定枚举，命中后回复方向高度同质。
  - 两条路径都没有跨账号 n-gram 去重——两个号在相邻时间命中同类笔记，回复极易高 trigram 重叠。
- **证据**：tuokeba.com 报告 37 个矩阵号一夜全封 + 绑定手机注销；XHS 官方 H1 2025 数据显示"同质化识别"召回率 +72%；XHS 自训 SNS 大模型 RedOne 专门做评论级语义聚类（**注：以上数字为方向性证据，本项目未独立验证**）。
- **影响**：这是单点封号风险**最高**的设计缺陷。一旦风控按文本相似度聚出"账号环"，整环连带封禁。
- **改进**：
  - 路径 A：把 `SERVICE_DESC` 从单字符串改成同义池（10+ 种表述），按账号绑定子集；不同账号用不同 LLM（Gemini / Claude / Qwen 混用）+ 不同 temperature / top_p。
  - 路径 B：profile 拆分为 `service_profile`（业务定义，开发用）和 `persona_profile`（每账号独立的语气 / 口头禅 / 错别字 / 常用 emoji）；同业务下 N 账号配 N persona。
  - 共用机制：增加跨账号 n-gram 去重，每条候选回复入库前查最近 14 天**所有账号**的发出回复，trigram 重叠率超阈值即重新生成。轻量 sqlite 即可承载。

### 2. 没有任何账号级速率上限（P0）

- **严重级别：极高**
- **位置**：`prod/comment_bot.py:822`（`while True:` 主循环）、`prod/config.json`（实际生效的延时配置）、`prod/comment_bot.py:788-793`（仅作回退默认值）、`prod/processed_cache.json`（仅记 feed_id 状态）
- **现象**：
  - 主循环无终止条件，无每日/每小时上限。
  - 延时分两套：
    - `comment_bot.py` 的实际配置（`prod/config.json:9-12`）：`keyword_delay 3-8` / `post_delay 2-5`，且**未配置** `round_delay_*`，落回 `comment_bot.py:788-793` 的默认 15-30。
    - `general_comment_bot.py` + `medical_beauty.json` 配置 45-90 / 3-6 / 60-120，反而比前者保守。
  - 综合下来，`comment_bot.py` 单号每天能发 200+ 条评论，远超人类基线；`general_comment_bot.py` 较温和但仍无上限。
  - `processed_cache.json` 只记 `{feed_id: {status, reason, processed_at}}`，**没有 per-account 的滑动窗口计数，也没有"已收警告→冻结"状态字段**。
  - `bot_lite.py:213-230` 已有"重定向 → 60-180s 冷却"逻辑（见"做得好的部分"），但只 sleep 不累计 warning，单次冷却结束后立即恢复——没有上升到账号级冷冻。
- **证据**：
  - 当前社区共识的养号期预算：**每天 3-5 条评论 / 5-10 个赞 / 2-3 个关注**（taokeshow.com、知乎 1986458524751000419）。
  - MediaCrawler issue #544 观察到 300 篇笔记爬取即触发"访问频次异常"。
  - issue #769（2025 年 11 月）：即使 `CRAWLER_MAX_SLEEP_SEC=200` 也会在二级评论枚举中途被封——速率不是唯一信号，但**绝对是最强的信号之一**。
  - **注**：上述阈值（3-5 条/天）为社区方向性共识，本项目未独立验证。下方 `day=5、week=25` 是据此定的起步值，不是通过实测得到。
- **影响**：与问题 1 叠加，是当前架构下封号的两条最快路径。
- **改进**：
  - 第一阶段最小可用 schema，避免过度设计：
    ```json
    {
      "<account_name>": {
        "day_count": 0,
        "day_started_at": "2026-05-09T00:00:00",
        "day_limit": 5,
        "last_action_at": null,
        "min_action_interval_sec": 1800,
        "frozen_until": null
      }
    }
    ```
    每条评论发出前 check：`day_count < day_limit` 且 `now > frozen_until` 且 `now - last_action_at >= min_action_interval_sec`。三条全过才放行。
  - **`min_action_interval_sec` 不可省**：`day_limit=5` + `keyword_delay_min=30` + `post_delay_min=10` 跑下来，5 条评论会集中在 ~6 分钟窗口内完成，剩下 23h54m 静默——这种 burst 本身就是机器节律。强制 ≥30 分钟（甚至更长）的最小操作间隔，把 5 条打散到一天里。
  - 第二阶段再补 `hour` / `week` 维度、`warning_count`、`last_warning_at`、软封计数。
  - `bot_lite.py:213-230` 的两档检测接入 warning 累计阶梯：1 次 4-6h（接续现有 60-180s 短冷却之后转长冷却）→ 2 次 24h → 3 次 7d → 4 次永久退役；同时把这套逻辑泛化到 `comment_bot.py`。`error_code=300013` 偶发命中也可能是真人手快，不要一次就重判 24h。
  - **同步收紧 delay 下限**：把 `prod/config.json` 的 `keyword_delay_min` 从 3 拉到 ≥30，`post_delay_min` 从 2 拉到 ≥10，与养号节奏对齐。否则单加配额、不动 delay，跑出来的节奏依然是"激进 + 早停"，不是"舒缓"。

### 3. 软封反馈环不完整（P1）

- **严重级别：高**
- **现状**：`bot_lite.py:213-230` 已有两档 URL 重定向感知（`error_code=300013` / `website-login/error`），命中即冷却。这是当前 codebase 中唯一的软封反应路径，思路对但仅覆盖"硬重定向"形态，且未泛化。
- **位置**：`prod/comment_bot.py:1037-1039`（评论失败仅记 string，未感知 URL 重定向）、整个 codebase 不存在"回复后回查可见性"的逻辑
- **缺口**：评论发出后不验证：
  - 回复是否真的对其他用户可见（**软封最常见的形态：折叠 / 仅自己可见**——`bot_lite.py` 的 URL 检测对此无能为力）
  - 是否被秒删（高敏感词或强风控判定）
  - 笔记下整体评论数是否增加（验证发送是否真的落库）
- **证据**：xiaohongshu-mcp issue #670/#672/#645 多次记录 `publish_content` 返回 200 但笔记并未落库的"假成功"——评论同理。
- **影响**：账号已经进入软封状态时，bot 仍以"成功"统计继续发，几小时内就把"轻封"踩成"硬封"。
- **改进**：
  - **泛化 `bot_lite.py` 的两档检测到所有 bot 入口**（`comment_bot.py` / `general_comment_bot.py`），并接入 `account_state.json` 的 warning 累计逻辑（见 #2）。
  - **新增可见性回查**：评论发送后 30-60s（用随机延时把"回查"也伪装成正常浏览），重新拉取该笔记的评论列表，匹配自己的 user_id + 文本前缀。
  - 连续 N 次（建议 N=3）回查不可见 → 该账号 `warning_count++` 并冻结 24h。
  - 把"回查不可见"作为独立指标记入 `account_state.json`，作为养号曲线的输入。

### 4. CDP 协议启用面残留风险（P2）

- **严重级别：低-中（无必做项，全部归入可选 / 观测）**
- **位置**：`scripts/cdp_publish.py:608, 866-867, 967, 1103, 1143, 1546, 3337-3338, 3486`（启用 `Page.enable` / `Network.enable` / `DOM.enable` 的调用点）
- **剩余风险面**：
  - **`Page.enable` / `Network.enable` / `DOM.enable` 在 attach 模式下的可检测性**：业界对这三者的检测讨论远少于 `Runtime.enable`，但理论上 `Page.frameNavigated` 等回调注册会改变某些时序特征。目前没有公开证据指向 XHS 检测这条，建议作为观测项。
  - **attach 模式固有的 `debugger;` 检测无解**：页面侧主动 `try { debugger; } catch (e) { /* 检查 e.stack 或执行时延 */ }` 可以判定 Chrome 是否被 attach。这跟项目是否启用某个 CDP domain、是否调用 `Runtime.evaluate` 都无关，只要继续用 attach 模式就盖不住。**这是 attach 路线的固有 trade-off，作为已知风险接受**——切换路线（如全 launch + Patchright）才能盖住，但代价远高于收益。
  - **裸 websocket 客户端 vs Patchright**：项目用裸 CDP 而非 Patchright，意味着如果未来真的需要启用 `Runtime.enable`（例如做事件监听），需手工实现泄漏修补。当前没有这个需求，留作设计空间。
- **改进**（全部为可选 / 观测，无必做项）：
  - **可选**：增加自检脚本——在 `xiaohongshu.com` 上跑一组指纹检测页面（如 bot.sannysoft.com、antoinevastel.com/bots），对比真实 Chrome 与本工具运行下的结果，作为回归测试。
  - **不建议短期内做**：迁移到 Patchright `connect_over_cdp`。`cdp_publish.py` 全文 5000 行，迁移成本高；当前协议启用面已经很小，ROI 不足。

### 5. 点击前置动作缺失 / 延时分布有指纹（P2）

- **严重级别：中**
- **位置**：`scripts/cdp_publish.py:4123-4155`（`_click_element_by_cdp`）、`scripts/cdp_publish.py:4109-4121`（`_click_mouse`，间隔 `time.sleep(0.05)`）、`prod/comment_bot.py:383`（`window.scrollBy`）、`prod/config.json` + 各 profile 的延时配置
- **现象**：
  - 点击：派发了 `mousePressed` + `mouseReleased`，但**点击前没有 `mouseMoved` 轨迹**、**press 与 release 间隔固定 50ms**（`cdp_publish.py:4121, 4155`）、**点击坐标固定为元素几何中心**（`cdp_publish.py:4142-4143`）——这三点叠加，事件时序仍呈"机器节律"。
  - 滚动：`window.scrollBy(0, pixels)` 是瞬时跳变，没有滚动惯性、没有滚动中停顿。
  - 延时分布：项目内统一使用 `random.uniform(a, b)`，分布形状与真人对数正态差异显著（真人短间隔为主，偶发长停顿）。
  - 没有任何"假动作"——不会随手划走、不会点错返回、不会停在无关笔记上发呆。
- **证据**：xiaohongshu-mcp issue #674 的 ccmagia2-gif 评论明确指出"行为节律是当前主要剩余检测面"；但贝塞尔曲线 vs 直线在浏览器端因 mousemove 节流到 ~60Hz，区分度不如预期。
- **影响**：单看一次操作不致命；但与问题 2 的高频叠加，会形成稳定的"机器节律"画像。
- **改进**（按 ROI 排序，不必全做）：
  - **必做（落地路径仅 1 行代码）**：项目里已有现成的 `_move_mouse`（`scripts/cdp_publish.py:4103-4109`，派发单条 `mouseMoved`），但没有被 `_click_element_by_cdp` 调用。在 `cdp_publish.py:4147` 派发 `mousePressed` 之前补一句 `self._move_mouse(cx + offset_x, cy + offset_y)`（offset 取 ±5~10 像素随机），并把同处的 `cx, cy` 也加 ±N 像素抖动，不要每次打元素正中心。
  - **必做**：press 与 release 之间从固定 50ms（`cdp_publish.py:4121, 4155`）改为 80-300ms 随机停顿。
  - **必做**：延时分布从 `random.uniform` 改为 `random.lognormvariate(mu, sigma)`，参数按真人滚动数据拟合（mu≈1.0, sigma≈0.6 起步）。
  - **可选**：每 5-10 个目标操作中插入 1 次"假动作"——随机滑两屏、点开一个无关笔记、3-8 秒后退回。
  - **可选**：`window.scrollBy` 改为分帧 `requestAnimationFrame` 滚动，带 ease-out 曲线。
  - **不建议**：贝塞尔鼠标轨迹——投入大、收益小，留到其他都做完再考虑。

### 6. headless 接口不应暴露给生产入口（P3）

- **严重级别：低（但便宜，建议直接做）**
- **位置**：`scripts/chrome_launcher.py:146`（`--headless=new` 可被传入）、`prod/*.py` 启动入口
- **现象**：`launch_chrome(headless=True)` 在生产路径上没有任何拦截。一旦误开，UA / WebGL renderer / 一系列 navigator 属性会立刻暴露 headless Chrome 身份。
- **影响**：单次误开就是即时硬指纹。
- **改进**：**不要在 `prod/*.py` 的 entry point 暴露 `--headless` 参数**——直接在调用处写死 `launch_chrome(headless=False)`。`chrome_launcher.py` 的 `headless` 形参保留给测试脚本用即可。assert + 环境变量后门是"留口子"思路，不如砍掉接口干净。

### 7. 多账号 IP 共享（P3，依赖外部条件）

- **严重级别：中（取决于运行环境）**
- **位置**：`scripts/chrome_launcher.py:111-175`（所有账号通过同一 CDP 端口 9222 走同一本地 IP）、`scripts/account_manager.py`（无 IP 绑定字段）
- **现象**：
  - 多账号串行轮换时共享公网 IP。
  - profile 隔离做了，但 IP / UA / 时区 / 操作时段相关性极强。
  - 没有"该账号绑定到哪个出口 IP"的元数据。
- **证据**：社区共识"一机一号一 IP"；XHS 风控对 IP 维度的账号关联识别已较成熟。
- **影响**：在矩阵规模较小（≤2 账号）时影响有限；规模超过 3 账号、且业务相似时是显著相关性来源。
- **改进**：
  - `account_manager` 新增 `proxy` / `egress_ip` 字段。
  - `chrome_launcher.launch_chrome` 支持 `--proxy-server=` 参数注入。
  - 高质量住宅代理或云手机出口；避免廉价/数据中心 IP。
  - 同一 IP 严格不并发，且不同账号操作时段应有自然区隔（不要号号都在 21:00-23:00 跑）。

## 改进路线图

按 ROI 与依赖关系排序。建议按阶段推进，每阶段稳定运行 7 天验证一波再上下一阶段。

### 第一阶段（1-2 天，立即做，封号风险下降一个数量级）

- [ ] **#2** 引入 `account_state.json` 最小 schema：`day_count` + `day_limit` + `last_action_at` + `min_action_interval_sec` + `frozen_until`；起步 `day_limit=5`、`min_action_interval_sec=1800`（≥30 分钟，避免 5 条挤在 6 分钟窗口里）
- [ ] **#2** 把 `prod/config.json` 的 `keyword_delay_min` 拉到 ≥30、`post_delay_min` 拉到 ≥10，与养号节奏对齐
- [ ] **#2/#3** 把 `bot_lite.py:213-230` 的两档 URL 检测泛化到 `comment_bot.py` / `general_comment_bot.py`，并接入 warning 阶梯：1 次 4-6h、2 次 24h、3 次 7d、4 次永久退役
- [ ] **#6** 砍掉 `prod/*.py` 入口的 `--headless` 参数，调用处写死 `headless=False`

### 第一阶段补充（3-5 天，单独验证，避免拖垮主线）

- [ ] **#3** 评论后 30-60s 回查可见性，3 次不可见即冻结。这一项含回查时机调度、评论列表 DOM 匹配 user_id + 文本前缀、连续 N 次状态机、失败计数持久化四个子项，单独花的时间接近 3-5 天，不要塞在第一阶段里跟主线一起跑。

### 第二阶段（3-5 天，对抗内容相似度风控）

- [ ] **#1** profile 拆分为 service_profile + persona_profile
- [ ] **#1** 同业务下 N 账号配 N persona + 多 LLM 混用
- [ ] **#1** 业务话术拆同义词池，按账号绑定子集
- [ ] **#1** 接入 trigram 跨账号去重器（轻量 sqlite 即可）

### 第三阶段（按需，对抗协议/行为层检测）

- [ ] **#2 续** 把 `account_state.json` 扩展为三层窗口（hour/day/week）+ `warning_count` + `last_warning_at`
- [ ] **#4** 给 `Runtime.evaluate` 调用点加 try/catch wrapper，避免异常 stack 回流页面
- [ ] **#4** 接入指纹自检页面（sannysoft / creep.js / antoinevastel.com）作为周期回归
- [ ] **#5** `_click_element_by_cdp` 补 `mouseMoved` 前置 + press/release 80-300ms 间隔 + 坐标 ±N px 抖动
- [ ] **#5** 延时分布换对数正态
- [ ] **#5** 加入假动作概率注入

### 第四阶段（依赖外部资源）

- [ ] **#7** `account_manager` 增加 proxy 字段，账号绑定独立出口 IP

## 验证与观测

修改后建议加的可观测指标（写到日志或 metric）：

- 每账号每日：发出评论数、回查可见率、`error_code=300013` 命中次数、warning 累计数。
- 跨账号：trigram 重叠率分布；高重叠告警阈值 ≥ 0.3。
- 全局：CDP 协议层指纹自检页面通过率（每周一次跑 sannysoft / creep.js）。

存活时间观测窗口：**至少 14 天**。2026 年的封禁延迟降到 ~1 天，14 天足以暴露绝大部分中长期风险，但不要在 7 天内下"我安全了"的结论。

## 参考资料

- xiaohongshu-mcp 项目 issue #674 / #680 / #670 / #672 / #645 / #641 / #648（封禁案例与"假成功"模式）
- MediaCrawler 项目 issue #544 / #769 / #865 / #266（爬取阈值、维护者反检测路线切换）
- XHS 2025 H1 治理战报（chinadaily.com.cn）+ RedOne 模型披露（qbitai.com）
- Patchright 项目（github.com/Kaliiiiiiiiii-Vinyzu/patchright）：CDP `Runtime.enable` / `Console.enable` 泄漏修补
- yousali.com 2026 浏览器自动化反检测综述
- tuokeba.com 矩阵号封禁案例（37 号一夜清零 + 手机注销）
- taokeshow.com / 知乎 1986458524751000419：2026 年养号节奏共识
- dev.to "How to scrape RedNote in 2026"：签名/cookies 现状（`a1` / `web_session` / `webId` / `xsec_token` / `sec_poison_id` 等）

> **关于引用的可信度**：以上 issue 编号与外部数据点（封号 1000 万+、37 号一夜清零、3-5 条/天养号节奏等）作为方向性证据可参考，本项目未独立核验；不应作为定量决策依据。所有具体阈值（如 `day_limit=5`、`week=25`、trigram 重叠率 0.3）均为社区共识起步值，需在本项目 14 天观测窗口中实测调整。
