"""D4 启动前 dry-run：用 D3 真实评论数据测新 prompt + neutral persona + JSON mode。

不启动 Playwright / 不真发评论，只调 evaluate_comments_with_llm 看输出质量。

验证目标：
1. JSON mode + chain-of-thought 中间字段是否生效
2. generated_reply 是否摆脱 D3 "想细说接着聊" 85% 模板化
3. forbidden_phrases 是否真的 0 漏出（neutral 30+ 邀约变体禁词）
4. note_title 是否让 LLM 给针对性 hook（vs D3 「dd」→「这种心情我挺懂的」式空泛共情）
5. 是否凭空臆造（D3 第 8 轮 16 「性格大方」的同类）
"""
import json, os, sys

sys.path.insert(0, r'C:\work\code\xhs\scripts')
sys.path.insert(0, r'C:\work\code\xhs\prod')

import persona as persona_mod
import bot_lite

# 选 5 条 D3 真实测试样本：评论从极短到长、从有意向到无意向、从直白到隐晦
# (note_title, original_comment, D3 当时的 reply, 测试目的)
TEST_CASES = [
    (
        "申请加帅哥微信",
        "哈哈哈哈哈哈，朋友圈全是画吗",
        "（D3 没发，被 min_interval 拦了，但 LLM 当时拟回「你这性格看着挺大方的」凭空臆造）",
        "凭空臆造测试：评论极短无具体细节，看 neutral 是否避免补对方人设",
    ),
    (
        "脱单第一志愿",
        "在小红书找对象第一天就这么详细写介绍，看得出来是认真的。我也是想找正缘但发了贴效果一般，想问下你目前有收到靠谱的私信吗，还是还在蹲状态啊纯好奇取取经",
        "我也是从这一步过来的，当初发帖也挺迷茫。其实发帖只是个开始，想细说接着聊。",
        "长文有意向：能不能针对'靠谱的私信''蹲状态'做精准回应（vs D3 套'我也是过来人'框架）",
    ),
    (
        "戒掉恋爱脑必读",
        "好喜欢10",
        "这种落差确实挺难受。我自己以前也掏心掏肺过，结果对方突然冷淡，特理解这种心塞。想细说接着聊。",
        "极短评论：D3 的输出过度推断'落差/掏心掏肺/冷淡'，看 neutral 会不会更克制",
    ),
    (
        "妈妈问我五一为什么去苏州",
        "苏州盖章本去哪买，朋友推荐了观前街但听说价格贵",
        "（D3 跳过，无意向）",
        "无意向测试：纯旅游攻略评论，验证 selected_index=-1 + 中间字段填空字符串",
    ),
    (
        "处女男回避型",
        "我之前也有这种困惑，但现在觉得，爱情的产生其实是非常自然的事，当遇到那个人的时候，这些自然都有答案了，没有模板，也没有参照物，大概这也是恋爱一个有意思的地方吧",
        "说实话，我也走过这一步。其实这不是教出来的，是心态得先转个弯。想细说的话，咱们可以接着聊。",
        "复杂情感：评论是哲学化思考，看 neutral 能不能给观点而不是邀约",
    ),
]


def run_one(case_idx: int, note_title: str, original: str, d3_reply: str, purpose: str):
    print(f"\n{'=' * 70}")
    print(f"#{case_idx}  [{note_title}]")
    print(f"目的: {purpose}")
    print(f"{'=' * 70}")
    print(f"  评论: {original}")
    print(f"  D3 当时输出: {d3_reply}")

    # 包装成 evaluate_comments_with_llm 的输入格式
    comments = [{"user": "测试用户", "content": original}]
    persona = persona_mod.load("matchmaker_dongbei_38_neutral")

    result = bot_lite.evaluate_comments_with_llm(comments, persona, note_title=note_title)

    if not result:
        print(f"  ✗ LLM 调用失败或返回 None")
        return

    print(f"\n  --- 新 prompt 输出 ---")
    print(f"  selected_index           : {result.get('selected_index')}")
    print(f"  reason                   : {result.get('reason')}")
    print(f"  specific_detail_picked   : {result.get('specific_detail_picked')!r}")
    print(f"  reaction_to_detail       : {result.get('reaction_to_detail')!r}")
    print(f"  generated_reply          : {result.get('generated_reply')!r}")

    # 自动质量检查
    reply = result.get("generated_reply") or ""
    hits = persona_mod.find_forbidden(reply, persona)
    print(f"\n  自动检查:")
    print(f"  - forbidden 漏出: {hits if hits else '✓ 0'}")
    print(f"  - reply 长度    : {len(reply)} 字 (cap 50)")
    print(f"  - 含 chain-of-thought 中间字段: "
          f"specific={bool(result.get('specific_detail_picked'))}, "
          f"reaction={bool(result.get('reaction_to_detail'))}")
    template_hits = []
    for tag in ['想细说', '接着聊', '可以再聊', '咱们聊', '我自己', '说实话', '真心说', '过来人', '我也走过这步']:
        if tag in reply:
            template_hits.append(tag)
    print(f"  - 命中 D3 模板词: {template_hits if template_hits else '✓ 0'}")


def main():
    if not bot_lite.OPENROUTER_API_KEY:
        print("⛔ OPENROUTER_API_KEY 未设置，无法调 LLM")
        sys.exit(1)
    print("D4 dry-run: neutral persona + 新 prompt + JSON mode")
    print(f"persona: matchmaker_dongbei_38_neutral")
    print(f"测试 case 数: {len(TEST_CASES)}")

    for i, (title, orig, d3_reply, purpose) in enumerate(TEST_CASES, 1):
        try:
            run_one(i, title, orig, d3_reply, purpose)
        except Exception as e:
            print(f"  ✗ 跑 case #{i} 出错: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
