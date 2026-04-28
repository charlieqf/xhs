# 搜索结果翻页定位逻辑梳理

本文档整理 `prod/general_comment_bot.py` 中当前“按搜索结果 feed_id 打开笔记卡片”的翻页定位逻辑，便于后续优化。

## 相关入口

主流程位置：`main()` 中处理每条 `feed` 时。

1. `publisher.search_feeds(keyword=keyword)` 先搜索关键词并返回 `feeds`。
2. 主流程取 `feeds[:post_per_keyword]` 作为本轮要处理的笔记列表。
3. 对每条笔记取 `feed_id = feed.get("id")`。
4. 调用 `click_note_card(publisher, feed_id, feed_index=feed_idx - 1)` 尝试在搜索页里打开这条笔记。
5. 如果第一次 `click_note_card()` 返回 `False`，主流程会打印：

   ```text
   -> 笔记卡片初次点击失败。尝试向下翻页并重试...
   ```

6. 然后额外执行 `_scroll_search_page(publisher, pixels=800)`。
7. 再调用一次 `click_note_card(publisher, feed_id, feed_index=0)`。
8. 如果仍失败，打印：

   ```text
   -> [跳过] 重试点击依然失败。
   ```

注意：这里的“初次点击失败”并不是只点了一次。`click_note_card()` 内部本身已经做了较长的滚动搜索和最多 3 次 CDP 点击重试。

## 当前函数关系

当前调用链如下：

```text
main()
  -> click_note_card(feed_id)
       -> _seek_card_by_scrolling(feed_id)
            -> _find_card_in_dom(feed_id)
                 -> _card_rect_js(feed_id)
            -> _center_card_in_dom(feed_id)
            -> _scroll_search_page(...)
            -> 打印可见笔记ID调试日志
       -> _center_card_in_dom(feed_id)
       -> publisher._click_element_by_cdp(..., _card_rect_js(feed_id))
```

## `_scroll_search_page`

用途：在搜索结果页向下滚动指定像素，触发懒加载或虚拟列表更新。

当前逻辑：

1. 在页面 JS 中定义 `delta = pixels`。
2. 定义 `isScrollable(el)`：
   - 元素必须存在。
   - `overflow-y` 必须匹配 `auto|scroll|overlay`。
   - `scrollHeight > clientHeight + 40`。
3. 构造候选滚动容器：
   - `document.scrollingElement`
   - `document.documentElement`
   - `document.body`
   - `main`
   - class 包含 `search`、`feeds`、`waterfall`、`container` 的元素
   - 所有 `div`
4. 去重后过滤出可滚动元素。
5. 按可滚动距离 `scrollHeight - clientHeight` 从大到小排序。
6. 选择滚动范围最大的元素作为 `target`。
7. 如果没有可滚动候选，则回退到 `document.scrollingElement || document.documentElement || document.body`。
8. 对 `target` 执行：

   ```js
   target.scrollBy({ top: delta, behavior: "instant" });
   ```

9. 同时派发一个 `WheelEvent("wheel", { deltaY: delta, bubbles: true })`。
10. Python 侧等待约 0.8 到 1.2 秒。

当前特点：

- 只按“滚动范围最大”选择容器。
- 没有校验滚动前后的 `scrollTop` 是否真的变化。
- 没有记录实际滚动的是哪个元素。
- `WheelEvent` 派发在 `window`，不是具体 `target`。
- 滚动距离固定由调用方传入，没有根据目标卡片的搜索结果顺序计算。

## `_card_rect_js`

用途：在当前可见 DOM 中找到指定 `feed_id` 对应的可点击区域，并返回矩形。

当前查找方式已按 `prod/notes.html` 中的真实结构收窄：

1. 目标 ID 来自 `feed_id`。
2. 定义 `noteIdFromHref(href)`，只从 `/explore/{note_id}` 路径里解析笔记 ID。
3. 定义 `visibleRect(el)`，只接受当前视口内可见且尺寸足够的元素：
   - 元素必须是 `HTMLElement`。
   - 宽高至少 20px。
   - 不能完全在视口上下左右之外。
4. 遍历 `#exploreFeeds section.note-item, section.note-item`。
5. 检查卡片内部是否存在 `/explore/{feed_id}` 链接。
6. 优先选择同一张卡片里的 `a.cover` 作为点击区域。
7. 如果 `a.cover` 不可见，再回退到 `section.note-item` 的矩形。
8. 找不到则返回 `null`。

当前特点：

- 只认当前 DOM 中已经渲染出来的元素。
- 只认当前视口内可见的矩形。
- 如果虚拟列表中目标卡片尚未渲染，即使 `search_feeds()` 返回了该 `feed_id`，这里仍会失败。
- 不再匹配 `[class*='feed']`、`[class*='card']`、`article`、`li`、`data-note-id` 等泛化选择器，避免误命中大容器或无关节点。

