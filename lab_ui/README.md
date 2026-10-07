# /lab dashboard

The newer PaperLab dashboard: Overview, Bots, Bot detail, Programs and the AI cost analyzer. It was built in Lovable
(a TanStack Start app with server functions) and ported here as a static single-page app that PaperLab serves itself.

- **Data:** read-only `GET /api/public/competition/*` and `/public/health/deep` on the same origin, refreshed every 15
  seconds (`src/lib/paperlab-api.ts`). Nothing on these pages can change a bot or place an order; going live stays in
  the operator console at `/`.
- **Cost analyzer** (`src/routes/analyze.tsx`): asks for the dashboard password, then posts the file to the private
  `POST /api/lab/analyze` (Basic auth + `X-PaperLab: 1`). The server calls OpenRouter with its own key
  (`app/ai/analyzer.py`) and streams the Markdown answer back. One analysis runs at a time; files are not stored.
- **Bot names:** PaperLab's own names by default (the ones Telegram and the console use). The sidebar toggle switches
  to the Lovable champion names (`src/lib/champions.ts`), remembered per browser.

```bash
npm install
npm run dev      # http://localhost:5174/lab; /api/public is proxied to Railway (PAPERLAB_API_URL overrides)
npm run build    # tsc + vite build into ../app/dashboard/lab/ (assets served from /static/lab/)
```

Commit `src/routeTree.gen.ts` and the build output with the source change; the Railway image has no Node.
