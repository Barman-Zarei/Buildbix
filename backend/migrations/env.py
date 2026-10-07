from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from buildbix import models  # noqa: F401  (register tables)
from buildbix.core.config import settings
from buildbix.core.db import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
URL = settings.database_url  # same BUILDBIX_DATABASE_URL the app uses


def run_migrations_offline():
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(URL)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
