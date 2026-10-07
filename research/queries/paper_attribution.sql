-- Performance attribution: the feature state at entry of winning vs losing PAPER trades, per family.
-- A feature whose mean differs a lot between winners and losers is a candidate FILTER (to test, not to adopt).
SELECT t.program, split_part(t.bot_key, '-', 1) AS family, (t.net > 0) AS winner, count(*) AS trades,
       round(avg(f.z_r_1h), 2) z_r_1h, round(avg(f.z_rv_1h), 2) z_rv_1h, round(avg(f.z_vol_z), 2) z_vol,
       round(avg(f.z_oi_chg_1h), 2) z_oi_1h, round(avg(f.z_funding), 2) z_funding, round(avg(f.z_premium), 2) z_prem,
       round(avg(f.z_taker_imb_1h), 2) z_taker_1h, round(avg(f.z_ls_ratio), 2) z_ls,
       round(avg(f.btc_corr_1d), 2) btc_corr, round(avg(t.entry_half_spread_bps), 2) spread_bps
FROM paper_trades t
JOIN features_5m f ON f.symbol = t.symbol AND f.ts = ((t.entry_ts - 60000) // 300000) * 300000
WHERE t.role = 'CONTROL' {where:}
GROUP BY ALL HAVING count(*) >= 20 ORDER BY 1, 2, 3;
