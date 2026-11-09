from __future__ import annotations

from alembic import command
from alembic.config import Config

from core.config import REPO_ROOT


def test_migrations_upgrade_downgrade_and_match_models(tmp_path, clean_env):
    clean_env.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models have drifted from the migrations
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
