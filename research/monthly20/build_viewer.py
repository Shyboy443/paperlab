"""Collect the monthly results of the rotation (02) and ML (10) agents into viewer_data.js for viewer.html.

    python research/monthly20/build_viewer.py
"""
import json
from pathlib import Path

from common import klines, monthly_returns, DEV_START, TEST_END

HERE = Path(__file__).resolve().parent
BOTS = [  # (key, folder, attempt name, label, plain description)
    ("rot2", "02_xs_momentum", "P4_mom14_LS10_weekly_2x", "Rotation · 2x",
     "Every week: buy the 10 coins that rose most over 14 days, short the 10 that fell most. 2x leverage."),
    ("ml1", "10_ml_walkforward", "A_hgb_H6_k3_1x", "ML · 1x",
     "Every day: a model retrained monthly buys the 3 coins it expects to do best tomorrow and shorts the 3 worst. No leverage."),
    ("ml3", "10_ml_walkforward", "B_hgb_H6_k3_3x", "ML · 3x",
     "The same ML bot at 3x leverage (the one that technically passed +20%/month)."),
    ("ml5", "10_ml_walkforward", "C_hgb_H6_k3_5x", "ML · 5x",
     "The same ML bot at 5x leverage."),
]

out = {"bots": [], "btc": {}}
for key, folder, name, label, what in BOTS:
    res = json.loads((HERE / folder / "results.json").read_text())
    a = next(x for x in res["attempts"] if x["name"] == name)
    out["bots"].append({"key": key, "label": label, "what": what,
                        "dev": a["dev"]["months"], "test": a["test"]["months"], "test2x": a["test_double_cost"]["months"],
                        "test_dd": a["test"]["max_dd_pct"], "test2x_dd": a["test_double_cost"]["max_dd_pct"],
                        "dev_dd": a["dev"]["max_dd_pct"], "liq": a["test"].get("liquidations")})
btc = klines("BTCUSDT", "1d", start=DEV_START, end=TEST_END)
out["btc"] = {k: round(v, 2) for k, v in monthly_returns([(r[0], r[4]) for r in btc]).items()}
(HERE / "viewer_data.js").write_text("window.M20=" + json.dumps(out, separators=(",", ":")) + ";\n")
print("bots:", [b["label"] for b in out["bots"]], "btc months:", len(out["btc"]))