## `_find_card_in_dom`

用途：判断目标卡片当前是否可见。

当前逻辑：

```python
return bool(publisher._evaluate(_card_rect_js(feed_id)))
```

也就是说，它不是判断 DOM 中是否存在目标卡片，而是判断目标卡片是否已经有可点击、可见的矩形。

## `_center_card_in_dom`

用途：如果 DOM 中能找到目标卡片，把它滚到视口中间。

当前逻辑：

1. 遍历 `#exploreFeeds section.note-item, section.note-item`。
2. 从卡片内部链接解析 `/explore/{note_id}`，精确匹配目标 `feed_id`。
3. 优先取同卡片内的 `a.cover`，否则使用 `section.note-item`。
4. 调用：

   ```js
   target.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
   ```

5. 找到并调用后返回 `true`，否则返回 `false`。

当前特点：

- 它只能处理“目标节点已经在 DOM 中”的情况。
- 如果目标卡片没有被虚拟列表渲染出来，它无法定位。
- 返回 `true` 只代表执行过 `scrollIntoView`，不代表滚动后 `_card_rect_js` 一定能拿到可点击矩形。

## `_seek_card_by_scrolling`

用途：通过不断滚动搜索结果容器，让目标 `feed_id` 对应的虚拟列表卡片渲染出来。

默认参数：

```python
max_steps = 24
```

当前流程：

1. 先检查 `_find_card_in_dom(feed_id)`。
   - 如果已经可见，直接返回 `True`。
2. 再尝试 `_center_card_in_dom(feed_id)`。
   - 如果能执行 `scrollIntoView`，等待 0.5 到 1.0 秒。
   - 再次 `_find_card_in_dom(feed_id)`。
   - 如果可见，返回 `True`。
3. 第一轮向下滚动搜索：
   - 最多 24 步。
   - 每步调用 `_scroll_search_page()`。
   - 滚动像素按以下节奏循环：

     ```python
     420 + (step % 3) * 120
     ```

   - 实际序列是：420、540、660、420、540、660...
   - 每次滚动后立刻 `_find_card_in_dom(feed_id)`。
   - 找到则返回 `True`。
4. 如果第一轮没找到，执行一次“回到顶部”：
   - 枚举和 `_scroll_search_page` 类似的根元素列表。
   - 对所有满足 `scrollHeight > clientHeight + 40` 的元素执行：

     ```js
     root.scrollTo({ top: 0, behavior: "instant" });
     ```

   - 等待 0.6 到 1.2 秒。
5. 第二轮从顶部重新向下滚动搜索：
   - 仍然最多 24 步。
   - 每步先 `_find_card_in_dom(feed_id)`，再 `_scroll_search_page()`。
   - 滚动像素仍然是 420、540、660 循环。
6. 两轮都失败后，收集当前可见卡片中的前 12 个去重 `/explore/` 笔记 ID：

   ```js
   document.querySelectorAll("#exploreFeeds section.note-item, section.note-item")
   ```

7. 打印调试日志：

   ```text
   -> [调试] 未定位到目标卡片，可见笔记ID: [...]
   ```

8. 返回 `False`。

当前最多滚动次数：

- 一次 `click_note_card()` 内部最多会触发：
  - 第一轮 24 次向下滚动。
  - 回顶一次。
  - 第二轮 24 次向下滚动。
- 主流程失败后还会额外滚动 800px，再调用第二次 `click_note_card()`。
- 所以单条笔记失败路径最多可能经历两套 `_seek_card_by_scrolling()`，加上额外一次 800px 滚动。

## `click_note_card`

用途：先让目标卡片出现在当前视口，再用 CDP 鼠标点击卡片封面。

当前流程：

1. 忽略传入的 `feed_index`：

   ```python
   _ = feed_index
   ```

2. 调用 `_seek_card_by_scrolling(feed_id)`。
   - 如果返回 `False`，直接点击失败。
3. 再调用 `_center_card_in_dom(feed_id)`。
   - 如果返回 `False`，点击失败。
4. 等待 0.8 到 1.5 秒。
5. 最多尝试 3 次 CDP 点击：

   ```python
   publisher._click_element_by_cdp("note card cover", _card_rect_js(feed_id))
   ```

6. 如果 CDP 点击异常：
   - 前 2 次打印重试日志。
   - 等待。
   - 再次 `_center_card_in_dom(feed_id)`。
   - 再等待。
7. 第 3 次仍失败，则打印最终失败日志并返回 `False`。

当前特点：

