# How to work on the dashboard UI

Set up the TypeScript dashboard for development, add to it, and keep the
contract with the Python API intact. By the end you will have hot reload against
a live backend and know the two rules that will otherwise bite you.

## Prerequisites

- Node 22+ and npm
- A source checkout with the Python package installed:
  `pip install -e ".[live,fastapi,flask,dashboard]"`
- Redis, if you want the Live tab to show anything

## Set up

```bash
cd gui
npm ci
```

Then run the backend and the dev server in two terminals:

```bash
# terminal 1 — the API on :8500
microguard dashboard

# terminal 2 — Vite with hot reload on :5173
cd gui && npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to `127.0.0.1:8500`
(`vite.config.ts`), so the UI is hot-reloaded while the API is the real thing.

Do **not** develop against `http://localhost:8500` — that serves the last
*built* bundle, so your edits will not appear and you will spend twenty minutes
wondering why.

## The layout

```
gui/src/
  api/
    schemas.ts        zod schemas — the contract with Python
    client.ts         fetch wrappers; every response parsed through a schema
    __fixtures__/     real captured API payloads, used by the schema tests
  components/         shared across tabs: ScoreRuler, SessionTable, Badges, …
  tabs/
    LiveTab.tsx  ScanTab.tsx  ModelTab.tsx
    logic.ts          the arithmetic, kept out of components so it is testable
  App.tsx             three-tab shell and hash routing
```

Two conventions worth following because the tests assume them:

**Arithmetic goes in `tabs/logic.ts`, not in a component.** Threshold re-slicing,
fail-open counting and feature-influence ranking are all checked against
hand-worked numbers in `logic.test.ts`. A calculation inside JSX can only be
tested through a rendered table.

**Every response is parsed through a zod schema** in `client.ts`. Nothing calls
`fetch` directly from a component.

## Rule 1: the fixture contract

`gui/src/api/__fixtures__/` holds **real payloads captured from the running
Python API**, and `schemas.test.ts` parses every one through the schema the app
actually uses. That is what stops the two languages drifting.

If you change a Python response shape, recapture:

```bash
python gui/scripts/capture_fixtures.py
cd gui && npm test
```

Then commit the changed fixtures. **CI fails if you do not** — the `gui` job
recaptures them and diffs against what is committed.

The capture is deterministic on purpose: wall-clock fields are pinned, and the
capture-only session store anchors its clock to the log's own timestamps rather
than `time.time()`. Without that, `duration` and every timing feature derived
from it changed on every run and the fixtures could never match.

Fixtures cover the awkward shapes too, including `scan-error.json` — the variant
that drops `threshold`, `model_used`, `integration_count` and `summary` and adds
`error`. If you add a response variant, add a fixture for it.

## Rule 2: the build ships inside the wheel

```bash
npm run build
```

This runs `tsc -b`, then Vite, then copies `gui/dist/` into
`microguard/dashboard/static/` — which is what the Python server serves and what
ships in the wheel. Running the dashboard never needs Node; building it does.

Two consequences:

- **`microguard/dashboard/static/` is committed.** Rebuild and commit it with any
  UI change, or a `pip install` ships a stale UI.
- **Asset filenames are stable, not content-hashed** (`vite.config.ts`).
  Content hashes would produce a two-file rename in every diff for a one-line CSS
  tweak, and nothing serves these from a CDN.

## Add a tab

1. Create `src/tabs/YourTab.tsx`. Put any arithmetic in `logic.ts` with tests.
2. Register it in `App.tsx`'s `TABS` array — hash routing and the rail pick it up
   automatically.
3. If it needs new data, add the endpoint to `client.ts` with a zod schema in
   `schemas.ts`, add a fixture, and extend `capture_fixtures.py`.
4. Write tests for the three states a person actually lands in: nothing loaded
   yet, nothing to show, and something went wrong. `tabs/tabs.test.tsx` has the
   pattern, including a `stub()` helper for per-route status codes.

## Styling

Design tokens are CSS variables in `src/index.css`, taken from
`report.py`'s HTML report so the dashboard and the generated report look like one
product. Use the tokens (`var(--red)`, `var(--card-border)`) rather than literal
hex.

No web fonts. This UI is served from localhost, often on a machine with no
internet, and a font that fails to load is worse than one never requested. The
monospace face carries the personality instead — every value here is machine
output.

Grid splits that must collapse on a narrow screen use the `.split-2` /
`.split-3` classes rather than inline `gridTemplateColumns`, because inline
styles cannot carry a media query.

## Run the checks

```bash
cd gui
npx tsc -b       # strict, noUncheckedIndexedAccess, no unused locals
npm test         # vitest + Testing Library
npm run build    # also refreshes microguard/dashboard/static/
```

All three run in CI's `gui` job, plus the fixture drift check.

## Verification

Confirm your setup is wired end to end:

```bash
cd gui && npm test && npx tsc -b && npm run build
cd .. && microguard dashboard
```

Open `http://127.0.0.1:8500`, go to the Scan tab, pick `sample_access.log`, and
confirm two sessions appear. That exercises the built bundle, the API, and the
engine in one pass.

## Troubleshooting

**Edits do not appear** — you are on `:8500` (the built bundle) instead of
`:5173` (the dev server).

**`API fixtures are stale` in CI** — run `python gui/scripts/capture_fixtures.py`
and commit the result.

**Fixtures change every time you capture them** — something in the payload is
wall-clock or otherwise nondeterministic. Pin it in `_stabilize()` in the capture
script, or make the capture anchor it, rather than committing the churn.

**`Cannot find module` after pulling** — `npm ci`.

**Live tab empty while the Scan tab works** — the dashboard has no Redis, or
nothing is scoring. Check `/api/health` for `redis_connected`, and confirm
`microguard serve` or the middleware is running against the same Redis.

**tsc errors about `vite.config.ts` and `test`** — the config imports
`defineConfig` from `vitest/config`, not `vite`. Only the vitest one knows about
the `test` key.

## Related

- [Run the dashboard](howto-run-the-dashboard.md) — using it rather than building it
- [Why the dashboard is built this way](explanation-dashboard-design.md)
- [Live API reference](reference-live-api.md) — every endpoint the client calls
- [CONTRIBUTING.md](../CONTRIBUTING.md)
