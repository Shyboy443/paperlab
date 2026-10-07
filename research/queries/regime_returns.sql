-- How the market behaves in each BTC regime: average next-hour move and volatility of the coins.
SELECT vol_regime, trend_regime, count(*) AS decisions,
       round(1e4 * avg(fwd_1h), 2) AS fwd1h_bp, round(1e4 * stddev(fwd_1h), 1) AS sd1h_bp,
       round(1e4 * avg(rv_1h), 1) AS rv_5m_bp
FROM features_5m WHERE vol_regime IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2;
