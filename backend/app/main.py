from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from .config import Settings
from .dashboard_api import DashboardEnvelope, dashboard_snapshot
from .domain import primitive
from .engine.strategy import Spect8StrategyEvaluator
from .market_data.clock import FixedClock, SystemClock
from .market_data.closed_bar import ClosedBarDetector
from .market_data.coordinator import MarketDataCoordinator
from .market_data.normalizer import CandleNormalizer
from .market_data.registry import CanonicalInstrumentRegistry
from .market_data.replay_provider import ReplayMarketDataProvider
from .market_data.runtime import MarketDataRuntime
from .market_data.twelve_data_provider import TwelveDataProvider
from .repository import SQLiteProjectionRepository
from .service import WalkingSkeletonService

SYNTHETIC_NOTICE = (
    "SYNTHETIC REPLAY MARKET DATA — no live provider is connected."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_environment()
    configured.validate()
    repository = SQLiteProjectionRepository(configured.database_path)
    if configured.market_data_provider == "twelve_data":
        assert configured.twelve_data_api_key is not None
        provider = TwelveDataProvider(
            configured.twelve_data_api_key,
            instrument=configured.instrument,
            timeframes=configured.timeframes,
        )
        clock = SystemClock()
    else:
        provider = ReplayMarketDataProvider(
            configured.repository_root / "golden",
            configured.selected_cases,
        )
        clock = FixedClock(provider.initial_clock_time())
    registry = CanonicalInstrumentRegistry(provider.discover_instruments())
    evaluator = Spect8StrategyEvaluator()
    service = WalkingSkeletonService(evaluator, None, repository)
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=registry,
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=service,
        repository=repository,
        clock=clock,
    )
    runtime = MarketDataRuntime(
        coordinator,
        repository,
        poll_seconds=configured.market_data_poll_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        runtime_task: asyncio.Task[None] | None = None
        if configured.auto_seed_synthetic and provider.identity.synthetic:
            runtime.run_once()
        elif (
            not provider.identity.synthetic
            and configured.market_data_runtime_enabled
        ):
            runtime_task = asyncio.create_task(
                runtime.run(), name="spect8-market-data-runtime"
            )
        try:
            yield
        finally:
            if runtime_task is not None:
                runtime.stop()
                runtime_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime_task

    app = FastAPI(
        title="Spect8 HedgeFund Dashboard — Phase 3A",
        version="0.5.0",
        description=(
            SYNTHETIC_NOTICE
            if provider.identity.synthetic
            else "READ-ONLY Twelve Data EUR/USD market-data scanner."
        ),
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.repository = repository
    app.state.service = service
    app.state.provider = provider
    app.state.registry = registry
    app.state.clock = clock
    app.state.coordinator = coordinator
    app.state.market_data_runtime = runtime

    def require_internal_key(
        x_spect8_internal_key: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_spect8_internal_key is None or not hmac.compare_digest(
            x_spect8_internal_key, configured.internal_api_key
        ):
            raise HTTPException(status_code=401, detail="Authentication required")

    protected = Depends(require_internal_key)

    def envelope(data: Any) -> dict[str, Any]:
        return {
            "synthetic": provider.identity.synthetic,
            "source": (
                "REPLAY_MARKET_DATA_PROVIDER"
                if provider.identity.synthetic
                else "TWELVE_DATA_PROVIDER"
            ),
            "notice": (
                SYNTHETIC_NOTICE
                if provider.identity.synthetic
                else "READ-ONLY Twelve Data EUR/USD market data."
            ),
            "data": data,
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        provider_health = coordinator.current_health()
        if provider_health is None:
            current = primitive(provider.health(clock.now()))
            provider_health = {
                "provider": current["provider_id"],
                "state": current["state"],
                "previous_state": None,
                "checked_at": current["checked_at"],
                "latest_completed_close": current[
                    "latest_completed_close"
                ],
                "freshness_seconds": current["freshness_seconds"],
                "detail": current["detail"],
                "synthetic": provider.identity.synthetic,
            }
        return envelope(
            {
                "status": "ok",
                "mode": (
                    "PHASE_2B_REPLAY_MARKET_DATA"
                    if provider.identity.synthetic
                    else "PHASE_3A_TWELVE_DATA_RUNTIME"
                ),
                "market_data": (
                    "REPLAY_ONLY"
                    if provider.identity.synthetic
                    else "TWELVE_DATA_EUR_USD"
                ),
                "database": "sqlite",
                "provider": primitive(provider.identity),
                "provider_health": provider_health,
            }
        )

    @app.get("/instruments", dependencies=[protected])
    def instruments() -> dict[str, Any]:
        return envelope(
            [
                {
                    "instrument_id": instrument.instrument_id,
                    "provider": instrument.provider_id,
                    "provider_symbol": instrument.provider_symbol,
                    "asset_class": instrument.asset_class,
                    "quote_currency": instrument.quote_currency,
                    "profit_currency": instrument.profit_currency,
                    "timeframes": [
                        timeframe.value
                        for timeframe in instrument.available_timeframes
                    ],
                    "session_timezone": instrument.session_timezone,
                    "point_size": float(instrument.point_size),
                    "tick_size": (
                        float(instrument.tick_size)
                        if instrument.tick_size is not None
                        else None
                    ),
                    "price_precision": instrument.price_precision,
                    "synthetic": instrument.synthetic,
                }
                for instrument in registry.all()
            ]
        )

    @app.get("/statuses", dependencies=[protected])
    def statuses(request: Request) -> dict[str, Any]:
        return envelope(request.app.state.repository.statuses())

    @app.get("/filtered", dependencies=[protected])
    def filtered(request: Request) -> dict[str, Any]:
        values = [
            status
            for status in request.app.state.repository.statuses()
            if status["filter_result"]["buy_matched"]
            or status["filter_result"]["sell_matched"]
        ]
        return envelope(values)

    @app.get("/signals", dependencies=[protected])
    def signals(request: Request) -> dict[str, Any]:
        values = [
            status
            for status in request.app.state.repository.statuses()
            if status["signal_result"]["confirmed_buy"]
            or status["signal_result"]["confirmed_sell"]
        ]
        return envelope(values)

    @app.get("/events", dependencies=[protected])
    def events(request: Request) -> dict[str, Any]:
        return envelope(request.app.state.repository.events())

    @app.get(
        "/dashboard",
        dependencies=[protected],
        response_model=DashboardEnvelope,
    )
    def dashboard() -> dict[str, Any]:
        return envelope(
            dashboard_snapshot(repository, registry.all()[0], clock.now())
        )

    @app.post("/synthetic/replay", dependencies=[protected])
    def replay() -> dict[str, Any]:
        if not provider.identity.synthetic:
            raise HTTPException(
                status_code=409,
                detail="Synthetic replay is unavailable in live mode.",
            )
        poll = runtime.run_once()
        return envelope(
            [
                {
                    "source_case_id": outcome.source_case_id,
                    "idempotency_key": outcome.idempotency_key,
                    "replayed": outcome.replayed,
                    "events_created": outcome.events_created,
                    "health_state": outcome.health_state.value,
                    "issues": list(outcome.issues),
                }
                for outcome in poll.outcomes
            ]
        )

    return app


app = create_app()
