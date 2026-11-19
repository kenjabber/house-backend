# TODO — house-backend

Open work for the shared backend package ([PLAN.md](PLAN.md)). Paths are
relative to `~/develop/house-backend` unless they start with `~`. What the
apps' copies look like today is in `docs/survey.md`, a local file that is
not committed (PLAN D-3).

| # | Item | Status |
|---|------|--------|
| 6 | [The testing and app modules](#6-the-testing-and-app-modules) | ready to start |
| 7 | [USAGE.md and release v1.0.0](#7-usagemd-and-release-v100) | after 6 |
| 8 | [Write the adoption item into each app's backlog](#8-write-the-adoption-item-into-each-apps-backlog) | after 7 |
| 9 | [Fold the vault client into house-backend](#9-fold-the-vault-client-into-house-backend) | optional |
| 10 | [The MCP record-tool layer](#10-the-mcp-record-tool-layer) | idea |
| 11 | [A check that an app holds no copies](#11-a-check-that-an-app-holds-no-copies) | idea |
| 12 | [The rule in Ken's user-level CLAUDE.md](#12-the-rule-in-kens-user-level-claudemd) | after 7 |

Next number: **13**.

**Status key:** the shared one (`~/develop/backlog/TODO-FORMAT.md`).

---

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
