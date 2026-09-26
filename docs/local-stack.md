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

Because both services read their `.env` by absolute path, starting them needs no
`source` and no particular working directory:

```bash
.venv/bin/python .venv/bin/uvicorn prometheus_auth.asgi:app    --host 127.0.0.1 --port 9000
.venv/bin/python .venv/bin/uvicorn prometheus_gateway.asgi:app --host 127.0.0.1 --port 8020
```

`auth-service/start.sh` also works but binds `0.0.0.0`, which publishes the
identity service to the local network. On a laptop, prefer the line above.

## The four secrets

None of them are recoverable once lost — there is no escrow, and reading them
back out of a running process is blocked. If one goes missing the only path is
to regenerate, which means restarting the service that holds it.

| secret | in | generate with | losing it costs |
|---|---|---|---|
| RSA signing keypair | `auth-service/.env` → `AUTH_PRIVATE_KEY_FILE` / `AUTH_PUBLIC_KEY_FILE` | `openssl genrsa -out auth-service/certs/jwt_private.pem 4096` then `openssl rsa -in … -pubout -out auth-service/certs/jwt_public.pem` | every issued token; the gateway picks the new key up from JWKS on its own |
| `AUTH_ADMIN_API_KEY` | `auth-service/.env`, **and the same value** as `AUTH_SERVICE_ADMIN_API_KEY` in `gateway/.env` | `openssl rand -hex 32` | the whole dashboard — see below |
| `SHARE_TOKEN_ENCRYPTION_KEY` | `auth-service/.env` | `openssl rand -hex 32` | stored credential-share secrets become undecryptable (they expire in 1 h, so in practice nothing) |
| OAuth2 client secrets | `gateway/.env` → `MANAGER_CLIENT_ID` / `MANAGER_CLIENT_SECRET` | registered once via `POST /admin/clients` | manager sync; re-register the client |

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
