# feeds-container 滚动前后 note-item 变化分析

## 样本文件

- 滚动前：`prod/elements/before_scroll.html`
- 滚动后：`prod/elements/after_scroll.html`

两个文件的根节点都是 `.feeds-container`，高度一致：

- `height: 16115.8px`
- 子元素使用瀑布流绝对/虚拟布局，每个 `.note-item` 通过 `transform: translate(x, y)` 定位。

## 关键结论

`.feeds-container` 内的 `.note-item` 是窗口化渲染，不是把所有笔记一次性放进 DOM。

滚动前 DOM 中有 12 个 `.note-item`：

| data-index | note id | transform |
| --- | --- | --- |
| 0 | `682bed44000000000f03ac78` | `translate(0px, 0px)` |
| 1 | `69ad7713000000002602e295` | `translate(247.75px, 0px)` |
| 2 | `68fc2c9a000000000703100f` | `translate(495.5px, 0px)` |
| 3 | `693cdde7000000001f00d6a3` | `translate(495.5px, 393.594px)` |
| 4 | `68ea2e120000000004016114` | `translate(0px, 413.188px)` |
| 5 | `69d1e28e0000000021004c1b` | `translate(247.75px, 413.188px)` |
| 6 | 非普通笔记，`大家都在搜` query card | `translate(495.5px, 806.781px)` |
| 7 | `6926f31c000000001e028943` | `translate(0px, 826.375px)` |
| 8 | `682d53ca000000001200791b` | `translate(247.75px, 826.375px)` |
| 9 | `68538e3100000000230052e9` | `translate(495.5px, 1106.78px)` |
| 10 | `6958f3dc000000001e0132f4` | `translate(0px, 1239.56px)` |
| 11 | `68fc6ec6000000000703692e` | `translate(247.75px, 1239.56px)` |

滚动后 DOM 中仍然是 12 个 `.note-item`，但范围变成：

| data-index | note id | transform |
| --- | --- | --- |
| 3 | `693cdde7000000001f00d6a3` | `translate(495.5px, 393.594px)` |
| 4 | `68ea2e120000000004016114` | `translate(0px, 413.188px)` |
| 5 | `69d1e28e0000000021004c1b` | `translate(247.75px, 413.188px)` |
| 6 | 非普通笔记，`大家都在搜` query card | `translate(495.5px, 806.781px)` |
| 7 | `6926f31c000000001e028943` | `translate(0px, 826.375px)` |
| 8 | `682d53ca000000001200791b` | `translate(247.75px, 826.375px)` |
| 9 | `68538e3100000000230052e9` | `translate(495.5px, 1106.78px)` |
| 10 | `6958f3dc000000001e0132f4` | `translate(0px, 1239.56px)` |
| 11 | `68fc6ec6000000000703692e` | `translate(247.75px, 1239.56px)` |
| 12 | `69ce0885000000001f004501` | `translate(495.5px, 1519.97px)` |
| 13 | `69706c19000000002103dc74` | `translate(247.75px, 1607.16px)` |
| 14 | `689af563000000001d03a901` | `translate(0px, 1629.16px)` |

## 滚动后的变化

滚动前后的交集是 `data-index=3..11`。

滚动后消失：

- `data-index=0`
- `data-index=1`
- `data-index=2`

滚动后新增：

- `data-index=12`
- `data-index=13`
- `data-index=14`

这说明页面滚动后，小红书会把靠上、离视口较远的卡片从 DOM 中移除，再把新的下方卡片加入 DOM。DOM 里只保留当前视口附近的一段连续窗口。

## data-index 的含义

`data-index` 更像搜索结果列表中的全局序号，而不是当前 DOM 顺序。

例如滚动后 DOM 的第一个 `.note-item` 是 `data-index=3`，不是 `0`。因此：

- 不能用当前 DOM 下标直接等同于全局第 N 篇。
- 应优先用 `section.note-item[data-index="<目标序号>"]` 定位。
- 如果目标序号已经被虚拟列表移出 DOM，则需要滚动到它所在范围才可能定位到。

## transform 的含义

每个卡片的位置由 `transform: translate(x, y)` 决定。

从样本看，瀑布流是 3 列布局：

- 第 1 列：`x = 0px`
- 第 2 列：`x = 247.75px`
- 第 3 列：`x = 495.5px`

`y` 是该卡片在整个瀑布流中的纵向位置。滚动前后，同一个 `data-index` 的 `transform` 保持不变，例如：

- `data-index=3` 前后都是 `translate(495.5px, 393.594px)`
- `data-index=10` 前后都是 `translate(0px, 1239.56px)`

这说明卡片的布局位置是稳定的，滚动改变的是 DOM 窗口和页面滚动位置，不是卡片自己的全局坐标。

## 特殊卡片

`data-index=6` 不是普通笔记卡片，而是一个 `query-note-wrapper`，内容为“大家都在搜”。

它没有 `/explore/<note_id>` 链接，因此不能当作可处理笔记。提取 DOM feed 时需要跳过没有笔记 ID 的 `note-item`。

## 对自动化逻辑的影响

1. 补加载搜索结果时，可以滚动 `.feeds-container` 所在页面区域，并在每次滚动后从 DOM 提取当前窗口中的 `/explore/<id>` 链接，合并去重。
2. 逐条处理时，若已经回到页首并准备处理前 `post_per_keyword` 篇，不应该在点击失败后继续无界下滚查找，否则会触发新的虚拟窗口切换。
3. 点击第 N 篇时，应使用 `data-index=N-1` 定位当前窗口中的卡片。
4. 如果目标 `data-index` 不在当前 DOM 中，需要判断它是否属于“已加载但被虚拟列表移出 DOM”的情况。可选择：
   - 只处理当前已经回到页首后可定位的范围；
   - 或者实现受控滚动到目标 index 附近，但滚动边界必须限制在一开始凑够 `post_per_keyword` 的范围内。
5. DOM 下标不稳定，`cards[targetIndex]` 只能作为兜底，不能作为主要定位方式。

## 推荐实现策略

搜索阶段：

1. 初次搜索后读取 DOM 中所有普通笔记卡片。
2. 如果数量小于 `post_per_keyword`，向下滚动搜索结果区域。
3. 每次滚动后提取当前 DOM 窗口中的普通笔记，按 note id 去重合并。
4. 达到 `post_per_keyword` 或连续多次没有新增后停止。
5. 回到页首。

处理阶段：

1. 按合并后的 feed 列表顺序处理。
2. 点击时优先用 `data-index` 定位：

```js
document.querySelector('section.note-item[data-index="12"]')
```

3. 如果当前 DOM 没有该 `data-index`，不要继续大范围下滚加载更多；应跳过或只做一次有限的回位/定位尝试。
4. 跳过没有 `/explore/<id>` 的特殊卡片，例如 `query-note-wrapper`。

