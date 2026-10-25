import sys; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","ADAUSDT","AVAXUSDT","DOTUSDT","NEARUSDT","ATOMUSDT","BNBUSDT","XRPUSDT",
 "LTCUSDT","BCHUSDT","LINKUSDT","UNIUSDT","AAVEUSDT","DOGEUSDT","1000SHIBUSDT","FILUSDT","ETCUSDT","TRXUSDT",
 "XLMUSDT","ALGOUSDT","CRVUSDT","SUSHIUSDT","ARBUSDT","OPUSDT"]
syms = {s["symbol"] for s in perp_symbols()}
for c in COINS:
    if c not in syms: print("missing", c, flush=True); continue
    b = klines(c, "1h"); f = funding(c); print(c, len(b), len(f), flush=True)
