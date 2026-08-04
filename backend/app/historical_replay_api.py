from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from .domain import Timeframe
from .historical_replay import ReplayConfig

T = TypeVar("T")


class HistoricalReplayEnvelope(BaseModel, Generic[T]):
    synthetic: Literal[False] = False
    source: Literal["TWELVE_DATA_HISTORICAL_REPLAY"] = (
        "TWELVE_DATA_HISTORICAL_REPLAY"
    )
    notice: str = "REPLAY - NOT LIVE. Functional validation only."
    data: T


class ReplayCreateRequest(BaseModel):
    display_start: datetime
    display_end: datetime
    instrument: Literal["EUR/USD"] = "EUR/USD"
    provider: Literal["TWELVE_DATA"] = "TWELVE_DATA"
    timeframes: tuple[Literal["H1"], Literal["H4"]] = ("H1", "H4")
    context_timeframe: Literal["D1"] = "D1"
    dataset_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64
    )

    def to_config(self) -> ReplayConfig:
        return ReplayConfig(
            display_start=self.display_start,
            display_end=self.display_end,
            instrument=self.instrument,
            provider=self.provider,
            timeframes=tuple(Timeframe(value) for value in self.timeframes),
            context_timeframe=Timeframe(self.context_timeframe),
            requested_dataset_fingerprint=self.dataset_fingerprint,
        )


class ReplayProgressView(BaseModel):
    total: int
    completed: int
    percent: float


class ReplayErrorView(BaseModel):
    code: str
    detail: str


class ReplayRunView(BaseModel):
    run_id: str
    dataset_fingerprint: str | None
    requested_dataset_fingerprint: str | None
    provider: str
    instrument: str
    display_start: datetime
    display_end: datetime
    timeframes: list[str]
    context_timeframe: str
    strategy_version: str
    status: Literal[
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "QUARANTINED",
    ]
    progress: ReplayProgressView
    duplicate_evaluations: int
    quarantined_windows: int
    determinism_digest: str | None
    error: ReplayErrorView | None
    orders: Literal[0]
    fills: Literal[0]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ReplayRunsView(BaseModel):
    items: list[ReplayRunView]


class ReplayDatasetView(BaseModel):
    fingerprint: str
    warmup_start: datetime
    requested_ranges: dict[str, dict[str, str]]
    returned_ranges: dict[str, dict[str, str | None]]
    candle_counts: dict[str, dict[str, int]]


class ReplayExecutionView(BaseModel):
    enabled: Literal[False]
    orders: Literal[0]
    fills: Literal[0]
    detail: str


class ReplaySummaryView(BaseModel):
    run: ReplayRunView
    dataset: ReplayDatasetView | None
    evaluation_counts: dict[str, int]
    reason_counts: dict[str, int]
    event_count: int
    data_quality: list[dict[str, Any]]
    execution: ReplayExecutionView


class ReplayEvaluationItem(BaseModel):
    id: int
    ordinal: int
    signal_close_utc: datetime
    replay_as_of_utc: datetime
    timeframe: Literal["H1", "H4"]
    filter_outcome: Literal["PASS", "FAIL"]
    signal_outcome: Literal["SIGNAL", "NO_SIGNAL"]
    dashboard_state: str
    d1_context_close_utc: datetime
    reason_codes: list[str]
    market_values: dict[str, Any]


class ReplayEvaluationPage(BaseModel):
    items: list[ReplayEvaluationItem]
    page: int
    page_size: int
    total: int
    pages: int


class ReplayEvaluationDetail(BaseModel):
    id: int
    ordinal: int
    signal_close_utc: datetime
    replay_as_of_utc: datetime
    timeframe: Literal["H1", "H4"]
    filter_outcome: Literal["PASS", "FAIL"]
    signal_outcome: Literal["SIGNAL", "NO_SIGNAL"]
    dashboard_state: str
    d1_context_close_utc: datetime
    reason_codes: list[str]
    market_values: dict[str, Any]
    status: dict[str, Any]
    evaluation: dict[str, Any]
    input: dict[str, Any]
    events: list[dict[str, Any]]


class ReplayDeleteView(BaseModel):
    deleted: Literal[True]
    run_id: str
