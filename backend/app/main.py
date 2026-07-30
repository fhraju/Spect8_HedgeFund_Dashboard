from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from .config import Settings
from .golden_adapter import FrozenExpectedResultAdapter
from .repository import SQLiteProjectionRepository
from .service import WalkingSkeletonService

SYNTHETIC_NOTICE = (
    "SYNTHETIC GOLDEN FIXTURE DATA — no live market-data provider is connected."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_environment()
    repository = SQLiteProjectionRepository(configured.database_path)
    adapter = FrozenExpectedResultAdapter(configured.repository_root)
    service = WalkingSkeletonService(adapter, repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        if configured.auto_seed_synthetic:
            service.process_cases(configured.selected_cases)
        yield

    app = FastAPI(
        title="Spect8 HedgeFund Dashboard — Phase 1",
        version="0.1.0",
        description=SYNTHETIC_NOTICE,
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.repository = repository
    app.state.service = service

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
            "synthetic": True,
            "source": "COMMITTED_GOLDEN_EXPECTED_RESULT_ADAPTER",
            "notice": SYNTHETIC_NOTICE,
            "data": data,
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return envelope(
            {
                "status": "ok",
                "mode": "PHASE_1_WALKING_SKELETON",
                "market_data": "SYNTHETIC_ONLY",
                "database": "sqlite",
            }
        )

    @app.get("/instruments", dependencies=[protected])
    def instruments(request: Request) -> dict[str, Any]:
        statuses = request.app.state.repository.statuses()
        unique: dict[str, dict[str, Any]] = {}
        for status in statuses:
            unique[status["instrument_id"]] = {
                "instrument_id": status["instrument_id"],
                "provider": status["provider"],
                "timeframes": sorted(
                    {
                        existing["timeframe"]
                        for existing in statuses
                        if existing["instrument_id"] == status["instrument_id"]
                    }
                ),
                "synthetic": True,
            }
        return envelope(list(unique.values()))

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

    @app.post("/synthetic/replay", dependencies=[protected])
    def replay(request: Request) -> dict[str, Any]:
        outcomes = request.app.state.service.process_cases(
            request.app.state.settings.selected_cases
        )
        return envelope(
            [
                {
                    "source_case_id": outcome.source_case_id,
                    "idempotency_key": outcome.idempotency_key,
                    "replayed": outcome.replayed,
                    "events_created": outcome.events_created,
                }
                for outcome in outcomes
            ]
        )

    return app


app = create_app()
