import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.market_data.models import ProviderIdentity


def test_http_starts_while_platform_history_is_still_preparing(tmp_path, monkeypatch):
    calls = []
    class Preparing:
        identity = ProviderIdentity('MARKET_DATA_PLATFORM', 'Platform', 'test', False)
        def run_once(self, **kwargs):
            raise AssertionError('HTTP startup must not synchronously wait for history')
        async def run(self):
            calls.append('background-started')
            self.done = asyncio.Event()
            await self.done.wait()
        def stop(self):
            calls.append('stopped')
            self.done.set()
        def close(self):
            calls.append('closed')
        def status(self):
            return dict(active_source='MARKET_DATA_PLATFORM', connection_state='CONNECTING',
                freshness_state='UNAVAILABLE', historical_state='BOOTSTRAPPING',
                last_processed_canonical_timestamp=None, last_error=None, running=True)
    fake = Preparing()
    monkeypatch.setattr('backend.app.main.PlatformAuthorityRuntime.from_database_url', lambda *a, **k: fake)
    app = create_app(Settings(repository_root=Path(__file__).resolve().parents[2],
        database_path=tmp_path/'app.sqlite3', historical_replay_database_path=tmp_path/'replay.sqlite3',
        internal_api_key='test', auto_seed_synthetic=False, market_data_source='MARKET_DATA_PLATFORM',
        enabled_instrument_ids=('EUR_USD',), market_data_platform_database_url='postgresql+psycopg://fixture@host/db',
        polling_enabled=True))
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert 'background-started' in calls
        assert app.state.platform_authority_result is None
    assert calls == ['background-started', 'stopped', 'closed']
