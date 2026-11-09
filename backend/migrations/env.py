from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.db import make_engine
from backend.models import Base
from core.settings import load_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Tests pass a URL in via the config; otherwise use the clinic's DATABASE_URL.
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit(f"DATABASE_URL is not set (looked in {settings.clinic_dir / '.env'} and the environment)")
    return settings.database_url


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


def _do_run(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # handed in by tests
        _do_run(connection)
        return
    asyncio.run(_run_async(make_engine(_url())))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
