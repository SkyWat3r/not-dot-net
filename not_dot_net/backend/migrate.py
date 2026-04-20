"""Run Alembic migrations programmatically."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_config(database_url: str) -> Config:
    alembic_dir = Path(__file__).resolve().parents[2] / "alembic"
    ini_path = alembic_dir.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def run_upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(_alembic_config(database_url), revision)


def stamp_head(database_url: str) -> None:
    command.stamp(_alembic_config(database_url), "head")
