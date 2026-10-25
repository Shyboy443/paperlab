"""One function that turns a config dict into a weight matrix, so DEV exploration, PREREG and the checks share code."""
import numpy as np
import lib as Lb


def build_W(P, cfg, mask=None):
    U = cfg.get("U", 40)
    m = Lb.universe_mask(P, U) if mask is None else mask
    L = cfg["L"]; rb = cfg["rb"]; sgn = 1.0 if cfg["dir"] == "follow" else -1.0
    if cfg["kind"].startswith("XS"):
        name = cfg["score"]
        if name == "share":
            sc = Lb.taker_share(P, L)
        elif name == "fdev":
            sc = Lb.flow_dev(P, L, cfg.get("base", 30))
        elif name == "fvol":
            sc = Lb.flow_vol(P, L, cfg.get("base", 30))
        elif name == "div":
            sc = Lb.xs_rank(Lb.flow_dev(P, L, cfg.get("base", 30)), m) - Lb.xs_rank(Lb.ret_lb(P, L), m)
        else:
            raise ValueError(name)
        k = cfg.get("k", 10); kS = cfg.get("kS", k)
        return Lb.xs_weights(sgn * sc, m, k, kS, rb)
    fd = Lb.flow_dev(P, L, cfg.get("base", 30)); rt = Lb.ret_lb(P, L)
    # threshold in units of the coin-pooled sd of flow_dev measured on DEV-era bars only (first half of the panel)
    sd = cfg.get("sd") or float(np.nanstd(fd[: len(fd) // 2]))
    thr = cfg["thr_sd"] * sd
    name = cfg["score"]
    if name == "ts_flow":
        sg = np.where(fd > thr, 1.0, np.where(fd < -thr, -1.0, 0.0))
    elif name == "ts_div":
        sg = np.where((rt < 0) & (fd > thr), 1.0, np.where((rt > 0) & (fd < -thr), -1.0, 0.0))
    elif name == "ts_conf":
        sg = np.where((rt > 0) & (fd > thr), 1.0, np.where((rt < 0) & (fd < -thr), -1.0, 0.0))
    else:
        raise ValueError(name)
    W = Lb.ts_weights(sgn * sg, m, U, rb)
    if cfg["kind"] == "TS_long":
        W = np.where(np.isnan(W), np.nan, np.maximum(W, 0.0))
    return W
