-- When funding is very negative, open interest jumps and price falls fast: what happens next?
-- params: fz (funding z, default -1.5), oz (OI-change z, default 1.5), pz (1h-return z, default -1.5)
-- One event per coin per hour (the first qualifying 5m decision), so a burst counts once.
WITH ev AS (
    SELECT *, row_number() OVER (PARTITION BY symbol, ts // 3600000 ORDER BY ts) AS k
    FROM features_5m
    WHERE z_funding <= {fz:-1.5} AND z_oi_chg_1h >= {oz:1.5} AND z_r_1h <= {pz:-1.5}
), base AS (
    SELECT avg(fwd_5m) b5, avg(fwd_15m) b15, avg(fwd_1h) b1h, avg(fwd_4h) b4h FROM features_5m
)
SELECT CASE WHEN ts < (SELECT (min(ts) + max(ts)) / 2 FROM features_5m) THEN 'half 1' ELSE 'half 2' END AS half,
       count(*) AS events, count(DISTINCT symbol) AS coins,
       round(1e4 * avg(fwd_5m), 1) AS fwd5m_bp,  round(1e4 * avg(fwd_15m), 1) AS fwd15m_bp,
       round(1e4 * avg(fwd_1h), 1) AS fwd1h_bp,  round(1e4 * avg(fwd_4h), 1) AS fwd4h_bp,
       round(1e4 * median(fwd_1h), 1) AS med1h_bp, round(avg(CASE WHEN fwd_1h > 0 THEN 1 ELSE 0 END), 3) AS up_1h,
       round(1e4 * (SELECT b1h FROM base), 1) AS all_1h_bp
FROM ev WHERE k = 1 GROUP BY 1 ORDER BY 1;
