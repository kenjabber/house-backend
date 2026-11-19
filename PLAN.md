# PLAN — house-backend

**house-backend** is the one copy of the backend code Ken's web apps under
`~/develop` have in common: AppConfig, kwicky, NestEgg, NetConfig, stocker,
and every new one. schematic-extract is not part of it. It is a Python
package that each app's backend depends on at a pinned version. It is the
backend counterpart of **house-ui** (`~/develop/house-ui`), and is run the
same way.

**How to use this file.** It records the plan as decided — nothing else.
Decisions are numbered **D-n** and are never renumbered; a changed decision is
edited in place. Open questions and pending work are in [TODO.md](TODO.md).
What the apps' copies look like today, file by file, is in
`docs/survey.md`: read it before writing a module. That file describes the
private apps, so it is in `.gitignore` and exists only in Ken's working copy
(D-3).
Implementation starts at the build order (§8).

---

## 1. Why

- **D-1** **Copies drift; a dependency does not.** The apps' login, session,
  API-token, database-engine and MCP plumbing all descend from one ancestor
  (wg-peer-manager → NetConfig → AppConfig and kwicky → NestEgg; stocker is an
  older branch), copied by hand with a `# SOURCE:` comment. As of 2026-11-19
  that is about 9,000 lines across five apps, and the copies have drifted:
  AppConfig's auth responses carry time zones and NetConfig's do not;
  NetConfig's MCP redaction knows three secret shapes AppConfig's copy does
  not; stocker has no API tokens, no `AUTH_DISABLED` and no human-only guard.
  A fix to login code has to be made five times today. house-backend
  replaces the copies with one source.

## 2. What house-backend is

- **D-2** **One repo, one package.** `~/develop/house-backend`, GitHub
  `kenjabber/house-backend`, distribution name `house-backend`, import name
  `house_backend`. Built with hatchling, like the apps.
