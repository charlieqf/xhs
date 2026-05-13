"""Per-account rate limit and freeze-state tracking.

State files live at ``prod/account_state/<account_name>.json``. All time
fields use Asia/Shanghai timezone (XHS risk control follows local time).

Concurrency: callers must wrap read-modify-write sequences with
``run_lock.single_instance(f"xhs_account_{account_name}")`` so two bots
acting on the same account cannot collide. With one file per account and
one lock per account, no additional file-level lock is needed.

Typical flow:

    allowed, reason = account_state.can_send("default")
    if not allowed:
        log(reason)
        return
    # ... send comment via XHS ...
    account_state.record_send("default")
    # ... if URL detection sees a 风控 redirect:
    account_state.record_warning("default")
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_DAY_LIMIT = 5
DEFAULT_MIN_ACTION_INTERVAL_SEC = 1800  # 30 min

# Far-future sentinel used when an account is permanently retired.
PERMANENT_FREEZE_ISO = "9999-12-31T23:59:59+08:00"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_PROJECT_ROOT, "prod", "account_state")


# ---------------------------------------------------------------------------
#  Time helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(TZ)


def _today_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
#  Path helpers
# ---------------------------------------------------------------------------

def _safe_name(account_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in account_name)


def state_path(account_name: str) -> str:
    return os.path.join(_STATE_DIR, f"{_safe_name(account_name)}.json")


def _ensure_state_dir() -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
#  Default state
# ---------------------------------------------------------------------------

def _default_state(account_name: str) -> dict[str, Any]:
    return {
        "account_name": account_name,
        "day_count": 0,
        "day_started_at": _today_str(),
        "day_limit": DEFAULT_DAY_LIMIT,
        "last_action_at": None,
        "min_action_interval_sec": DEFAULT_MIN_ACTION_INTERVAL_SEC,
        "warning_count": 0,
        "last_warning_at": None,
        "frozen_until": None,
        # 可见性回查指标（评审文档 P0-3）
        "consecutive_invisible_count": 0,
        "total_invisible": 0,
    }


def _apply_day_rollover(state: dict[str, Any]) -> None:
    """Reset day_count when day_started_at is older than today (in-memory)."""
    today = _today_str()
    if state.get("day_started_at") != today:
        state["day_count"] = 0
        state["day_started_at"] = today


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def load(account_name: str) -> dict[str, Any]:
    """Read state from disk (or initialize defaults). Day rollover is
    applied in memory; callers that want to persist the rollover should
    call ``save`` after."""
    path = state_path(account_name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = _default_state(account_name)
    _apply_day_rollover(state)
    return state


def save(account_name: str, state: dict[str, Any]) -> None:
    """Atomically write state to disk."""
    _ensure_state_dir()
    path = state_path(account_name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def can_send(account_name: str) -> tuple[bool, str]:
    """Check whether the account is allowed to send a comment now.

    Returns ``(allowed, reason)``; ``reason`` is empty when allowed.
    Possible reasons: ``frozen``, ``daily_quota_exceeded``,
    ``min_interval_not_met``.
    """
    state = load(account_name)
    now = _now()

    frozen_until = _parse_iso(state.get("frozen_until"))
    if frozen_until and now < frozen_until:
        return False, f"frozen until {state['frozen_until']}"

    if state["day_count"] >= state["day_limit"]:
        return False, (
            f"daily_quota_exceeded ({state['day_count']}/{state['day_limit']})"
        )

    last_action = _parse_iso(state.get("last_action_at"))
    if last_action:
        elapsed = (now - last_action).total_seconds()
        if elapsed < state["min_action_interval_sec"]:
            remaining = int(state["min_action_interval_sec"] - elapsed)
            return False, f"min_interval_not_met ({remaining}s remaining)"

    return True, ""


def record_send(account_name: str) -> dict[str, Any]:
    """Record a successful send: increment day_count, update last_action_at."""
    state = load(account_name)
    state["day_count"] += 1
    state["last_action_at"] = _iso(_now())
    save(account_name, state)
    return state


def _ladder_freeze_until(warning_count: int) -> str:
    """Compute ``frozen_until`` ISO for the Nth warning (1-indexed).

    Ladder: 1 -> 4-6h random, 2 -> 24h, 3 -> 7d, 4+ -> permanent.
    """
    now = _now()
    if warning_count == 1:
        return _iso(now + timedelta(hours=random.uniform(4, 6)))
    if warning_count == 2:
        return _iso(now + timedelta(hours=24))
    if warning_count == 3:
        return _iso(now + timedelta(days=7))
    return PERMANENT_FREEZE_ISO


def record_warning(account_name: str) -> tuple[int, str]:
    """Record a 风控 warning hit. Returns ``(new_warning_count, frozen_until)``."""
    state = load(account_name)
    state["warning_count"] += 1
    state["last_warning_at"] = _iso(_now())
    state["frozen_until"] = _ladder_freeze_until(state["warning_count"])
    save(account_name, state)
    return state["warning_count"], state["frozen_until"]


CONSECUTIVE_INVISIBLE_WARNING_THRESHOLD = 3


def record_visibility_result(
    account_name: str, visible: bool
) -> tuple[int, int, bool]:
    """Record one visibility re-check outcome.

    - ``visible=True``: 清零 ``consecutive_invisible_count``。
    - ``visible=False``: 自增 ``consecutive_invisible_count`` 与 ``total_invisible``。
      累计阈值 (``CONSECUTIVE_INVISIBLE_WARNING_THRESHOLD``) 达成时，调用方
      应当调用 ``record_warning`` 走阶梯（本函数不主动触发，避免双写 state）。

    Returns ``(consecutive_invisible_count, total_invisible, should_warn)``。
    ``should_warn`` 为 True 时调用方应进一步调用 ``record_warning``。
    """
    state = load(account_name)
    # 旧 state 文件可能没有这两个字段，兼容
    state.setdefault("consecutive_invisible_count", 0)
    state.setdefault("total_invisible", 0)

    if visible:
        state["consecutive_invisible_count"] = 0
    else:
        state["consecutive_invisible_count"] += 1
        state["total_invisible"] += 1

    save(account_name, state)
    should_warn = (
        not visible
        and state["consecutive_invisible_count"] >= CONSECUTIVE_INVISIBLE_WARNING_THRESHOLD
    )
    return (
        state["consecutive_invisible_count"],
        state["total_invisible"],
        should_warn,
    )
