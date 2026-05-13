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


def test_record_visibility_result_resets_on_visible():
    acc = _fresh("vis_reset")
    # 预置不可见计数
    account_state.record_visibility_result(acc, False)
    account_state.record_visibility_result(acc, False)
    # 一次可见就清零
    cons, total, warn = account_state.record_visibility_result(acc, True)
    assert cons == 0
    assert total == 2  # 累计计数不回退
    assert warn is False


def test_record_visibility_result_increments_on_invisible_and_warns_at_threshold():
    acc = _fresh("vis_warn")
    cons, total, warn = account_state.record_visibility_result(acc, False)
    assert (cons, total, warn) == (1, 1, False)
    cons, total, warn = account_state.record_visibility_result(acc, False)
    assert (cons, total, warn) == (2, 2, False)
    cons, total, warn = account_state.record_visibility_result(acc, False)
    assert cons == 3 and total == 3 and warn is True, \
        "consecutive count hitting 3 should signal should_warn"


def test_visibility_fields_present_in_default_state():
    acc = _fresh("vis_default")
    s = account_state.load(acc)
    assert s["consecutive_invisible_count"] == 0
    assert s["total_invisible"] == 0


def test_record_visibility_handles_legacy_state_without_field():
    acc = _fresh("vis_legacy")
    # 模拟旧 state 文件（没有 visibility 字段）
    s = account_state.load(acc)
    s.pop("consecutive_invisible_count", None)
    s.pop("total_invisible", None)
    s.pop("visibility_window", None)
    account_state.save(acc, s)
    # 第一次调用应当能正常补齐字段
    cons, total, warn = account_state.record_visibility_result(acc, False)
    assert cons == 1 and total == 1 and warn is False
    # 滑动窗也要补齐
    s2 = account_state.load(acc)
    assert s2["visibility_window"] == [False]


def test_record_visibility_result_appends_to_window():
    acc = _fresh("vis_window_append")
    account_state.record_visibility_result(acc, True)
    account_state.record_visibility_result(acc, False)
    account_state.record_visibility_result(acc, True)
    s = account_state.load(acc)
    assert s["visibility_window"] == [True, False, True]


def test_visibility_window_caps_at_max():
    acc = _fresh("vis_window_cap")
    # 推入超过 MAX 的样本，window 应被裁剪到 MAX
    for _ in range(account_state.VISIBILITY_WINDOW_MAX + 5):
        account_state.record_visibility_result(acc, True)
    s = account_state.load(acc)
    assert len(s["visibility_window"]) == account_state.VISIBILITY_WINDOW_MAX
    assert all(s["visibility_window"])  # 全 True


def test_recent_invisible_rate_returns_none_when_sample_too_small():
    acc = _fresh("vis_rate_small")
    # window 长度 < INVISIBLE_RATE_WINDOW
    for _ in range(account_state.INVISIBLE_RATE_WINDOW - 1):
        account_state.record_visibility_result(acc, False)
    rate = account_state.recent_invisible_rate(acc)
    assert rate is None, f"expected None for small sample, got {rate}"


def test_recent_invisible_rate_correct_when_full_window():
    acc = _fresh("vis_rate_full")
    # 推 10 条：4 条 False、6 条 True → rate = 0.4
    for v in [False, True, False, True, False, True, False, True, True, True]:
        account_state.record_visibility_result(acc, v)
    rate = account_state.recent_invisible_rate(acc)
    assert rate == 0.4, f"expected 0.4, got {rate}"


def test_recent_invisible_rate_uses_only_last_n():
    acc = _fresh("vis_rate_window")
    # 推 15 条：前 5 全 False（应被滑动窗排除），后 10 全 True → rate = 0
    for _ in range(5):
        account_state.record_visibility_result(acc, False)
    for _ in range(10):
        account_state.record_visibility_result(acc, True)
    rate = account_state.recent_invisible_rate(acc)
    assert rate == 0.0, f"expected 0.0 (last 10 all True), got {rate}"


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
    test_record_visibility_result_resets_on_visible,
    test_record_visibility_result_increments_on_invisible_and_warns_at_threshold,
    test_visibility_fields_present_in_default_state,
    test_record_visibility_handles_legacy_state_without_field,
    test_record_visibility_result_appends_to_window,
    test_visibility_window_caps_at_max,
    test_recent_invisible_rate_returns_none_when_sample_too_small,
    test_recent_invisible_rate_correct_when_full_window,
    test_recent_invisible_rate_uses_only_last_n,
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
