"""每日实验参数一键切换：python scripts/daily_apply.py D4

写入 prod/account_state/<account>.json (day_limit + min_action_interval_sec)
+ prod/config.json (active_windows + active_windows_enabled)。

D4-D8 是 5 天渐进梯度（量频 + 时段散布），目标 5 天累计 2000 条 / 探边界。
注意：D7-D8 的"回查窗口砍短"和"打字仿真砍"是代码层改动，本脚本不处理；
那两项要在 D6 跑完后再人工开启 config flag（待实施）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import account_state

PRESETS = {
    "D4": {
        "day_limit": 200,
        "min_action_interval_sec": 120,
        "active_windows": [["08:00", "10:30"], ["14:00", "16:30"], ["20:00", "22:30"]],
        "note": "200 条 / 8h 共 3 段 / 120s 间隔 — 落入账号 B 几百条区间下界",
    },
    "D5": {
        "day_limit": 300,
        "min_action_interval_sec": 100,
        "active_windows": [["07:30", "11:00"], ["13:30", "16:30"], ["19:30", "22:30"]],
        "note": "300 条 / 9.5h 共 3 段 / 100s 间隔 — 累计 532 / 进入 B 中段",
    },
    "D6": {
        "day_limit": 400,
        "min_action_interval_sec": 90,
        "active_windows": [["07:00", "11:30"], ["13:00", "17:00"], ["19:00", "22:30"]],
        "note": "400 条 / 12h 共 3 段 / 90s 间隔 — 累计 932 / 接近账号 A 下界",
    },
    "D7": {
        "day_limit": 500,
        "min_action_interval_sec": 80,
        "active_windows": [["06:30", "13:30"], ["16:30", "23:30"]],
        "note": "500 条 / 14h 共 2 段 / 80s 间隔 — 累计 1432 / A 中段；回查窗口需砍到 15-30s 否则跟不上",
    },
    "D8": {
        "day_limit": 600,
        "min_action_interval_sec": 70,
        "active_windows": [["06:00", "21:00"]],
        "note": "600 条 / 15h 共 1 段 / 70s 间隔 — 累计 2032 / A 上界；回查砍 + 打字仿真砍才物理可行",
    },
}

PROD_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "prod"))
CONFIG_PATH = os.path.join(PROD_DIR, "config.json")


def apply_preset(day_label: str, account: str, dry_run: bool = False):
    if day_label not in PRESETS:
        print(f"⛔ 未知 day label: {day_label}（可选: {', '.join(PRESETS)}）")
        sys.exit(1)
    preset = PRESETS[day_label]

    # 1) state.json 改 day_limit + min_action_interval_sec
    state = account_state.load(account)
    before_state = {
        "day_limit": state.get("day_limit"),
        "min_action_interval_sec": state.get("min_action_interval_sec"),
        "day_count": state.get("day_count"),
        "day_started_at": state.get("day_started_at"),
    }
    state["day_limit"] = preset["day_limit"]
    state["min_action_interval_sec"] = preset["min_action_interval_sec"]

    # 2) config.json 改 active_windows + 启用
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    before_config = {
        "active_windows": config.get("active_windows"),
        "active_windows_enabled": config.get("active_windows_enabled"),
    }
    config["active_windows"] = preset["active_windows"]
    config["active_windows_enabled"] = True

    print(f"=== daily_apply {day_label} (account={account}) ===")
    print(f"  说明: {preset['note']}")
    print()
    print(f"  state.day_limit                  : {before_state['day_limit']} -> {preset['day_limit']}")
    print(f"  state.min_action_interval_sec    : {before_state['min_action_interval_sec']} -> {preset['min_action_interval_sec']}")
    print(f"  state.day_count (不动)           : {before_state['day_count']}（bot 启动时按 day_started_at 自动 rollover）")
    print(f"  state.day_started_at (不动)      : {before_state['day_started_at']}")
    print(f"  config.active_windows            : {before_config['active_windows']}")
    print(f"                                  -> {preset['active_windows']}")
    print(f"  config.active_windows_enabled    : {before_config['active_windows_enabled']} -> True")

    if dry_run:
        print()
        print("--dry-run，未写入。")
        return

    account_state.save(account, state)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

    print()
    print(f"✅ 已写入 state.json + config.json。")
    print(f"接下来启动 bot:")
    print(f"  python prod/bot_lite.py --persona matchmaker_dongbei_38_neutral")


def main():
    parser = argparse.ArgumentParser(description="一键应用每日实验参数")
    parser.add_argument("day", choices=list(PRESETS.keys()), help="实验日 label (D4-D8)")
    parser.add_argument("--account", default="19921371193", help="账号名")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = parser.parse_args()
    apply_preset(args.day, args.account, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
