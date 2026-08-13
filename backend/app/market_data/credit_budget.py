from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from ..repository import SQLiteProjectionRepository


PROVIDER_ID = "TWELVE_DATA"
CONSERVATIVE_ENDPOINT_CREDITS: dict[str, int] = {
    "/time_series": 1,
    "/stocks": 1,
    "/etfs/list": 1,
    "/commodities": 1,
    "/cryptocurrencies": 1,
    "/indices": 1,
    "/bonds": 1,
    "/symbol_search": 1,
}


class CreditBudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CreditBudgetStatus:
    provider: str
    window: str
    daily_limit: int
    operational_budget: int
    reserve: int
    estimated_credits_used: int
    estimated_operational_remaining: int
    estimated_total_remaining: int
    reserve_preserved: bool
    request_count: int
    state: str
    provider_quota_limit: int | None = None
    provider_quota_used: int | None = None
    provider_quota_remaining: int | None = None
    provider_quota_window: str | None = None


class DailyCreditBudgetGuard:
    """Persistent conservative UTC-calendar-day request-credit guard.

    Twelve Data's Basic daily allowance resets at 00:00 UTC.  Using a rolling
    24-hour window would continue counting requests from the previous provider
    day after that reset and can incorrectly stop otherwise permitted scans.
    """

    def __init__(
        self,
        repository: SQLiteProjectionRepository,
        *,
        daily_limit: int = 800,
        operational_budget: int = 700,
        reserve: int = 100,
        clock: Callable[[], datetime] | None = None,
        endpoint_credits: Mapping[str, int] = CONSERVATIVE_ENDPOINT_CREDITS,
    ) -> None:
        if daily_limit <= 0 or operational_budget <= 0 or reserve < 0:
            raise ValueError("credit budgets must be positive")
        if operational_budget + reserve > daily_limit:
            raise ValueError("operational budget plus reserve exceeds daily limit")
        self._repository = repository
        self.daily_limit = daily_limit
        self.operational_budget = operational_budget
        self.reserve = reserve
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._endpoint_credits = dict(endpoint_credits)

    def reserve_request(
        self,
        endpoint: str,
        category: str,
        *,
        started_at: datetime | None = None,
    ) -> int:
        now = (started_at or self._clock()).astimezone(timezone.utc)
        credits = self._endpoint_credits.get(endpoint, 1)
        reservation = self._repository.reserve_provider_credits(
            provider=PROVIDER_ID,
            request_started_at=now,
            endpoint=endpoint,
            request_category=category,
            estimated_credits=credits,
            window_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
            operational_budget=self.operational_budget,
        )
        if reservation is None:
            raise CreditBudgetExhausted(
                "Twelve Data UTC-day operational credit budget is exhausted; reserve preserved."
            )
        return reservation

    def finalize_request(
        self,
        reservation_id: int,
        *,
        status: str,
        http_status: int | None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        normalized = {
            str(key).lower(): str(value) for key, value in (headers or {}).items()
        }

        def integer(*names: str) -> int | None:
            for name in names:
                value = normalized.get(name)
                if value is not None and value.isdigit():
                    return int(value)
            return None

        quota_used = integer(
            "api-credits-used", "x-api-credits-used", "x-ratelimit-used"
        )
        quota_remaining = integer(
            "api-credits-left",
            "api-credits-remaining",
            "x-api-credits-remaining",
            "x-ratelimit-remaining",
        )
        quota_limit = integer(
            "api-credits-limit", "x-api-credits-limit", "x-ratelimit-limit"
        )
        if (
            quota_limit is None
            and quota_used is not None
            and quota_remaining is not None
        ):
            quota_limit = quota_used + quota_remaining

        self._repository.finalize_provider_credit(
            reservation_id,
            request_status=status,
            http_status=http_status,
            quota_limit=quota_limit,
            quota_used=quota_used,
            quota_remaining=quota_remaining,
        )

    def status(self, *, as_of: datetime | None = None) -> CreditBudgetStatus:
        now = (as_of or self._clock()).astimezone(timezone.utc)
        usage = self._repository.provider_credit_usage(
            PROVIDER_ID,
            window_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        used = int(usage["estimated_credits_used"])
        operational_remaining = max(0, self.operational_budget - used)
        total_remaining = max(0, self.daily_limit - used)
        return CreditBudgetStatus(
            provider=PROVIDER_ID,
            window="UTC_CALENDAR_DAY",
            daily_limit=self.daily_limit,
            operational_budget=self.operational_budget,
            reserve=self.reserve,
            estimated_credits_used=used,
            estimated_operational_remaining=operational_remaining,
            estimated_total_remaining=total_remaining,
            reserve_preserved=used <= self.operational_budget,
            request_count=int(usage["request_count"]),
            state=(
                "AVAILABLE"
                if operational_remaining > 0
                else "OPERATIONAL_BUDGET_EXHAUSTED"
            ),
            provider_quota_limit=usage["provider_quota_limit"],
            provider_quota_used=usage["provider_quota_used"],
            provider_quota_remaining=usage["provider_quota_remaining"],
            provider_quota_window=(
                "MINUTE"
                if usage["provider_quota_limit"] is not None
                or usage["provider_quota_used"] is not None
                or usage["provider_quota_remaining"] is not None
                else None
            ),
        )
