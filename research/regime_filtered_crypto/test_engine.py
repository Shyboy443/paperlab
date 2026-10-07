from types import SimpleNamespace
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from engine import Engine, IntegrityError, lagged_funding_score
from model import Config,wilder_atr
from market_data import spot_identity

def book(days=3,n=1,weight=.04,fund=.0001):
    hours=pd.date_range('2019-01-01',periods=days*24,freq='h',tz='UTC');shape=(len(hours),n)
    price={k:np.full(shape,100.) for k in ['open','high','low','close','mark','spot_open','spot_close']}
    target={'ranks':np.tile(np.arange(1,n+1),(days,1)).astype(float),'weights':np.full((days,n),weight),
            'members':np.ones((days,n),bool),'up':np.ones(days,bool),'atr':np.full((days,n),5.)}
    b=SimpleNamespace(hours=hours,start_ms=1546300800000,symbols=[f'COIN{i}USDT' for i in range(n)],price=price,
        tradable=np.ones(shape,bool),spot_tradable=np.ones(shape,bool),spot_known=np.ones((days,n),bool),
        events=np.zeros(shape,bool),expected=np.zeros(shape,bool),rates=np.full(shape,fund),score=np.full(shape,.0001),
        terminal=np.zeros(shape,bool),spot_terminal=np.zeros(shape,bool),quality=[{'bybit_spot_identity':{}} for _ in range(n)])
    b.events[::8]=True;b.expected=b.events.copy();b.daily=SimpleNamespace(targets=lambda c,s:target)
    return b,target

def run(b,c=None):
    c=c or Config(fee=0,slippage=0,transfer_fee=0,harvest=False)
    return Engine(b,c).run('2019-01-01',str((b.hours[-1]+pd.Timedelta(hours=1)).date()))

def reconcile(result):
    o=result['orders'];c=result['config'];h=result['history'];pnl=0.
    if len(o):
        momentum=o.kind=='momentum';hedge=~momentum
        pnl=-np.sum(o.loc[momentum,'delta_contract_units']*o.loc[momentum,'perp_reference'])
        pnl+=np.sum(o.loc[hedge,'delta_contract_units']*o.loc[hedge,'perp_reference'])
        pnl-=np.sum(o.loc[hedge,'delta_contract_units']*o.loc[hedge,'spot_reference_per_contract_unit'])
        for _,g in o.groupby(['symbol','kind']):assert abs(g.delta_contract_units.sum())<1e-7
    expected=c['starting_equity']+pnl+h.funding_cash.iloc[-1]-h.fees.iloc[-1]-h.slippage.iloc[-1]-h.transfer_fees.iloc[-1]
    assert h.equity.iloc[-1]==pytest.approx(expected,abs=1e-6)

def test_funding_score_strictly_lagged_and_expires():
    h=3600000;s=np.array([0,8,16,24])*h
    out=lagged_funding_score(s,np.array([.0001,.0001,.0001,.0004]),0,40)
    assert np.isnan(out[24]);assert out[25]==pytest.approx(.0002)
    assert np.isnan(out[34])

def test_funding_interval_normalization_2h_and_4h():
    h=3600000;s=np.array([0,2,4,8])*h
    out=lagged_funding_score(s,np.array([0,.00002,.00002,.00004]),0,12)
    assert out[9]==pytest.approx(.00008)

def test_funding_gap_restarts_three_actual_settlements():
    h=3600000;s=np.array([0,8,16,24,72,80,88,96])*h
    out=lagged_funding_score(s,np.full(len(s),.0001),0,100)
    assert np.isnan(out[73]);assert np.isnan(out[89]);assert out[97]==pytest.approx(.0001)

def test_native_contract_mapping():
    assert spot_identity('1000PEPEUSDT')==('PEPEUSDT',1000)
    assert spot_identity('1000000MOGUSDT')==('MOGUSDT',1000000)
    assert spot_identity('LUNAUSDT')[0] is None
    assert spot_identity('LUNA2USDT')[0]=='LUNAUSDT'

def test_delta_neutral_true_spot_cash_and_basis():
    b,t=book(weight=0)
    for k in b.price:b.price[k][24:]=110
    c=Config(momentum=False,fee=0,slippage=0,transfer_fee=0)
    r=run(b,c);reconcile(r)
    assert r['history'].equity.iloc[-1]>100000
    assert r['history'].gross_exposure.max()<=1.5+1e-7
    assert r['history'].bybit_equity.iloc[0]==pytest.approx(50000)
    # A spot basis loss is a real loss even when equal native quantities hedge.
    b,t=book(weight=0,fund=0);b.price['spot_open'][24:]=99;b.price['spot_close'][24:]=99
    r=run(b,c);reconcile(r);assert r['history'].equity.iloc[-1]<100000

