# CLAUDE.md — house-backend

The shared backend package for Ken's web apps (AppConfig, kwicky, NestEgg,
NetConfig, stocker). Built on 2026-11-19; all five apps run on it (each
pinned to a release in its `backend/pyproject.toml`, v2.1.0 on 2026-11-19).
A change here is released, then each app bumps its pin (PLAN D-24).

**Where to look**

| File | What it is |
|---|---|
| [USAGE.md](USAGE.md) | How an app uses it: each module, the options, the security behaviour, releasing |
| [TODO.md](TODO.md) | The backlog. Start here |
| [PLAN.md](PLAN.md) | The decisions (`D-n`). Decided plans only; open questions go in TODO.md |
| `docs/survey.md` | The apps' current copies, file by file, and what an adopting session should know. In `.gitignore`: it describes the private apps and is never committed |
| `tests/apps/` | The two example apps (single- and multi-user); every change is checked against them |
| `~/develop/house-ui` | The frontend counterpart; its `PLAN.md`, `release.sh` and `README.md` are the model for this repo's |

## Rules

1. **This repo is public** (PLAN D-3). Never commit a secret, hostname,
   address, user name or anything about the network or its data. Tests and
   examples use `example.test`, `localhost` and made-up names. Commits use
   the noreply email already in this repo's git config.
2. **Nothing a user or a database can see changes when an app adopts it**
   (PLAN D-9): same tables, same routes and JSON, same cookie names.
3. **The security behaviour of PLAN D-14 is kept exactly**, each line with a
   test. A change to it is Ken's decision, recorded in PLAN.md first.
4. **Read the apps, do not edit them, while building this package.** The
   apps change only in their own adoption items (TODO 8), each in that app's
   repo under that app's `CLAUDE.md`.
5. **Two apps make it shared** (PLAN D-19). Do not add an option or a module
   for a case no app has.

## Working agreement

- A piece of work is finished when its tests and `ruff check` pass, it is
  committed and pushed, and its TODO.md item is removed (row and section).
  Ken has said commits and pushes need no further asking in this repo.
- A new decision is added to PLAN.md with the next `D-n`; a changed one is
  edited in place. PLAN.md never holds options or history.
- TODO.md follows `~/develop/backlog/TODO-FORMAT.md`; run
  `~/develop/backlog/check-todo.py TODO.md` after editing it.
- Questions for Ken go to him one at a time, in plain words, with the
  options and a recommendation.
