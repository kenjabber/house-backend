# house-backend

The shared backend package for Ken's web apps: passkey login, sessions, API
tokens, the SQLite engine and the MCP plumbing, released as a Python wheel
that each app pins. The backend counterpart of
house-ui (`~/develop/house-ui`, on ruby).

- **[USAGE.md](USAGE.md)**: how an app installs, pins and uses it; each
  module; the security behaviour; releasing.
- **[PLAN.md](PLAN.md)**: the decisions (D-1 …).
- **[TODO.md](TODO.md)**: open work.
- **[tests/apps/](tests/apps)**: two small example apps built on it.
