import copy,json
from datetime import datetime,timezone
from dataclasses import replace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.value_reversal.model import Book,Config,aggregate,profile,MINUTE,RULE
from app.value_reversal.data import download
from app.value_reversal.service import ValueReversalService
from app.core.api_value_reversal import router

BASE=int(datetime(2026,10,6,13,30,tzinfo=timezone.utc).timestamp()*1000)
S={'date':'2026-10-06','open_ms':BASE,'close_ms':BASE+26*15*MINUTE}
def bar(i,o=100,h=101,l=99,c=100,v=100):return [BASE+i*15*MINUTE,o,h,l,c,v]
def seeded():
 b=Book();b.session=dict(S);b.previous={'low':99,'high':103,'poc':101}
 b.process(bar(0,100,101,99.5,100),S)
 return b
def signal_book():
 b=seeded()
 b.process(bar(1,100,100.2,97.8,98,200),S)
 b.process(bar(2,98,98.2,97.4,97.5,100),S)
 b.process(bar(3,97.4,100.2,97.3,100,250),S)
 return b

def test_volume_profile_conserves_volume_and_exposes_source_coverage_limitation():
 p=profile([bar(0,100,103,97,99,600),bar(1,100,100,100,100,50)],Config())
 assert sum(p['volume'])==pytest.approx(650)
 width=p['row_width'];first=round((p['low']-p['row_low'])/width);last=round((p['high']-p['row_low'])/width)
 assert p['actual_band_volume_pct']==pytest.approx(sum(p['volume'][first:last])/650*100)
 # Published radial expansion skips the unchosen side at each distance. It can undershoot.
 assert p['actual_band_volume_pct']<70

def test_signal_is_close_confirmed_and_next_open_only():
 b=signal_book();assert b.pending and not b.position and not b.fills
 b.process(bar(4,100,101,99.5,100.5),S)
 assert b.position and b.fills[0]['ts']==BASE+4*15*MINUTE
 assert b.position['entry']==pytest.approx(100.02)
 assert b.position['qty']<=100 # <=1x whole shares

def test_stop_wins_target_tie_with_adverse_costs():
 b=signal_book();b.process(bar(4,100,104,96,101),S)
 assert len(b.trades)==1 and b.trades[0]['reason']=='stop'
 assert b.trades[0]['exit']<b.trades[0]['stop']
 assert b.trades[0]['net']<0 and b.trades[0]['fees']>0

def test_gap_beyond_target_does_not_enter():
 b=signal_book();b.process(bar(4,104,105,103.5,104),S)
 assert not b.position and not b.fills and b.rejects==1

def test_missing_next_bar_cancels_pending_not_backfills():
 b=signal_book();b.process(bar(5,100,101,99.5,100),S)
 assert not b.position and not b.pending and not b.fills

def test_restart_is_identical_and_never_duplicates_fill():
 b=signal_book();b.process(bar(4,100,101,99.5,100.5),S)
 rebuilt=Book.restore(json.loads(json.dumps(b.snapshot())),Config())
 rebuilt.process(bar(4,100,104,96,101),S)
 assert len(rebuilt.fills)==1
 rebuilt.process(bar(5,100,104,99.5,102),S)
 b.process(bar(5,100,104,99.5,102),S)
 assert rebuilt.snapshot()==b.snapshot()

def test_prefix_signals_do_not_use_future_profile():
 first=signal_book();p=copy.deepcopy(first.pending)
 first.process(bar(4,100,1000,1,500,9999999),S)
 assert first.signals[0]['stop']==p['stop'] and first.signals[0]['target']==p['target']

def test_expired_excursion_cannot_signal():
 b=seeded();b.process(bar(1,100,100.2,97.8,98,200),S)
 for i in range(2,7):b.process(bar(i,98,98.2,97.4,97.5,50),S)
 b.process(bar(7,97.4,100.2,97.3,100,250),S)
 assert not b.pending and not b.signals

def test_short_is_mirrored():
 b=seeded();b.process(bar(1,100,101,99.5,100),S)
 b.process(bar(2,100,104.2,99.8,104,200),S)
 b.process(bar(3,104,104.6,103.8,104.5,100),S)
 b.process(bar(4,104.6,104.7,101,101.5,250),S)
 assert b.pending['side']==-1 and b.pending['target']==99

def test_no_overnight_entry_and_close_at_session_end():
 b=signal_book();b.process(bar(4,100,101,99.5,100.5),S)
 short_session={**S,'close_ms':BASE+6*15*MINUTE}
 b.process(bar(5,100,101,99.5,100.5),short_session)
 assert not b.position and b.trades[-1]['reason']=='session_close'

def test_incomplete_previous_session_is_not_usable():
 b=Book();b.process(bar(0),S)
 nxt={**S,'date':'2026-10-07','open_ms':BASE+86400000,'close_ms':S['close_ms']+86400000}
 b.process([nxt['open_ms'],100,101,99,100,100],nxt)
 assert b.previous is None

def test_early_close_and_thirty_minute_aggregation():
 s={**S,'close_ms':BASE+14*15*MINUTE}
 groups=aggregate([bar(i) for i in range(14)],[s],30)
 assert groups[0]['complete'] and len(groups[0]['bars'])==7
 assert not aggregate([bar(i) for i in range(13)],[s],30)[0]['complete']

def test_drawdown_halt_does_not_reset():
 b=Book();b.cash=7999;b.process(bar(0),S)
 assert b.halted and b.cash==7999

