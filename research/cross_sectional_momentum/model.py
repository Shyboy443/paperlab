"""Lagged daily features, point-in-time universes and deterministic allocations."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import numpy as np
import pandas as pd


def wilder_atr(values,period=14):
    """Wilder smoothing seeded by the first complete period's simple mean."""
    out=np.full(values.shape,np.nan)
    for j in range(values.shape[1]):
        count,total,previous=0,0.,np.nan
        for i,value in enumerate(values[:,j]):
            if not np.isfinite(value):
                count,total,previous=0,0.,np.nan
            elif count<period:
                count+=1
                total+=value
                if count==period:
                    previous=total/period
                    out[i,j]=previous
            else:
                previous+=(value-previous)/period
                out[i,j]=previous
    return out


@dataclass(frozen=True)
class Config:
    lookback: int = 63
    universe_size: int = 50
    mode: str = 'long_short'
    min_universe: int = 40
    listing_age_days: int = 180
    volume_days: int = 30
    vol_days: int = 30
    target_vol: float = 0.40
    position_cap: float = 0.05
    gross_cap: float = 2.0
    rank_buffer: int = 2
    fee: float = 0.0004
    slippage: float = 0.0002
    starting_equity: float = 100_000.0
    with_funding: bool = True


class DailyData:
    def __init__(self, root: Path):
        self.root = root
        metadata = json.loads((root / 'metadata.json').read_text())
        self.symbols = sorted(metadata)
        self.dates = pd.date_range('2019-01-01','2024-12-31',freq='D',tz='UTC')
        columns = ('open','high','low','close','quote_volume')
        self.raw = {k:pd.DataFrame(index=self.dates,columns=self.symbols,dtype=float) for k in columns}
        for symbol in self.symbols:
            frame = pd.read_csv(root / 'daily' / (symbol + '.csv'))
            frame.index = pd.to_datetime(frame['timestamp'],unit='ms',utc=True)
            for key in columns:
                self.raw[key][symbol] = frame[key].reindex(self.dates)
        close, high, low = (self.raw[k] for k in ('close','high','low'))
        self.vol = np.log(close / close.shift()).rolling(30,min_periods=30).std(ddof=1).mul(np.sqrt(365)).shift()
        self.volume = self.raw['quote_volume'].rolling(30,min_periods=30).mean().shift()
        self.last_close = close.shift()
        tr = pd.DataFrame(np.fmax.reduce([high.to_numpy()-low.to_numpy(),
              (high-close.shift()).abs().to_numpy(), (low-close.shift()).abs().to_numpy()]),
              index=self.dates,columns=self.symbols)
        # Wilder ATR; unavailable/gapped observations are excluded from signals.
        self.atr = pd.DataFrame(wilder_atr(tr.to_numpy()),index=self.dates,columns=self.symbols).shift()
        self.long_line = high.rolling(20,min_periods=20).max().shift()-2*self.atr
        self.short_line = low.rolling(20,min_periods=20).min().shift()+2*self.atr
        self.momentum = {n:(close/close.shift(n)-1).where(close.rolling(n+1).count()==n+1).shift()
                         for n in (30,63,126)}
        # The archive gives the listing day rather than the exact onboarding
        # instant. Its day-end is a conservative upper bound for age eligibility.
        first = pd.Series({s:pd.to_datetime(metadata[s]['first_observed_ms'],unit='ms',utc=True)+pd.Timedelta(days=1) for s in self.symbols})
        age = np.array([(date-first).dt.total_seconds().to_numpy()/86400 for date in self.dates])
        # A relisted contract is a new lifecycle, even when the ticker is reused.
        # The reset becomes effective on its observed relisting date only.
        for j,symbol in enumerate(self.symbols):
            for session in metadata[symbol].get('sessions',[])[1:]:
                relisted=pd.to_datetime(session['first_ms'],unit='ms',utc=True)
                mask=self.dates>=relisted
                age[mask,j]=(self.dates[mask]-relisted-pd.Timedelta(days=1)).total_seconds()/86400
        self.eligible = pd.DataFrame(age>=180,index=self.dates,columns=self.symbols)
        self.eligible &= self.last_close.notna() & self.vol.gt(0) & self.volume.gt(0)
        self.eligible &= close.rolling(127,min_periods=127).count().shift().eq(127)
        self._cache = {}

    def allocation(self, date: pd.Timestamp, config: Config):
        key = (date,config)
        if key in self._cache:
            return self._cache[key]
        eligible = self.volume.loc[date].where(self.eligible.loc[date]).dropna()
        universe = eligible.sort_values(ascending=False,kind='stable').head(config.universe_size).index.tolist()
        if len(universe)<config.min_universe:
            result = {'universe':[], 'available':len(eligible), 'ranks':{}, 'targets':{}, 'vol_median':None, 'chop_excluded':[]}
        else:
            momentum = self.momentum[config.lookback].loc[date,universe]
            ordered = momentum.sort_values(ascending=False,kind='stable').index.tolist()
            ranks = {s:i+1 for i,s in enumerate(ordered)}
            vol = self.vol.loc[date,universe]
            median = float(vol.median())
            chop = set(vol[vol>2*median].index)
            if config.mode=='buy_hold':
                targets = {s:1/len(universe) for s in universe}
            else:
                if config.mode=='tsm':
                    sides = {s:float(np.sign(momentum[s])) for s in universe}
                else:
                    k = max(1,int(len(universe)*0.20))
                    sides = {s:1. for s in ordered[:k]}
                    if config.mode=='long_short':
                        sides.update({s:-1. for s in ordered[-k:]})
                targets = {s:side*min(config.position_cap,config.target_vol/float(vol[s]))
                           for s,side in sides.items() if s not in chop and side!=0}
                gross = sum(abs(w) for w in targets.values())
                if gross>config.gross_cap:
                    targets = {s:w*config.gross_cap/gross for s,w in targets.items()}
            result = {'universe':universe,'available':len(eligible),'ranks':ranks,'targets':targets,
                      'vol_median':median,'chop_excluded':sorted(chop)}
        self._cache[key] = result
        return result


