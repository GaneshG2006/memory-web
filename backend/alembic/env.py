from logging.config import fileConfig
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"

# Make `app` imports work when Alembic runs from the project root.
sys.path.insert(0, str(BACKEND_DIR))

# Load backend/.env
load_dotenv(BACKEND_DIR / ".env")

from app.database import Base, get_database_url
from app import models  # noqa: F401 - registers all models with Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the same URL as the running FastAPI backend.
config.set_main_option(
    "sqlalchemy.url",
    get_database_url().replace("%", "%%"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()