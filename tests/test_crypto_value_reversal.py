import math
from datetime import datetime,timezone
from dataclasses import replace
import pytest
from app.value_reversal.crypto_data import sessions
from app.value_reversal.crypto_research import Book,Config,metrics,simulate
from app.value_reversal.crypto_indicator import profile

def row(ts,o=100,h=101,l=99,c=100,signal=None,close=86400000):
    return {'bar':[ts,o,h,l,c,100],'session_close':close,'signals':{'previous-5':signal} if signal else {}}

def signal(ts=0,side=1):
    return {'ts':ts,'side':side,'stop':90 if side>0 else 110,'target':110 if side>0 else 90}

def test_next_open_and_stop_first_tie():
    b=Book(Config());b.process(row(0,signal=signal()),[])
    assert b.position is None
    b.process(row(900000,o=100,h=112,l=88),[])
    assert len(b.trades)==1 and b.trades[0]['entry_ts']==900000
    assert b.trades[0]['reason']=='stop' and b.cash<10000
    assert b.signals[0]['execution']=='entered'

@pytest.mark.parametrize('side',[1,-1])
def test_funding_sign_and_wallet_reconciliation(side):
    b=Book(Config());b.process(row(0,signal=signal(side=side)),[])
    b.process(row(900000),[[900001,8,.0001,100]])
    qty=b.position['qty'];expected=side*qty*100*.0001
    assert b.funding_cost==pytest.approx(expected)
    b.exit(100,1800000,'test')
    assert b.cash-10000==pytest.approx(sum(t['net'] for t in b.trades))
    assert b.trades[0]['funding_cost']==pytest.approx(expected)

def test_flat_and_exact_entry_settlement_no_funding_credit():
    b=Book(Config());b.process(row(0),[[1,8,.01,100]])
    assert b.funding_events==0
    b.process(row(900000,signal=signal(900000)),[])
    b.process(row(1800000),[[1800000,8,.01,100]])
    assert b.funding_events==0

def test_opening_gap_exits_before_later_settlement():
    b=Book(Config());b.process(row(0,signal=signal()),[]);b.process(row(900000),[])
    b.process(row(1800000,o=89,h=90,l=88,c=89),[[1800001,8,.01,100]])
    assert b.position is None and b.trades[0]['reason']=='stop_gap'
    assert b.trades[0]['exit_ts']==1800000 and b.funding_cost==0

def test_no_funding_control():
    b=Book(replace(Config(),funding=False));b.process(row(0,signal=signal()),[])
    b.process(row(900000),[[900001,8,.01,100]])
    assert b.funding_cost==0 and b.funding_events==0

def test_gap_outside_target_rejected_but_arrow_retained():
    b=Book(Config());b.process(row(0,signal=signal()),[])
    b.process(row(900000,o=111,h=112,l=110,c=111),[])
    assert b.position is None and len(b.signals)==1
    assert b.signals[0]['execution']=='entry_outside_stop_target'

def test_fractions_and_unlevered_cap():
    b=Book(Config());b.process(row(0,signal={'ts':0,'side':1,'stop':9999,'target':10010}),[])
    b.process(row(900000,o=10000,h=10001,l=9999.5,c=10000),[])
    p=b.position
    assert p and 0<p['qty']<1
    assert p['qty']*p['entry']+p['entry_fee']<=10000
    assert p['qty']/.001==pytest.approx(round(p['qty']/.001))

def test_arrows_survive_open_inventory_and_halt():
    b=Book(Config());b.process(row(0,signal=signal()),[]);b.process(row(900000),[])
    b.process(row(1800000,signal=signal(1800000)),[])
    assert b.signals[-1]['execution']=='already_in_position'
    b.exit(100,2700000,'test');b.cash=7900;b.process(row(2700000,signal=signal(2700000)),[])
    assert b.halted and b.signals[-1]['execution']=='drawdown_halt'
    assert len(b.signals)==3

def test_session_close_arrow_not_entered():
    b=Book(Config());b.process(row(0,signal=signal(),close=900000),[])
    assert b.pending is None and b.signals[0]['execution']=='session_close'

def test_period_exit_uses_last_period_price_not_final_dataset_price():
    rows=[row(0,signal=signal()),row(900000),row(1800000,o=1000,h=1001,l=999,c=1000)]
    result=simulate(rows,Config(),[],0,1800000)
    assert result['recent_trades'][0]['exit']<101
    assert result['return_pct']<0

def test_month_boundary_returns_stay_in_correct_month():
    ts=int(datetime(2024,1,31,23,45,tzinfo=timezone.utc).timestamp()*1000)
    b=Book(Config());b.process(row(ts),[])
    assert metrics(b)['monthly'][0]['month']=='2024-01'

@pytest.mark.parametrize('date,hours',[('2024-03-09T23:00:00+00:00',23),('2024-11-02T22:00:00+00:00',25)])
def test_ny18_dst_sessions(date,hours):
    a=int(datetime.fromisoformat(date).timestamp()*1000)
    bars=[[t,100,101,99,100,1] for t in range(a,a+hours*3600000,900000)]
    groups=sessions(bars,30)
    assert len(groups)==1 and groups[0]['complete']
    assert len(groups[0]['bars'])==hours*2

def test_published_fractional_volume_precision_boundary():
    bar=[0,67997.5,68600.,67904.4,68440.,13841.931]
    p=profile([bar],Config())
    assert p['high']==pytest.approx(68391.3199999986,abs=1e-8)

def test_cost_stress_reduces_same_target_trade_profit():
    rows=[row(0,signal=signal()),row(900000,o=100,h=112,l=99)]
    base=simulate(rows,Config(),[],0,1800000)
    stress=simulate(rows,replace(Config(),fee_bps=8,slip_bps=4),[],0,1800000)
    assert stress['return_pct']<base['return_pct']
