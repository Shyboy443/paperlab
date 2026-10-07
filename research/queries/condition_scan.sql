-- Generic event study: any feature beyond a z threshold -> forward returns by half. params: feat, z (sign = side)
-- e.g. python research/query.py condition_scan feat=taker_imb_1h z=2
WITH ev AS (
    SELECT *, row_number() OVER (PARTITION BY symbol, ts // 3600000 ORDER BY ts) AS k FROM features_5m
    WHERE CASE WHEN {z:2} >= 0 THEN z_{feat:oi_chg_1h} >= {z:2} ELSE z_{feat:oi_chg_1h} <= {z:2} END
)
SELECT CASE WHEN ts < (SELECT (min(ts) + max(ts)) / 2 FROM features_5m) THEN 'half 1' ELSE 'half 2' END AS half,
       count(*) events, round(1e4 * avg(fwd_5m), 1) fwd5m_bp, round(1e4 * avg(fwd_15m), 1) fwd15m_bp,
       round(1e4 * avg(fwd_1h), 1) fwd1h_bp, round(1e4 * avg(fwd_4h), 1) fwd4h_bp,
       round(avg(CASE WHEN fwd_1h > 0 THEN 1 ELSE 0 END), 3) up_1h
FROM ev WHERE k = 1 GROUP BY 1 ORDER BY 1;
