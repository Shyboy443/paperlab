-- Execution check on the PAPER bots: how much of each family's result is fees and slippage.
SELECT program, split_part(bot_key, '-', 1) AS family, count(*) trades,
       round(sum(gross), 2) gross, round(sum(fees), 2) fees, round(sum(slippage), 2) slippage, round(sum(net), 2) net,
       round(avg(fees / NULLIF(risk_usd, 0)), 3) AS fees_in_r, round(avg(entry_half_spread_bps), 2) AS entry_half_spread_bps
FROM paper_trades WHERE role = 'CONTROL' {where:} GROUP BY ALL ORDER BY 1, 2;
