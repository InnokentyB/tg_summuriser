from __future__ import annotations

import asyncio
import inspect
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tg_summariser.models import Base


@pytest.fixture()
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    loop: asyncio.AbstractEventLoop = pyfuncitem.funcargs["event_loop"]
    test_kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    loop.run_until_complete(test_function(**test_kwargs))
    return True


@pytest.fixture()
def db_session(event_loop: asyncio.AbstractEventLoop) -> Generator[AsyncSession, None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    event_loop.run_until_complete(_create_schema(engine))

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = session_factory()
    try:
        yield session
    finally:
        event_loop.run_until_complete(session.close())
        event_loop.run_until_complete(engine.dispose())


async def _create_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
