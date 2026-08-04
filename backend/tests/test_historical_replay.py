from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain import Timeframe
from backend.app.engine.models import StrategyRequest
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.historical_replay import (
    HistoricalReplayRepository,
    HistoricalReplayService,
    ReplayConfig,
    ReplayConflictError,
    TwelveDataHistoricalSource,
    build_historical_dataset,
)
from backend.app.main import create_app
from backend.app.market_data.models import RawProviderCandle
from backend.app.market_data.twelve_data_provider import TwelveDataProvider
from backend.app.repository import SQLiteProjectionRepository

UTC = timezone.utc
DISPLAY_START = datetime(2026, 7, 1, tzinfo=UTC)
DISPLAY_END = datetime(2026, 7, 2, tzinfo=UTC)
API_KEY = "test-internal-key"


def _raw_candles(
    timeframe: Timeframe,
    first_open: datetime,
    end_open: datetime,
) -> tuple[RawProviderCandle, ...]:
    step = {
        Timeframe.H1: timedelta(hours=1),
        Timeframe.H4: timedelta(hours=4),
        Timeframe.D1: timedelta(days=1),
    }[timeframe]
    values: list[RawProviderCandle] = []
    current = first_open
    index = 0
    while current < end_open:
        middle = 1.08 + index * 0.00001
        values.append(
            RawProviderCandle(
                provider_id="TWELVE_DATA",
                provider_symbol="EUR/USD",
                timeframe=timeframe,
                raw_open_time=current.isoformat().replace("+00:00", "Z"),
                raw_close_time=(current + step)
                .isoformat()
                .replace("+00:00", "Z"),
                open=f"{middle:.5f}",
                high=f"{middle + 0.00100:.5f}",
                low=f"{middle - 0.00100:.5f}",
                close=f"{middle + 0.00010:.5f}",
                volume=None,
                is_complete=True,
                session_timezone="UTC",
            )
        )
        current += step
        index += 1
    return tuple(values)


def _raw_history() -> dict[Timeframe, tuple[RawProviderCandle, ...]]:
    return {
        Timeframe.H1: _raw_candles(
            Timeframe.H1,
            datetime(2026, 6, 20, 23, tzinfo=UTC),
            DISPLAY_END,
        ),
        Timeframe.H4: _raw_candles(
            Timeframe.H4,
            datetime(2026, 6, 20, 21, tzinfo=UTC),
            DISPLAY_END,
        ),
        Timeframe.D1: _raw_candles(
            Timeframe.D1,
            datetime(2026, 6, 20, tzinfo=UTC),
            DISPLAY_END,
        ),
    }


