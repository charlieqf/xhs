# 新增功能计划：post_comment_to_feed（一级评论）

## 目标
- 在 `scripts/cdp_publish.py` 增加对指定笔记发表评论（一级评论）的 CLI 能力。

## 实施步骤
- [x] 参考 `xiaohongshu-mcp` 的 `post_comment_to_feed` 调用链，确认参数与页面交互选择器。
- [x] 在 `XiaohongshuPublisher` 中实现 `post_comment_to_feed(feed_id, xsec_token, content)`。
- [x] 增加页面可访问性检查（删除/私密/失效等场景提前报错）。
- [x] 在 CLI 中新增 `post-comment-to-feed`（别名 `post_comment_to_feed`）子命令。
- [x] 支持 `--content` 与 `--content-file` 两种内容输入方式。
- [x] 更新 `README.md` 与 `SKILL.md` 的功能说明和命令示例。
- [ ] 冒烟验证：在已登录测试账号上执行新命令并确认评论发出（不做破坏性自动发布流程）。

---

# 新增功能计划：get_notification_mentions（评论和@通知接口）

## 目标
- 在 `scripts/cdp_publish.py` 增加抓取通知页 `you/mentions` 接口数据的 CLI 能力。

## 实施步骤
- [x] 在 `XiaohongshuPublisher` 中实现 `get_notification_mentions(wait_seconds)`。
- [x] 增加通知页 tab 自动点击逻辑，尽量触发 `https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions` 请求。
- [x] 在 CLI 中新增 `get-notification-mentions`（别名 `get_notification_mentions`）子命令。
- [x] 输出结构化结果（请求 URL、条数、分页游标、原始 payload）。
- [x] 更新 `README.md` 与 `SKILL.md` 的功能说明和命令示例。
- [ ] 冒烟验证：在已登录测试账号上执行新命令并确认可抓到 `you/mentions` 返回。
