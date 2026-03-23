# not-dot-net

A simple intranet application for [LPP](https://www.lpp.polytechnique.fr/) (Laboratoire de Physique des Plasmas).

Built with [NiceGUI](https://nicegui.io/) for the frontend, [FastAPI-Users](https://fastapi-users.github.io/fastapi-users/) for authentication, and async SQLite via SQLAlchemy for persistence.

## Features

- **People directory** — searchable staff directory with team, office, phone, and employment status
- **Workflow engine** — configurable multi-step request workflows (VPN access, onboarding, etc.) with role-based approval routing and email notifications
- **Booking system** — resource reservation with conflict detection, calendar view, and OS/software selection
- **Dashboard** — overview of pending requests, recent activity, and actionable items
- **Audit log** — tracks authentication events, workflow actions, and admin operations
- **Auth** — local password + LDAP authentication, role-based access (member / staff / director / admin)
- **i18n** — English and French with validated translation keys

## Install

```bash
pip install -e .
# or with uv
uv pip install -e .
```

## Usage

```bash
# Start the server
not-dot-net serve --host localhost --port 8000 --env-file config.yaml

# Start with fake seed data (development)
not-dot-net serve --host localhost --port 8000 --seed-fake-users

# Create a user
not-dot-net create-user user@example.com password --env-file config.yaml

# Print default configuration
not-dot-net default-config
```

## Configuration

Configuration uses pydantic-settings with YAML files. Generate a starting config with `not-dot-net default-config`.

Key settings:

| Setting | Description |
|---------|-------------|
| `backend.database_url` | SQLAlchemy async database URL (default: `sqlite+aiosqlite:///./test.db`) |
| `backend.users.auth.ldap.*` | LDAP server URL, domain, base DN, port |
| `jwt_secret` | Secret for JWT token signing |
| `admin_email` / `admin_password` | Default admin account created on first start |
| `workflows` | Workflow type definitions with steps, fields, and notification rules |

## Testing

```bash
pip install -e ".[test]"
pytest
```

Tests use the NiceGUI testing plugin (`nicegui.testing.User`) for frontend integration tests.

## License

MIT