def _golden_raw(case_id: str, timeframe: Timeframe) -> tuple[RawProviderCandle, ...]:
    filename = "daily_bars.csv" if timeframe is Timeframe.D1 else "signal_bars.csv"
    path = (
        Path(__file__).resolve().parents[2]
        / "golden"
        / "cases"
        / case_id
        / filename
    )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    return tuple(
        RawProviderCandle(
            provider_id="TWELVE_DATA",
            provider_symbol="EUR/USD",
            timeframe=timeframe,
            raw_open_time=row["open_time"],
            raw_close_time=row["close_time"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            is_complete=row["is_complete"].lower() == "true",
            session_timezone="UTC",
        )
        for row in rows
    )


def _equal_boundary_history() -> dict[Timeframe, tuple[RawProviderCandle, ...]]:
    return {
        Timeframe.H1: _golden_raw("equal_close_filter_boundary_h1", Timeframe.H1),
        Timeframe.H4: _golden_raw("equal_close_filter_boundary_h4", Timeframe.H4),
        Timeframe.D1: _golden_raw("equal_close_filter_boundary_h1", Timeframe.D1),
    }


def _new_york_equal_boundary_history() -> dict[
    Timeframe, tuple[RawProviderCandle, ...]
]:
    boundary = datetime(2026, 7, 15, 21, tzinfo=UTC)
    return {
        Timeframe.H1: _raw_candles(
            Timeframe.H1,
            boundary - timedelta(days=15),
            boundary,
        ),
        Timeframe.H4: _raw_candles(
            Timeframe.H4,
            boundary - timedelta(hours=35 * 4),
            boundary,
        ),
        Timeframe.D1: (),
    }


def _instrument():
    return TwelveDataProvider("fixture-provider-key").discover_instruments()[0]


class FixtureSource:
    def __init__(
        self,
        raw: dict[Timeframe, tuple[RawProviderCandle, ...]] | None = None,
    ) -> None:
        self.raw = raw or _raw_history()
        self.calls = 0

    def load(self, config: ReplayConfig):
        self.calls += 1
        return build_historical_dataset(config, _instrument(), self.raw)


def _service(tmp_path: Path, source: FixtureSource | None = None):
    live_path = tmp_path / "live.sqlite3"
    replay_path = tmp_path / "historical.sqlite3"
    SQLiteProjectionRepository(live_path).initialize()
    repository = HistoricalReplayRepository(replay_path, live_path)
    repository.initialize()
    return HistoricalReplayService(repository, source or FixtureSource())


def _config(
    *,
    start: datetime = DISPLAY_START,
    end: datetime = DISPLAY_END,
    fingerprint: str | None = None,
) -> ReplayConfig:
    return ReplayConfig(
        display_start=start,
        display_end=end,
        requested_dataset_fingerprint=fingerprint,
    )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _execute(service: HistoricalReplayService, config: ReplayConfig):
    run = service.create_run(config)
    service.execute(run["run_id"])
    return service.repository.get_run(run["run_id"])


def test_date_bounded_provider_request_uses_header_auth_and_exact_bounds() -> None:
    class Transport:
        def __init__(self) -> None:
            self.call = None

        def get(self, path, params, headers, **kwargs):
            self.call = (path, params, headers, kwargs)
            from backend.app.market_data.twelve_data_provider import HttpResponse

            return HttpResponse(
                status_code=200,
                headers={},
                body=(
                    b'{"status":"ok","meta":{"symbol":"EUR/USD",'
                    b'"interval":"1h"},"values":[]}'
                ),
            )

    transport = Transport()
    provider = TwelveDataProvider("private-test-key", transport=transport)
    assert provider.fetch_historical_bars(
        Timeframe.H1, DISPLAY_START, DISPLAY_END
    ) == ()
    assert transport.call is not None
    path, params, headers, _ = transport.call
    assert path == "/time_series"
    assert params["start_date"] == "2026-06-30 23:00:00"
    assert params["end_date"] == "2026-07-02 00:00:00"
    assert params["timezone"] == "UTC"
    assert "apikey" not in params
    assert headers == {"Authorization": "apikey private-test-key"}


def test_dataset_records_warmup_ranges_quality_and_stable_fingerprint() -> None:
    config = _config()
    first = build_historical_dataset(config, _instrument(), _raw_history())
    second = build_historical_dataset(config, _instrument(), _raw_history())
    assert first.fingerprint == second.fingerprint
    assert first.requested_ranges["H1"]["start_utc"] == (
        "2026-06-10T00:00:00Z"
    )
    assert first.returned_ranges["H1"]["last_close_utc"] == (
        "2026-07-01T23:00:00Z"
    )
    assert first.candle_counts["H1"]["warmup"] >= 30
    assert first.candle_counts["H4"]["warmup"] >= 30
    assert first.candle_counts["D1"]["warmup"] >= 6
    assert first.candle_counts["H1"]["display"] == 24
    assert first.candle_counts["H4"]["display"] == 6
    assert first.findings == ()


def test_replay_boundaries_merge_and_lookahead_guards(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = _execute(service, _config())
    assert run["status"] == "COMPLETED"
    summary = service.repository.summary(run["run_id"])
    assert summary["evaluation_counts"] == {
        "total": 30,
        "H1": 24,
        "H4": 6,
        "filter_pass": summary["evaluation_counts"]["filter_pass"],
        "filter_fail": summary["evaluation_counts"]["filter_fail"],
        "signal": summary["evaluation_counts"]["signal"],
        "no_signal": summary["evaluation_counts"]["no_signal"],
    }
    assert summary["evaluation_counts"]["filter_pass"] + summary[
        "evaluation_counts"
    ]["filter_fail"] == 30
    assert summary["evaluation_counts"]["signal"] + summary[
        "evaluation_counts"
    ]["no_signal"] == 30
    page = service.repository.evaluations(
        run["run_id"], page=1, page_size=100
    )
    assert page["total"] == 30
    ordered = [(item["signal_close_utc"], item["timeframe"]) for item in page["items"]]
    assert ordered == sorted(ordered, key=lambda item: (item[0], ("H1", "H4").index(item[1])))
    assert page["items"][0]["signal_close_utc"] == "2026-07-01T00:00:00Z"
    assert page["items"][-1]["signal_close_utc"] < "2026-07-02T00:00:00Z"
    overlap = [item for item in page["items"] if item["signal_close_utc"] == "2026-07-01T01:00:00Z"]
    assert [item["timeframe"] for item in overlap] == ["H1", "H4"]
    for item in page["items"]:
        assert _dt(item["signal_close_utc"]) < _dt(item["replay_as_of_utc"])
        assert _dt(item["d1_context_close_utc"]) <= _dt(
            item["signal_close_utc"]
        )
        detail = service.repository.evaluation(run["run_id"], item["id"])
        assert len(detail["input"]["signal_bars"]) == 30
        assert 6 <= len(detail["input"]["daily_bars"]) <= 10
        assert all(
            _dt(bar["close_time"]) < _dt(detail["replay_as_of_utc"])
            for bar in detail["input"]["signal_bars"]
        )
        assert all(
            _dt(bar["close_time"]) <= _dt(detail["signal_close_utc"])
            for bar in detail["input"]["daily_bars"]
        )
    first = page["items"][0]
    assert first["d1_context_close_utc"] == "2026-06-30T21:00:00Z"


def test_historical_replay_matches_equal_close_h1_h4_direct_results(
    tmp_path: Path,
) -> None:
    boundary = datetime(2026, 7, 15, 21, tzinfo=UTC)
    source = FixtureSource(_new_york_equal_boundary_history())
    service = _service(tmp_path, source)
    run = _execute(
        service,
        _config(start=boundary, end=boundary + timedelta(seconds=1)),
    )

    assert run["status"] == "COMPLETED"
    assert run["strategy_version"] == "SPECT8_MICRO_DAILY_V1_0_3"
    page = service.repository.evaluations(run["run_id"], page=1, page_size=10)
    assert page["total"] == 2
    by_timeframe = {item["timeframe"]: item for item in page["items"]}
    assert set(by_timeframe) == {"H1", "H4"}
    dataset = source.load(_config(start=boundary, end=boundary + timedelta(seconds=1)))
    for timeframe in ("H1", "H4"):
        item = by_timeframe[timeframe]
        assert item["d1_context_close_utc"] == item["signal_close_utc"]
        detail = service.repository.evaluation(run["run_id"], item["id"])
        selected_timeframe = Timeframe(timeframe)
        direct = Spect8StrategyEvaluator().evaluate(
            StrategyRequest(
                case_id=f"direct:{timeframe}",
                strategy_id="SPECT8_MICRO_DAILY_V1_0",
                timeframe=selected_timeframe,
                evaluation_time=boundary + timedelta(microseconds=1),
                signal_bars=tuple(
                    bar
                    for bar in dataset.bars[selected_timeframe]
                    if bar.close_time <= boundary
                )[-30:],
                daily_bars=tuple(
                    bar
                    for bar in dataset.bars[Timeframe.D1]
                    if bar.close_time <= boundary
                )[-10:],
                instrument=dataset.instrument.to_strategy_metadata(),
            )
        )
        assert direct.classification is not None
        assert direct.indicators is not None
        for name in (
            "buy_filter_matched",
            "sell_filter_matched",
        ):
            assert detail["evaluation"]["classification"][name] == getattr(
                direct.classification, name
            )
        for name in (
            "atr_d1_wilder_5",
            "activation_buffer",
            "daily_raw_low",
            "daily_raw_high",
            "daily_buy_level",
            "daily_sell_level",
            "recent_low_21",
            "recent_high_21",
        ):
            assert detail["evaluation"]["indicators"][name] == float(
                getattr(direct.indicators, name)
            )


def test_duplicate_malformed_gap_and_quarantine_are_recorded(tmp_path: Path) -> None:
    raw = _raw_history()
    duplicate = raw[Timeframe.H1][10]
    malformed = replace(
        duplicate,
        raw_open_time="2026-06-21T10:30:00Z",
        raw_close_time="2026-06-21T11:30:00Z",
        open="not-a-price",
    )
    h1 = list(raw[Timeframe.H1])
    h1.append(duplicate)
    h1.append(malformed)
    h1 = [
        value
        for value in h1
        if value.raw_close_time != "2026-06-30T20:00:00Z"
    ]
    raw[Timeframe.H1] = tuple(h1)
    source = FixtureSource(raw)
    dataset = source.load(_config())
    codes = {finding.code for finding in dataset.findings}
    assert {"DUPLICATE_CANDLE", "MALFORMED_CANDLE", "MISSING_CANDLE"} <= codes
    service = _service(tmp_path, source)
    run = _execute(service, _config())
    assert run["status"] == "PARTIAL"
    assert run["quarantined_windows"] > 0
    summary = service.repository.summary(run["run_id"])
    assert summary["evaluation_counts"]["total"] < 30
    assert any(item["code"] == "QUARANTINED_WINDOW" for item in summary["data_quality"])


def test_repeat_run_uses_same_dataset_and_is_deterministic(tmp_path: Path) -> None:
    source = FixtureSource()
    service = _service(tmp_path, source)
    first = _execute(service, _config())
    second = _execute(
        service, _config(fingerprint=first["dataset_fingerprint"])
    )
    assert source.calls == 1
    assert first["dataset_fingerprint"] == second["dataset_fingerprint"]
    assert first["determinism_digest"] == second["determinism_digest"]
    first_page = service.repository.evaluations(
        first["run_id"], page=1, page_size=100
    )
    second_page = service.repository.evaluations(
        second["run_id"], page=1, page_size=100
    )
    comparable = lambda page: [
        (
            item["signal_close_utc"],
            item["timeframe"],
            item["filter_outcome"],
            item["signal_outcome"],
            item["reason_codes"],
        )
        for item in page["items"]
    ]
    assert comparable(first_page) == comparable(second_page)
    assert first_page["total"] == second_page["total"] == 30
    assert first["duplicate_evaluations"] == second["duplicate_evaluations"] == 0


def test_active_duplicate_is_rejected_and_terminal_rerun_is_allowed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.create_run(_config())
    with pytest.raises(ReplayConflictError):
        service.create_run(_config())
    service.execute(first["run_id"])
    second = service.create_run(_config())
    assert second["status"] == "PENDING"


def test_replay_run_delete_and_rerun_do_not_touch_live_tables(tmp_path: Path) -> None:
    service = _service(tmp_path)
    live_path = service.repository.live_database_path
    with closing(sqlite3.connect(live_path)) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "canonical_bars",
                "processed_bars",
                "event_history",
                "provider_health",
                "provider_sync",
                "runtime_sessions",
                "runtime_poll_history",
            )
        }
    run = _execute(service, _config())
    assert service.repository.delete_run(run["run_id"])
    rerun = _execute(service, _config())
    assert rerun["status"] == "COMPLETED"
    with closing(sqlite3.connect(live_path)) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before
    assert service.repository.database_path != live_path


