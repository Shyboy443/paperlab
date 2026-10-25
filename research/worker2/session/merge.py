"""Merge repro.json, run_out.json, decay.json and size_cost.json into results.json.
Order: python repro.py; python run.py; python decay.py; python size_cost.py; python merge.py"""
import json

R = json.load(open("run_out.json")); rep = json.load(open("repro.json"))
sc = json.load(open("size_cost.json")); dc = json.load(open("decay.json"))
pr = R["primary"]
out = {"agent": "worker2/session",
       "candidate": "r4_09_session IM ALT H16 k2 X26 K5 W480 + pre-registered vol-scaled sizing (PREREG.md)",
       "test_seen_before": True, "reproduction_of_r4_09": rep, "primary": pr,
       "neighbours": {k: R[k] for k in ("N1 sig_ref=0.834%", "N2 sig_ref=1.551%")},
       "r4_09_original": R["r4_09_original"], "checks": pr["checks"], "checks_passed": pr["checks_passed"],
       "robust": pr["robust"], "verdict_8_checks": "PASS" if pr["robust"] else "FAIL",
       "years_each_from_own_start": dc, "worker1": R["worker1"],
       "w1_daily_vs_monthly_maxabsdiff": R["w1_daily_vs_monthly_maxabsdiff"], "size_cost_500usdt": sc}
json.dump(out, open("results.json", "w"), indent=1)
print("checks passed", pr["checks_passed"])
