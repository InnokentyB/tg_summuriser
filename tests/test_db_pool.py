from tg_summariser import db


def test_postgres_engine_uses_bounded_pool(monkeypatch):
    monkeypatch.setattr(db.settings, "database_pool_size", 2)
    monkeypatch.setattr(db.settings, "database_max_overflow", 2)

    assert db._engine_options("postgresql+asyncpg://example/db") == {
        "future": True,
        "pool_size": 2,
        "max_overflow": 2,
    }


def test_sqlite_engine_does_not_receive_queue_pool_options():
    assert db._engine_options("sqlite+aiosqlite:///:memory:") == {"future": True}