def test_empty_running_failed_and_partial_states(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pending = service.create_run(_config())
    assert pending["status"] == "PENDING"
    assert service.repository.start_run(pending["run_id"])
    assert service.repository.get_run(pending["run_id"])["status"] == "RUNNING"
    service.repository.fail_run(pending["run_id"], "TEST_FAILURE", "Sanitized failure.")
    assert service.repository.get_run(pending["run_id"])["status"] == "FAILED"

    empty_source = FixtureSource(
        {timeframe: () for timeframe in (Timeframe.H1, Timeframe.H4, Timeframe.D1)}
    )
    empty = _execute(_service(tmp_path / "empty", empty_source), _config())
    assert empty["status"] == "COMPLETED"
    assert empty["progress"]["total"] == 0


def test_api_auth_schemas_pagination_filters_detail_and_zero_execution(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "api-live.sqlite3"
    replay_path = tmp_path / "api-replay.sqlite3"
    repository = HistoricalReplayRepository(replay_path, live_path)
    replay_service = HistoricalReplayService(repository, FixtureSource())
    settings = Settings(
        repository_root=Path(__file__).resolve().parents[2],
        database_path=live_path,
        historical_replay_database_path=replay_path,
        internal_api_key=API_KEY,
        auto_seed_synthetic=False,
        market_data_runtime_enabled=False,
    )
    app = create_app(settings, replay_service)
    with TestClient(app) as client:
        assert client.get("/historical-replays").status_code == 401
        response = client.post(
            "/historical-replays",
            headers={"X-Spect8-Internal-Key": API_KEY},
            json={
                "display_start": "2026-07-01T00:00:00Z",
                "display_end": "2026-07-02T00:00:00Z",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["data"]["run_id"]
        status = client.get(
            f"/historical-replays/{run_id}",
            headers={"X-Spect8-Internal-Key": API_KEY},
        )
        assert status.status_code == 200
        assert status.json()["data"]["status"] == "COMPLETED"
        summary = client.get(
            f"/historical-replays/{run_id}/summary",
            headers={"X-Spect8-Internal-Key": API_KEY},
        ).json()["data"]
        assert summary["evaluation_counts"]["total"] == 30
        assert summary["execution"] == {
            "enabled": False,
            "orders": 0,
            "fills": 0,
            "detail": "Functional replay only; execution is disabled.",
        }
        page = client.get(
            f"/historical-replays/{run_id}/evaluations",
            headers={"X-Spect8-Internal-Key": API_KEY},
            params={"page": 1, "page_size": 5, "timeframe": "H1", "filter_outcome": "FAIL"},
        )
        assert page.status_code == 200
        body = page.json()["data"]
        assert len(body["items"]) <= 5
        assert all(item["timeframe"] == "H1" for item in body["items"])
        evaluation_id = client.get(
            f"/historical-replays/{run_id}/evaluations",
            headers={"X-Spect8-Internal-Key": API_KEY},
            params={"page_size": 1},
        ).json()["data"]["items"][0]["id"]
        detail = client.get(
            f"/historical-replays/{run_id}/evaluations/{evaluation_id}",
            headers={"X-Spect8-Internal-Key": API_KEY},
        )
        assert detail.status_code == 200
        detail_body = detail.json()["data"]
        assert detail_body["input"]["signal_bars"]
        assert detail_body["events"][0]["event_type"] == "BAR_CLOSED"
        assert client.get(
            "/historical-replays",
            headers={"X-Spect8-Internal-Key": API_KEY},
        ).json()["data"]["items"]


def test_replay_database_cannot_equal_live_database(tmp_path: Path) -> None:
    same = tmp_path / "same.sqlite3"
    with pytest.raises(ValueError, match="different"):
        HistoricalReplayRepository(same, same)
    settings = Settings(
        repository_root=tmp_path,
        database_path=same,
        historical_replay_database_path=same,
        internal_api_key=API_KEY,
    )
    with pytest.raises(ValueError, match="separate"):
        settings.validate()


def test_historical_source_uses_all_required_timeframes() -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = []

        def discover_instruments(self):
            return (_instrument(),)

        def fetch_historical_bars(self, timeframe, start, end):
            self.calls.append((timeframe, start, end))
            return _raw_history()[timeframe]

    provider = Provider()
    dataset = TwelveDataHistoricalSource(provider).load(_config())
    assert [call[0] for call in provider.calls] == [
        Timeframe.H1,
        Timeframe.H4,
    ]
    assert provider.calls[0][1] == DISPLAY_START - timedelta(days=23)
    assert provider.calls[1][1] == DISPLAY_START - timedelta(days=21)
    assert all(call[2] == DISPLAY_END for call in provider.calls)
    assert dataset.fingerprint


def test_live_and_historical_providers_do_not_share_mutable_state(
    tmp_path: Path,
) -> None:
    settings = Settings(
        repository_root=Path(__file__).resolve().parents[2],
        database_path=tmp_path / "live.sqlite3",
        historical_replay_database_path=tmp_path / "replay.sqlite3",
        internal_api_key=API_KEY,
        market_data_provider="twelve_data",
        twelve_data_api_key="separate-provider-test-key",
        auto_seed_synthetic=False,
        market_data_runtime_enabled=False,
    )
    app = create_app(settings)
    source = app.state.historical_replay_service.source
    assert isinstance(source, TwelveDataHistoricalSource)
    assert source._provider is not app.state.provider
    assert source._provider.telemetry().network_attempts == 0
    assert app.state.provider.telemetry().network_attempts == 0
