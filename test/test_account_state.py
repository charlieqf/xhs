"""Layer 1 单元测试：account_state + risk_control 的纯逻辑。

跑法（项目根目录）：
    python test/test_account_state.py

不需要 Chrome、不需要真实账号。所有断言用临时账号名 ``_test_unit_*``，
跑完会清理 ``prod/account_state/_test_unit_*.json``。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 让 import 找到 scripts/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import account_state  # noqa: E402
import risk_control  # noqa: E402


TZ = ZoneInfo("Asia/Shanghai")
TEST_PREFIX = "_test_unit_"


def _fresh(name: str) -> str:
    """返回一个干净的测试账号名，并删掉历史 state 文件。"""
    full = TEST_PREFIX + name
    path = account_state.state_path(full)
    if os.path.exists(path):
        os.remove(path)
    return full


def cleanup_all():
    state_dir = os.path.join(ROOT, "prod", "account_state")
    if not os.path.isdir(state_dir):
        return
    for fn in os.listdir(state_dir):
        if fn.startswith(TEST_PREFIX) and fn.endswith(".json"):
            os.remove(os.path.join(state_dir, fn))


# ---------------------------------------------------------------------------
# account_state
# ---------------------------------------------------------------------------

def test_default_state_is_written_on_first_load():
    acc = _fresh("default_load")
    s = account_state.load(acc)
    assert s["account_name"] == acc
    assert s["day_count"] == 0
    assert s["day_limit"] == 5
    assert s["min_action_interval_sec"] == 1800
    assert s["warning_count"] == 0
    assert s["last_action_at"] is None
    assert s["frozen_until"] is None


def test_can_send_blocks_until_min_interval_elapses():
    acc = _fresh("min_interval")
    ok, _ = account_state.can_send(acc)
    assert ok, "first send should be allowed"
    account_state.record_send(acc)
    ok, reason = account_state.can_send(acc)
    assert not ok and "min_interval" in reason, f"expected min_interval block, got {reason!r}"


def test_can_send_blocks_when_daily_quota_exhausted():
    acc = _fresh("quota")
    s = account_state.load(acc)
    s["day_count"] = s["day_limit"]
    s["last_action_at"] = None  # 先把 min_interval 路径绕开，单独测 quota
    account_state.save(acc, s)
    ok, reason = account_state.can_send(acc)
    assert not ok and "daily_quota" in reason, f"expected daily_quota block, got {reason!r}"


def test_day_rollover_resets_day_count():
    acc = _fresh("rollover")
    s = account_state.load(acc)
    s["day_count"] = 99
    s["day_started_at"] = "2020-01-01"
    account_state.save(acc, s)
    s2 = account_state.load(acc)
    assert s2["day_count"] == 0, "stale day_started_at should reset day_count"
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    assert s2["day_started_at"] == today


def test_can_send_blocks_when_frozen():
    acc = _fresh("frozen")
    s = account_state.load(acc)
    s["frozen_until"] = (datetime.now(TZ) + timedelta(hours=2)).isoformat(timespec="seconds")
    account_state.save(acc, s)
    ok, reason = account_state.can_send(acc)
    assert not ok and "frozen" in reason, f"expected frozen block, got {reason!r}"


def test_warning_ladder_progression():
    acc = _fresh("ladder")
    now = datetime.now(TZ)

    cnt, frozen = account_state.record_warning(acc)
    assert cnt == 1
    delta = datetime.fromisoformat(frozen) - now
    assert timedelta(hours=3, minutes=50) < delta < timedelta(hours=6, minutes=10), \
        f"warning #1 should freeze 4-6h, got {delta}"

    cnt, frozen = account_state.record_warning(acc)
    assert cnt == 2
    delta = datetime.fromisoformat(frozen) - now
    assert timedelta(hours=23, minutes=50) < delta < timedelta(hours=24, minutes=10), \
        f"warning #2 should freeze ~24h, got {delta}"

    cnt, frozen = account_state.record_warning(acc)
    assert cnt == 3
    delta = datetime.fromisoformat(frozen) - now
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1), \
        f"warning #3 should freeze ~7d, got {delta}"

    cnt, frozen = account_state.record_warning(acc)
    assert cnt == 4
    assert frozen == account_state.PERMANENT_FREEZE_ISO, \
        f"warning #4 should be PERMANENT, got {frozen}"


# ---------------------------------------------------------------------------
# risk_control
# ---------------------------------------------------------------------------

def test_detect_risk_redirect_classifies_known_urls():
    assert risk_control.detect_risk_redirect(
        "https://www.xiaohongshu.com/?error_code=300013"
    ) == risk_control.RATE_LIMIT
    assert risk_control.detect_risk_redirect(
        "https://www.xiaohongshu.com/page?error_msg=foo"
    ) == risk_control.RATE_LIMIT
    assert risk_control.detect_risk_redirect(
        "https://www.xiaohongshu.com/website-login/error?return_url=x"
    ) == risk_control.LOGIN_REDIRECT
    assert risk_control.detect_risk_redirect(
        "https://www.xiaohongshu.com/explore"
    ) is None
    assert risk_control.detect_risk_redirect(None) is None
    assert risk_control.detect_risk_redirect("") is None


def test_check_and_record_writes_warning_to_state():
    acc = _fresh("rc_record")
    result = risk_control.check_and_record(acc, "https://x.com/explore")
    assert result is None, "clean URL should not record a warning"

    result = risk_control.check_and_record(
        acc, "https://www.xiaohongshu.com/?error_code=300013"
    )
    assert result is not None
    kind, count, frozen = result
    assert kind == risk_control.RATE_LIMIT
    assert count == 1

    s = account_state.load(acc)
    assert s["warning_count"] == 1
    assert s["frozen_until"] == frozen
    assert s["last_warning_at"] is not None


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

TESTS = [
    test_default_state_is_written_on_first_load,
    test_can_send_blocks_until_min_interval_elapses,
    test_can_send_blocks_when_daily_quota_exhausted,
    test_day_rollover_resets_day_count,
    test_can_send_blocks_when_frozen,
    test_warning_ladder_progression,
    test_detect_risk_redirect_classifies_known_urls,
    test_check_and_record_writes_warning_to_state,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR   {fn.__name__}: {type(e).__name__}: {e}")
    cleanup_all()
    print()
    if failed:
        print(f"{failed}/{len(TESTS)} failed")
        return 1
    print(f"all {len(TESTS)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
