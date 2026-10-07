"""Hourly event-driven linear-perpetual accounting with historical funding.

Information order: funding on existing inventory; gap/stale-data exits; Monday
allocation; frozen daily trailing stops; closing marks; mandatory cap reductions.
No daily OHLC extreme is used to construct the stop executed on that same day.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import numpy as np
import pandas as pd
from model import Config, DailyData

HOUR_MS = 3_600_000


class IntegrityError(RuntimeError):
    pass


class HourlyData:
    def __init__(self, daily: DailyData, selection: Path):
        self.daily = daily
        selected = json.loads(selection.read_text())
        self.symbols = sorted(selected)
        self.index = {s:i for i,s in enumerate(self.symbols)}
        self.hours = pd.date_range('2019-01-01','2024-12-31 23:00',freq='h',tz='UTC')
        self.start_ms = int(self.hours[0].timestamp()*1000)
        shape = (len(self.hours),len(self.symbols))
        self.price = {k:np.full(shape,np.nan,dtype=float) for k in ('open','high','low','close','mark_open')}
        self.rates = np.zeros(shape)
        self.funding_event = np.zeros(shape,dtype=bool)
        self.expected_event = np.zeros(shape,dtype=bool)
        self.last_day = {}
        self.terminal_time = {}
        self.terminal_events = {}
        self.tradable = np.zeros(shape,dtype=bool)
        metadata = json.loads((daily.root/'metadata.json').read_text())
        quality = []
        for symbol in self.symbols:
            column = self.index[symbol]
            self.last_day[column] = pd.to_datetime(metadata[symbol]['last_observed_ms'],unit='ms',utc=True)
            for folder, keys in [('hourly',('open','high','low','close')),('mark_hourly',('open',))]:
                frame = pd.read_csv(daily.root/folder/(symbol+'.csv'))
                if frame.empty:
                    raise IntegrityError('No '+folder+' for '+symbol)
                indices = ((frame['timestamp'].to_numpy(dtype='int64')-self.start_ms)//HOUR_MS)
                valid = (indices>=0)&(indices<len(self.hours))
                if frame['timestamp'].duplicated().any():
                    raise IntegrityError('Duplicate '+folder+' rows for '+symbol)
                for k in keys:
                    dest = 'mark_open' if folder=='mark_hourly' else k
                    self.price[dest][indices[valid],column] = frame[k].to_numpy()[valid]
                if folder=='hourly':
                    positive=frame['volume'].to_numpy()>0 if 'volume' in frame else np.ones(len(frame),dtype=bool)
                    self.tradable[indices[valid],column]=positive[valid]
            last_hour = np.flatnonzero(np.isfinite(self.price['open'][:,column]))[-1]
            if (self.hours[last_hour].normalize()==self.last_day[column]
                    and self.last_day[column]<pd.Timestamp('2024-12-31',tz='UTC')):
                self.terminal_time[column] = self.hours[last_hour]+pd.Timedelta(hours=1)
            self.terminal_events[column]=set()
            for session in metadata[symbol].get('sessions',[]):
                end_day=pd.to_datetime(session['last_ms'],unit='ms',utc=True)
                mask=(self.hours>=pd.to_datetime(session['first_ms'],unit='ms',utc=True))&(self.hours<end_day+pd.Timedelta(days=1))
                active=np.flatnonzero(mask&np.isfinite(self.price['open'][:,column]))
                if len(active) and self.hours[active[-1]].normalize()==end_day and end_day<pd.Timestamp('2024-12-31',tz='UTC'):
                    self.terminal_events[column].add(self.hours[active[-1]]+pd.Timedelta(hours=1))
            frame = pd.read_csv(daily.root/'funding'/(symbol+'.csv'))
            if frame.empty:
                raise IntegrityError('No actual funding for '+symbol)
            stamps = frame['timestamp'].to_numpy(dtype='int64')
            indices = ((stamps-self.start_ms)//HOUR_MS)
            valid = (indices>=0)&(indices<len(self.hours))
            if len(set(indices[valid]))!=int(valid.sum()):
                raise IntegrityError('Multiple settlements inside an hour: '+symbol)
            self.rates[indices[valid],column] = frame['rate'].to_numpy()[valid]
            self.funding_event[indices[valid],column] = True
            # At recorded settlements, exact rate/mark are mandatory. Forecast the
            # next settlement using the reported interval; internal missing rates
            # are never silently treated as zero. Disjoint downloaded months are
            # not funding gaps in the selected strategy's coverage.
            for row in frame.itertuples(index=False):
                nxt = int(row.timestamp)+int(float(row.interval_hours)*HOUR_MS)
                i = (nxt-self.start_ms)//HOUR_MS
                if 0<=i<len(self.hours) and self.hours[i].strftime('%Y-%m') in selected[symbol]:
                    self.expected_event[i,column] = True
            missing_rates = np.flatnonzero(self.expected_event[:,column]&~self.funding_event[:,column]
                                          &np.isfinite(self.price['open'][:,column]))
            missing_marks = np.flatnonzero(self.funding_event[:,column]&~np.isfinite(self.price['mark_open'][:,column])
                                          &np.isfinite(self.price['open'][:,column]))
            quality.append({'symbol':symbol,'settlements':int(valid.sum()),'expected_rate_gaps':len(missing_rates),
                            'missing_event_marks':len(missing_marks),
                            'rate_gap_times':[self.hours[i].isoformat() for i in missing_rates[:10]]})
        self.quality = quality
        self.lines = {}
        for key,frame in [('long',daily.long_line),('short',daily.short_line)]:
            self.lines[key] = frame.reindex(columns=self.symbols).to_numpy()
        self.day_index = {d:i for i,d in enumerate(daily.dates)}


@dataclass
class Position:
    symbol: str
    qty: float  # signed contract base units; 1000-token contracts remain in their native exchange units
    entry: float
    entry_time: pd.Timestamp
    stop: float
    gross: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0


class Engine:
    def __init__(self,data:HourlyData,config:Config):
        self.data,self.config = data,config
        self.cash = config.starting_equity
        self.positions = {}
        self.anchor_ranks,self.previous_ranks = {},{}
        self.last_prices = np.full(len(data.symbols),np.nan)
        self.trades,self.orders,self.universes = [],[],[]
        self.fees,self.funding,self.slippage = 0.,0.,0.
        self.first_trade = None
        self.cap_actions = 0
        self.pending_exits = set()
        self.executable = np.ones(len(data.symbols),dtype=bool)

    def equity(self):
        return self.cash+sum(p.qty*(self.last_prices[j]-p.entry) for j,p in self.positions.items())

    def resize(self,j,target,reference,time,reason,stop=None):
        cfg = self.config
        pos = self.positions.get(j)
        old = pos.qty if pos else 0.
        # Closing before changing direction keeps independent round-trip records.
        if old*target<0:
            self.resize(j,0.,reference,time,reason)
            self.resize(j,target,reference,time,reason,stop=stop)
            return
        delta = target-old
        if abs(delta)*reference<1e-7:
            return
        price = reference*(1+np.sign(delta)*cfg.slippage)
        fee = abs(delta)*price*cfg.fee
        slip = abs(delta)*reference*cfg.slippage
        self.cash -= fee
        self.fees += fee
        self.slippage += slip
        if pos is None:
            pos = Position(self.data.symbols[j],target,price,time,float(stop) if stop is not None else np.nan)
            self.positions[j] = pos
            self.first_trade = self.first_trade or time
        elif abs(target)<abs(old):
            pnl = (abs(old)-abs(target))*np.sign(old)*(price-pos.entry)
            self.cash += pnl
            pos.gross += pnl
        elif abs(target)>abs(old):
            added = abs(target)-abs(old)
            pos.entry = (abs(old)*pos.entry+added*price)/abs(target)
        pos.qty = target
        pos.fees += fee
        pos.slippage += slip
        self.orders.append({'timestamp':time.isoformat(),'symbol':pos.symbol,'reason':reason,
                            'delta_qty':delta,'reference':reference,'fill_price':price,'fee':fee,'slippage_cost':slip})
        if target==0:
            self.trades.append({'symbol':pos.symbol,'side':'long' if old>0 else 'short',
                 'entry_time':pos.entry_time.isoformat(),'exit_time':time.isoformat(),
                 'holding_days':(time-pos.entry_time).total_seconds()/86400,'gross':pos.gross,
                 'fees':pos.fees,'funding_cost':pos.funding,'slippage_cost':pos.slippage,
                 'net':pos.gross-pos.fees-pos.funding,'exit_reason':reason})
            del self.positions[j]
            self.pending_exits.discard(j)

    def caps(self,time):
        if self.config.mode=='buy_hold' or not self.positions:
            return
        # Caps apply at hourly observed prices, independently of the weekly rank
        # deadband. A 1bp weight buffer prevents fee-driven oscillation at 5%.
        for _ in range(3):
            eq = self.equity()
            if eq<=0:
                for j in list(self.positions):
                    self.resize(j,0,self.last_prices[j],time,'insolvent')
                self.cash = max(0,self.cash)
                return
            gross = sum(abs(p.qty)*self.last_prices[j] for j,p in self.positions.items())/eq
            changed = False
            for j,p in list(self.positions.items()):
                if not self.executable[j]:
                    continue
                weight = abs(p.qty)*self.last_prices[j]/eq
                scale = min(1.,(self.config.position_cap-0.0001)/weight) if weight>self.config.position_cap+1e-9 else 1.
                if gross>self.config.gross_cap+1e-9:
                    scale = min(scale,(self.config.gross_cap-0.0001)/gross)
                if scale<1:
                    self.resize(j,p.qty*scale,self.last_prices[j],time,'risk_cap')
                    self.cap_actions += 1
                    changed = True
            if not changed:
                break

    def weekly(self,time,day,open_prices):
        cfg = self.config
        alloc = self.data.daily.allocation(day,cfg)
        self.universes.append({'timestamp':time.isoformat(),'available':alloc['available'],
                              'universe_size':len(alloc['universe']),'universe':alloc['universe'],
                              'ranks':alloc['ranks'],'chop_excluded':alloc['chop_excluded']})
        if cfg.mode=='buy_hold' and (self.first_trade is not None or not alloc['universe']):
            return
        ranks = alloc['ranks']
        target_weights = dict(alloc['targets'])
        changes = {}
        for symbol in sorted(set(target_weights)|{p.symbol for p in self.positions.values()}):
            j = self.data.index[symbol]
            old = self.positions.get(j)
            target = target_weights.get(symbol,0.)
            forced = symbol not in alloc['universe'] or symbol in alloc['chop_excluded']
            previous = self.anchor_ranks.get(symbol,self.previous_ranks.get(symbol))
            moved = previous is None or abs(ranks.get(symbol,previous)-previous)>=cfg.rank_buffer
            if cfg.mode in ('buy_hold','tsm') or forced or moved:
                changes[j] = target
        # Release obsolete inventory before buying; funding at this timestamp has
        # already been applied to the positions held before the rebalance.
        for j,weight in list(changes.items()):
            p = self.positions.get(j)
            if p and (weight==0 or weight*p.qty<0):
                if not self.executable[j]:
                    self.pending_exits.add(j)
                    continue
                self.resize(j,0,self.last_prices[j],time,'weekly_exit')
                symbol=self.data.symbols[j]
                if symbol in ranks:
                    self.anchor_ranks[symbol]=ranks[symbol]
                else:
                    self.anchor_ranks.pop(symbol,None)
        for j,weight in changes.items():
            if weight==0:
                continue
            if not self.executable[j]:
                continue
            price = open_prices[j]
            if not np.isfinite(price):
                raise IntegrityError('No execution bar for '+self.data.symbols[j]+' '+str(time))
            side = 'long' if weight>0 else 'short'
            line = self.data.lines[side][self.data.day_index[day],j]
            if cfg.mode!='buy_hold' and (not np.isfinite(line) or (price<line if weight>0 else price>line)):
                continue
            # Reserve fee/slippage cost to avoid intentionally breaching 5% at entry.
            equity = self.equity()
            target = weight*equity/(price*(1+abs(weight)*(cfg.fee+cfg.slippage)))
            self.resize(j,target,price,time,'weekly_allocation',stop=line)
            self.anchor_ranks[self.data.symbols[j]] = ranks[self.data.symbols[j]]
        self.previous_ranks = dict(ranks)

    def run(self,start,end):
        cfg,data = self.config,self.data
        start,end = pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC')
        begin = int((start-data.hours[0]).total_seconds()/3600)
        finish = int((end-data.hours[0]).total_seconds()/3600)
        history = []
        for i in range(begin,finish):
            time = data.hours[i]
            day = time.normalize()
            op,hi,lo,cl = (data.price[k][i] for k in ('open','high','low','close'))
            valid = np.isfinite(op)
            self.executable = data.tradable[i] if hasattr(data,'tradable') else valid
            self.last_prices[valid] = op[valid]
            for j,p in list(self.positions.items()):
                if not valid[j]:
                    if (time in getattr(data,'terminal_events',{}).get(j,set())
                            or time>=data.terminal_time.get(j,pd.Timestamp.max.tz_localize('UTC'))):
                        self.resize(j,0,self.last_prices[j],time,'archival_terminal_settlement_proxy')
                        continue
                    raise IntegrityError('Internal hourly gap while held: '+p.symbol+' '+str(time))
                if cfg.with_funding:
                    if data.expected_event[i,j] and not data.funding_event[i,j]:
                        raise IntegrityError('Missing actual funding while held: '+p.symbol+' '+str(time))
                    if data.funding_event[i,j]:
                        mark = data.price['mark_open'][i,j]
                        if not np.isfinite(mark) or mark<=0:
                            raise IntegrityError('Missing settlement mark: '+p.symbol+' '+str(time))
                        cost = p.qty*mark*data.rates[i,j]
                        self.cash -= cost
                        p.funding += cost
                        self.funding += cost
                if not self.executable[j]:
                    continue
                if j in self.pending_exits:
                    self.resize(j,0,op[j],time,'deferred_weekly_exit')
                    self.pending_exits.discard(j)
                    continue
                if cfg.mode!='buy_hold':
                    candidate = data.lines['long' if p.qty>0 else 'short'][data.day_index[day],j]
                    if np.isfinite(candidate):
                        p.stop = max(p.stop,candidate) if p.qty>0 else min(p.stop,candidate)
                    if (op[j]<p.stop if p.qty>0 else op[j]>p.stop):
                        self.resize(j,0,op[j],time,'gap_stop')
            self.caps(time)
            if time.hour==0 and time.weekday()==0:
                self.weekly(time,day,op)
            for j,p in list(self.positions.items()):
                if not self.executable[j]:
                    continue
                if cfg.mode!='buy_hold' and (lo[j]<p.stop if p.qty>0 else hi[j]>p.stop):
                    self.resize(j,0,p.stop,time+pd.Timedelta(minutes=59,seconds=59),'trailing_stop')
            valid = np.isfinite(cl)
            self.last_prices[valid] = cl[valid]
            self.caps(time+pd.Timedelta(minutes=59,seconds=59))
            if i==finish-1:
                for j in list(self.positions):
                    self.resize(j,0,self.last_prices[j],end-pd.Timedelta(milliseconds=1),'period_end')
            eq = self.equity()
            notionals = [abs(p.qty)*self.last_prices[j] for j,p in self.positions.items()]
            history.append({'timestamp':(time+pd.Timedelta(hours=1)).isoformat(),'equity':eq,
                 'gross_exposure':sum(notionals)/eq if eq>0 else 0,
                 'net_exposure':sum(p.qty*self.last_prices[j] for j,p in self.positions.items())/eq if eq>0 else 0,
                 'max_position_weight':max(notionals,default=0)/eq if eq>0 else 0,
                 'cumulative_fees':self.fees,'cumulative_funding_cost':self.funding,
                 'cumulative_slippage_cost':self.slippage})
        return {'config':asdict(cfg),'history':pd.DataFrame(history),'trades':pd.DataFrame(self.trades),
                'orders':pd.DataFrame(self.orders),'universes':self.universes,'first_trade':str(self.first_trade),
                'cap_actions':self.cap_actions}
