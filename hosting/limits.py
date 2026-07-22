"""Guardrails for running the demo as a public, unauthenticated-by-default endpoint.

Locally this module does nothing: `PUBLIC_DEMO` is off, and the app keeps the
Phase 3 budget defaults. Hosted, it adds the three things a public
code-execution endpoint cannot ship without — a global daily spend cap with a
kill switch, a per-session rate limit, and a shared passphrase — plus tighter
per-request ceilings than the local defaults.

The spend ledger and rate limiter are SQLite-backed rather than in-memory
because Streamlit reruns the script on every interaction and a free host may run
more than one worker: per-process state would reset under the user's feet and
would not be shared across sessions, which is exactly what a global cap needs.
"""

from __future__ import annotations

import hmac
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agent.state import Budget

DEFAULT_STATE_PATH = Path(".hosted_state/limits.db")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else int(raw)


@dataclass(frozen=True)
class HostedConfig:
    enabled: bool = False
    passphrase: str | None = None
    daily_spend_cap_usd: float = 1.00
    rate_limit_runs: int = 5
    rate_limit_window_s: int = 300
    max_attempts: int = 2
    max_total_tokens: int = 8_000
    max_cost_usd: float = 0.006
    max_sandbox_seconds: float = 40.0
    per_run_timeout_s: int = 20
    state_path: Path = DEFAULT_STATE_PATH

    def misconfigurations(self) -> list[str]:
        """Refuse-to-start reasons. A public endpoint should fail loudly, not quietly."""
        if not self.enabled:
            return []

        problems = []
        if not self.passphrase:
            problems.append(
                "PUBLIC_DEMO is on but DEMO_PASSPHRASE is unset — that would publish "
                "an open code-execution endpoint."
            )
        if self.daily_spend_cap_usd <= 0:
            problems.append("DAILY_SPEND_CAP_USD must be greater than 0.")
        if self.max_cost_usd >= self.daily_spend_cap_usd:
            problems.append(
                "HOSTED_MAX_COST_USD must be below DAILY_SPEND_CAP_USD, or a single "
                "request could exhaust the whole day's budget."
            )
        if self.rate_limit_runs <= 0:
            problems.append("HOSTED_RATE_LIMIT_RUNS must be greater than 0.")
        return problems


def load_hosted_config() -> HostedConfig:
    enabled = _env_bool("PUBLIC_DEMO", False)
    passphrase = os.getenv("DEMO_PASSPHRASE") or None
    state = os.getenv("HOSTED_STATE_PATH")

    return HostedConfig(
        enabled=enabled,
        passphrase=passphrase,
        daily_spend_cap_usd=_env_float("DAILY_SPEND_CAP_USD", 1.00),
        rate_limit_runs=_env_int("HOSTED_RATE_LIMIT_RUNS", 5),
        rate_limit_window_s=_env_int("HOSTED_RATE_LIMIT_WINDOW_S", 300),
        max_attempts=_env_int("HOSTED_MAX_ATTEMPTS", 2),
        max_total_tokens=_env_int("HOSTED_MAX_TOTAL_TOKENS", 8_000),
        max_cost_usd=_env_float("HOSTED_MAX_COST_USD", 0.006),
        max_sandbox_seconds=_env_float("HOSTED_MAX_SANDBOX_SECONDS", 40.0),
        per_run_timeout_s=_env_int("HOSTED_PER_RUN_TIMEOUT_S", 20),
        state_path=Path(state) if state else DEFAULT_STATE_PATH,
    )


def check_passphrase(supplied: str | None, expected: str | None) -> bool:
    """Constant-time compare so the gate can't be probed character by character."""
    if not expected:
        return False
    return hmac.compare_digest((supplied or "").strip(), expected.strip())


