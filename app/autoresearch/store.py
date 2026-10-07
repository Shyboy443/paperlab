from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from .model import START_EQUITY

class Store:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),timeout=20)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA busy_timeout=20000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS markets(id TEXT PRIMARY KEY,venue TEXT,symbol TEXT,active INTEGER,liquid INTEGER,
          volume REAL,spread REAL,price REAL,seen INTEGER,studied INTEGER DEFAULT 0,error TEXT);
        CREATE TABLE IF NOT EXISTS candles(market TEXT,ts INTEGER,o REAL,h REAL,l REAL,c REAL,v REAL,PRIMARY KEY(market,ts));
        CREATE TABLE IF NOT EXISTS funding(market TEXT,ts INTEGER,rate REAL,mark REAL,PRIMARY KEY(market,ts));
        CREATE TABLE IF NOT EXISTS observations(market TEXT,ts INTEGER,volume REAL,spread REAL,price REAL,PRIMARY KEY(market,ts));
        CREATE TABLE IF NOT EXISTS studies(id INTEGER PRIMARY KEY,market TEXT,ts INTEGER,state TEXT,report TEXT);
        CREATE INDEX IF NOT EXISTS study_market ON studies(market,id);
        CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY,market TEXT,created INTEGER,state TEXT,rule TEXT,
          evidence TEXT,equity REAL DEFAULT 100,position TEXT,last_bar INTEGER,last_quote INTEGER,
          trades INTEGER DEFAULT 0,net REAL DEFAULT 0,peak REAL DEFAULT 100,drawdown REAL DEFAULT 0,error TEXT);
        CREATE TABLE IF NOT EXISTS paper_trades(id INTEGER PRIMARY KEY,bot TEXT,opened INTEGER,closed INTEGER,net REAL,detail TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts INTEGER,kind TEXT,detail TEXT);
        ''')
        if 'constraints' not in [r[1] for r in self.db.execute('PRAGMA table_info(markets)')]:
            self.db.execute("ALTER TABLE markets ADD COLUMN constraints TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
    def close(self):self.db.close()
    def set(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,allow_nan=False)));self.db.commit()
    def get(self,key,default=None):
        r=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def event(self,kind,detail):
        self.db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(int(time.time()*1000),kind,str(detail)[:500]));self.db.commit()
    def markets(self,venue,rows,now):
        with self.db:
            self.db.execute('UPDATE markets SET active=0,liquid=0 WHERE venue=?',(venue,))
            for r in rows:
                mid=venue+':'+r['symbol']
                self.db.execute('''INSERT INTO markets(id,venue,symbol,active,liquid,volume,spread,price,seen)
                  VALUES(?,?,?,1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET active=1,liquid=excluded.liquid,
                  volume=excluded.volume,spread=excluded.spread,price=excluded.price,seen=excluded.seen''',
                  (mid,venue,r['symbol'],int(r['liquid']),r['volume'],r['spread'],r['price'],now))
                self.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',
                                (mid,now//3_600_000*3_600_000,r['volume'],r['spread'],r['price']))
                self.db.execute('UPDATE markets SET constraints=? WHERE id=?',(json.dumps(r.get('constraints',{})),mid))
    def save_bars(self,mid,bars):
        with self.db:self.db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)',[(mid,*b) for b in bars])
    def bars(self,mid,start=0):
        return [list(r) for r in self.db.execute('SELECT ts,o,h,l,c,v FROM candles WHERE market=? AND ts>=? ORDER BY ts',(mid,start))]
    def save_funding(self,mid,rows):
        with self.db:self.db.executemany('INSERT OR REPLACE INTO funding VALUES(?,?,?,?)',[(mid,*r) for r in rows])
    def funding(self,mid,start=0):
        return [tuple(r) for r in self.db.execute('SELECT ts,rate,mark FROM funding WHERE market=? AND ts>=? ORDER BY ts',(mid,start))]
    def record_study(self,mid,report,now):
        with self.db:
            self.db.execute('INSERT INTO studies(market,ts,state,report) VALUES(?,?,?,?)',(mid,now,report['state'],json.dumps(report)))
            self.db.execute('UPDATE markets SET studied=?,error=NULL WHERE id=?',(now,mid))
    def next_market(self,now):
        # Previously unseen pairs are eventually visited; high volume breaks ties, never starves the tail.
        r=self.db.execute('''SELECT * FROM markets WHERE active=1 AND liquid=1 AND seen>=? AND studied<?
          ORDER BY studied ASC,volume DESC LIMIT 1''',(now-900_000,now-86_400_000)).fetchone()
        return dict(r) if r else None
    def paper_bots(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM bots WHERE state IN ('PAPER_PROBATION','PAPER_QUALIFIED','DRAINING')")]
    def admit(self,mid,report,now,max_bots=8):
        if report['state']!='HISTORICALLY_QUALIFIED':return False
        active=self.paper_bots()
        if any(b['market']==mid for b in active) or len(active)>=max_bots:return False
        bid=f"{mid}:{report['rule_key']}:{now}"
        with self.db:self.db.execute('''INSERT INTO bots(id,market,created,state,rule,evidence,last_bar,last_quote,equity,peak)
          VALUES(?,?,?,'PAPER_PROBATION',?,?,?,?,?,?)''',(bid,mid,now,json.dumps(report['rule']),json.dumps(report),now//3_600_000*3_600_000-3_600_000,0,START_EQUITY,START_EQUITY))
        self.event('paper_bot_created',bid);return True
    def update_bot(self,b):
        fields=['state','equity','position','last_bar','last_quote','trades','net','peak','drawdown','error']
        with self.db:self.db.execute('UPDATE bots SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[b.get(k) for k in fields]+[b['id']])
    def trade(self,b,p,raw,net,now,reason):
        # Trade ledger and paper book checkpoint commit atomically: restart cannot duplicate a fill.
        fields=['state','equity','position','last_bar','last_quote','trades','net','peak','drawdown','error']
        with self.db:
            self.db.execute('INSERT INTO paper_trades(bot,opened,closed,net,detail) VALUES(?,?,?,?,?)',
                            (b['id'],p['opened_ts'],now,net,json.dumps({**p,'exit':raw,'reason':reason})))
            self.db.execute('UPDATE bots SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[b.get(k) for k in fields]+[b['id']])
    def prune(self,now):
        with self.db:
            for table in ['candles','funding','observations']:
                self.db.execute(f'DELETE FROM {table} WHERE ts<?',(now-120*86_400_000,))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)')
            self.db.execute('DELETE FROM studies WHERE id NOT IN (SELECT id FROM studies ORDER BY id DESC LIMIT 10000)')

def snapshot(path):
    if not Path(path).exists():return {'health':{'status':'STARTING'},'markets':[],'bots':[],'studies':[]}
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
    try:
        meta={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
        counts=[dict(r) for r in db.execute('''SELECT venue,COUNT(*) markets,SUM(liquid) liquid,SUM(studied>0) studied,
          MAX(studied) latest_study FROM markets WHERE active=1 GROUP BY venue''')]
        markets=[dict(r) for r in db.execute('SELECT * FROM markets WHERE active=1 ORDER BY liquid DESC,volume DESC LIMIT 80')]
        bots=[]
        for r in db.execute('SELECT * FROM bots ORDER BY created DESC LIMIT 30'):
            b=dict(r);b['rule']=json.loads(b['rule']);b['position']=json.loads(b['position']) if b['position'] else None
            b['evidence']=json.loads(b['evidence']);bots.append(b)
        studies=[{**dict(r),'report':json.loads(r['report'])} for r in db.execute('SELECT * FROM studies ORDER BY id DESC LIMIT 30')]
        events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 15')]
        return {'health':meta,'coverage':counts,'markets':markets,'bots':bots,'studies':studies,'events':events,
                'execution':'PAPER_ONLY','quote_asset':'USDT','timeframe':'1h with completed UTC 4h context',
                'read_only':True,'scope':'All active Binance USDT spot and perpetual listings; deep study only when liquid'}
    finally:db.close()
