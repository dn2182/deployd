# deployd admin UI

React/Vite administration console for app registration, secret rotation,
deployment history, redeploys, and live step status.

```bash
pnpm install --frozen-lockfile
pnpm dev       # proxies /api to http://127.0.0.1:8300
pnpm lint
pnpm test
pnpm build
```

For production, serve `dist/` from the same HTTPS origin used for the console
and proxy `/api/*` to deployd after removing the `/api` prefix. Keep the UI and
`/admin` API behind localhost, a private network, or an identity-aware proxy.
The admin token is retained only in browser session storage and is cleared when
the tab session ends.
