# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this

**not-dot-net** is a simple intranet application for LPP (Laboratoire de Physique des Plasmas). It uses NiceGUI for the frontend, FastAPI-Users for authentication (local + LDAP), and SQLAlchemy with async SQLite for persistence. Configuration is via pydantic-settings with YAML files.

## Commands

```bash
# Install (uses flit)
pip install -e .

# Run the app
python -m not_dot_net.cli serve --host localhost --port 8000 --env-file config.yaml

# Create a user
python -m not_dot_net.cli create-user <email> <password> --env-file config.yaml

# Dump default config
python -m not_dot_net.cli default-config

# Run tests (uses nicegui testing plugin)
pytest
```

## Architecture

### Plugin/loader registration pattern

Both frontend pages and auth backends use the same pattern: a `register.py` module holds a global list, a decorator appends loaders to it, and the package `__init__.py` auto-discovers modules via filesystem iteration then calls all registered loaders.

- **Auth backends**: `backend/users/auth/register.py` holds `AUTH_BACKENDS`. Each auth module (e.g. `local.py`, `ldap/ldap.py`) decorates a `load(get_user_db, get_user_manager, get_jwt_strategy)` function with `@register_backend_loader`. To add a new auth method, create a new module in `backend/users/auth/` with that decorator.

- **Frontend pages**: `frontend/register.py` holds `FRONTEND_LOADERS`. Each page module (e.g. `login.py`, `user_page.py`) decorates a `load(ndtapp: NotDotNetApp)` function with `@register_frontend_loader`. To add a new page, create a new module in `frontend/` with that decorator.

### Wiring flow

`App.__init__` (in `app.py`) → creates `NotDotNetApp` (which sets up DB + auth backends) → calls `load_frontend(ndtapp)` (registers NiceGUI pages) → calls `register_routes(app)` (mounts FastAPI auth/user routers).

### Key types

- `DB` dataclass (`backend/db.py`): bundles async session maker, table creation, and user DB dependencies.
- `NotDotNetAuthBackend` dataclass (`backend/users/users.py`): bundles JWT/cookie auth backends, FastAPIUsers instance, and user manager factory.
- `Settings` hierarchy (`config.py`): nested pydantic-settings models (Settings → BackendSettings → UsersSettings → AuthSettings → LDAPSettings). Loaded from YAML, stored on `app.state.settings`.

### Testing

Tests use `nicegui.testing.User` (configured via `pytest` plugin in pyproject.toml: `-p nicegui.testing.user_plugin`). The test `app.py` entry point is configured as `main_file = "app.py"` in `[tool.pytest]`.