def required_months(data: DailyData, output: Path):
    """Download a superset of all nine universes and passive benchmark holdings.

    Future data availability never selects tradable assets. This union only chooses
    which files the engine must have locally, including history for stopped assets.
    """
    selected = {}
    def add(symbol,date):
        selected.setdefault(symbol,set()).add(date.strftime('%Y-%m'))
    weekly = [d for d in data.dates if d.weekday()==0]
    coverage = []
    for date in weekly:
        alloc = data.allocation(date,Config(universe_size=60))
        coverage.append({'date':date.isoformat(),'available':alloc['available'],'universe':len(alloc['universe'])})
        for symbol in alloc['universe']:
            # Adjacent months cover positions carried past a month boundary, and
            # the last day before the next weekly decision.
            for offset in (-1,0,1):
                month = date+pd.DateOffset(months=offset)
                if pd.Timestamp('2020-01-01',tz='UTC')<=month<pd.Timestamp('2025-01-01',tz='UTC'):
                    add(symbol,month)
    for start,end in [('2019-01-01','2022-01-01'),('2022-01-01','2025-01-01')]:
        dates = [d for d in weekly if start<=d.strftime('%Y-%m-%d')<end]
        first = next((d for d in dates if data.allocation(d,Config())['universe']),None)
        if first is not None:
            for symbol in data.allocation(first,Config())['universe']:
                for month in pd.date_range(first.replace(day=1),pd.Timestamp(end,tz='UTC'),freq='MS',inclusive='left'):
                    add(symbol,month)
    output.mkdir(parents=True,exist_ok=True)
    (output/'required_months.json').write_text(json.dumps({s:sorted(ms) for s,ms in sorted(selected.items())},indent=2))
    pd.DataFrame(coverage).to_csv(output/'universe_coverage.csv',index=False)
    print(json.dumps({'contracts':len(selected),'contract_months':sum(map(len,selected.values())),
                     'first_40_contract_universe':next((c['date'] for c in coverage if c['universe']>=40),None)}),flush=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path(__file__).parent/'output')
    args=p.parse_args()
    required_months(DailyData(args.data),args.output)
