# Running the stack on this machine

Bare-metal, on the MacBook Pro. Written after an outage that took two hours to
diagnose and whose cause was entirely in configuration: the gateway was
restarted from a terminal that did not have the stack's environment, and five
variables pointing at a Podman network vanished at once.

**The rule this file exists to enforce: nothing the stack needs to start may
live only in a shell.** Every value below belongs in a file.

## Where configuration lives

| file | read by | how |
|---|---|---|
| `gateway/.env` | gateway | `pydantic-settings`, resolved from the module path — **not** sourced, not cwd-dependent |
| `auth-service/.env` | auth-service | same |
| `.env` (repo root) | **podman-compose only** | never read by either service |

That last row has cost time twice. A value in the repo-root `.env` looks
authoritative and is invisible to a bare-metal process. To find out what a
service is *actually* using, ask the service, not a file — see **Diagnosing**.

Because both services read their `.env` by absolute path, their own configuration
needs no `source` and no particular working directory. **Telemetry is the
exception, and the commands below load it.** RM-94 established why: an OTel SDK
reads `os.environ`, and a `.env` parsed by pydantic-settings never reaches it — so
the telemetry variables live in `runtime/telemetry.env` and have to be in the
process environment at launch. A process started without it runs **dark**: no
spans leave it, and `instrument_fastapi` becomes a no-op, so there is no server
span and no `http.route` either.

**Start Redis first — it is infrastructure, and it is a container even here (PRM-191):**

```bash
docker compose -f podman-compose.yml -f compose.baremetal.yml up -d redis
```

That overlay exists because this machine runs **neither** of the two deployments this
repository describes. The services below run on macOS; Redis does not. The base file keeps
Redis internal-only, which is right for the all-container deployment and leaves it
unreachable from a process on the host — so the overlay adds one host binding,
`127.0.0.1:6379:6379`, and nothing else. The all-container path never passes
`-f compose.baremetal.yml` and is unchanged by it.

Skipping it does not stop the stack: the gateway starts, logs
`rate_limit.redis_not_configured_fail_open`, and serves. **It loses two controls silently** —
rate limiting and token revocation — which is the failure this file exists to prevent, so
start it first and check it is there (`docker exec prometheus-redis redis-cli ping`).

```bash
uv run --env-file runtime/telemetry.env uvicorn prometheus_auth.asgi:app    --host 127.0.0.1 --port 9000
```

```bash
uv run --env-file runtime/telemetry.env uvicorn prometheus_gateway.asgi:app --host 127.0.0.1 --port 8020
```

The managers take a second env file each — their own identity (PRM-152) — and
`--env-file` may be repeated:

```bash
uv run --env-file runtime/telemetry.env --env-file runtime/manager/local.env --project runtime/manager/api pmgr-api --config runtime/manager/manager.toml
```

```bash
uv run --env-file runtime/telemetry.env --env-file runtime/manager/lab.env --project runtime/manager/api pmgr-api --config runtime/manager/manager-lab.toml
```

`auth-service/start.sh` also works but binds `0.0.0.0`, which publishes the
identity service to the local network. On a laptop, prefer the line above.

**How to tell, in one request** — and **not** with `/health`:

```bash
curl -si http://127.0.0.1:8020/v1/models -H "Authorization: Bearer $TOKEN" | grep x-trace-id
```

A **32-character hex** id means spans are leaving the process; a **UUID** means
they are not. `TraceIDMiddleware` deliberately advertises its own id rather than an
OTel one nobody could look up, so the format is the signal.