def resolve_hosted_backend(configured: str) -> str:
    """Free hosts don't give you Docker-in-Docker; E2B is the only remote-safe path."""
    if configured.lower() != "e2b":
        raise RuntimeError(
            f"SANDBOX_BACKEND={configured!r} cannot be used with PUBLIC_DEMO=1. "
            "Hosted runs must use the E2B backend — free hosts do not provide a "
            "Docker daemon, and the 'fake' backend does not execute anything."
        )
    return "e2b"


def hosted_budget(cfg: HostedConfig, model: str) -> Budget:
    """Per-request ceilings, deliberately tighter than the local defaults."""
    return Budget(
        model=model,
        max_total_tokens=cfg.max_total_tokens,
        max_cost_usd=cfg.max_cost_usd,
        max_sandbox_seconds=cfg.max_sandbox_seconds,
        per_run_timeout_s=cfg.per_run_timeout_s,
    )


@contextmanager
def _db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
    finally:
        conn.close()


class SpendLedger:
    """Global daily spend cap with a kill switch, shared across every session."""

    def __init__(self, path: Path, cap_usd: float):
        self.path = path
        self.cap_usd = cap_usd
        with _db(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS spend ("
                "  day TEXT PRIMARY KEY, committed_usd REAL NOT NULL DEFAULT 0,"
                "  reserved_usd REAL NOT NULL DEFAULT 0, runs INTEGER NOT NULL DEFAULT 0)"
            )

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def _row(self, conn) -> tuple[float, float, int]:
        row = conn.execute(
            "SELECT committed_usd, reserved_usd, runs FROM spend WHERE day = ?",
            (self._today(),),
        ).fetchone()
        return row or (0.0, 0.0, 0)

    def spent_today(self) -> float:
        with _db(self.path) as conn:
            committed, reserved, _ = self._row(conn)
        return committed + reserved

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent_today())

    def reserve(self, amount: float) -> bool:
        """Hold `amount` up front, before the run starts.

        Checking the cap and then charging the real cost afterwards leaves a
        window where concurrent sessions all pass the check and blow through it
        together. Reserving the per-request maximum first and reconciling to the
        actual cost in `settle()` closes that window: the cap is enforced against
        worst-case exposure, never against optimistic accounting.
        """
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                committed, reserved, runs = self._row(conn)
                if committed + reserved + amount > self.cap_usd:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT INTO spend (day, committed_usd, reserved_usd, runs) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
                    "reserved_usd = reserved_usd + ?, runs = runs + 1",
                    (self._today(), 0.0, amount, 1, amount),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def settle(self, reserved: float, actual: float) -> None:
        """Release the reservation and commit what the run actually cost."""
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE spend SET reserved_usd = MAX(0, reserved_usd - ?), "
                    "committed_usd = committed_usd + ? WHERE day = ?",
                    (reserved, actual, self._today()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def exhausted(self) -> bool:
        return self.remaining() <= 0.0


class RateLimiter:
    """Per-session sliding window, so one visitor can't monopolise the day's cap."""

    def __init__(self, path: Path, limit: int, window_s: int):
        self.path = path
        self.limit = limit
        self.window_s = window_s
        with _db(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "  session_id TEXT NOT NULL, ts REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS runs_session ON runs (session_id, ts)")

    def _prune(self, conn, now: float) -> None:
        conn.execute("DELETE FROM runs WHERE ts < ?", (now - self.window_s,))

    def check(self, session_id: str, now: float | None = None) -> tuple[bool, float]:
        """Return (allowed, seconds_until_next_slot)."""
        now = time.time() if now is None else now
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._prune(conn, now)
                rows = conn.execute(
                    "SELECT ts FROM runs WHERE session_id = ? ORDER BY ts",
                    (session_id,),
                ).fetchall()
                if len(rows) >= self.limit:
                    retry_after = max(0.0, rows[0][0] + self.window_s - now)
                    conn.execute("COMMIT")
                    return False, retry_after
                conn.execute(
                    "INSERT INTO runs (session_id, ts) VALUES (?, ?)", (session_id, now)
                )
                conn.execute("COMMIT")
                return True, 0.0
            except Exception:
                conn.execute("ROLLBACK")
                raise
