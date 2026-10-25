import concurrent.futures,sqlite3
import pytest
from app.core.storage import Storage
from app.core.storage_read_view import read_view
from app.core.arena_live_view import candles

def test_concurrent_read_snapshots_during_writes_are_isolated_and_readonly(tmp_path):
    storage=Storage(tmp_path/'arena.db')
    storage.conn.execute("INSERT INTO meta VALUES('test','0')")
    def read(_):
        with read_view(storage) as view:
            assert view.conn is not storage.conn
            for i in range(10):assert view.conn.execute("SELECT value FROM meta WHERE key='test'").fetchone()
            with pytest.raises(sqlite3.OperationalError):view.conn.execute("DELETE FROM meta")
            return True
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures=[pool.submit(read,i) for i in range(120)]
        for i in range(100):storage.conn.execute("UPDATE meta SET value=? WHERE key='test'",(str(i),))
        assert all(f.result() for f in futures)
    assert storage.conn.execute('SELECT COUNT(*) FROM meta').fetchone()[0]>0
    storage.close()

def test_historical_chart_window_is_bounded_and_excludes_future_bars(tmp_path):
    storage=Storage(tmp_path/'bars.db');start=1735772400000
    storage.fwd6_bars_save([{'symbol':'DOGEUSDT','open_time':start+i*60000,'open':1,'high':2,'low':.5,'close':1.5,'volume':10} for i in range(2000)])
    endpoint=start+49*60000
    view=candles(storage,None,'DOGEUSDT','1m',10,now_ms=endpoint)
    assert len(view['candles'])==10
    assert view['candles'][0][0]==start+40*60000
    assert view['candles'][-1][0]==endpoint
    assert all(r[0]<=endpoint for r in view['candles'])
    storage.close()
