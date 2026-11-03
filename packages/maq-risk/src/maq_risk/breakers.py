"""Circuit breakers: pause new positions when the machinery is unhealthy.

The drawdown and daily-loss stops live in the kernel's checks. These breakers
watch the rest: model API failures, model output that fails the schema, and
LLM spend. A tripped rate breaker stays tripped for ``cooldown`` and then
starts again from a clean window; the budget breaker resets at midnight UTC.
Feed them from the agent layer; pass ``tripped(now)`` into ``RiskContext``.
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta

from maq_core.config.models import BreakerSettings


class RateBreaker:
    def __init__(
        self,
        name: str,
        *,
        threshold: float,
        window: int,
        min_samples: int,
        cooldown: timedelta,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        if window < 1 or not 1 <= min_samples <= window:
            raise ValueError("need 1 <= min_samples <= window")
        self.name = name
        self.threshold = threshold
        self.min_samples = min_samples
        self.cooldown = cooldown
        self._outcomes: deque[bool] = deque(maxlen=window)
        self._tripped_until: datetime | None = None
        self._trip_detail = ""

    def record(self, ok: bool, at: datetime) -> None:
        if self._tripped_until is not None and at < self._tripped_until:
            return  # cooling down; the window restarts afterwards
        self._outcomes.append(ok)
        failures = self._outcomes.count(False)
        total = len(self._outcomes)
        if total >= self.min_samples and failures / total >= self.threshold:
            self._tripped_until = at + self.cooldown
            self._trip_detail = f"{failures} of the last {total} failed; paused until {self._tripped_until:%H:%M} UTC"
            self._outcomes.clear()

    def status(self, now: datetime) -> str | None:
        if self._tripped_until is not None and now < self._tripped_until:
            return f"{self.name}: {self._trip_detail}"
        return None


class BudgetBreaker:
    def __init__(self, name: str, budget: float) -> None:
        if budget <= 0:
            raise ValueError("budget must be positive")
        self.name = name
        self.budget = budget
        self._day: date | None = None
        self._spent = 0.0

    def add(self, amount: float, at: datetime) -> None:
        if at.date() != self._day:
            self._day, self._spent = at.date(), 0.0
        self._spent += amount

    def spent(self, now: datetime) -> float:
        return self._spent if now.date() == self._day else 0.0

    def status(self, now: datetime) -> str | None:
        spent = self.spent(now)
        if spent >= self.budget:
            return f"{self.name}: spent {spent:.2f} of {self.budget:.2f} today"
        return None


class CircuitBreakers:
    def __init__(self, settings: BreakerSettings | None = None) -> None:
        settings = settings or BreakerSettings()
        cooldown = timedelta(minutes=settings.cooldown_minutes)
        self.api = RateBreaker(
            "api_error_rate",
            threshold=settings.api_error_rate,
            window=settings.window,
            min_samples=settings.min_samples,
            cooldown=cooldown,
        )
        self.schema = RateBreaker(
            "schema_failure_rate",
            threshold=settings.schema_failure_rate,
            window=settings.window,
            min_samples=settings.min_samples,
            cooldown=cooldown,
        )
        self.llm_budget = (
            BudgetBreaker("llm_daily_budget_usd", settings.llm_daily_budget_usd)
            if settings.llm_daily_budget_usd is not None
            else None
        )

    def record_api_call(self, ok: bool, at: datetime) -> None:
        self.api.record(ok, at)

    def record_schema_check(self, ok: bool, at: datetime) -> None:
        self.schema.record(ok, at)

    def record_llm_cost(self, usd: float, at: datetime) -> None:
        if self.llm_budget is not None:
            self.llm_budget.add(usd, at)

    def tripped(self, now: datetime) -> list[str]:
        breakers: list[RateBreaker | BudgetBreaker | None] = [
            self.api,
            self.schema,
            self.llm_budget,
        ]
        return [s for b in breakers if b is not None and (s := b.status(now)) is not None]
