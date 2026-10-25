import sys
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
from core import *
P = dict(SEL, sig_ref=0.01154)
a = dayidx(int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000))
c, tr, _, rows = sim(P, a, d1)
tot = {}
for t in tr:
    day = datetime.fromtimestamp(td[t[0]]/1000, timezone.utc).strftime("%m-%d")
    tot[day] = tot.get(day, 0) + t[4]
    print(day, syms[t[1]], "long " if t[2]>0 else "short", f"{t[6]*100:6.1f}%  equity effect {t[4]*100:5.2f}%")
print({k: round(v*100,2) for k,v in tot.items()})
print("long", round(sum(t[4] for t in tr if t[2]>0)*100,2), "short", round(sum(t[4] for t in tr if t[2]<0)*100,2))
