import sys, json; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round6/signal")
import lib6 as L
SEL = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round5/high52_plus/dev.json"))["selected"]["cfg"]
P = L.L4.make_panel(L.L4.build()); HP = L.L4.make_panel(L.L4.holdout_panel())
for nm, Pn, s, e in (("DEV", P, L.DEV_START, L.DEV_END), ("TEST", P, L.TEST_START, L.TEST_END), ("HO", HP, L.HO_START, L.HO_END)):
    r = L.run5(Pn, SEL, s, e); print(nm, L.stats(r)["geo"], L.stats(r)["max_dd"], r["net_pct"])
