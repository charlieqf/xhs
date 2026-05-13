"""每日 state + responses 快照归档。

7 天急性边界实验里 state 文件每天被覆盖，跨天对比会丢数据。这个脚本把
当日的 state + 当日生成的 bot_lite_responses_*.json 都拷贝到一个按日期
组织的归档目录里：

    prod/account_state_log/<account>/<YYYY-MM-DD>/
        state.json                       # 当时的 state 快照
        bot_lite_responses_<TS>.json     # 当天的 responses 文件（按时间戳命名）
        meta.json                        # 归档时间 + 简要统计

用法：
    python scripts/snapshot_state.py <account>

设计要点：
- 幂等：同一天重复运行不破坏先前归档（state.json 会被新值覆盖；responses
  按时间戳命名不会撞）
- 不依赖 bot：随时可调，包括 bot 跑着或停了
- 简单：纯文件操作，无 cron / 后台进程
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_ROOT, "prod", "account_state")
_RESPONSES_DIR = os.path.join(_ROOT, "prod")
_LOG_DIR = os.path.join(_ROOT, "prod", "account_state_log")


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def archive_account(account_name: str) -> dict:
    """Archive current state + today's responses for ``account_name``.

    Returns a meta dict describing what was archived.
    """
    today = _today_str()
    target_dir = os.path.join(_LOG_DIR, _safe(account_name), today)
    os.makedirs(target_dir, exist_ok=True)

    meta = {
        "account": account_name,
        "archived_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "date": today,
        "files": [],
    }

    # 1. state snapshot
    state_src = os.path.join(_STATE_DIR, f"{_safe(account_name)}.json")
    if os.path.exists(state_src):
        state_dst = os.path.join(target_dir, "state.json")
        shutil.copy2(state_src, state_dst)
        meta["files"].append("state.json")
        # 同时把 state 摘要写到 meta 里方便快速浏览
        try:
            with open(state_src, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            meta["state_summary"] = {
                "day_count": state_data.get("day_count"),
                "day_limit": state_data.get("day_limit"),
                "min_action_interval_sec": state_data.get("min_action_interval_sec"),
                "warning_count": state_data.get("warning_count"),
                "consecutive_invisible_count": state_data.get("consecutive_invisible_count", 0),
                "total_invisible": state_data.get("total_invisible", 0),
                "frozen_until": state_data.get("frozen_until"),
            }
        except Exception:
            pass
    else:
        meta["state_missing"] = True

    # 2. today's bot_lite_responses_<YYYYMMDD>_*.json
    date_compact = today.replace("-", "")
    if os.path.isdir(_RESPONSES_DIR):
        for fn in os.listdir(_RESPONSES_DIR):
            if (
                fn.startswith("bot_lite_responses_")
                and date_compact in fn
                and fn.endswith(".json")
            ):
                src = os.path.join(_RESPONSES_DIR, fn)
                dst = os.path.join(target_dir, fn)
                shutil.copy2(src, dst)
                meta["files"].append(fn)

    # 3. write meta
    with open(os.path.join(target_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def main() -> int:
    # Windows 默认 cp1252 stdout 会被中文 print 炸；切到 utf-8 才能在普通 powershell 跑
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法: python scripts/snapshot_state.py <account>", file=sys.stderr)
        return 1
    account = sys.argv[1]
    meta = archive_account(account)
    print(f"归档到: {os.path.join(_LOG_DIR, _safe(account), meta['date'])}")
    print(f"  文件: {meta['files']}")
    if "state_summary" in meta:
        print(f"  state 摘要: {meta['state_summary']}")
    if meta.get("state_missing"):
        print(f"  ⚠️ state 文件不存在: prod/account_state/{_safe(account)}.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
