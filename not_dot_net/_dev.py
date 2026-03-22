"""Dev entry point with auto-reload.

Usage: uv run python not_dot_net/_dev.py [--env-file config.yaml] [--seed-fake-users]
"""
import sys

from not_dot_net.app import create_app

from nicegui import ui

from not_dot_net.config import get_settings


def _parse_env_file() -> str | None:
    if "--env-file" in sys.argv:
        idx = sys.argv.index("--env-file")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


create_app(
    config_file=_parse_env_file(),
    _seed_fake_users="--seed-fake-users" in sys.argv,
)
settings = get_settings()
ui.run(
    storage_secret=settings.storage_secret,
    host="localhost",
    port=8088,
    reload=True,
    title="NotDotNet",
)
