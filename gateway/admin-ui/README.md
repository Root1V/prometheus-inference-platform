# Prometheus Admin Dashboard

React + Vite + TypeScript single-page app for the Prometheus gateway admin dashboard
(RM-10 phase 1). It is a pure static SPA — no server of its own — that calls the
gateway's `/admin/api/*` JSON REST endpoints and is served by the Python gateway
itself at `/admin`.

## Stack

React 19, Vite, TypeScript, Tailwind CSS v4, `react-router-dom` (HashRouter),
`@tanstack/react-query`, `axios`, `lucide-react`. ESLint 9 flat config +
typescript-eslint.

## Primary workflow: build

```sh
npm install
npm run build
```

This runs `tsc -b && vite build` and writes the static output to
`../src/prometheus_gateway/admin/static` (relative to this directory) — exactly
where `gateway/src/prometheus_gateway/main.py` expects it when
`ADMIN_DASHBOARD_ENABLED=true`. There is no separate deploy step: the gateway
serves that directory directly via `StaticFiles(html=True)` mounted at `/admin`.

This repo's `.githooks/pre-push` will be wired to run `npm run lint` and
`npm run build` in this directory, so keep both green.

```sh
npm run lint
```

## Local development (optional polish)

`npm run dev` starts a standalone Vite dev server. It has no backend of its
own, so API calls to `/admin/api/*` need somewhere to go — wire a dev proxy in
`vite.config.ts` (`server.proxy`) pointing `/admin/api` at a running gateway
instance (`ADMIN_DASHBOARD_ENABLED=true`). Not configured out of the box —
`npm run build` against a real gateway deployment is the supported flow.
`npm run dev` is convenience-only for UI iteration against a proxied backend.

## Auth flow

The login form collects a client ID and client secret (operators register an
admin client via the auth-service's own admin UI beforehand, granting
`admin:read admin:write`) and POSTs them to the gateway's own
`POST /admin/api/auth/login` — the *only* `/admin/api/*` route that doesn't
require a Bearer token, since obtaining one is the whole point. The gateway
proxies that request server-side to its configured `AUTH_SERVICE_TOKEN_URL`
(client_credentials grant) and returns the resulting JWT. The SPA never calls
the auth-service directly: a real cross-origin browser request to it would be
blocked by CORS (auth-service doesn't set CORS headers, and doesn't run on
the gateway's origin), and routing through the gateway also means the SPA
never needs to know the auth-service's URL at all.

On success the access token is kept in memory and in `sessionStorage`
(cleared on tab close, survives a page refresh). Every other `/admin/api/*`
request carries `Authorization: Bearer <token>`; a 401 response clears the
token and redirects to `/#/login`.
