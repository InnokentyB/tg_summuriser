from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tg_summariser.config import settings


def _ensure_sqlite_parent(url: str) -> None:
    """Ensure parent directory exists for SQLite database paths."""
    if not url.startswith("sqlite"):
        return
    db_path = url.split("///", maxsplit=1)[-1]
    if db_path.startswith("./"):
        Path(db_path[2:]).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent(settings.normalized_database_url)


def _engine_options(url: str) -> dict[str, object]:
    """Build engine connection pool options based on database URL."""
    options: dict[str, object] = {"future": True}
    if url.startswith(("postgresql+asyncpg://", "postgresql://", "postgres://")):
        options.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return options


engine = create_async_engine(
    settings.normalized_database_url,
    **_engine_options(settings.normalized_database_url),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional asynchronous database session context."""
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
