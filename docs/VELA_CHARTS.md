# Vela signal charts

The value-reversal dashboard uses the pinned Apache-2.0 Vela 0.8.3 browser build, served from Railway at `/static/vendor/vela-0.8.3/vela.global.min.js`. The package's SHA-512 integrity is checked before vendoring. LICENSE, NOTICE and the bundle SHA-256 manifest are shipped beside the bundle. Every chart shows visible Vela attribution and retains the built-in watermark.

`app/dashboard/value-reversal-vela.js` supplies closed OHLCV bars directly, with epoch-millisecond timestamps. No Vela provider or Pine addon is installed. The chart renders the existing verified replay/forward output; it does not recalculate signals or send orders. Historical simulations, saved books, qualification gates, stock strategy controls and frozen rules are independent of this renderer.

QQQ and crypto session replays, and the QQQ live focus chart, show volume, verified reclaim triangles (PVA/CVA), VAL/VAH dashed lines and POC dotted lines. Per-bar missing profile values remain gaps. Live snapshots use supplied static levels only when per-bar levels are unavailable. The time axis and OHLCV readout use America/New_York, including daylight-saving changes.

Fit, zoom, previous/next signal and fullscreen controls sit above the plot. Touch/keyboard interaction is handled by Vela. Reduced-motion preferences disable animation. Live polls reuse the mounted chart and preserve the viewport; unchanged snapshots do not remount. Removed charts release their renderer and listeners.

Vela 0.8.3's native geometry backend does not paint neutral `MarkerSeries` models, so the adapter uses its supported drawing-label triangles. This preserves the source's `plotshape` placement above/below the exact signal candle. Labels and levels come directly from the verified data, with no client-side trading interpretation.

Reproduce the vendor bundle:

```powershell
.venv/Scripts/python.exe scripts/vendor_vela.py
```

With the existing local read-only fixture server on port 8775, run `node tests/check_value_reversal_vela.cjs`. To run against Railway, set `PREVIEW_URL` to the hosted value-reversal page. The test verifies exact arrow timestamps/directions and profile values, actual native scene outputs, NY time, wheel/button zoom, signal navigation, fullscreen, live instance/viewport preservation and responsive layouts at 1280 and 390 pixels. The test injects a live-focus fixture in the browser only; it does not write service state or orders.

## Bot dashboard migration

The private and public bot dashboards now load the same self-hosted Vela 0.8.3 native bundle. They do not load TradingView Lightweight Charts. `dashboard-vela.js` owns native candle/equity instances and a renderer-layer plugin; `arena.js` supplies existing read-only candles and saved trade geometry. Native coordinates keep hover boxes, TP ladders, stop labels and exit results aligned while panning and zooming. Milliseconds are used throughout. Candles display UTC; portfolio equity uses the browser time zone. Live refresh preserves the time window, follows new bars only when already viewing the latest data, and keeps selected trades pinned.

Bot capital growth and the operator equity chart use native line models with unchanged return/equity values. The timeline includes all selected curves. Fit, zoom and fullscreen controls, native mouse/touch interaction, resize cleanup, attribution and the Vela watermark are retained. The source verification and execution rules are untouched.

`LOCAL_ASSETS=1 node tests/check_dashboard_vela.cjs` previews workspace assets over the deployed read-only API; unset the flag to verify the deployed assets. Browser checks cover 1440/390 widths, 300 real DOGE candles, native renderer identity, no TradingView script or network request, actual painted trade geometry, hover, trade selection, live viewport preservation, growth curves and equity toggles. The synthetic equity rows exist only in the test browser.
