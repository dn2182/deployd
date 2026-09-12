# deployd admin UI

React/Vite administration console for app registration, secret rotation,
deployment history, redeploys, rollbacks, cancellation, freezing, and step
status on demand.

Applications use a searchable, ten-per-page list with one detail card. The
card shows the live app status (current release, frozen state, queue, last
deploy and health), and accessible tabs for website connection, retained
versions, and GitHub Actions setup. Deploy history filters by app and status,
searches by commit prefix, and pages with Load more. An Audit log tab reads
`/admin/audit`.

While any deploy is queued or running the console polls `/admin/deploys` and
the expanded deploy detail every two seconds and toasts terminal results; when
idle it polls every 15 seconds so CI-started deploys still appear, and it
pauses while the tab is hidden. Refresh is always available (button or `r`). Other
shortcuts: `/` focuses the application search, Esc clears it.

Styling is Tailwind v4; `src/index.css` holds the design tokens and the
`data-theme` dark variant. Dialogs use the native `<dialog>` element.

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
The admin token is retained only in browser session storage, is cleared by
Log out, and is discarded when the tab session ends.
