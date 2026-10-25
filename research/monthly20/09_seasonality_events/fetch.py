import sys; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
SYMS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","LTCUSDT","DOTUSDT","TRXUSDT"]
for s in SYMS:
    k = klines(s, "1h"); f = funding(s)
    print(s, len(k), len(f), flush=True)
