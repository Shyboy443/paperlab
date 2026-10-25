"""Durable isolated desk state. No connection to any broker or existing book."""
from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=15000')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS candles(symbol TEXT,t INTEGER,o REAL,h REAL,l REAL,c REAL,v REAL,PRIMARY KEY(symbol,t));
          CREATE TABLE IF NOT EXISTS funding(symbol TEXT,t INTEGER,rate REAL,mark REAL,PRIMARY KEY(symbol,t));
          CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,ts INTEGER,data TEXT,PRIMARY KEY(kind,id));
        ''')
        self.db.commit()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, allow_nan=False)))

    def record(self, kind, id, ts, data):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)', (kind,id,ts,json.dumps(data,allow_nan=False)))

    def execution(self,id,ts,data,books):
        """Commit the decision and its book mutation together across restarts."""
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',('execution',id,ts,json.dumps(data,allow_nan=False)))
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('books',json.dumps(books,allow_nan=False)))

    def records(self, kind, limit=100):
        with self.lock:
            rows = self.db.execute('SELECT data FROM records WHERE kind=? ORDER BY ts DESC,id DESC LIMIT ?', (kind,limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def has(self, kind, id):
        with self.lock:
            return bool(self.db.execute('SELECT 1 FROM records WHERE kind=? AND id=?',(kind,id)).fetchone())

    def save_bars(self, symbol, bars):
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)',[(symbol,*[b[k] for k in ('t','o','h','l','c','v')]) for b in bars])

    def bars(self, symbol, since=0):
        with self.lock:
            rows=self.db.execute('SELECT t,o,h,l,c,v FROM candles WHERE symbol=? AND t>=? ORDER BY t',(symbol,since)).fetchall()
        return [dict(zip(('t','o','h','l','c','v'),r)) for r in rows]

    def save_funding(self,symbol,rows):
        with self.lock,self.db:
            self.db.executemany('INSERT OR REPLACE INTO funding VALUES(?,?,?,?)',[(symbol,*r) for r in rows])

    def funding(self,symbol,since=0):
        with self.lock:
            return [tuple(r) for r in self.db.execute('SELECT t,rate,mark FROM funding WHERE symbol=? AND t>=? ORDER BY t',(symbol,since))]

    def close(self):
        with self.lock:self.db.close()
