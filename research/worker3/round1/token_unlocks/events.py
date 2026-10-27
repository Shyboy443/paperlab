"""Build the unlock event table from the cached DefiLlama emission files (data/w3r1_token_unlocks/proto/*.json).

Source: https://defillama-datasets.llama.fi/emissions/<protocol> (public, no key; the api.llama.fi/emission* routes are
paywalled now). Symbols via https://coins.llama.fi/prices/current/<token>. Both cached in data/w3r1_token_unlocks/.

Event = a CLIFF unlock (unlockType == 'cliff') of the pre-scheduled vesting categories INSIDERS (team/advisors) and
PRIVATESALE (investors), the two categories whose schedules are fixed in the token's published vesting terms at TGE.
Same-day cliffs of one token are summed. Size = cliff tokens / tokens already unlocked the day before (sum of the
documented 'unlocked' curves of all sections), i.e. % of the schedule's circulating supply.

LIMITATION: DefiLlama only serves today's version of each schedule. Insider/investor vesting terms are normally published
at TGE, but schedules are sometimes revised (delays, burns) and today's file shows the final version. Treat it as a
mild look-ahead risk.
Output: data/w3r1_token_unlocks/events.json  [{sym, ts, day, size, tokens, supply, cats}]"""
import json, glob, collections
from pathlib import Path
import numpy as np

D = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r1_token_unlocks")
CATS = {"insiders", "privateSale"}
OVERRIDE = {"fantom": "FTMUSDT", "pudgy-penguins": "PENGUUSDT", "uxlink": "UXLINKUSDT",
            "cetus": "CETUSUSDT", "ton": None, "flowswap": None}  # ton/flowswap: symbol collisions (GRAM, FLOW)


def build():
    S = json.loads((D / "symbols.json").read_text())
    z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_rotation/panel_1d.npz")
    syms = set(map(str, z["syms"]))
    seen_tok, out = set(), []
    for f in sorted(glob.glob(str(D / "proto" / "*.json"))):
        pid = Path(f).stem
        d = json.loads(Path(f).read_text())
        m = d.get("metadata", {}) or {}
        tok = m.get("token")
        if pid in OVERRIDE:
            sym = OVERRIDE[pid]
        else:
            c = S["coins"].get(tok)
            if not c:
                continue
            s = c["symbol"].upper()
            cand = [x for x in (s + "USDT", "1000" + s + "USDT") if x in syms]
            sym = cand[0] if cand else None
        if not sym or sym not in syms or tok in seen_tok:
            continue
        seen_tok.add(tok)
        # schedule-unlocked supply per day
        sup = collections.defaultdict(float)
        for sec in d["documentedData"]["data"]:
            for r in sec["data"]:
                sup[int(r["timestamp"]) // 86400] += float(r.get("unlocked") or 0)
        days = np.array(sorted(sup)); vals = np.array([sup[k] for k in days])
        ev = collections.defaultdict(lambda: [0.0, set()])
        for e in m.get("events", []) or []:
            if e.get("unlockType") != "cliff" or e.get("category") not in CATS:
                continue
            n = sum(float(x or 0) for x in (e.get("noOfTokens") or []))
            if n <= 0:
                continue
            k = int(e["timestamp"]) // 86400
            ev[k][0] += n; ev[k][1].add(e["category"])
        for k, (n, cats) in ev.items():
            i = np.searchsorted(days, k) - 1  # last documented day strictly before the unlock day
            if i < 0 or vals[i] <= 0:
                continue
            out.append(dict(sym=sym, pid=pid, day=k, ts=k * 86400, tokens=n, supply=float(vals[i]),
                            size=n / float(vals[i]), cats=sorted(cats)))
    out.sort(key=lambda r: (r["day"], r["sym"]))
    (D / "events.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    ev = build()
    import datetime as dt
    big = [e for e in ev if e["size"] >= 0.02]
    print(len(ev), "events;", len(big), ">=2%")
    for y in range(2020, 2027):
        yy = [e for e in big if dt.datetime.utcfromtimestamp(e["ts"]).year == y]
        print(y, len(yy), len({e["sym"] for e in yy}))