`/health` returns a UUID **either way** and is useless for this: RM-95 suppressed
probe spans at source (they were 57% of the gateway's telemetry), so there is no
span on that route to take an id from. Checking there reads as "dark" on a process
that is exporting perfectly — which happened while writing this section.

The whole stack was running dark on 2026-09-27 because these commands did not load
the file.

## The five secrets

None of them are recoverable once lost — there is no escrow, and reading them
back out of a running process is blocked. If one goes missing the only path is
to regenerate, which means restarting the service that holds it.

| secret | in | generate with | losing it costs |
|---|---|---|---|
| RSA signing keypair | `auth-service/.env` → `AUTH_PRIVATE_KEY_FILE` / `AUTH_PUBLIC_KEY_FILE` | `openssl genrsa -out auth-service/certs/jwt_private.pem 4096` then `openssl rsa -in … -pubout -out auth-service/certs/jwt_public.pem` | every issued token; the gateway picks the new key up from JWKS on its own |
| `AUTH_ADMIN_API_KEY` | `auth-service/.env`, **and the same value** as `AUTH_SERVICE_ADMIN_API_KEY` in `gateway/.env` | `openssl rand -hex 32` | the whole dashboard — see below |
| `SHARE_TOKEN_ENCRYPTION_KEY` | `auth-service/.env` | `openssl rand -hex 32` | stored credential-share secrets become undecryptable (they expire in 1 h, so in practice nothing) |
| OAuth2 client secrets | `gateway/.env` → `MANAGER_CLIENT_ID` / `MANAGER_CLIENT_SECRET` | registered once via `POST /admin/clients` | manager sync; re-register the client |
| Per-node fleet secrets | the environment of each `manager-api` → `PMGR_FLEET_CLIENT_SECRET` | one `POST /admin/clients` per node — see **A node reporting in** | that node stops reporting its own liveness; re-register it |

Keep the private key at `chmod 600`. `auth-service/certs/*.pem`,
`auth-service/.env` and `gateway/.env` are all gitignored — verify with
`git check-ignore <path>` before writing anything into them.

### Why the gateway needs an admin key at all

Two different trust boundaries, and conflating them wastes an afternoon:

```
you ──user+password──> gateway        a scoped JWT: admin:read / admin:write
      gateway ──X-Admin-Key──> auth-service     a shared service credential
```

The login moved to user+password; the hop between the two services did not
change. `auth-service`'s admin router validates `X-Admin-Key` and nothing else —
it issues JWTs but has no code anywhere that verifies one.

So a wrong or missing key does not stop you logging in. It stops the gateway
fetching the data those pages are made of: **Users** (auth-service's
`principals`), **Nodes** (auth-service's `nodes`), and **Instances** and the
**Playground**, which need the node list to know which managers to ask. The
login succeeds and three pages come back empty, which is why this failure reads
as a UI bug.

Removing the shared secret means teaching auth-service to validate the caller's
JWT. That is filed, not done.

## Bare metal or containers — one choice, seven lines

`gateway/.env` and `auth-service/.env` each describe **one** deployment. Every
hostname below is the same decision, and mixing them is the failure mode: the
service starts, reports healthy, and fails on the first call that crosses a
network.

**Redis is the exception to this either/or**, and PRM-191 is why it has its own file: the
bare-metal rows below point at `127.0.0.1:6379`, which only exists if
`compose.baremetal.yml` is in the command. Before that file, the only way to satisfy this
row was a hand-typed `docker run` — a container belonging to no compose project, recreated
by nothing, and invisible to anyone reading this repository. Exactly what the rule at the
top of this file forbids.

| variable | bare metal | Podman |
|---|---|---|
| `JWT_JWKS_URL` | `http://127.0.0.1:9000/.well-known/jwks.json` | `https://auth-service:9000/...` |
| `AUTH_SERVICE_TOKEN_URL` | `http://127.0.0.1:9000/oauth2/token` | `https://auth-service:9000/...` |
| `AUTH_SERVICE_SHARE_URL` | `http://127.0.0.1:9000/share` | `https://auth-service:9000/share` |
| `AUTH_SERVICE_ADMIN_URL` | `http://127.0.0.1:9000/admin` | `https://auth-service:9000/admin` |
| `MANAGER_URL` | `http://127.0.0.1:8090` | `http://manager:8090` |
| `JWT_REVOCATION_REDIS_URL` | `redis://127.0.0.1:6379/0` | `redis://redis:6379/0` |
| `AUTH_DB_URL` | `sqlite+aiosqlite:///<repo>/data/auth-service/auth.db` | `sqlite+aiosqlite:////data/auth.db` |

**`JWT_ISSUER` is not on that list and must not be changed to `127.0.0.1`
reflexively.** An issuer is an identifier, never fetched. It has to equal, string
for string, the `iss` the running auth-service puts in its tokens — which is set
by `AUTH_JWT_ISSUER` in `auth-service/.env`. A mismatch is reported as
`Token signature validation failed`, because the middleware treats any failed
claim check that way, so the message points at the key and the cause is the
issuer.

## A node reporting in

Every node reports its own liveness to the fleet coordinator every 10 s
(PRM-152). **One OAuth2 client per node**, so a node speaks only for itself: the
coordinator refuses a report whose token does not name the node in the path, and
PRM-157's audit trail records which node acted. A single shared credential would
let any node forge liveness for any other.

The coordinator is the exception — it owns `fleet.db`, so it stamps its own row in
process and needs no credential to talk to itself. It still needs to know **which
row is its own**.

Node ids come from the coordinator, which assigns a UUID at registration:

```bash
curl -s http://127.0.0.1:8090/v1/fleet/nodes -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json; [print(n["id"], n["name"]) for n in json.load(sys.stdin)]'
```

Then one client per node, granted exactly two scopes — `fleet:heartbeat` and
`node:<that id>`:

```bash
curl -s -X POST http://127.0.0.1:9000/admin/clients \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"client_name":"node-lab","role":"app",
       "allowed_scopes":["fleet:heartbeat","node:<NODE_ID>"]}'
```

The `client_secret` comes back once and is never retrievable again. Put the three
identity facts in that node's environment — never in `manager.toml`, which this
repository tracks:

```
PMGR_FLEET_NODE_ID=<the UUID above>
PMGR_FLEET_CLIENT_ID=<from the response>
PMGR_FLEET_CLIENT_SECRET=<from the response>
```

Durably, not in a shell — that is what PRM-147 was. A gitignored env file loaded
at launch is enough:

```bash
uv run --env-file runtime/manager/lab.env --project runtime/manager/api \
  pmgr-api --config runtime/manager/manager-lab.toml
```

**On a multi-host fleet, the coordinator's 8090 has to be reachable from the other
nodes — and since PRM-193 it is not, by default.** `podman-compose.yml` binds it to
`127.0.0.1`, because the two consumers named in that file need nothing wider: the
gateway reaches the manager by service name on the internal network, and the `pmgr`
CLI runs on the host. The control plane starts and stops models, registers and deletes
nodes, and launches downloads, and no installer in this repository configures a
firewall — so it ships closed.

When nodes live on separate machines, set this in the **coordinator's** environment:

```
MANAGER_BIND_IP=0.0.0.0        # or the specific interface the fleet reaches it on
```

Prefer the specific address over `0.0.0.0`. Either way the exposure is then declared
in one deployment's environment rather than shipped to every deployment that never
needed it. `gateway/tests/check_compose_ports.py`, which runs in `pre-push`, reports
what the repository's own files bind — it cannot see what you export, and does not
try to.

Missing any of the three disables the heartbeat and logs
`fleet.heartbeat_disabled` naming what is absent; the node keeps serving
inference. `fleet.heartbeat_started` on startup and
`fleet.heartbeat_token_renewed` are the two lines that say it is working, and
`last_seen_at` on the node's row is the fact itself.

`[fleet] sweep` in the coordinator's `manager.toml` is the probe from before nodes
could authenticate. Leave it on until every node reports in, then turn it off:
while a probe also stamps `last_seen_at`, "the node is down" and "I could not
reach it" stay indistinguishable — which is the whole reason nodes report in.

## Diagnosing

Ask the running service, never a file:

```bash
# what issuer is actually being minted — the only authoritative answer
TOKEN=$(curl -s -X POST http://127.0.0.1:8020/admin/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"…","password":"…"}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
python3 -c "import base64,json,sys;p=sys.argv[1].split('.')[1];print(json.loads(base64.urlsafe_b64decode(p+'='*(-len(p)%4))))" "$TOKEN"

# which database and key files a process really opened
lsof -p $(pgrep -f prometheus_auth.asgi) | grep -E '\.db$|\.pem$'

# does the admin key match
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-Admin-Key: $(grep '^AUTH_SERVICE_ADMIN_API_KEY=' gateway/.env | cut -d= -f2)" \
  http://127.0.0.1:9000/admin/nodes
```

### Symptoms and what they actually mean

| symptom | cause |
|---|---|
| Login works, every other call `500` | `JWT_JWKS_URL` unreachable. Login is exempt from JWT validation, so it keeps working and hides this |
| `401 Token signature validation failed` | usually **not** the key — `JWT_ISSUER` or `JWT_AUDIENCE` does not match the token |
| Login works, Users/Nodes/Instances empty, `403` from `/admin/api/*` | `AUTH_SERVICE_ADMIN_API_KEY` does not match auth-service's `AUTH_ADMIN_API_KEY` |
| `/admin/api/*` all `404` | `ADMIN_DASHBOARD_ENABLED` is not `true` |
| Healthy, 10 models, then 0 models a minute later | the node registry is unreachable. Since PRM-146 the catalog is retained and the log says `manager_sync.node_registry_never_reached` |
| `400 unknown-model` for a model `GET /v1/models` just listed | the same thing, before PRM-146 |
| Gateway refuses to start naming three variables | `ADMIN_DASHBOARD_ENABLED=true` with `AUTH_SERVICE_ADMIN_URL` or `AUTH_SERVICE_ADMIN_API_KEY` blank. Deliberate: a loud refusal beats a gateway serving zero models |
| `x-trace-id` is a UUID on a real route (not `/health`) | The process was launched without `runtime/telemetry.env`. Nothing it emits reaches Argus — no spans, no server span, no `http.route`. See the launch commands above |
| `x-trace-id` is a UUID on `/health` | Nothing. RM-95 suppresses probe spans, so that route has no id to advertise whether tracing is on or off |

## Verifying a restart

All of these, not just the first:

```bash
curl -s -o /dev/null -w 'auth   %{http_code}\n' http://127.0.0.1:9000/health
curl -s -o /dev/null -w 'gw     %{http_code}\n' http://127.0.0.1:8020/health
curl -s http://127.0.0.1:8020/v1/models | python3 -c 'import json,sys;print("models",len(json.load(sys.stdin)["data"]))'
for e in nodes instances users; do
  curl -s -o /dev/null -w "$e %{http_code}\n" -H "Authorization: Bearer $TOKEN" \
    http://127.0.0.1:8020/admin/api/$e
done
grep -o 'manager_sync\.[a-z_]*' <gateway log> | sort | uniq -c
```

The last line is the one that matters. `refreshed` and `token_renewed` alone
means the node registry is being read live. Any `unreachable` or
`never_reached` means the catalog you are looking at came from the snapshot on
disk and is only as current as the last good poll.

## Before killing a process

Check whether what it needs to come back exists on disk:

```bash
test -f auth-service/.env && test -f gateway/.env && echo "config en ficheros"
lsof -p <pid> | grep -E '\.db$|\.pem$'
```

A process holding configuration that no file records is not restartable, and it
will not tell you so until it is gone. That is how this outage started.