def test_long_pays_and_hedge_short_receives_actual_funding():
    b,t=book(fund=.001);r=run(b);reconcile(r);assert r['history'].funding_cash.iloc[-1]<0
    b,t=book(weight=0,fund=.001);r=run(b,Config(momentum=False,fee=0,slippage=0,transfer_fee=0))
    reconcile(r);assert r['history'].funding_cash.iloc[-1]>0

def test_entry_stop_not_trailing_and_gap_uses_actual_open():
    b,t=book(days=2);b.price['high'][:]=200
    b.price['open'][10]=85;b.price['low'][10]=80;b.price['close'][10]=85
    r=run(b);reconcile(r);o=r['orders'];exit=o[o.reason=='gap_stop'].iloc[0]
    assert exit.perp_reference==85
    # 200 intraday high did not ratchet the 90 fixed entry stop upward.
    assert not ((o.hour<10)&(o.reason=='entry_atr_stop')).any()

def test_intrahour_stop_accounts_exact_crossed_level():
    b,t=book(days=2);b.price['low'][10]=89
    r=run(b);reconcile(r)
    assert r['orders'].query("reason == 'entry_atr_stop'").iloc[0].perp_reference==90

def test_regime_off_flatten_overrides_rank_deadband():
    b,t=book(days=3);t['up'][1:]=False;t['weights'][1:]=0
    r=run(b);reconcile(r)
    assert r['history'].momentum_exposure.iloc[24:].max()==0

def test_same_rank_does_not_generate_daily_growth_orders():
    b,t=book(days=3);b.price['open'][24:]=90;b.price['close'][24:]=90;b.price['low'][24:]=90
    r=run(b);reconcile(r)
    assert len(r['orders'].query("delta_contract_units > 0"))==1

def test_rank_change_two_allows_rebalance():
    b,t=book(days=3);t['ranks'][1:]=3;b.price['open'][24:]=95;b.price['close'][24:]=95;b.price['low'][24:]=95
    r=run(b);reconcile(r)
    assert len(r['orders'].query("delta_contract_units > 0"))>=2

def test_coin_volatility_reduction_overrides_unchanged_rank():
    b,t=book(days=3,fund=0);t['risk_weights']=t['weights'].copy();t['risk_weights'][1:]=.01;t['weights'][1:]=.01
    r=run(b);reconcile(r)
    assert r['history'].momentum_exposure.iloc[24:48].max()<=.01+1e-8

def test_seed_cost_floor_reproducibility_and_distinct_stresses():
    b,t=book();c=Config(harvest=False,seed=11,transfer_fee=0)
    a=run(b,c);z=run(b,c);other=run(b,replace(c,seed=29))
    reconcile(a);assert a['orders'].adverse_slippage_rate.min()>=.0002
    assert a['history'].equity.iloc[-1]==z['history'].equity.iloc[-1]
    assert a['history'].equity.iloc[-1]!=other['history'].equity.iloc[-1]

def test_missing_actual_funding_fails_instead_of_zero_imputation():
    b,t=book();b.events[8]=False
    with pytest.raises(IntegrityError,match='Missing actual funding'):run(b)

def test_missing_settlement_mark_fails():
    b,t=book();b.price['mark'][8]=np.nan
    with pytest.raises(IntegrityError,match='Missing settlement mark'):run(b)

def test_hard_kill_is_permanent_and_gap_can_overshoot_threshold():
    b,t=book(days=3,n=10,weight=.05,fund=0);t['atr'][:]=100
    for k in b.price:b.price[k][8:24]=50
    r=run(b);reconcile(r)
    assert r['kill_reason']=='drawdown_kill'
    assert r['history'].momentum_exposure.iloc[8:].max()==0
    assert r['history'].equity.iloc[8]<80000

def test_wilder_seed_and_gap_reset():
    a=np.r_[np.ones(14)*2,np.nan,np.ones(14)*4][:,None];out=wilder_atr(a)
    assert out[13,0]==2;assert np.isnan(out[15,0]);assert out[-1,0]==4

def test_costs_apply_both_hedge_legs_and_close():
    b,t=book(weight=0,fund=0)
    r=run(b,Config(momentum=False,transfer_fee=0));reconcile(r)
    o=r['orders'];f=o.delta_contract_units.abs()*(o.perp_reference+o.spot_reference_per_contract_unit)
    assert o.fee.sum()==pytest.approx(float(f.sum()*.0004))
    assert o.slippage.sum()==pytest.approx(float(f.sum()*.0002))

def test_unknown_spot_candle_is_not_executable_history():
    b,t=book(weight=0);b.price['spot_open'][8]=np.nan
    with pytest.raises(IntegrityError,match='Unobserved Bybit'):run(b,Config(momentum=False))
