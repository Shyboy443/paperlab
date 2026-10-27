import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib6 as L
from lib6 import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END
t0=time.time()
SEL = json.loads(Path(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round5/high52_plus/dev.json").read_text())["selected"]["cfg"]
D = L.L4.build(); P = L.L4.make_panel(D); H = L.L4.holdout_panel(); HP = L.L4.make_panel(H)
print("load", time.time()-t0)
for nm,(Pn,s,e) in dict(DEV=(P,DEV_START,DEV_END),TEST=(P,TEST_START,TEST_END),HO=(HP,HO_START,HO_END)).items():
    t1=time.time(); r=L.run5(Pn,SEL,s,e); print(nm, L.stats(r), r["orders_per_month"], r["xstat"], f"{time.time()-t1:.1f}s")
