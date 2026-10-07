"""Accounting, information-boundary and risk regressions on known paths."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import numpy as np
import pandas as pd
import pytest
from engine import Engine, IntegrityError
from model import Config, DailyData, wilder_atr
from download import funding_api_month, repair_mark_gaps, repair_trade_gaps


@pytest.mark.parametrize('hours,valid', [([0,2,8,16],True),([0,8,24,32],False)])
def test_funding_api_schedule_bridge_does_not_hide_missing_settlement(tmp_path,hours,valid):
    base=int(pd.Timestamp('2022-12-14',tz='UTC').timestamp()*1000)
    records=[{'fundingTime':base+h*3_600_000,'fundingRate':'0.0001'} for h in hours]
    folder=tmp_path/'api_funding'/'BNXUSDT'
    folder.mkdir(parents=True)
    (folder/'2022-12.json').write_text(json.dumps({'records':records}))
    if valid:
        rows=funding_api_month(tmp_path,'BNXUSDT','2022-12')
        assert [r[1] for r in rows]==[2,6,8,8]
        assert all(r[2]=='0.0001' for r in rows)
    else:
        with pytest.raises(ValueError,match='Unexpected funding gap'):
            funding_api_month(tmp_path,'BNXUSDT','2022-12')


@pytest.mark.parametrize('available',[True,False])
def test_mark_repair_requires_real_api_bar(tmp_path,available,monkeypatch):
    stamp=1627286400000
    (tmp_path/'hourly').mkdir()
    (tmp_path/'hourly'/'XUSDT.csv').write_text('timestamp,open,high,low,close\n'+str(stamp)+',100,101,99,100\n')
    folder=tmp_path/'api_mark'/'XUSDT'
    folder.mkdir(parents=True)
    records=[[stamp,'100','101','99','100']] if available else []
    (folder/(str(stamp)+'-'+str(stamp)+'.json')).write_text(json.dumps({'records':records}))
    def unavailable(*args,**kwargs):
        raise HTTPError('mock',404,'absent',None,None)
    monkeypatch.setattr('download.download_one',unavailable)
    if available:
        assert repair_mark_gaps(tmp_path,'XUSDT',{})[stamp]==records[0]
    else:
        with pytest.raises(ValueError,match='Actual mark bars unavailable'):
            repair_mark_gaps(tmp_path,'XUSDT',{})


def test_trade_repair_keeps_verified_quiet_hours_without_padding_termination(tmp_path):
    base=int(pd.Timestamp('2021-01-04',tz='UTC').timestamp()*1000)
    (tmp_path/'metadata.json').write_text(json.dumps({'XUSDT':{'first_observed_ms':base,
        'last_observed_ms':base,'sessions':[{'first_ms':base,'last_ms':base}]}}))
    collected={base:[base,'100','100','100','100','10','1000'],
               base+2*3_600_000:[base+2*3_600_000,'100','100','100','100','10','1000']}
    folder=tmp_path/'api_trade'/'XUSDT'
    folder.mkdir(parents=True)
    stamp=base+3_600_000
    quiet=[stamp,'100','100','100','100','0',stamp+3_600_000-1,'0']
    padded=[base+3*3_600_000,'100','100','100','100','0',base+4*3_600_000-1,'0']
    (folder/(str(stamp)+'-'+str(stamp)+'.json')).write_text(json.dumps({'records':[quiet,padded]}))
    result=repair_trade_gaps(tmp_path,'XUSDT',['2021-01'],collected)
    assert float(result[stamp][5])==0
    assert base+3*3_600_000 not in result


def tape(side=1,days=2,stop=80,rate=0.001):
    hours=pd.date_range('2021-01-04',periods=days*24,freq='h',tz='UTC')
    dates=hours.normalize().unique()
    shape=(len(hours),1)
    values={k:np.full(shape,100.) for k in ('open','high','low','close','mark_open')}
    events=np.zeros(shape,dtype=bool)
    events[::8]=True
    def allocation(date,cfg):
        return {'universe':['XUSDT'],'available':40,'ranks':{'XUSDT':1},
                'targets':{'XUSDT':side*.05},'chop_excluded':[]}
    return SimpleNamespace(symbols=['XUSDT'],index={'XUSDT':0},hours=hours,price=values,
          rates=np.full(shape,rate),funding_event=events,expected_event=events.copy(),
          terminal_time={},last_day={0:hours[-1].normalize()},
          lines={'long':np.full((len(dates),1),stop),'short':np.full((len(dates),1),200-stop)},
          day_index={d:i for i,d in enumerate(dates)},daily=SimpleNamespace(allocation=allocation))


@pytest.mark.parametrize('side,rate,sign',[(1,.001,1),(-1,.001,-1),(1,-.001,-1),(-1,-.001,1)])
def test_actual_funding_sign_and_no_charge_on_new_boundary_entry(side,rate,sign):
    data=tape(side=side,rate=rate)
    engine=Engine(data,Config(position_cap=1))
    result=engine.run('2021-01-04','2021-01-06')
    entry=result['orders'].iloc[0]
    # 08/16 on day one, 00/08/16 on day two; first Monday 00 predates entry.
    assert engine.funding==pytest.approx(entry.delta_qty*100*rate*5)
    assert np.sign(engine.funding)==sign
    assert result['history'].iloc[-1].equity-100000==pytest.approx(result['trades'].net.sum())


def test_without_funding_preserves_execution_costs():
    engine=Engine(tape(),Config(with_funding=False,position_cap=1))
    result=engine.run('2021-01-04','2021-01-06')
    assert engine.funding==0
    assert engine.fees>0 and engine.slippage>0
    assert result['history'].iloc[-1].equity==pytest.approx(100000-engine.fees-engine.slippage)


def test_missing_rate_is_an_error_not_a_free_funding_payment():
    data=tape()
    data.funding_event[8,0]=False
    with pytest.raises(IntegrityError,match='Missing actual funding'):
        Engine(data,Config()).run('2021-01-04','2021-01-06')


def test_missing_mark_is_an_error():
    data=tape()
    data.price['mark_open'][8,0]=np.nan
    with pytest.raises(IntegrityError,match='Missing settlement mark'):
        Engine(data,Config()).run('2021-01-04','2021-01-06')


@pytest.mark.parametrize('side,low,high,reference',[(1,90,100,95),(-1,100,110,105)])
def test_stop_fills_at_crossing_level_with_friction_not_hour_extreme(side,low,high,reference):
    data=tape(side=side,stop=95)
    data.price['low'][1,0]=low
    data.price['high'][1,0]=high
    result=Engine(data,Config()).run('2021-01-04','2021-01-06')
    stop=result['orders'].query("reason=='trailing_stop'").iloc[0]
    assert stop.reference==reference
    assert stop.fill_price==pytest.approx(reference*(1-side*.0002))


def test_stop_gap_fills_at_open_not_fictitious_stop():
    data=tape(stop=95)
    for k in ('open','high','low','close'):
        data.price[k][1,0]=90
    result=Engine(data,Config()).run('2021-01-04','2021-01-06')
    stop=result['orders'].query("reason=='gap_stop'").iloc[0]
    assert stop.reference==90


def test_trailing_line_cannot_loosen_when_atr_increases():
    data=tape(stop=95)
    data.lines['long'][1,0]=80
    data.price['low'][24,0]=90
    result=Engine(data,Config()).run('2021-01-04','2021-01-06')
    stop=result['orders'].query("reason=='trailing_stop'").iloc[0]
    assert stop.reference==95


def test_rank_deadband_preserves_position_after_one_rank_move():
    data=tape(days=9)
    def alloc(date,cfg):
        rank=1 if date.day==4 else 2
        target=.05 if date.day==4 else 0
        return {'universe':['XUSDT'],'available':40,'ranks':{'XUSDT':rank},
                'targets':{'XUSDT':target},'chop_excluded':[]}
    data.daily.allocation=alloc
    result=Engine(data,Config(with_funding=False,position_cap=1)).run('2021-01-04','2021-01-13')
    assert not len(result['orders'].query("reason=='weekly_exit'"))
    assert result['trades'].iloc[0].holding_days>8


def test_two_rank_moves_trigger_weekly_exit():
    data=tape(days=9)
    data.daily.allocation=lambda date,cfg: {'universe':['XUSDT'],'available':40,
                'ranks':{'XUSDT':1 if date.day==4 else 3},
                'targets':{'XUSDT':.05} if date.day==4 else {},'chop_excluded':[]}
    result=Engine(data,Config(with_funding=False,position_cap=1)).run('2021-01-04','2021-01-13')
    assert len(result['orders'].query("reason=='weekly_exit'"))==1


def test_rank_exit_updates_deadband_before_reentry():
    data=tape(days=16)
    def alloc(date,cfg):
        target={} if date.day==11 else {'XUSDT':.05}
        return {'universe':['XUSDT'],'available':40,'ranks':{'XUSDT':1 if date.day==4 else 3},
                'targets':target,'chop_excluded':[]}
    data.daily.allocation=alloc
    result=Engine(data,Config(with_funding=False,position_cap=1)).run('2021-01-04','2021-01-20')
    # Jan 11 records an allocation to zero at rank 3. Jan 18 rank 3 is unchanged.
    assert len(result['orders'].query("reason=='weekly_allocation'"))==1


def test_hard_position_cap_overrides_rank_deadband():
    data=tape()
    for k in ('open','high','low','close'):
        data.price[k][1:,0]=200
    result=Engine(data,Config(with_funding=False)).run('2021-01-04','2021-01-06')
    assert result['history'].max_position_weight.max()<=.05+1e-8
    assert len(result['orders'].query("reason=='risk_cap'"))>0


def test_internal_price_gap_cannot_be_silently_settled():
    data=tape()
    data.price['open'][7,0]=np.nan
    with pytest.raises(IntegrityError,match='Internal hourly gap'):
        Engine(data,Config()).run('2021-01-04','2021-01-06')


def test_each_contract_lifecycle_is_settled_before_relisting():
    data=tape(days=3)
    data.terminal_events={0:{data.hours[7]}}
    data.price['open'][7:24,0]=np.nan
    result=Engine(data,Config()).run('2021-01-04','2021-01-07')
    assert result['trades'].iloc[0].exit_reason=='archival_terminal_settlement_proxy'
    assert len(result['orders'].query("reason=='weekly_allocation'"))==1


def test_confirmed_quiet_hour_does_not_execute_a_stop():
    data=tape(stop=95)
    data.tradable=np.ones((len(data.hours),1),dtype=bool)
    data.tradable[1,0]=False
    for k in ('open','high','low','close'):
        data.price[k][1:3,0]=90
    result=Engine(data,Config()).run('2021-01-04','2021-01-06')
    stop=result['orders'].query("reason=='gap_stop'").iloc[0]
    assert pd.Timestamp(stop.timestamp)==data.hours[2]


def test_confirmed_quiet_monday_cannot_open_a_position():
    data=tape()
    data.tradable=np.ones((len(data.hours),1),dtype=bool)
    data.tradable[0,0]=False
    result=Engine(data,Config()).run('2021-01-04','2021-01-06')
    assert result['orders'].empty


@pytest.fixture
def daily(tmp_path):
    root=tmp_path/'data'
    (root/'daily').mkdir(parents=True)
    dates=pd.date_range('2020-01-01','2024-12-31',tz='UTC')
    metadata={}
    for j,symbol in enumerate(('AUSDT','BUSDT','CUSDT','DUSDT')):
        t=np.arange(len(dates))
        price=100*np.exp(np.cumsum(.002+j*.0002+.005*np.sin(t*.8+j)))
        frame=pd.DataFrame({'timestamp':dates.astype('int64')//1_000_000,'open':price,
          'high':price*1.01,'low':price*.99,'close':price,'volume':1000,'quote_volume':1e7+j*1e6})
        frame.to_csv(root/'daily'/(symbol+'.csv'),index=False)
        metadata[symbol]={'first_observed_ms':int(frame.timestamp.iloc[0]),'last_observed_ms':int(frame.timestamp.iloc[-1])}
    (root/'metadata.json').write_text(json.dumps(metadata))
    return DailyData(root)


def test_monday_signal_uses_sunday_close_and_no_future_price(daily):
    day=pd.Timestamp('2021-01-04',tz='UTC')
    sunday=day-pd.Timedelta(days=1)
    expected=daily.raw['close'].loc[sunday,'AUSDT']/daily.raw['close'].loc[sunday-pd.Timedelta(days=63),'AUSDT']-1
    assert daily.momentum[63].loc[day,'AUSDT']==pytest.approx(expected)
    assert daily.long_line.loc[day,'AUSDT']==pytest.approx(
        daily.raw['high'].loc[sunday-pd.Timedelta(days=19):sunday,'AUSDT'].max()-2*daily.atr.loc[day,'AUSDT'])


def test_new_listing_not_admitted_early_despite_high_volume(daily):
    day=pd.Timestamp('2020-06-01',tz='UTC')
    assert not daily.allocation(day,Config(universe_size=4,min_universe=1))['universe']


def test_relisting_restarts_180_day_age_without_using_future_relisting(daily):
    path=daily.root/'metadata.json'
    metadata=json.loads(path.read_text())
    relisted=pd.Timestamp('2021-01-01',tz='UTC')
    metadata['AUSDT']['sessions']=[{'first_ms':metadata['AUSDT']['first_observed_ms'],
        'last_ms':int(pd.Timestamp('2020-12-15',tz='UTC').timestamp()*1000)},
        {'first_ms':int(relisted.timestamp()*1000),'last_ms':metadata['AUSDT']['last_observed_ms']}]
    path.write_text(json.dumps(metadata))
    updated=DailyData(daily.root)
    assert updated.eligible.loc['2020-12-01','AUSDT']
    assert not updated.eligible.loc['2021-06-28','AUSDT']
    assert not updated.eligible.loc['2021-06-30','AUSDT']
    assert updated.eligible.loc['2021-07-01','AUSDT']


def test_target_vol_never_overrides_five_percent_cap(daily):
    day=pd.Timestamp('2021-01-04',tz='UTC')
    cfg=Config(universe_size=4,min_universe=4)
    alloc=daily.allocation(day,cfg)
    assert all(abs(w)<=.05 for w in alloc['targets'].values())
    assert sum(map(abs,alloc['targets'].values()))<=2


def test_chop_filter_skips_outlier_without_refilling_quintile(daily):
    day=pd.Timestamp('2021-01-04',tz='UTC')
    cfg=Config(universe_size=4,min_universe=4)
    ranked=daily.allocation(day,cfg)['ranks']
    winner=min(ranked,key=ranked.get)
    daily.vol.loc[day,winner]=10
    daily._cache.clear()
    alloc=daily.allocation(day,cfg)
    assert winner in alloc['chop_excluded'] and winner not in alloc['targets']


def test_future_data_changes_do_not_change_historical_allocation(daily):
    day=pd.Timestamp('2021-01-04',tz='UTC')
    cfg=Config(universe_size=4,min_universe=4)
    expected=daily.allocation(day,cfg)
    for path in (daily.root/'daily').glob('*.csv'):
        frame=pd.read_csv(path)
        future=frame.timestamp>=int(day.timestamp()*1000)
        frame.loc[future,['open','high','low','close','quote_volume']]*=100
        frame.to_csv(path,index=False)
    updated=DailyData(daily.root).allocation(day,cfg)
    assert expected.pop('vol_median')==pytest.approx(updated.pop('vol_median'),abs=1e-14)
    assert expected==updated


def test_gross_cap_scales_both_sides_without_reversing_signs(daily):
    day=pd.Timestamp('2021-01-04',tz='UTC')
    cfg=Config(universe_size=4,min_universe=4,mode='tsm',position_cap=1,gross_cap=2)
    alloc=daily.allocation(day,cfg)
    assert sum(map(abs,alloc['targets'].values()))==pytest.approx(2)
    assert all(np.sign(w)==np.sign(daily.momentum[63].loc[day,s]) for s,w in alloc['targets'].items())


def test_atr_uses_simple_seed_and_wilder_recursion():
    values=np.arange(1,17,dtype=float).reshape(-1,1)
    atr=wilder_atr(values)
    assert np.isnan(atr[:13]).all()
    assert atr[13,0]==pytest.approx(7.5)
    assert atr[14,0]==pytest.approx((7.5*13+15)/14)
