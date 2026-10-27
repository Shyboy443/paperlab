"""DEV-only control: is the gross edge specific to the SAME block (periodicity), or generic short-term residual momentum?
Score taken from a different block of the same past K days (roll the block axis by r), cost 0."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, lib, sys
B, K, k = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
NB = 24 // B
S0 = lib.score(B, K).copy()
for r in range(NB):
    lib._sc[(B, K, "raw")] = np.roll(S0, r, axis=1)
    g = lib.run(B=B, K=K, k=k, start=lib.DEV_START, end=lib.DEV_END, cost_mult=0)
    print(f"B{B} K{K} k{k} roll {r}: gross geo {g['geo_month']}  median {g['median_month']}")
lib._sc[(B, K, "raw")] = S0
