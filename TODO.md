# TODO — house-backend

Open work for the shared backend package ([PLAN.md](PLAN.md)). Paths are
relative to `~/develop/house-backend` unless they start with `~`. What the
apps' copies look like today is in `docs/survey.md`, a local file that is
not committed (PLAN D-3).

| # | Item | Status |
|---|------|--------|
| 2 | [Package skeleton and release script](#2-package-skeleton-and-release-script) | ready to start |
| 3 | [The db, fields and settings modules](#3-the-db-fields-and-settings-modules) | after 2 |
| 4 | [The auth module](#4-the-auth-module) | after 3 |
| 5 | [The mcp module](#5-the-mcp-module) | after 4 |
| 6 | [The testing and app modules](#6-the-testing-and-app-modules) | after 5 |
| 7 | [USAGE.md and release v1.0.0](#7-usagemd-and-release-v100) | after 6 |
| 8 | [Write the adoption item into each app's backlog](#8-write-the-adoption-item-into-each-apps-backlog) | after 7 |
| 9 | [Fold the vault client into house-backend](#9-fold-the-vault-client-into-house-backend) | optional |
| 10 | [The MCP record-tool layer](#10-the-mcp-record-tool-layer) | idea |
| 11 | [A check that an app holds no copies](#11-a-check-that-an-app-holds-no-copies) | idea |
| 12 | [The rule in Ken's user-level CLAUDE.md](#12-the-rule-in-kens-user-level-claudemd) | after 7 |

Next number: **13**.

**Status key:** the shared one (`~/develop/backlog/TODO-FORMAT.md`).

---

## 2. Package skeleton and release script

PLAN build order 1 (D-2, D-4 to D-8, D-25).

- `pyproject.toml`: hatchling, name `house-backend`, `requires-python =
  ">=3.13"`, the core dependencies and the `mcp` and `test` extras of D-8,
  pytest (`asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope =
  "function"`), ruff with D-25's rules. The version lives in one place
  (`house_backend/__init__.py`, read by hatch).
- `house_backend/__init__.py` with `__version__`, and `py.typed`.
- `release.sh`, modelled line for line on `~/develop/house-ui/release.sh`:
  the same guards (on `main`, clean, level with origin, tag unused, repo
  public), then `ruff check`, `pytest`, set the version, `python -m build
  --wheel`, commit `Release vX.Y.Z`, tag, `git push --atomic`, `gh release
  create` with the wheel, and the final `curl` of the public URL. Keep
  `--dry-run`.
- `.gitignore` already covers `dist/`, virtualenvs and caches.

**Done when:** `pip install -e '.[mcp,test]'` works in a fresh 3.13
virtualenv, `pytest` and `ruff check` pass (one trivial test), and
`./release.sh --dry-run 0.0.1` builds a wheel and undoes the version bump.

## 3. The db, fields and settings modules

PLAN build order 2 (D-13, D-15, D-16, D-17). The survey names the copy to
start from for each.

- `house_backend.db`: `create_sqlite_engine(path)` (the five apps' factory,
  taking a path, not a settings object); `snapshot(src, dest)`,
  `backup_bytes(path)` and `restore_bytes(path, data, required_tables)` using
  SQLite's backup API, with NetConfig's checks (the SQLite magic, the
  required tables, checkpoint before restore, chmod 600 after);
  `table_columns(conn, table)` and `table_exists(conn, table)`.
- `house_backend.fields`: `UTCDateTime` (AppConfig's `api/fields.py`) and the
  `x-house: plain` marker as a reusable `Field` extra, with helpers such as
  `Plain[str]` if that reads well.
- `house_backend.settings`: `HouseSettings` with D-13's names and the apps'
  defaults.

Tests: the pragmas are set on every connection; a snapshot taken while a
second connection holds uncommitted and committed-but-uncheckpointed data
contains the committed rows and opens without a `-wal` file; restore refuses
a non-SQLite file and one missing a required table; a naive datetime
serialises with its zone.

**Done when:** the three modules are in place with those tests passing.

## 4. The auth module

PLAN build order 3 (D-9 to D-12, D-14, D-21). The largest item; the survey's
rows for `services/auth.py`, `api/dependencies/auth.py`,
`api/routers/auth.py`, `services/api_tokens.py` and the auth tables say what
differs between the copies. Start from kwicky's copies (multi-user, no
app-specific columns) and AppConfig's `UTCDateTime`.

1. **Mixins** (`auth/models.py`): the five tables' columns as declarative
   mixins, with `declared_attr` where a column has a foreign key. Check them
   against NetConfig's and NestEgg's `schema.py` column by column (types,
   lengths, nullability, defaults, indexes, `ondelete`).
2. **`HouseAuth`** (D-11): holds the app name, settings, model classes and
   options; no module-level state (the rate limiter's hit table becomes an
   attribute).
3. **Service**: the functions of `services/auth.py` and
   `services/api_tokens.py` as methods or functions taking the `HouseAuth`.
4. **Dependencies**: `get_db_session`, `require_user`, `require_admin`,
   `require_human`, `require_human_admin`, `require_actor`,
   `current_user_or_none`, `is_agent_request`, `bearer_token`, and the
   constants (`SESSION_COOKIE_NAME`, `VIA_HEADER`, `VIA_MCP`) as attributes.
5. **Router**: `auth.router()` gives the `APIRouter` the app includes under
   `/api`: the 17 routes, the 5 user routes when `multi_user`, the 3 token
   routes only when `api_tokens`. Response models use `UTCDateTime` (D-15)
   and the `x-house` marker (D-16).
6. **Extension points** (D-12), decided here with the two example apps as the
   proof: extra response fields per user, extra accepted fields on user
   create and edit, and the values for a new or bootstrap user row.
7. **Example apps** (`tests/apps/single.py`, `tests/apps/multi.py`; D-21)
   and the tests: port kwicky's and NestEgg's `tests/test_auth.py`, and add
   one test per line of D-14. WebAuthn ceremonies are faked the way those
   suites do it.

**Done when:** both example apps pass; a database created by NetConfig's
current `schema.py` and one created by the single-user example app have the
same `.schema` for the five tables, and likewise kwicky's against the
multi-user example; every invariant of D-14 has a test.

## 5. The mcp module

PLAN build order 4 (D-17), behind the `mcp` extra. Survey rows:
`mcp_server/endpoint.py`, `rest.py`, `catalog.py`, `redact.py`,
`tests/test_mcp_parity.py`.

- `endpoint`: `McpEndpoint`, `mcp_lifespan(app, build_server)` and the
  transport-security allow-list, taking the `HouseAuth` and the 401 text
  (the apps name different Settings pages).
- `rest`: `call(app, authorization, method, template, path=, query=, body=,
  raw=)`, refusing an operation the catalog does not expose, marking the
  request with the via header, and passing the result through an optional
  redaction hook `(method, template, data) -> data`.
- `catalog`: a small class or functions over the app's `TOOL_OPERATIONS`,
  `EQUIVALENT`, `HUMAN_ONLY` and `HUMAN_ONLY_ENFORCED` giving
  `exposed_operations()` and `tool_names()`.
- `redact`: NetConfig's engine with the secret field names, the pattern and
  the placeholder prefixes supplied by the app; `contains_redaction_marker`.
- The multi-user example app gains one MCP tool so the endpoint is tested
  end to end.

**Done when:** the example app's `/mcp` answers a tool call with a token,
refuses a cookie, a wrong token, a GET and an unknown Host; `rest.call`
refuses an unexposed operation; the redactor passes NetConfig's
`tests/test_mcp.py` redaction cases when given NetConfig's names and pattern.

## 6. The testing and app modules

PLAN build order 4 (D-17).

- `house_backend.testing`: the fixtures and helpers of the survey's
  `conftest.py` row, usable as `pytest_plugins = ["house_backend.testing"]`
  plus a few app-supplied fixtures (the app object, its `Base`), and the
  parity checks as functions (`check_operations_partition(app, catalog)`,
  `check_human_only_refuses_tokens(client, token, catalog)`), which an app's
  own `test_mcp_parity.py` calls. Rewrite this repo's own tests onto them.
- `house_backend.app`: `prepare_database(settings, path, Base, migrate)`
  (directory, engine, `create_all`, the app's migration function, chmod 600,
  `app.state`), `warn_if_auth_disabled`, `add_cors`, `mount_mcp`, and
  `build_info()` (NetConfig's `services/build_info.py`). Helpers an app's
  lifespan calls; not a function that builds the whole app (D-18).

**Done when:** the example apps' `main` modules are a few lines of helpers
plus their routers, and this repo's tests use `house_backend.testing` only.

## 7. USAGE.md and release v1.0.0

PLAN build order 5 (D-20). Write `USAGE.md` (install and pin, the editable
workflow of D-6, each module, the options, the invariants, the "new app"
skeleton taken from `tests/apps/`), cut `README.md` down to a pointer, check
the repo once more against D-3, and run `./release.sh 1.0.0`.

**Done when:** the wheel downloads from the release URL without credentials
and installs with the D-4 line in a scratch app's `pyproject.toml`.

## 8. Write the adoption item into each app's backlog

PLAN build order 6. One item in each of `~/develop/kwicky/TODO.md`,
`appconfig`, `netconfig`, `nestegg` and stocker's backlog
(`~/develop/stocker/docs/todo.md`), each a brief a fresh session can start
from: the steps of build order 6, that app's row and notes from
`docs/survey.md`, and its own checks. Per app:

- **kwicky** — first. `multi_user`, `api_tokens`; `group_ids` on the user
  detail through the extension point.
- **AppConfig** — single user. It is deployed by `deploy/deploy-local.sh`,
  not by itself; its `persistence/db.py` keeps `prepare_schema`.
- **NetConfig** — single user. `require_actor`; the redactor with its own
  names and pattern; `db_admin.py` onto `house_backend.db`; timestamps gain
  a zone (check the Settings screens and the tests that compare them).
- **NestEgg** — `multi_user`, households through the extension points; teach
  `mcp_server/redact.py` and `classification_gaps` that `x-house: plain` is
  plain (D-16); its Python floor is 3.14, which is fine.
- **stocker** — last. `multi_user`, `api_tokens` off, so its behaviour does
  not change; its `Settings` gains `AUTH_DISABLED` (default off) by
  inheritance. Whether stocker should then get API tokens is its own
  question for Ken, not part of adoption.

Run `~/develop/backlog/check-todo.py` on each file edited.

**Done when:** the five items exist, pass `check-todo.py`, and are pushed.

## 9. Fold the vault client into house-backend

**Trigger:** Ken's repos have moved to a private git server. Until then the
vault client stays copied (PLAN D-26).

AppConfig, NestEgg and NetConfig each hold a byte-identical copy of the
`secretstore` client (v1.5.2 on 2026-11-19) and of `deploy/bw-agent/`
(Dockerfile and entrypoint). Their home is
`~/develop/skills/vaultwarden/tools/` (client in `client/python`, agent in
`agent/`), with a written vendoring rule: copy the directory, keep the
`# SOURCE:` lines, never edit the copy. That is the hand copying
house-backend exists to end. It waits for the private server because the
client shows how the vault is used, which does not belong in a public repo.

When the trigger occurs:

1. Move the client into this package as `house_backend.secretstore`
   (stdlib only today: keep it free of the web dependencies, so the `vw`
   tooling and the vaultwarden skill can use it without FastAPI), with its
   tests and changelog from the skills repo.
2. Make the skills repo use the packaged client instead of holding the
   original, and rewrite its vendoring rule and "Who vendors what" list.
3. In each of the three apps, delete `services/secretstore/`, import from
   `house_backend.secretstore`, bump the pin, run the tests, deploy.
4. Decide with Ken how the bw-agent image is shared (built once and pulled
   by each app's Compose file, or still copied).
5. The move to a private server also changes how apps download this package
   (PLAN D-3, D-4, D-5): bring those decisions up to date in the same work.

**Done when:** no app holds a copy of `secretstore`, the skills repo holds no
second original, and D-26 is replaced in PLAN.md.

## 10. The MCP record-tool layer

NetConfig's and AppConfig's `mcp_server/tools.py` share a generic layer over
a `RESOURCES` table: `_resource`, `_matches` (`where` / `contains`
filtering), `_guard_payload`, `_resolve`, `_body_schema`, `_record_schema`,
and the `list_records` / `get_record` / `create_record` style tools built on
them. Two apps use it, so by D-19 it belongs here, but it is large and still
changing in NetConfig. **Trigger:** a third app wants record tools, or a fix
has to be made in both copies. Then diff the two, and move the common part to
`house_backend.mcp.records`.

**Done when:** decided with Ken; if yes, both apps import it.

## 11. A check that an app holds no copies

house-ui ships `house-ui-check`, which each app's `npm test` runs so drift is
caught. The counterpart here would be a small script or pytest helper that
fails when an app's backend defines what house-backend provides: a
`require_user` function, a `WebAuthnCredential` class not built on the mixin,
a file with `# SOURCE:` naming one of the replaced modules, or
`create_engine(` outside `house_backend`. Worth having once two apps have
adopted and the list of things to look for is known from experience.

**Done when:** decided with Ken; if yes, each adopted app's test run includes it.

## 12. The rule in Ken's user-level CLAUDE.md

PLAN D-23. `~/.claude/CLAUDE.md` should say, next to its house-ui line, that
a new web app's backend starts on house-backend (the text of D-22, in
short). That file is outside this repo: propose the two or three lines to
Ken, or edit it in a session that can reach it.

**Done when:** the rule is in `~/.claude/CLAUDE.md`.
