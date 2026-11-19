# Using house-backend

house-backend is the backend code the house apps share: passkey login,
sessions, API tokens, the SQLite engine and the MCP plumbing. An app depends
on one released version of it and keeps its own domain: its tables beyond the
five auth tables, its services and routers, its migrations, its MCP tools and
what it redacts (PLAN D-18). The decisions behind it are in [PLAN.md](PLAN.md).

- [Install and pin](#install-and-pin)
- [Changing house-backend while working on an app](#changing-house-backend-while-working-on-an-app)
- [A new app](#a-new-app)
- [Modules](#modules): [settings](#house_backendsettings), [db](#house_backenddb), [fields](#house_backendfields), [auth](#house_backendauth), [mcp](#house_backendmcp), [app](#house_backendapp), [testing](#house_backendtesting)
- [The security behaviour](#the-security-behaviour)
- [Releasing](#releasing)

## Install and pin

An app's `backend/pyproject.toml` names one release by the URL of its wheel
(PLAN D-4). With the MCP plumbing:

```toml
[project]
dependencies = [
    "house-backend[mcp] @ https://github.com/kenjabber/house-backend/releases/download/v1.0.0/house_backend-1.0.0-py3-none-any.whl",
    # … the app's own dependencies
]

[project.optional-dependencies]
test = ["house-backend[test] @ https://github.com/kenjabber/house-backend/releases/download/v1.0.0/house_backend-1.0.0-py3-none-any.whl"]

[tool.hatch.metadata]
allow-direct-references = true
```

An app without an MCP server drops `[mcp]`. The release is public, so a
Docker build downloads it with no token. Upgrading is a change in the app:
bump both URLs, `pip install -e 'backend[test]'`, run the app's tests, commit.

Python 3.13 or newer. Core dependencies: `fastapi`, `sqlalchemy>=2`,
`pydantic-settings`, `webauthn>=2`. `[mcp]` adds `mcp>=2.2,<3` and `httpx`;
`[test]` adds `pytest`, `pytest-asyncio` and `httpx`.

## Changing house-backend while working on an app

A change is made here first (PLAN D-24): code, tests, this file, a release.

1. In the app's virtualenv: `pip install -e ~/develop/house-backend`. The app
   now runs against your working copy.
2. Make the change here, with its tests; `pytest` and `ruff check` pass.
3. Commit and push here, then `./release.sh X.Y.Z` (see [Releasing](#releasing)).
4. In the app, bump the pin to `vX.Y.Z` and reinstall from the pin
   (`pip install -e 'backend[test]'`), so the app's tests run against what
   the server will download. Then commit the app.

A security fix is a patch release, and every app's pin is bumped in the same
piece of work.

## A new app

The two example apps in [`tests/apps/`](tests/apps) are complete, small apps
on house-backend: [`single.py`](tests/apps/single.py) (one account, API
tokens) and [`multi.py`](tests/apps/multi.py) (accounts made by an admin, a
column of its own on the user, an MCP tool, a migration). In an app the parts
live in the usual files:

`config.py`

```python
from house_backend.settings import HouseSettings


class Settings(HouseSettings):
    SITE_NAME: str = "Example"
    WEBAUTHN_RP_NAME: str = "Example"
    WEBAUTHN_ORIGIN: str = "http://localhost:5173"
    SQLITE_PATH: str = str(Path.home() / ".local" / "share" / "example" / "example.db")


settings = Settings()
```

`persistence/schema.py`

```python
from house_backend.auth import (
    BOOTSTRAP_USER_ID,
    ApiTokenMixin,
    UserMixin,
    UserSessionMixin,
    WebAuthnChallengeMixin,
    WebAuthnCredentialMixin,
)


class Base(DeclarativeBase):
    pass


class User(UserMixin, Base):
    pass


class WebAuthnCredential(WebAuthnCredentialMixin, Base):
    pass


class UserSession(UserSessionMixin, Base):
    pass


class WebAuthnChallenge(WebAuthnChallengeMixin, Base):
    pass


class ApiToken(ApiTokenMixin, Base):
    pass
```

`api/dependencies/auth.py`

```python
from house_backend.auth import AuthModels, HouseAuth

auth = HouseAuth(
    app_name="example",
    settings=settings,
    models=AuthModels(User, WebAuthnCredential, UserSession, WebAuthnChallenge, ApiToken),
    multi_user=True,
)
# Re-exported under the names the routers import.
get_db_session = auth.get_db_session
require_user = auth.require_user
require_admin = auth.require_admin
require_human = auth.require_human
require_human_admin = auth.require_human_admin
```

`main.py`

```python
from house_backend.app import add_cors, mount_mcp, prepare_database
from house_backend.mcp.endpoint import mcp_lifespan

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.warn_if_auth_disabled(logger)
    prepare_database(app, settings.SQLITE_PATH, Base, migrate=run_sqlite_migrations)
    async with mcp_lifespan(app, build_server, settings):
        yield


app = FastAPI(title="example", lifespan=lifespan)
add_cors(app, settings)
app.include_router(auth.router(), prefix="/api")
app.include_router(things_router.router, prefix="/api")
mount_mcp(app, auth, "MCP requires an API token (Settings → API tokens)")
```

`tests/conftest.py`

```python
pytest_plugins = ["house_backend.testing"]


@pytest.fixture
def house_app():
    return main.app


@pytest.fixture
def house_auth():
    return dependencies.auth


@pytest.fixture
def house_base():
    return schema.Base
```

The app's `CLAUDE.md` gets the "Shared backend" section of PLAN D-22.

## Modules

### `house_backend.settings`

`HouseSettings`, the base of the app's `Settings` (a pydantic-settings class
reading `.env`). The app subclasses it, overrides its own defaults and adds
its own settings, including the database path (the apps name it differently).

| Setting | Default | |
|---|---|---|
| `SITE_NAME` | `""` | Shown on the sign-in page; in `/auth/status` of a multi-user app |
| `BUILD_VERSION` | `"dev"` | For apps that report a build |
| `WEBAUTHN_RP_ID` | `"localhost"` | The passkeys' relying party: the app's host name |
| `WEBAUTHN_RP_NAME` | `""` | Shown by the authenticator |
| `WEBAUTHN_ORIGIN` | `"http://localhost:5173"` | Comma-separated allowed origins; also the CORS origins |
| `SESSION_COOKIE_SECURE` | `False` | `True` behind HTTPS |
| `SESSION_LIFETIME_DAYS` | `30` | |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `20` | Per client address, on the sign-in routes |
| `AUTH_DISABLED` | `False` | Development only: every request is the bootstrap admin |
| `MCP_ALLOWED_HOSTS` | `""` | Comma-separated extra Host names `/mcp` answers |

Properties: `webauthn_allowed_origins` and `mcp_extra_hosts` (the two lists).

### `house_backend.db`

```python
from house_backend.db import create_sqlite_engine, snapshot, backup_bytes, restore_bytes, RestoreError

engine = create_sqlite_engine(settings.SQLITE_PATH)  # NullPool, WAL, foreign keys, busy timeout
snapshot(settings.SQLITE_PATH, backups / "app-2026-01-01.db")  # a consistent copy, mode 600
data = backup_bytes(settings.SQLITE_PATH)  # the same, as bytes (a download)
restore_bytes(settings.SQLITE_PATH, upload, required_tables={"user", "things"})  # RestoreError if not valid
```

Never copy a WAL database's file: the copy can miss what is still in the
`-wal` file. These use SQLite's online backup API. `restore_bytes` refuses a
file that is not SQLite or lacks a required table, checkpoints the live
database, copies the upload's pages over it (the engine keeps working) and
makes the file mode 600.

Migration helpers for the app's own migrations, on a SQLAlchemy connection:
`table_columns(conn, "things")` (a set of names, empty if no such table) and
`table_exists(conn, "things")`.

### `house_backend.fields`

```python
from house_backend.fields import PLAIN, Plain, UTCDateTime


class ThingResponse(BaseModel):
    name: Plain[str]  # x-house: plain in the JSON schema
    note: Annotated[str | None, PLAIN]
    created: UTCDateTime  # naive UTC from SQLite leaves as "…Z"
```

`UTCDateTime` gives a naive value the UTC zone and converts an aware one
(PLAN D-15). `Plain[T]` marks a field `x-house: plain`: it holds nothing an
app withholds from an agent (D-16). Every scalar field of the shared auth
responses carries the marker; a field holding a model does not (its own
fields are classified, the app's extension fields with their own markers).
`house_marker(property_schema)` reads it. A redactor that trusts the marker
reads it on scalar fields only.

### `house_backend.auth`

The tables (PLAN D-10): the app declares `User`, `WebAuthnCredential`,
`UserSession`, `WebAuthnChallenge` and `ApiToken` on its own `Base` from
`UserMixin`, `WebAuthnCredentialMixin`, `UserSessionMixin`,
`WebAuthnChallengeMixin` and `ApiTokenMixin`. The tables are exactly the apps'
existing ones (`tests/test_auth_models.py` pins their `.schema`). The mixin
columns come first; an app's own columns (NestEgg's `household_id`) come
after them. `BOOTSTRAP_USER_ID` is the all-zero UUID the first account takes
over.

`HouseAuth(app_name=, settings=, models=AuthModels(…), multi_user=False,
api_tokens=True, extension=None)` configures everything else. `app_name`
names the session cookie `<app>_session`, the via header `x-<app>-via`, the
token prefix `<app>_` and the `/mcp` realm. Nothing is module-level: two apps
(or test apps) in one process do not share state.

**Options** (PLAN D-12):

| Option | Off | On |
|---|---|---|
| `multi_user` | One account: the first registrant; `register/*` answers 409 once a passkey exists (more passkeys come through `/auth/credentials`); usernames are kept as typed; `/auth/status` has no `site_name` and answers from the cookie only | The first registrant bootstraps the admin; an admin creates every later account and its owner registers a passkey for it; usernames are lower-cased and limited to letters, digits and `. _ -`; the five `/auth/users` routes; `/auth/status` carries `site_name` and also recognises a token |
| `api_tokens` | Cookie sessions only; no token routes; `/mcp` cannot be mounted | Bearer tokens accepted by every `require_*`; the three `/auth/tokens` routes |

`GET /auth/users` is open to an admin's API token (an agent needs the ids it
grants access to); creating, changing and deleting accounts, and resetting
passkeys, are human-only.

**What it provides:**

| Name | What |
|---|---|
| `auth.router()` | The `/auth` `APIRouter`; include it under `/api` |
| `auth.get_db_session` | The session dependency (`app.state.SessionFactory`) |
| `auth.require_user` | 401 unless a valid token, the `AUTH_DISABLED` stand-in or a live session |
| `auth.require_admin` | 403 unless the user is an admin (always passes under `AUTH_DISABLED`) |
| `auth.require_human` | 403 for an agent's request (a token, or the via header) |
| `auth.require_human_admin` | Both of the above |
| `auth.require_actor` | The acting user's name for audit rows, `"<name> (agent)"` for an agent |
| `auth.current_user_or_none(request, session)` | The user or `None`; never raises |
| `auth.is_agent_request(request)`, `auth.bearer_token(request)` | |
| `auth.SESSION_COOKIE_NAME`, `auth.VIA_HEADER`, `auth.VIA_MCP` | The names |
| `auth.service` | Passkeys, sessions and accounts: `is_bootstrapped`, `get_user_by_username`, `create_session`, `take_over_bootstrap_user`, `create_user`, `list_all_users`, `delete_user_credentials`, … |
| `auth.tokens` | `create_token(session, user_id=, name=)` (row and plaintext), `list_tokens`, `user_for_token` |
| `auth.warn_if_auth_disabled(logger)` | The start-up warning |

**The routes** (under `/api/auth`): `GET status`, `POST register/begin`,
`POST register/complete`, `POST login/begin`, `POST login/complete`,
`POST logout`, `GET me`, `GET credentials`, `POST credentials/begin`,
`POST credentials/complete`, `PATCH credentials/{credential_id}`,
`DELETE credentials/{credential_id}`, `GET sessions`,
`DELETE sessions/{session_id}`; with `api_tokens`: `GET tokens`,
`POST tokens`, `DELETE tokens/{token_id}`; with `multi_user`: `GET users`,
`POST users`, `PATCH users/{user_id}`, `DELETE users/{user_id}`,
`POST users/{user_id}/reset-passkeys`.

**What an app adds to a user** is a `UserExtension` subclass, passed as
`extension=`:

```python
class UserTeam(BaseModel):
    team_id: int


class Teams(UserExtension):
    user_fields = UserTeam  # on every user response (me, status, register, login, users)
    detail_fields = UserTokens  # on the admin's user detail only
    create_fields = UserTeam  # accepted by POST /auth/users
    patch_fields = UserTeamChange  # accepted by PATCH /auth/users/{id}

    def detail_values(self, session, user):  # computed values (default: user attributes)
        return {"token_count": count_tokens(session, user.id)}

    def new_user_values(self, session, values):  # check, raise HTTPException(422), return columns
        ...
    def update_user(self, session, user, values):  # only the patch fields the request set
        ...
    def bootstrap_values(self, session):  # columns for rows the library creates
        return {"team_id": first_team(session).id}
```

The extension's fields carry their own `x-house` markers, or none.

### `house_backend.mcp`

Behind the `[mcp]` extra.

```python
from house_backend.mcp.catalog import Catalog
from house_backend.mcp.endpoint import mcp_lifespan
from house_backend.mcp.redact import Redactor
from house_backend.mcp.rest import RestCaller

CATALOG = Catalog(TOOL_OPERATIONS, EQUIVALENT, HUMAN_ONLY)  # the app's data, in its catalog.py
redactor = Redactor(SECRET_FIELDS, SECRET_PATTERN, placeholder_prefixes=("key-ref-",))
rest = RestCaller(auth, CATALOG, redact=lambda app, method, template, data: redactor.redact(data))


async def _api(ctx, method, template, **kwargs):  # in the app's tools.py
    request = ctx.request_context.request
    return await rest.call(request.app, request.headers.get("authorization"), method, template, **kwargs)
```

- **endpoint**: `mcp_lifespan(app, build_server, settings)` builds a fresh
  server at each start-up and serves it; `McpEndpoint(auth, unauthorized_detail)`
  is the `/mcp` route (`house_backend.app.mount_mcp` adds it).
  `transport_security(settings)` is the Host/Origin allow-list.
- **rest**: `RestCaller.call(app, authorization, method, template, path=,
  query=, body=, content=, raw=)` calls one REST operation in-process
  (`body` is sent as JSON, `content` as raw bytes, for an upload). It refuses an
  operation the catalog does not expose, marks the request with the via
  header, turns a 4xx/5xx into a `ToolError` with the API's `detail`, and
  passes the result (the text, when `raw`) through the redaction hook
  `(app, method, template, data) -> data` (the app, for a redactor built from
  its OpenAPI document).
- **catalog**: `Catalog(tool_operations, equivalent, human_only,
  human_only_enforced=None)` with `exposed_operations()` and `tool_names()`.
  `human_only_enforced` defaults to every human-only operation outside
  `register`, `login` and `logout`. A human-only operation is never exposed, even
  when a tool names it (a generic record tool over a resource with one
  human-only operation). An operation is `(METHOD, path template)`.
- **redact**: `Redactor(secret_fields, pattern, placeholder_prefixes)` with
  `redact(value)`, `redact_text(text)` and `contains_marker(value)`;
  `contains_redaction_marker(value)` and `REDACTED` (`"<redacted>"`). Each
  alternative of `pattern` has the named groups `<p>label`, `<p>sep` and
  `<p>value` (one prefix `<p>` per alternative, empty allowed): the value is
  replaced, empty values and placeholders pass through.

### `house_backend.app`

Helpers a lifespan and `main.py` call; not a function that builds the app
(PLAN D-18).

| Helper | Does |
|---|---|
| `prepare_database(app, path, Base, migrate=None)` | Makes the directory, opens the engine, `create_all`, runs `migrate(engine)`, makes the file mode 600, sets `app.state.engine` and `app.state.SessionFactory`; returns the engine |
| `add_cors(app, settings)` | CORS for the WebAuthn origins, with credentials |
| `mount_mcp(app, auth, unauthorized_detail)` | Adds the raw `/mcp` route |
| `build_info(build_file)` | The running commit: from `build_file` when a `git archive` filled its `$Format:%H$` and `$Format:%cI$`, else from git; `commit`, `short`, `committed_at`, `dirty`, `source`, `started_at`; read once per process, `build_info.cache_clear()` for tests |

### `house_backend.testing`

A pytest plugin: `pytest_plugins = ["house_backend.testing"]` in the app's
root `conftest.py`, with the app's `house_app`, `house_auth` and `house_base`
fixtures (see [A new app](#a-new-app)). Optional overrides: `house_db_setting`
(the settings attribute with the database path, default `SQLITE_PATH`) and
`house_user_values` (the app's own column values for seeded users).

| Fixture | |
|---|---|
| `engine`, `session` | A throw-away database (the setting is pointed at it, so the app's lifespan opens it too) |
| `client` | A `TestClient` with the lifespan run |
| `seed_user(user_id=, username=, is_admin=, **columns)` | A user row |
| `login_as(client, user)` | Gives the client a session cookie for `user` |
| `admin`, `member` | `alex` (admin) and `sam` |
| `auth_client`, `member_client` | Clients signed in as them |
| `api_token` | The plaintext of a token of `admin` |
| `mcp` | An `McpClient` with that token: `tools()`, `call(tool, **args)`, `call_error(…)`, `post(method, params, headers=)` |
| `fake_passkeys` | `FakePasskeys`: replaces the `webauthn` library's two verify functions and builds the browser's answers (`registration(options, name)`, `assertion(options, name)`, `credential_id(name)`); the challenges, credentials, counters and checks around them are real |

`AUTH_DISABLED` is pinned off for every test and the rate limiter is reset.

`seed_user(auth, session, user_id=, username=, is_admin=, **columns)` and
`login_as(auth, client, session, user)` are also plain functions, so an app
keeps its own helpers' signatures and its tests' calls unchanged:

```python
pytest.register_assert_rewrite("house_backend.testing")  # before importing it

from house_backend import testing  # noqa: E402


def seed_user(session, user_id="test-admin", username="ken", is_admin=True):
    return testing.seed_user(auth, session, user_id, username, is_admin)


def login_as(client, session, user):
    return testing.login_as(auth, client, session, user)


@pytest.fixture
def admin(session):  # overrides the shared one ("alex")
    return seed_user(session)
```

A conftest that imports `house_backend.testing` calls
`pytest.register_assert_rewrite` first, as above; otherwise pytest warns that
it cannot rewrite the module's asserts. A fixture of the same name in the
app's conftest overrides the shared one and may request it
(`def engine(engine, pages_dir): return engine`).

The MCP parity checks, for the app's `tests/test_mcp_parity.py`:

```python
def test_every_rest_operation_is_classified():
    check_operations_partition(main.app, catalog.CATALOG)


def test_registered_tools_match_the_catalog(mcp):
    check_tools_match_catalog(mcp, catalog.CATALOG)


def test_human_only_operations_refuse_an_api_token(client, api_token):
    check_human_only_refuses_tokens(client, api_token, catalog.CATALOG)
```

## The security behaviour

PLAN D-14, kept exactly and tested line by line (`tests/single/test_invariants.py`,
`tests/multi/test_mcp.py`). A change is Ken's decision, recorded in PLAN.md first.

- A presented bearer token is always verified, even under `AUTH_DISABLED`,
  and a wrong one is a 401, never a fall-through to another credential.
- Only the SHA-256 of an API token is stored; the plaintext is shown once.
- `/mcp` accepts a bearer token only, never the session cookie; POST only;
  Host and Origin are checked against the RP ID, the localhost names and
  `MCP_ALLOWED_HOSTS`.
- A request made by the MCP layer carries the via header, which can only
  reduce privilege; `require_human` refuses token and MCP requests.
- An expired session is deleted when it is next presented; WebAuthn
  challenges are single-use and expire after 5 minutes; the sign-in routes
  are rate-limited per address.
- `AUTH_DISABLED` logs a loud warning at start-up and makes every request the
  bootstrap admin.
- The database file is made mode 600 at start-up.

## Releasing

```bash
./release.sh --dry-run 1.2.3
./release.sh 1.2.3
```

The script refuses unless on `main`, clean, level with `origin`, the tag
unused and the repository public; runs `ruff check` and `pytest`; sets the
version in `house_backend/__init__.py`; builds the wheel; commits
`Release v1.2.3`, tags, pushes, uploads the wheel to the GitHub Release and
checks it downloads without credentials. Versions are semantic (PLAN D-5): a
removed or renamed name, or a changed table, route or response shape, is a
major version.

Working on this repository: `python3 -m venv .venv && .venv/bin/pip install -e
'.[mcp,test]' ruff`, then `.venv/bin/pytest` and `.venv/bin/ruff check .`.