- **D-3** **The GitHub repo is public** (Ken, 2026-11-19), for house-ui's
  reason (its D-21): apps are built in Docker on the servers, where `pip`
  downloads the pinned release file, and a private repo would need a token in
  every build. It holds login code but no secret. **Never put a secret,
  hostname, address, user name or anything about the network or its data in
  this repo** — tests and examples use `example.test`, `localhost` and
  made-up names. `docs/survey.md`, which describes the private apps' current
  code, is in `.gitignore` and is never committed. Commits use Ken's GitHub
  noreply email (set in the repo's git config), not his own address.
- **D-4** **Apps pin a version.** An app's `backend/pyproject.toml` names an
  exact tagged release by the URL of its wheel:
  `"house-backend[mcp] @ https://github.com/kenjabber/house-backend/releases/download/vX.Y.Z/house_backend-X.Y.Z-py3-none-any.whl"`
  (the app's pyproject needs `[tool.hatch.metadata] allow-direct-references = true`).
  Upgrading is a deliberate change in that app — bump the pin, run the app's
  tests, commit — never automatic. No app keeps a copy of any house-backend
  file.
- **D-5** **A release is a wheel on the GitHub Release.** `release.sh X.Y.Z`
  (modelled on house-ui's) checks the tree is clean and pushed, runs the
  tests and lint, sets the version, builds the wheel, tags `vX.Y.Z`, pushes,
  uploads the wheel to that tag's GitHub Release, and checks the URL
  downloads without credentials. `dist/` is never committed. Versions are
  semantic: a removed or renamed name, a changed table, route or response
  shape bumps the major version.
- **D-6** **Local development uses an editable install.** To change
  house-backend while working in an app:
  `pip install -e ~/develop/house-backend` in the app's virtualenv. Before
  the app's change is committed, release house-backend, bump the app's pin
  and reinstall from the pin (`pip install -e backend`), so the app's tests
  ran against what the server will download.
- **D-7** **Python 3.13 or newer** (`requires-python = ">=3.13"`): the oldest
  interpreter any app is deployed on (the backend images are 3.13 and 3.14).
  The apps' own `requires-python` lines (3.11, 3.12, 3.14) are not a
  constraint, since nothing runs them on less than 3.13.
- **D-8** **Dependencies.** Core: `fastapi`, `sqlalchemy>=2`,
  `pydantic-settings`, `webauthn>=2`. The MCP plumbing is the extra
  `house-backend[mcp]` (`mcp>=2.2,<3`, `httpx`), so an app without an MCP
  server does not install the SDK. The test fixtures are the extra
  `house-backend[test]` (`pytest`, `pytest-asyncio`, `httpx`). Lower bounds
  only, as in the apps; the MCP SDK keeps its `<3` cap because its server API
  changed between majors.

## 3. How an app uses it

- **D-9** **Adopting it changes nothing a user or a database can see.** The
  five auth tables keep their names and columns (`user`,
  `webauthn_credential`, `user_session`, `webauthn_challenge`, `api_token`),
  so no data is migrated. Every `/api/auth/*` route keeps its path, request
  and response shape, so no frontend changes. The session cookie keeps its
  name (`<app>_session`), so nobody is signed out. `/mcp` behaves as before.
  The one allowed difference: a timestamp in an auth response is always
  UTC with its zone (D-15); the adopting app checks its frontend parses that.
- **D-10** **The app owns its tables; house-backend supplies mixins.** The
  library defines the columns of the five auth tables as SQLAlchemy mixins
  (`UserMixin`, `WebAuthnCredentialMixin`, `UserSessionMixin`,
  `WebAuthnChallengeMixin`, `ApiTokenMixin`) and `BOOTSTRAP_USER_ID`. The app
  declares the classes on its own `Base`
  (`class User(UserMixin, Base): …`), so the tables stay in the app's
  metadata and migrations, and an app adds its own columns (NestEgg's
  `household_id` and `person_id`) and relationships in its own class.
- **D-11** **One object configures auth for the app.** The app builds it once
  (in `api/dependencies/auth.py`) from its settings, its five model classes
  and its options, and everything else comes from it:

  ```python
  auth = HouseAuth(
      app_name="nestegg",        # cookie "nestegg_session", header "x-nestegg-via", realm
      settings=settings,          # a HouseSettings (D-13)
      models=AuthModels(User, WebAuthnCredential, UserSession, WebAuthnChallenge, ApiToken),
      multi_user=True,            # D-12
      api_tokens=True,            # D-12
  )
  require_user = auth.require_user        # re-exported, so routers' imports do not change
  ```

  The names above are a sketch; the requirement is one configured object, no
  module-level state shared between two apps in one process (the tests build
  several), and the app's `api/dependencies/auth.py` re-exporting the
  dependencies under the names its routers already import.
- **D-12** **The options are the differences the apps really have.**
  - `multi_user` — off: one account, the first registrant (AppConfig,
    NetConfig). On: the first registrant bootstraps the admin, an admin
    creates every later account and its owner registers a passkey; adds the
    five `/auth/users` routes (kwicky, NestEgg, stocker).
  - `api_tokens` — on adds the bearer-token credential, the three
    `/auth/tokens` routes, `require_human` and the MCP endpoint's check.
  - Extension points for what an app adds to a user: extra fields on the
    user responses (kwicky's `group_ids`, NestEgg's `household_id` and
    `person_id`), extra fields accepted when an admin creates or edits a user,
    and the values a new or bootstrap user row needs (NestEgg's household).
  An option is added only for a difference two apps have or one app needs;
  the library is not a framework.
- **D-13** **`HouseSettings` is the base of each app's `Settings`.** A
  pydantic-settings class holding what the shared code reads:
  `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`,
  `SESSION_COOKIE_SECURE`, `SESSION_LIFETIME_DAYS`,
  `AUTH_RATE_LIMIT_PER_MINUTE`, `AUTH_DISABLED`, `MCP_ALLOWED_HOSTS`,
  `SITE_NAME`, `BUILD_VERSION`, and the properties `webauthn_allowed_origins`
  and `mcp_extra_hosts`. Names and defaults are the apps' current ones, so no
  `.env` or Compose file changes. The app subclasses it and adds its own
  settings and its own defaults (ports, RP name). The database path is not in
  it: the apps name it differently, so the engine factory takes a path.
- **D-14** **The security behaviour is the apps' current one, kept exactly**
  and covered by house-backend's own tests:
  - a presented bearer token is always verified, even under `AUTH_DISABLED`,
    and a wrong one is a 401, never a fall-through to another credential;
  - only the SHA-256 of an API token is stored; the plaintext is shown once;
  - `/mcp` accepts a bearer token only, never the session cookie; POST only;
    Host and Origin are checked against the RP ID, localhost names and
    `MCP_ALLOWED_HOSTS`;
  - a request made by the MCP layer is marked with the via header, which can
    only reduce privilege; `require_human` refuses token and MCP requests;
  - an expired session is deleted when it is next presented; WebAuthn
    challenges are single-use and expire; auth routes are rate-limited per
    address;
  - `AUTH_DISABLED` logs the loud warning at start-up and makes every request
    the bootstrap admin;
  - the database file is made mode 600 at start-up.
- **D-15** **Every datetime leaving the shared routes is UTC with its zone.**
  SQLite keeps naive UTC; the response models add the zone (AppConfig's
  `UTCDateTime`), so a client never guesses.
- **D-16** **Every field of the shared response models is marked
  `x-house: plain`** in its JSON schema. NestEgg withholds any response field
  it cannot classify (its D-21); the shared auth responses hold no figure, and
  the marker lets NestEgg's redactor and parity test accept them by rule
  instead of by a list of names.

## 4. What is in it

- **D-17** **Modules.** Each replaces the copies `docs/survey.md` lists for it.

  | Module | Holds |
  |---|---|
  | `house_backend.settings` | `HouseSettings` (D-13) |
  | `house_backend.db` | The SQLite engine factory (NullPool, WAL, foreign keys, busy timeout), a consistent snapshot, backup-to-bytes and validated restore (SQLite's backup API, never a file copy), and the migration helpers `table_columns` and `table_exists` |
  | `house_backend.fields` | `UTCDateTime` (D-15) and the `x-house` marker (D-16) |
  | `house_backend.auth` | The mixins (D-10), `HouseAuth` (D-11): the passkey service (registration, login, sessions, challenges, bootstrap, rate limit), API tokens, the FastAPI dependencies, and the `/api/auth` router |
  | `house_backend.mcp` | The `/mcp` endpoint and its lifespan, the in-process REST caller (with a redaction hook and raw-text results), the catalog helpers (`exposed_operations`, `tool_names`, the three-way classification) and the name-based secret redactor with the app's own field names and patterns |
  | `house_backend.app` | Start-up helpers: prepare the data directory and engine, the session factory, CORS for the WebAuthn origins, the `AUTH_DISABLED` warning, mounting `/mcp`; and the build identity (`build.json` or git) |
  | `house_backend.testing` | pytest fixtures and helpers the apps' `conftest.py` files repeat: `engine`, `session`, `client`, `seed_user`, `login_as`, `api_token`, `McpClient`, and the MCP parity checks as functions an app's test calls |

- **D-18** **What stays in each app.** Its domain: schema beyond the auth
  tables, services, routers, the content of its migrations, its MCP tools,
  catalog and `INSTRUCTIONS`, what it redacts (NestEgg's schema-driven
  withholding of figures; the field names and patterns NetConfig and
  AppConfig redact), its background workers, and its `main.py`, which stays
  a readable list of routers and start-up steps that calls the helpers.
- **D-19** **Two apps make it shared**, as in house-ui (its D-12). Code used
  by two or more apps belongs in house-backend; code one app uses stays in
  that app. NestEgg's no-echo error handling and NetConfig's actor name for
  audit rows move when a second app wants them.
- **D-26** **The vault client stays copied for now** (Ken, 2026-11-19).
  AppConfig, NestEgg and NetConfig keep their vendored copies of
  `secretstore` and of `deploy/bw-agent/`, taken from
  `~/develop/skills/vaultwarden/tools/` under that repo's vendoring rule.
  house-backend does not ship or replace them. It is folded in once the repos
  are on a private git server (TODO 9).

## 5. The documentation

- **D-20** **`USAGE.md`** — the counterpart of house-ui's `STYLEGUIDE.md`:
  how an app installs and pins it (D-4, D-6), each module with what it
  exports and a snippet, the options of D-12, the invariants of D-14, and a
  worked "new app" skeleton. `README.md` stays one screen and points to it.
- **D-21** **The tests include two small example apps** (`tests/apps/`): one
  single-user, one multi-user with an extra user column and API tokens and an
  MCP tool. They are how a change is checked, and what `USAGE.md`'s snippets
  are taken from.

## 6. Keeping apps on it

- **D-22** **Each app's `CLAUDE.md` gets a "Shared backend" section:**

  > Login, sessions, API tokens, the SQLite engine and the MCP plumbing come
  > from **house-backend** (`~/develop/house-backend`, pinned in
  > `backend/pyproject.toml`). Read its `USAGE.md` before touching auth, the
  > database engine or `/mcp`. Do not copy its code here or re-implement it;
  > if something two apps would use is missing, add it to house-backend (or
  > its `TODO.md`) and bump the pin.

- **D-23** **Ken's user-level `~/.claude/CLAUDE.md` carries the same rule in
  short**, so a new app starts on house-backend from its first commit.

## 7. Changing house-backend

- **D-24** **A change is made in house-backend first**: code, tests,
  `USAGE.md`, a release. Then each app that wants it bumps its pin and runs
  its own tests. A security fix is released as a patch version and every
  app's pin is bumped in the same piece of work.
- **D-25** **Tests and lint gate a release.** `pytest` and `ruff check`
  (NestEgg's rule set: `E, F, W, I, B, UP`, line length 120, `B008` ignored)
  must be clean; `release.sh` runs both.

## 8. Build order

1. Repo on GitHub (public), package skeleton, `release.sh`, lint and test
   set-up (D-2 to D-8, D-25).
2. The modules that need no design: `db`, `fields`, `settings` (D-13, D-15,
   D-17).
3. `auth`: mixins, `HouseAuth`, service, dependencies, router, with the two
   example apps and the invariants of D-14 as tests (D-9 to D-12, D-21).
4. `mcp`, then `testing`, then `app` (D-17).
5. `USAGE.md` (D-20), release `v1.0.0`.
6. Adoption, one app per item in that app's own backlog, in this order:
   kwicky (small, multi-user, low stakes), AppConfig, NetConfig, NestEgg (the
   `x-house` marker, D-16), stocker. Each adoption: pin house-backend, turn
   the app's auth tables into mixin subclasses with **no schema change**
   (check with a copy of the live database), make `Settings` a
   `HouseSettings`, replace the copied modules with thin re-exports or
   delete them, keep the app's own test suite passing with only its imports
   and fixtures changed, add the `CLAUDE.md` section (D-22), deploy, and
   check sign-in with an existing passkey, an existing API token and `/mcp`.
