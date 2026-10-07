import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from model import Config,DailyData

@pytest.fixture()
def fixture_data(tmp_path):
    dates=pd.date_range('2019-01-01',periods=360,tz='UTC');meta={}
    (tmp_path/'daily').mkdir()
    for j in range(52):
        s=f'C{j:02}USDT';x=100+np.arange(360)*(1+j*.01)+np.sin(np.arange(360)/8)
        f=pd.DataFrame({'timestamp':dates.as_unit('ms').asi8,'open':x,'high':x+2,'low':x-2,'close':x,
                        'volume':100.,'quote_volume':100000+j*100})
        f.to_csv(tmp_path/'daily'/(s+'.csv'),index=False)
        meta[s]={'sessions':[{'first_ms':int(f.timestamp.iloc[0]),'last_ms':int(f.timestamp.iloc[-1])}]}
    (tmp_path/'metadata.json').write_text(json.dumps(meta))
    dates=pd.date_range('2018-01-01',periods=725,tz='UTC');x=100+np.arange(725)
    pd.DataFrame({'timestamp':dates.as_unit('ms').asi8,'close':x}).to_csv(tmp_path/'btc_spot.csv',index=False)
    return tmp_path

def test_first_day_and_listing_age_are_conservative(fixture_data):
    d=DailyData(fixture_data)
    assert not d.universe[180];assert len(d.universe[181])==50

def test_today_close_volume_and_btc_cannot_change_today_signal(fixture_data):
    d=DailyData(fixture_data);date=pd.Timestamp('2019-09-01',tz='UTC');i=d.dates.get_loc(date)
    old_btc=d.btc_up[i];old_breadth=d.breadth[i];old_universe=d.universe[i];old_mom=d.momentum[63].iloc[i].copy()
    for name in ['daily/C51USDT.csv','btc_spot.csv']:
        p=fixture_data/name;f=pd.read_csv(p);f['close']=f.close.astype(float);mask=f.timestamp==int(date.timestamp()*1000)
        f.loc[mask,'close']=.01
        if 'quote_volume' in f:f.loc[mask,'quote_volume']=1e15
        f.to_csv(p,index=False)
    changed=DailyData(fixture_data)
    assert changed.btc_up[i]==old_btc;assert changed.breadth[i]==old_breadth;assert changed.universe[i]==old_universe
    pd.testing.assert_series_equal(changed.momentum[63].iloc[i],old_mom)
    assert changed.btc_up[i+1]!=old_btc

def test_volume_universe_is_point_in_time_and_ranks_are_cross_sectional(fixture_data):
    d=DailyData(fixture_data);i=200;u=d.universe[i]
    assert 'C00USDT' not in u and 'C01USDT' not in u
    t=d.targets(Config(regime=False),sorted(set(sum(d.universe,[]))))
    assert (t['weights'][i]>0).sum()==10
    assert sorted(t['ranks'][i][np.isfinite(t['ranks'][i])])==list(range(1,51))
    assert t['weights'][i].max()<=.05

def test_strict_breadth_threshold(fixture_data):
    d=DailyData(fixture_data);i=200;d.breadth[i]=.5;d.btc_up[i]=True
    symbols=sorted(set(sum(d.universe,[])))
    assert d.targets(Config(),symbols)['weights'][i].sum()==0
    assert d.targets(Config(regime=False),symbols)['weights'][i].sum()>0

def test_relisting_resets_180_day_clock(fixture_data):
    p=fixture_data/'metadata.json';m=json.loads(p.read_text());s='C51USDT'
    m[s]['sessions'][0]['last_ms']=int(pd.Timestamp('2019-07-30',tz='UTC').timestamp()*1000)
    m[s]['sessions'].append({'first_ms':int(pd.Timestamp('2019-08-10',tz='UTC').timestamp()*1000),
                             'last_ms':int(pd.Timestamp('2019-12-26',tz='UTC').timestamp()*1000)})
    p.write_text(json.dumps(m));d=DailyData(fixture_data)
    assert s not in d.universe[d.dates.get_loc('2019-09-01')]
