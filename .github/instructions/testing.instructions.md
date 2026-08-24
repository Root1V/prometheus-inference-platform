---
description: "Use when writing, updating, or reviewing tests for any module in Prometheus: gateway, auth-service, manager, or runtime scripts."
applyTo: "**/tests/**"
---

# Testing — Guidelines

## Test Framework & Tools

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner for all Python modules |
| `pytest-asyncio` | Async test support (`@pytest.mark.asyncio`) |
| `httpx.AsyncClient` + `ASGITransport` | HTTP client for FastAPI endpoint tests |
| `fastapi.testclient.TestClient` | Sync client for simple endpoint tests |
| `unittest.mock` (`MagicMock`, `patch`) | Mocking external dependencies |
| `tmp_path` (pytest fixture) | Isolated filesystem per test |

## Naming Convention

Every test name must reference the AC it validates:

```python
def test_jwt_expired_returns_401_AC2():  # memory/specs/002-jwt-authentication-middleware.md
    ...

async def test_rate_limit_per_user_AC3():  # memory/specs/007-rate-limiting-and-throughput.md
    ...
```

## Test Structure

- One test file per module: `test_<module>.py`.
- Group related tests with a descriptive comment block — no unnecessary classes.
- Use `conftest.py` for shared fixtures — never duplicate fixture logic across test files.
- Fixtures that generate RSA keys or start services use `scope="session"` to avoid regenerating per test.

## Fixture Patterns

```python
# Filesystem isolation — always use tmp_path for file-based tests
@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.yaml"

# Override FastAPI dependencies for auth tests
app.dependency_overrides[require_auth] = lambda: {"sub": "test-user", "scope": "inference:read"}

# Reset global caches between tests (e.g. JWKS cache)
@pytest.fixture(autouse=True)
def reset_jwks_cache():
    _reset_cache_for_testing()
    yield
```

## Coverage Requirements

- Minimum **80% coverage** enforced by the pre-push hook (`--cov-fail-under=80`).
- Every acceptance criterion in a spec must have at least one test.
- Edge cases to always cover: missing auth, expired token, wrong scope, invalid input.

## Running Tests

```bash
# Gateway
uv run pytest gateway/tests/ -v --cov=gateway/src --cov-fail-under=80

# Auth-service
(cd auth-service && uv run pytest tests/ -v --cov=src --cov-fail-under=80)

# Manager
(cd runtime/manager && uv run pytest tests/ -v --cov=src --cov-fail-under=80)

# Runtime scripts
bash runtime/tests/test_runtime_scripts.sh
```

## Rules

- Never make real HTTP calls to llama.cpp, Redis, or external services in unit tests — always mock.
- Never hardcode ports or file paths — use `tmp_path` and fixture-provided values.
- Async tests must use `@pytest.mark.asyncio` and `AsyncClient` with `ASGITransport`.
- Tests must be hermetic — no shared mutable state between test functions.
- If a test requires network or a running service, mark it `@pytest.mark.integration` and exclude from the default run.