- `feed_index` 没参与定位，所以搜索结果中的顺序没有转化为滚动距离或页数。
- 点击坐标完全依赖 `_card_rect_js(feed_id)` 返回的第一个可见候选矩形。
- 如果 `_seek_card_by_scrolling()` 找不到目标，后续不会尝试通过详情 URL、搜索结果数据或接口数据打开。

## 当前失败路径对应日志

你看到的日志：

```text
-> 笔记卡片初次点击失败。尝试向下翻页并重试...
-> [调试] 未定位到目标卡片，可见笔记ID: [...]
-> [跳过] 重试点击依然失败。
```

大致含义是：

1. 第一次 `click_note_card()` 内部 `_seek_card_by_scrolling()` 没有让目标 `feed_id` 出现在可见 DOM 中，或者后续 CDP 点击失败。
2. 主流程额外向下滚动 800px。
3. 第二次 `click_note_card()` 再跑完整定位流程。
4. 第二次定位仍没有在可见 DOM 里找到目标 `feed_id`。
5. `_seek_card_by_scrolling()` 打印了当前页面可见的前 12 个 `/explore/` 链接 ID。
6. 因为可见 ID 里没有目标 `feed_id`，最终跳过。

当前调试日志已按卡片维度去重，避免同一张卡片内隐藏链接、封面链接、标题链接重复输出同一个 ID。

## 目前逻辑的关键假设

当前翻页定位依赖这些假设：

1. `search_feeds()` 返回的 `feed_id` 会在当前搜索页 DOM 中通过滚动出现。
2. 搜索结果页的真实滚动容器可以通过“可滚动范围最大”选中。
3. 对选中的容器执行 `scrollBy` 能推动小红书的虚拟列表加载下一批卡片。
4. 目标卡片出现后，它的链接或属性里会包含 `feed_id`。
5. 目标卡片出现后，它会进入当前视口，`_card_rect_js()` 能返回可点击矩形。
6. 从顶部重新滚一遍可以覆盖遗漏情况。

如果其中任一假设不成立，就可能出现“搜索结果数据里有 feed_id，但页面可见 DOM 一直找不到”的情况。

## 当前可能的断点

以下是仅基于现有代码能看到的潜在问题点：

1. `search_feeds()` 的结果来源和当前可见 DOM 可能不同步  
   搜索结果来自 `FeedExplorer` 提取的数据，但点击必须依赖页面当前已渲染的 DOM。数据中排在前面的笔记，不一定仍能通过当前滚动方式渲染出来。

2. 滚动容器可能选错  
   `_scroll_search_page()` 选择“可滚动范围最大”的元素，但真正驱动瀑布流/虚拟列表的容器未必是这个元素。

3. 没有确认滚动是否生效  
   当前没有读取 `scrollTop` 前后变化。如果 `scrollBy` 没推动实际列表，代码仍会继续重试直到失败。

4. `WheelEvent` 没有派发给具体滚动容器  
   当前事件发在 `window` 上。有些前端监听的是具体列表容器或真实鼠标滚轮输入。

5. 回顶动作可能影响搜索状态  
   第一轮失败后会对多个可滚动 root 全部 `scrollTo(top=0)`，可能改变的元素比预期更多。

6. `feed_index` 没有使用  
   主流程知道当前处理的是搜索结果第几条，但定位时没有根据 index 估算目标应在第几屏、也没有判断当前可见 ID 相对目标顺序。

7. 调试可见 ID 未去重且只取前 12 个链接  
   当前日志能看到重复 ID，但无法判断实际可见卡片数量、滚动位置、滚动容器、目标 feed_id、以及是否还在加载。

8. 只支持向下滚动和回顶重扫  
   如果目标在当前位置上方附近，第一次向下搜索会越滚越远，直到回顶后再扫；这会增加耗时，也可能错过虚拟列表短暂渲染窗口。

## 后续优化时可优先补充的观测项

为了判断到底是“滚错容器”“数据与 DOM 不一致”还是“目标不在当前页面结果中”，建议先增强调试信息：

1. 每次 `_scroll_search_page()` 返回实际滚动目标信息：
   - 标签名
   - className
   - `scrollTop` 前后值
   - `clientHeight`
   - `scrollHeight`
   - 是否真的发生位移
2. `_seek_card_by_scrolling()` 打印目标 `feed_id` 和当前 step。
3. 可见 ID 去重，并输出数量。
4. 同时输出当前页面中所有包含目标 `feed_id` 的节点数量，而不仅是可见矩形。
5. 如果滚动连续多次没有位移，提前切换滚动策略。
6. 记录 `feed_index`，观察失败目标是否集中在搜索结果较靠后的位置。
