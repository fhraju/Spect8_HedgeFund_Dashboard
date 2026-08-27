from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timezone
from typing import Annotated, Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel

from .config import Settings
from .dashboard_api import (
    DashboardEnvelope,
    ScannerEnvelope,
    dashboard_snapshot,
    scanner_snapshot,
)
from .domain import FilterMode, primitive
from .engine.strategy import Spect8StrategyEvaluator
from .historical_replay import (
    HistoricalReplayRepository,
    HistoricalReplayService,
    ReplayConflictError,
    ReplayNotFoundError,
    TwelveDataHistoricalSource,
)
from .historical_replay_api import (
    HistoricalReplayEnvelope,
    ReplayCreateRequest,
    ReplayDeleteView,
    ReplayEvaluationDetail,
    ReplayEvaluationPage,
    ReplayRunsView,
    ReplayRunView,
    ReplaySummaryView,
)
from .market_data.clock import FixedClock, SystemClock
from .market_data.closed_bar import ClosedBarDetector
from .market_data.coordinator import MarketDataCoordinator
from .market_data.credit_budget import DailyCreditBudgetGuard
from .market_data.multi_provider import MultiInstrumentTwelveDataProvider
from .market_data.normalizer import CandleNormalizer
from .market_data.platform_authority import (
    PlatformAuthorityError,
    PlatformAuthorityRuntime,
    UnifiedPlatformAuthorityRuntime,
)
from .market_data.platform_shadow import PlatformShadowRuntime
from .market_data.registry import (
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from .market_data.replay_provider import ReplayMarketDataProvider
from .market_data.runtime import MarketDataRuntime
from .market_data.runtime_support import configure_runtime_logging
from .market_data.signal_lifecycle import SignalLifecycleService
from .market_data.twelve_data_provider import TwelveDataProvider
from .repository import SQLiteProjectionRepository
from .service import WalkingSkeletonService

SYNTHETIC_NOTICE = "SYNTHETIC REPLAY MARKET DATA — no live provider is connected."


class FilterModeRequest(BaseModel):
    mode: FilterMode


class FilterModeView(BaseModel):
    active_filter_mode: FilterMode
    filter_timeframe: str


def _live_not_ready(
    runtime: PlatformAuthorityRuntime | None, instrument_id: str | None = None
) -> bool:
    """Return only explicit live-readiness failures; preserve legacy providers."""

    if runtime is None:
        return False
    status = runtime.status()
    if instrument_id is None:
        readiness = status.get("overall_live_readiness")
        return readiness is not None and readiness != "LIVE_READY"
    instruments = status.get("live_instruments")
    if not isinstance(instruments, dict):
        return False
    item = instruments.get(instrument_id)
    return bool(
        status.get("partial_data_state") == "NOT_READY"
        or (isinstance(item, dict) and item.get("state") == "NOT_READY")
    )


def _live_ready(
    runtime: PlatformAuthorityRuntime | None, instrument_id: str
) -> bool:
    """Map only explicit per-instrument Platform readiness to dashboard health."""

    if runtime is None:
        return False
    status = runtime.status()
    instruments = status.get("live_instruments")
    item = instruments.get(instrument_id) if isinstance(instruments, dict) else None
    return bool(
        status.get("overall_live_readiness") == "LIVE_READY"
        and isinstance(item, dict)
        and item.get("state") == "READY"
        and item.get("partial_state") == "READY"
    )


def create_app(
    settings: Settings | None = None,
    historical_replay_service: HistoricalReplayService | None = None,
) -> FastAPI:
    configured = settings or Settings.from_environment()
    configured.validate()
    if configured.is_production and not configured.database_path.is_file():
        raise FileNotFoundError(
            "Configured production database does not exist: "
            f"{configured.database_path}. Copy and validate the database, or run "
            "the explicit deployment initialization command before starting FastAPI."
        )
    repository = SQLiteProjectionRepository(configured.database_path)
    credit_budget = DailyCreditBudgetGuard(
        repository,
        daily_limit=configured.twelve_data_daily_credit_limit,
        operational_budget=configured.market_data_daily_operational_budget,
        reserve=configured.market_data_credit_reserve,
    )
    platform_selected = configured.market_data_source == "MARKET_DATA_PLATFORM"
    configured_instruments = twelve_data_instruments(configured.enabled_instrument_ids)
    if platform_selected:
        clock = SystemClock()
        discovered_instruments = tuple(
            replace(item, provider_id="MARKET_DATA_PLATFORM", synthetic=False)
            for item in configured_instruments
            if item.enabled
        )
        provider = None
    elif configured.market_data_provider == "twelve_data":
        assert configured.twelve_data_api_key is not None
        provider = MultiInstrumentTwelveDataProvider(
            configured.twelve_data_api_key,
            configured_instruments,
            max_requests_per_minute=(configured.market_data_max_requests_per_minute),
            min_interval_seconds=(configured.market_data_request_min_interval_seconds),
            max_retries_per_instrument=(
                configured.market_data_max_retries_per_instrument
            ),
            stale_after_seconds=configured.market_data_stale_after_seconds,
            credit_budget=credit_budget,
            repository=repository,
        )
        clock = SystemClock()
    else:
        provider = ReplayMarketDataProvider(
            configured.repository_root / "golden",
            configured.selected_cases,
        )
        clock = FixedClock(provider.initial_clock_time())
    if not platform_selected:
        assert provider is not None
        discovered_instruments = (
            provider.discover_instruments()
            if provider.identity.synthetic or configured.provider_discovery_enabled
            else configured_instruments
        )
    registry = CanonicalInstrumentRegistry(discovered_instruments)
    evaluator = Spect8StrategyEvaluator()
    service = WalkingSkeletonService(evaluator, None, repository)
    signal_lifecycle = SignalLifecycleService(repository)
    platform_authority_runtime = None
    if platform_selected:
        if configured.is_multi_db_platform:
            platform_authority_runtime = UnifiedPlatformAuthorityRuntime.from_settings(
                configured,
                repository,
                service,
                tuple(item.instrument_id for item in discovered_instruments),
                signal_lifecycle=signal_lifecycle,
            )
        else:
            platform_authority_runtime = PlatformAuthorityRuntime.from_database_url(
                configured.market_data_platform_database_url or "",
                repository,
                service,
                tuple(item.instrument_id for item in discovered_instruments),
                stale_after_seconds=configured.market_data_stale_after_seconds,
                poll_seconds=configured.market_data_poll_seconds,
                signal_lifecycle=signal_lifecycle,
            )
    if platform_authority_runtime is not None:
        provider = platform_authority_runtime
    platform_shadow_repository = (
        SQLiteProjectionRepository(
            configured.database_path.with_name(
                f"{configured.database_path.stem}.platform-shadow"
                f"{configured.database_path.suffix}"
            )
        )
        if configured.market_data_platform_shadow_enabled
        else None
    )
    platform_shadow_runtime = (
        PlatformShadowRuntime.from_database_url(
            configured.market_data_platform_database_url or "",
            platform_shadow_repository,
            WalkingSkeletonService(evaluator, None, platform_shadow_repository),
        )
        if platform_shadow_repository is not None
        else None
    )
    coordinator = (
        None
        if platform_selected
        else MarketDataCoordinator(
            provider=provider,
            registry=registry,
            normalizer=CandleNormalizer(),
            detector=ClosedBarDetector(),
            service=service,
            repository=repository,
            clock=clock,
            cross_source_continuity=True,
        )
    )
    runtime_logger = (
        configure_runtime_logging(
            configured.runtime_log_path
            or configured.repository_root / "var" / "spect8_runtime.log",
            max_bytes=configured.runtime_log_max_bytes,
            backup_count=configured.runtime_log_backup_count,
        )
        if (not provider.identity.synthetic and configured.effective_polling_enabled)
        else None
    )
    runtime = (
        platform_authority_runtime
        if platform_authority_runtime is not None
        else MarketDataRuntime(
            coordinator,
            repository,
            clock,
            poll_seconds=configured.market_data_poll_seconds,
            safety_delay_seconds=(
                configured.market_scan_after_hour_seconds
                if configured.market_scan_enabled
                else configured.market_data_safety_delay_seconds
            ),
            startup_backfill_enabled=configured.startup_backfill_enabled,
            logger=runtime_logger,
        )
    )
    replay_database_path = (
        configured.historical_replay_database_path
        or configured.repository_root / "var" / "spect8_historical_replay.sqlite3"
    )
    replay_repository = HistoricalReplayRepository(
        replay_database_path, configured.database_path
    )
    replay_service = historical_replay_service or HistoricalReplayService(
        replay_repository,
        (
            TwelveDataHistoricalSource(
                TwelveDataProvider(configured.twelve_data_api_key or "")
            )
            if configured.market_data_source == "TWELVE_DATA"
            and configured.market_data_provider == "twelve_data"
            else None
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        replay_service.repository.initialize()
        if platform_shadow_repository is not None:
            platform_shadow_repository.initialize()
        if platform_shadow_runtime is not None:
            app.state.platform_shadow_result = platform_shadow_runtime.run_once(
                available_as_of=SystemClock().now()
            )
        runtime_task: asyncio.Task[None] | None = None
        if platform_authority_runtime is not None:
            try:
                app.state.platform_authority_result = (
                    platform_authority_runtime.run_once(
                        available_as_of=SystemClock().now()
                    )
                )
            except PlatformAuthorityError as error:
                # Fail-closed startup: stale/unavailable data must not kill the
                # process. The runtime loop retries every poll and /health,
                # /scanner and signal safety all report truthful STALE/UNAVAILABLE
                # states; no evaluations occur while unhealthy.
                logging.getLogger(__name__).warning(
                    "platform_authority_startup_deferred error_type=%s detail=%s",
                    type(error).__name__,
                    str(error)[:300],
                )
            if configured.effective_polling_enabled:
                runtime_task = asyncio.create_task(
                    runtime.run(), name="spect8-platform-authority-runtime"
                )
        elif configured.auto_seed_synthetic and provider.identity.synthetic:
            runtime.run_once()
        elif not provider.identity.synthetic and configured.effective_polling_enabled:
            runtime_task = asyncio.create_task(
                runtime.run(), name="spect8-market-data-runtime"
            )
        try:
            yield
        finally:
            if runtime_task is not None:
                runtime.stop()
                await runtime_task
            if platform_shadow_runtime is not None:
                platform_shadow_runtime.close()
            if platform_authority_runtime is not None:
                platform_authority_runtime.close()

    app = FastAPI(
        title="Spect8 HedgeFund Market Scanner",
        version="0.7.0",
        description=(
            SYNTHETIC_NOTICE
            if provider.identity.synthetic
            else (
                "READ-ONLY Market Data Platform authoritative market scanner."
                if platform_selected
                else "READ-ONLY Twelve Data multi-instrument market scanner."
            )
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
    app.state.signal_lifecycle = signal_lifecycle
    app.state.platform_shadow_runtime = platform_shadow_runtime
    app.state.platform_shadow_repository = platform_shadow_repository
    app.state.platform_shadow_result = None
    app.state.platform_authority_runtime = platform_authority_runtime
    app.state.platform_authority_result = None
    app.state.credit_budget = credit_budget
    app.state.historical_replay_service = replay_service

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
                else (
                    "MARKET_DATA_PLATFORM"
                    if platform_selected
                    else "TWELVE_DATA_PROVIDER"
                )
            ),
            "notice": (
                SYNTHETIC_NOTICE
                if provider.identity.synthetic
                else (
                    "READ-ONLY Market Data Platform PostgreSQL authority."
                    if platform_selected
                    else "READ-ONLY Twelve Data multi-instrument market data."
                )
            ),
            "data": data,
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        if platform_authority_runtime is not None:
            authority_status = platform_authority_runtime.status()
            provider_health = {
                "provider": "MARKET_DATA_PLATFORM",
                "state": authority_status["freshness_state"],
                "checked_at": primitive(clock.now()),
                "latest_completed_close": authority_status[
                    "last_processed_canonical_timestamp"
                ],
                "freshness_seconds": None,
                "detail": authority_status["last_error"] or "Platform gate passed.",
                "synthetic": False,
            }
        else:
            assert coordinator is not None
            provider_health = coordinator.current_health()
        if (
            not platform_selected
            and not provider.identity.synthetic
            and not configured.effective_polling_enabled
        ):
            provider_health = {
                "provider": provider.identity.provider_id,
                "state": "POLLING_DISABLED",
                "previous_state": (
                    provider_health.get("state") if provider_health else None
                ),
                "checked_at": primitive(clock.now()),
                "latest_completed_close": (
                    provider_health.get("latest_completed_close")
                    if provider_health
                    else None
                ),
                "freshness_seconds": (
                    provider_health.get("freshness_seconds")
                    if provider_health
                    else None
                ),
                "detail": "Polling is intentionally disabled by configuration.",
                "synthetic": False,
            }
        elif provider_health is None:
            current = primitive(provider.health(clock.now()))
            provider_health = {
                "provider": current["provider_id"],
                "state": current["state"],
                "previous_state": None,
                "checked_at": current["checked_at"],
                "latest_completed_close": current["latest_completed_close"],
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
                    else (
                        "PHASE_22A4_PLATFORM_AUTHORITY"
                        if platform_selected
                        else "PHASE_3B_TWELVE_DATA_RUNTIME"
                    )
                ),
                "market_data": (
                    "REPLAY_ONLY"
                    if provider.identity.synthetic
                    else (
                        "MARKET_DATA_PLATFORM"
                        if platform_selected
                        else "TWELVE_DATA_MARKET_SCANNER"
                    )
                ),
                "database": "sqlite",
                "active_filter_mode": repository.active_filter_mode().value,
                "provider": primitive(provider.identity),
                "authority": {
                    "configured_source": configured.market_data_source,
                    "active_source": (
                        platform_authority_runtime.status()["active_source"]
                        if platform_authority_runtime is not None
                        else provider.identity.provider_id
                    ),
                    "platform": (
                        platform_authority_runtime.status()
                        if platform_authority_runtime is not None
                        else None
                    ),
                },
                "provider_health": provider_health,
                "credit_budget": primitive(credit_budget.status(as_of=clock.now())),
                "operations": {
                    "polling_enabled": configured.effective_polling_enabled,
                    "polling_state": (
                        "RUNNING"
                        if runtime.status()["running"]
                        else (
                            "DISABLED_BY_CONFIGURATION"
                            if not configured.effective_polling_enabled
                            else "STARTING"
                        )
                    ),
                    "startup_backfill_enabled": (configured.startup_backfill_enabled),
                    "provider_discovery_enabled": (
                        configured.provider_discovery_enabled
                    ),
                },
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
                    "display_symbol": (
                        instrument.display_symbol or instrument.provider_symbol
                    ),
                    "display_name": instrument.display_name,
                    "asset_class": instrument.asset_class,
                    "instrument_kind": instrument.instrument_kind.value,
                    "exposure_category": instrument.exposure_category.value,
                    "underlying_description": instrument.underlying_description,
                    "is_proxy": instrument.is_proxy,
                    "proxy_for": instrument.proxy_for,
                    "provider_exchange": instrument.exchange,
                    "session_profile": instrument.session_profile.value,
                    "enabled": instrument.enabled,
                    "quote_currency": instrument.quote_currency,
                    "profit_currency": instrument.profit_currency,
                    "timeframes": [
                        timeframe.value for timeframe in instrument.available_timeframes
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

    @app.get("/signals/current", dependencies=[protected])
    def signals_current(request: Request) -> dict[str, Any]:
        """Current signals view.

        FORMING is only ever produced from real authoritative partial-bar data
        delivered by the Market Data Platform live pipeline while Platform
        freshness is genuinely HEALTHY. There is deliberately no synthetic or
        wall-clock fallback: if the live pipeline is not advancing, forming is
        empty. Confirmed signals remain governed purely by persisted
        visible_until.
        """
        now = request.app.state.clock.now()
        current = request.app.state.signal_lifecycle.current_confirmed(now)
        platform_healthy = False
        if request.app.state.platform_authority_runtime is not None:
            status = request.app.state.platform_authority_runtime.status()
            live_readiness = status.get("overall_live_readiness")
            platform_healthy = (
                (
                    live_readiness == "LIVE_READY"
                    and status.get("freshness_state") == "HEALTHY"
                    and status.get("connection_state") == "HEALTHY"
                    and status.get("historical_state", "READY") == "READY"
                )
                if live_readiness is not None
                else (
                    status.get("freshness_state") == "HEALTHY"
                    and status.get("connection_state") == "HEALTHY"
                )
            )
        forming_reader = getattr(
            request.app.state.platform_authority_runtime, "forming_signals", None
        )
        forming = (
            [primitive(item) for item in forming_reader(as_of=now)]
            if platform_healthy and callable(forming_reader)
            else []
        )
        return envelope(
            {
                "confirmed": current,
                "forming": forming,
                "as_of": primitive(now),
                "platform_healthy": platform_healthy,
            }
        )

    @app.get("/signals/confirmed", dependencies=[protected])
    def signals_confirmed(request: Request) -> dict[str, Any]:
        now = request.app.state.clock.now()
        return envelope(
            {
                "current": request.app.state.signal_lifecycle.current_confirmed(now),
                "all": request.app.state.signal_lifecycle.all_confirmed(),
                "as_of": primitive(now),
            }
        )

    @app.get("/signals/history", dependencies=[protected])
    def signals_history(
        request: Request,
        date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        # Deterministic timezone: America/New_York (session authority), reuse existing convention
        now = request.app.state.clock.now()
        tz_name = "America/New_York"
        if date is None:
            # Default to current calendar day in that timezone
            import zoneinfo

            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc
            date = now.astimezone(tz).date().isoformat()
        try:
            rows = request.app.state.repository.confirmed_signals_for_date(date, tz_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return envelope(
            {
                "date": date,
                "timezone": tz_name,
                "confirmed": rows,
                "as_of": primitive(now),
            }
        )

    @app.get("/events", dependencies=[protected])
    def events(request: Request) -> dict[str, Any]:
        return envelope(request.app.state.repository.events())

    @app.get("/runtime/status", dependencies=[protected])
    def runtime_status() -> dict[str, Any]:
        instrument = registry.all()[0]
        return envelope(
            {
                "runtime": runtime.status(),
                "configuration": {
                    "authoritative_source": configured.market_data_source,
                    "polling_enabled": configured.effective_polling_enabled,
                    "startup_backfill_enabled": (configured.startup_backfill_enabled),
                    "provider_discovery_enabled": (
                        configured.provider_discovery_enabled
                    ),
                },
                "observation": repository.observation_report(
                    instrument.provider_id, instrument.instrument_id
                ),
            }
        )

    @app.get("/filter-mode", dependencies=[protected], response_model=FilterModeView)
    def filter_mode() -> FilterModeView:
        mode = repository.active_filter_mode()
        return FilterModeView(
            active_filter_mode=mode,
            filter_timeframe="W1" if mode is FilterMode.MACRO else "D1",
        )

    @app.patch("/filter-mode", dependencies=[protected], response_model=FilterModeView)
    def select_filter_mode(selection: FilterModeRequest) -> FilterModeView:
        mode = repository.set_active_filter_mode(selection.mode, updated_at=clock.now())
        return FilterModeView(
            active_filter_mode=mode,
            filter_timeframe="W1" if mode is FilterMode.MACRO else "D1",
        )

    @app.get(
        "/dashboard",
        dependencies=[protected],
        response_model=DashboardEnvelope,
    )
    def dashboard() -> dict[str, Any]:
        snapshot = dashboard_snapshot(repository, registry.all()[0], clock.now())
        if _live_not_ready(platform_authority_runtime):
            snapshot = snapshot.model_copy(update={"data_state": "STALE", "stale": True})
        return envelope(snapshot)

    @app.get(
        "/dashboard/{instrument_id}",
        dependencies=[protected],
        response_model=DashboardEnvelope,
    )
    def instrument_dashboard(instrument_id: str) -> dict[str, Any]:
        try:
            instrument = registry.by_id(instrument_id)
        except KeyError:
            raise HTTPException(
                status_code=404, detail="Instrument not found."
            ) from None
        snapshot = dashboard_snapshot(repository, instrument, clock.now())
        if _live_not_ready(platform_authority_runtime, instrument_id):
            snapshot = snapshot.model_copy(update={"data_state": "STALE", "stale": True})
        return envelope(snapshot)

    @app.get(
        "/scanner",
        dependencies=[protected],
        response_model=ScannerEnvelope,
    )
    def scanner() -> dict[str, Any]:
        snapshot = scanner_snapshot(
            repository,
            registry.all(),
            clock.now(),
            credit_budget=primitive(credit_budget.status(as_of=clock.now())),
            stale_after_seconds=configured.market_data_stale_after_seconds,
        )
        if platform_authority_runtime is not None:
            rows = []
            for row in snapshot.instruments:
                if _live_not_ready(platform_authority_runtime, row.instrument_id):
                    row = row.model_copy(
                        update={
                            "provider_health": "STALE",
                            "data_status": "STALE",
                            "stale": True,
                            "latest_error_summary": (
                                "Historical strategy state is ready, but canonical "
                                "streaming or partial-data evidence is not fully ready."
                            ),
                        }
                    )
                elif _live_ready(platform_authority_runtime, row.instrument_id):
                    row = row.model_copy(
                        update={
                            "provider_health": "HEALTHY",
                            "data_status": "HEALTHY",
                            "stale": False,
                            "latest_error_summary": None,
                        }
                    )
                rows.append(row)
            snapshot = snapshot.model_copy(update={"instruments": rows})
        return envelope(snapshot)

    def historical_envelope(data: Any) -> dict[str, Any]:
        return {
            "synthetic": False,
            "source": "TWELVE_DATA_HISTORICAL_REPLAY",
            "notice": "REPLAY - NOT LIVE. Functional validation only.",
            "data": data,
        }

    def replay_not_found() -> HTTPException:
        return HTTPException(status_code=404, detail="Replay record not found.")

    @app.post(
        "/historical-replays",
        dependencies=[protected],
        status_code=202,
        response_model=HistoricalReplayEnvelope[ReplayRunView],
    )
    def create_historical_replay(
        payload: ReplayCreateRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        try:
            config = payload.to_config()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        if (
            replay_service.source is None
            and config.requested_dataset_fingerprint is None
        ):
            raise HTTPException(
                status_code=409,
                detail="Historical provider source is unavailable.",
            )
        try:
            run = replay_service.create_run(config)
        except ReplayConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        background_tasks.add_task(replay_service.execute, run["run_id"])
        return historical_envelope(run)

    @app.get(
        "/historical-replays",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplayRunsView],
    )
    def list_historical_replays(
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return historical_envelope(
            {"items": replay_service.repository.list_runs(limit)}
        )

    @app.get(
        "/historical-replays/{run_id}",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplayRunView],
    )
    def historical_replay_status(run_id: str) -> dict[str, Any]:
        try:
            value = replay_service.repository.get_run(run_id)
        except ReplayNotFoundError:
            raise replay_not_found() from None
        return historical_envelope(value)

    @app.get(
        "/historical-replays/{run_id}/summary",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplaySummaryView],
    )
    def historical_replay_summary(run_id: str) -> dict[str, Any]:
        try:
            value = replay_service.repository.summary(run_id)
        except ReplayNotFoundError:
            raise replay_not_found() from None
        return historical_envelope(value)

    @app.get(
        "/historical-replays/{run_id}/evaluations",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplayEvaluationPage],
    )
    def historical_replay_evaluations(
        run_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        timeframe: str | None = Query(default=None, pattern="^(H1|H4)$"),
        outcome: str | None = Query(default=None, pattern="^(SIGNAL|NO_SIGNAL)$"),
        filter_outcome: str | None = Query(default=None, pattern="^(PASS|FAIL)$"),
        reason_code: str
        | None = Query(
            default=None, min_length=1, max_length=80, pattern="^[A-Z0-9_]+$"
        ),
    ) -> dict[str, Any]:
        try:
            value = replay_service.repository.evaluations(
                run_id,
                page=page,
                page_size=page_size,
                timeframe=timeframe,
                outcome=outcome,
                filter_outcome=filter_outcome,
                reason_code=reason_code,
            )
        except ReplayNotFoundError:
            raise replay_not_found() from None
        return historical_envelope(value)

    @app.get(
        "/historical-replays/{run_id}/evaluations/{evaluation_id}",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplayEvaluationDetail],
    )
    def historical_replay_evaluation(run_id: str, evaluation_id: int) -> dict[str, Any]:
        try:
            value = replay_service.repository.evaluation(run_id, evaluation_id)
        except ReplayNotFoundError:
            raise replay_not_found() from None
        return historical_envelope(value)

    @app.delete(
        "/historical-replays/{run_id}",
        dependencies=[protected],
        response_model=HistoricalReplayEnvelope[ReplayDeleteView],
    )
    def delete_historical_replay(run_id: str) -> dict[str, Any]:
        try:
            deleted = replay_service.repository.delete_run(run_id)
        except ReplayConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        if not deleted:
            raise replay_not_found()
        return historical_envelope({"deleted": True, "run_id": run_id})

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


try:
    app = create_app()
except Exception:  # pragma: no cover - lazy import for tests without platform DB
    app = None  # type: ignore
    import logging as _logging

    _logging.getLogger(__name__).debug("app not created at import time (test/config)")

