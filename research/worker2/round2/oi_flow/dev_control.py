"""DEV only. Does the OI leg add anything? Same book (k=7, h=3) with price-only momentum (long top-q / short bottom-q
of 7-day return, OI ignored) and OI-only (long top-q / short bottom-q of 7-day OI change, price ignored)."""
import json
import numpy as np
import core as C
S = C.S
orig = C.regime_masks
out = []
for mode in ("price_only", "oi_only", "both(selected family)"):
    for k, q in ((5, 0.2), (7, 0.2), (7, 0.3)):
        def rm(k_, q_, mode=mode):
            R, ok = orig(k_, q_)
            if mode == "both(selected family)":
                return R, ok
            x = S.logchg(C.P.c if mode == "price_only" else C.M["oi"], k_)
            xr = S.xrank(np.where(ok, x, np.nan))
            return {"UU": xr >= 1 - q_, "DU": xr <= q_, "UD": np.zeros_like(ok), "DD": np.zeros_like(ok)}, ok
        C.regime_masks = rm
        W = C.weights(k, q, 3, {"UU": 1, "DU": -1})
        d = C.run(W, C.DEV_START, C.DEV_END)
        v = np.array([x for m, x in d["months"].items() if m >= "2021-12"])
        r = dict(mode=mode, k=k, q=q, h=3, net=d["net_pct"], geo=d["geo_month"], dd=d["max_dd_pct"],
                 t=round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2), trades=d["trades"])
        out.append(r); print(r, flush=True)
C.regime_masks = orig
json.dump(out, open("dev_control.json", "w"), indent=1, default=float)
