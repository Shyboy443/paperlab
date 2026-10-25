import sys; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","DOGEUSDT","XRPUSDT","AVAXUSDT","BNBUSDT","LINKUSDT"]
for s in COINS:
    b = klines(s, "15m"); f = funding(s)
    print(s, len(b), len(f), flush=True)
