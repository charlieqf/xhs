"""URL-based 风控 signal detection.

XHS redirects a browser to specific error URLs when its risk-control system
flags suspicious activity. This module classifies the URL and (optionally)
records a warning into the per-account state so the warning ladder
(``account_state.record_warning``) drives the freeze duration.

Two signal types are recognized today:

- ``rate_limit``: URL contains ``error_code=300013`` or ``error_msg``.
  Triggered when XHS thinks the account is operating too fast.
- ``login_redirect``: URL contains ``website-login/error``.
  Triggered when XHS forces a re-login (often a soft-ban precursor).

Usage from a bot::

    signal = risk_control.check_and_record(account_name, current_url)
    if signal is not None:
        kind, warning_count, frozen_until = signal
        print(f"[风控] {kind}: warning #{warning_count}, frozen until {frozen_until}")
        return  # abort current iteration; ``account_state.can_send`` will
                # block further sends until ``frozen_until`` passes
"""

from __future__ import annotations

from typing import Optional

import account_state


RATE_LIMIT = "rate_limit"
LOGIN_REDIRECT = "login_redirect"


def detect_risk_redirect(url: str | None) -> Optional[str]:
    """Classify ``url`` as a 风控 redirect signal, or ``None`` if clean."""
    if not url:
        return None
    if "error_code=300013" in url or "error_msg" in url:
        return RATE_LIMIT
    if "website-login/error" in url:
        return LOGIN_REDIRECT
    return None


def check_and_record(
    account_name: str, url: str | None
) -> Optional[tuple[str, int, str]]:
    """Detect 风控 in ``url``; if found, append a warning to account state.

    Returns ``None`` when the URL is clean. When a signal is detected,
    returns ``(signal_kind, new_warning_count, frozen_until_iso)`` so the
    caller can log and decide how to abort the current iteration.
    """
    signal = detect_risk_redirect(url)
    if signal is None:
        return None
    count, frozen_until = account_state.record_warning(account_name)
    return signal, count, frozen_until
