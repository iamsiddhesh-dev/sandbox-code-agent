"""Public-exposure guardrails: spend cap, rate limit, passphrase, backend lockdown."""

import pytest

from hosting.limits import (
    HostedConfig,
    RateLimiter,
    SpendLedger,
    check_passphrase,
    hosted_budget,
    load_hosted_config,
    resolve_hosted_backend,
)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "limits.db"


def test_disabled_by_default_so_local_runs_are_untouched(monkeypatch):
    for key in ("PUBLIC_DEMO", "DEMO_PASSPHRASE", "DAILY_SPEND_CAP_USD"):
        monkeypatch.delenv(key, raising=False)

    cfg = load_hosted_config()

    assert cfg.enabled is False
    assert cfg.misconfigurations() == []


def test_public_demo_without_a_passphrase_is_a_refusal(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "1")
    monkeypatch.delenv("DEMO_PASSPHRASE", raising=False)

    problems = load_hosted_config().misconfigurations()

    assert any("DEMO_PASSPHRASE" in p for p in problems)


def test_per_request_ceiling_must_sit_below_the_daily_cap():
    cfg = HostedConfig(enabled=True, passphrase="x", daily_spend_cap_usd=0.005, max_cost_usd=0.006)

    assert any("DAILY_SPEND_CAP_USD" in p for p in cfg.misconfigurations())


def test_hosted_ceilings_are_tighter_than_the_local_defaults():
    from agent.state import AgentState, Budget

    local = Budget(model="llama-3.3-70b-versatile")
    cfg = HostedConfig(enabled=True, passphrase="x")
    hosted = hosted_budget(cfg, "llama-3.3-70b-versatile")

    assert hosted.max_total_tokens < local.max_total_tokens
    assert hosted.max_cost_usd < local.max_cost_usd
    assert hosted.max_sandbox_seconds < local.max_sandbox_seconds
    assert hosted.per_run_timeout_s < local.per_run_timeout_s
    assert cfg.max_attempts < AgentState(request="x").max_attempts


def test_passphrase_rejects_wrong_empty_and_unset():
    assert check_passphrase("hunter2", "hunter2") is True
    assert check_passphrase("hunter3", "hunter2") is False
    assert check_passphrase("", "hunter2") is False
    assert check_passphrase(None, "hunter2") is False
    assert check_passphrase("anything", None) is False


def test_hosted_mode_refuses_every_backend_except_e2b():
    assert resolve_hosted_backend("e2b") == "e2b"

    for backend in ("docker", "fake"):
        with pytest.raises(RuntimeError, match="PUBLIC_DEMO"):
            resolve_hosted_backend(backend)


def test_spend_ledger_caps_the_day_and_flips_the_kill_switch(db):
    ledger = SpendLedger(db, cap_usd=0.010)

    assert ledger.reserve(0.006) is True
    assert ledger.reserve(0.006) is False
    assert ledger.exhausted() is False

    ledger.settle(reserved=0.006, actual=0.001)

    assert ledger.spent_today() == pytest.approx(0.001)
    assert ledger.reserve(0.006) is True


def test_reservation_blocks_concurrent_runs_from_overshooting_the_cap(db):
    """Two sessions reserving at once must not both pass a cap only one fits under."""
    ledger = SpendLedger(db, cap_usd=0.010)

    first = ledger.reserve(0.006)
    second = ledger.reserve(0.006)

    assert (first, second) == (True, False)
    assert ledger.spent_today() <= ledger.cap_usd


def test_kill_switch_engages_when_the_cap_is_consumed(db):
    ledger = SpendLedger(db, cap_usd=0.010)
    ledger.reserve(0.006)
    ledger.settle(reserved=0.006, actual=0.010)

    assert ledger.exhausted() is True
    assert ledger.remaining() == 0.0
    assert ledger.reserve(0.0001) is False


def test_ledger_state_survives_a_new_process(db):
    SpendLedger(db, cap_usd=0.010).reserve(0.004)

    reopened = SpendLedger(db, cap_usd=0.010)

    assert reopened.spent_today() == pytest.approx(0.004)


def test_rate_limiter_allows_up_to_the_limit_then_blocks(db):
    limiter = RateLimiter(db, limit=3, window_s=300)
    now = 1_000.0

    assert [limiter.check("s1", now + i)[0] for i in range(3)] == [True, True, True]

    allowed, retry_after = limiter.check("s1", now + 3)

    assert allowed is False
    assert 0 < retry_after <= 300


def test_rate_limit_is_per_session(db):
    limiter = RateLimiter(db, limit=1, window_s=300)
    now = 1_000.0

    assert limiter.check("s1", now)[0] is True
    assert limiter.check("s1", now)[0] is False
    assert limiter.check("s2", now)[0] is True


def test_rate_limit_window_slides(db):
    limiter = RateLimiter(db, limit=1, window_s=60)
    now = 1_000.0

    assert limiter.check("s1", now)[0] is True
    assert limiter.check("s1", now + 30)[0] is False
    assert limiter.check("s1", now + 61)[0] is True
