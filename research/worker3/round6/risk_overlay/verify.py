import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib6 as L
from lib6 import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END
t0 = time.time()
SEL = {'book': 'LS', 'exit': 'D', 'chan': '30', 'univ': 20, 'k': 5}
D = L.L4.build(); P = L.L4.make_panel(D); H = L.L4.holdout_panel(); HP = L.L4.make_panel(H)
for nm, Pn, s, e in (("DEV", P, DEV_START, DEV_END), ("TEST", P, TEST_START, TEST_END), ("HO", HP, HO_START, HO_END)):
    r = L.run5(Pn, SEL, s, e)
    print(nm, L.stats(r), "liq", r["liquidations"], f"[{time.time()-t0:.0f}s]")
