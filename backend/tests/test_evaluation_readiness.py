from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.market_data.evaluation_readiness import candidate_evaluation_status, forex_candidate_ready


def test_forex_m30_gap_does_not_hide_current_macro():
    now = datetime(2026, 9, 11, 4, tzinfo=UTC)
    bar = SimpleNamespace(close_time=now)
    history = SimpleNamespace(m30=(bar,)*40,h1=(bar,)*128,h4=(bar,)*30,d1=(bar,)*10,w1=(bar,)*10)
    completed = {(mode,tf):now.isoformat() for mode,tf in [('MICRO','H1'),('MACRO','H1'),('MACRO','H4')]}
    pending = [{'mode':'MICRO','timeframe':'M30','close_time':now.isoformat(),'reason':'MISSING_SIGNAL_CANDLE'}]
    result = candidate_evaluation_status(history,completed,pending,fresh=True,readiness=forex_candidate_ready)
    assert result['evaluation_modes']['MICRO']['evaluation_freshness']=='STALE'
    assert result['evaluation_modes']['MACRO']['evaluation_freshness']=='CURRENT'
    assert [c['state'] for c in result['evaluation_candidates']]==['STALE','CURRENT','CURRENT','CURRENT']
    completed['MICRO','M30']=now.isoformat()
    repaired = candidate_evaluation_status(history,completed,[],fresh=True,readiness=forex_candidate_ready)
    assert all(c['state']=='CURRENT' for c in repaired['evaluation_candidates'])
    stale = candidate_evaluation_status(history,completed,[],fresh=False,readiness=forex_candidate_ready)
    assert all(c['state']=='STALE' for c in stale['evaluation_candidates'])