@pytest.mark.asyncio
async def test_data_requests_are_only_calendar_and_consolidated_gets():
 class Client:
  def __init__(self):self.calls=[]
  async def request(self,method,path,data=False):
   self.calls.append((method,path,data))
   if '/calendar' in path:return [{'date':'2026-10-06','open':'09:30','close':'13:00'}]
   return {'bars':{'QQQ':[{'t':'2026-10-06T13:30:00Z','o':100,'h':101,'l':99,'c':100,'v':50}]}}
 c=Client();d=await download(c,'2026-10-06T00:00:00Z','2026-10-07T00:00:00Z')
 assert d['sessions'][0]['close_ms']-d['sessions'][0]['open_ms']==210*MINUTE
 assert all(method=='GET' for method,_,_ in c.calls)
 assert 'feed=sip' in c.calls[1][1] and c.calls[1][2]

def test_public_routes_have_no_order_or_mutation_method(tmp_path):
 class Providers:
  def keys(self,*a):return None
 s=ValueReversalService(tmp_path/'paper.json',Providers())
 app=FastAPI();app.state.value_reversal=s;app.include_router(router)
 c=TestClient(app);d=c.get('/api/public/value-reversal').json()
 assert d['broker_orders_enabled'] is False and d['execution']=='DELAYED_FEED_PAPER_SIMULATION'
 assert c.post('/api/public/value-reversal').status_code==405
 assert len(d['books'])==8

def test_frozen_identity_refuses_changed_costs():
 b=Book()
 with pytest.raises(ValueError):Book.restore(b.snapshot(),replace(Config(),fee_bps=2))

@pytest.mark.asyncio
async def test_service_warms_history_without_prestart_fills_and_resumes(tmp_path,monkeypatch):
 import app.value_reversal.service as module
 class Providers:
  def keys(self,*a):return ('test','test')
  def client(self,*a):return object()
  def redact(self,text):return str(text)
 data={'bars':[bar(i) for i in range(26)],'sessions':[S]}
 async def get_recent(*a):return data
 monkeypatch.setattr(module,'recent',get_recent)
 path=tmp_path/'paper.json';s=ValueReversalService(path,Providers());s.report={'test':True}
 now=datetime.fromtimestamp((S['close_ms']+60*MINUTE)/1000,timezone.utc)
 await s.tick(now)
 assert all(not b.fills and not b.curve and b.last_bar>0 for b in s.books.values())
 assert s.started==int(now.timestamp()*1000)
 saved=json.loads(path.read_text());resumed=ValueReversalService(path,Providers());resumed.report={'test':True}
 await resumed.tick(now)
 assert json.loads(path.read_text())==saved

def test_fills_round_against_trader_to_one_cent():
 b=Book();buy,_=b.fill(100.123,1,1,BASE,BASE,'entry');sell,_=b.fill(100.123,-1,1,BASE,BASE,'exit')
 assert buy==100.15 and sell==100.10

def test_corrupt_derived_report_does_not_block_saved_books(tmp_path):
 class Providers:pass
 path=tmp_path/'paper.json';s=ValueReversalService(path,Providers());s.books[Config().key].cash=9990;s.checkpoint()
 path.with_name('qqq-value-reversal-v2-report.json').write_text('{broken')
 resumed=ValueReversalService(path,Providers())
 assert resumed.books[Config().key].cash==9990 and resumed.report is None
 assert resumed.status=='STARTING' and resumed.research_status['phase']=='PENDING'

def test_published_volume_slowdown_survives_intervening_opposite_candle():
 b=seeded();b.process(bar(1,100,100.2,97.8,98,200),S)
 b.process(bar(2,98,98.5,97.7,98.3,999),S) # bullish outside: not last bearish volume
 b.process(bar(3,98.3,98.4,97.4,97.5,100),S)
 b.process(bar(4,97.4,100.2,97.3,100,250),S)
 assert b.pending and b.pending['last_directional_volume']==100

def test_published_signal_does_not_require_previous_inside_close():
 b=Book();b.session=dict(S);b.previous={'low':99,'high':103,'poc':101}
 b.process(bar(0,100,100.2,97.8,98,200),S)
 b.process(bar(1,98,98.2,97.4,97.5,100),S)
 b.process(bar(2,97.4,100.2,97.3,100,250),S)
 assert b.pending and b.pending['outside_bars']==2

def test_five_outside_bars_followed_by_reclaim_matches_source_count():
 b=seeded();b.process(bar(1,100,100.2,97.8,98,200),S)
 for i in range(2,6):b.process(bar(i,98,98.2,97.4,97.5,100-i),S)
 b.process(bar(6,97.4,100.2,97.3,100,250),S)
 assert b.pending and b.pending['outside_bars']==5

def test_current_profile_uses_completed_candle_without_lookahead():
 b=Book(Config(profile='developing'));x=bar(0,100,105,95,102,100)
 b.process(x,S);p=profile([x],b.cfg)
 assert b.latest_profile==p
 frozen=copy.deepcopy(b.replay[-1]);b.process(bar(1,102,200,90,150,99999),S)
 assert b.replay[0]==frozen

def test_indicator_keeps_all_arrows_separate_from_trade_eligibility():
 b=signal_book();b.process(bar(4,100,101,99.5,100.5),S)
 assert b.position
 b.process(bar(5,100,100.2,97.8,98,200),S) # exits earlier trade
 b.process(bar(6,98,98.2,97.4,97.5,100),S)
 b.halted=True
 b.process(bar(7,97.4,100.2,97.3,100,250),S)
 assert len(b.signals)==2 and b.signals[-1]['entry_eligible'] is False and b.pending is None

def test_old_book_identity_cannot_be_loaded_into_corrected_rules():
 from app.value_reversal.legacy_v1.model import Book as OldBook
 with pytest.raises(ValueError):Book.restore(OldBook().snapshot(),Config())
