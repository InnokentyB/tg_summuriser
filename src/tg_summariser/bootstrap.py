from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import text

from tg_summariser.models import Base


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_column(
            conn,
            table_name="channels",
            column_name="last_synced_at",
            column_definition="TIMESTAMP",
        )
        await _ensure_column(
            conn,
            table_name="posts",
            column_name="source_published_at",
            column_definition="TIMESTAMP",
        )
        await _ensure_column(
            conn,
            table_name="posts",
            column_name="is_promotional",
            column_definition="BOOLEAN DEFAULT FALSE",
        )
        await _ensure_column(
            conn,
            table_name="posts",
            column_name="ai_batch_job_id",
            column_definition="INTEGER",
        )
        await _ensure_column(
            conn,
            table_name="posts",
            column_name="product_matches_json",
            column_definition="TEXT",
        )
        await _ensure_column(
            conn,
            table_name="posts",
            column_name="product_review_sent",
            column_definition="BOOLEAN DEFAULT FALSE",
        )
        if conn.dialect.name == "postgresql":
            await _upgrade_postgres_bigint_columns(conn)
            await _upgrade_postgres_text_columns(conn)


async def _upgrade_postgres_bigint_columns(conn) -> None:
    await _upgrade_postgres_column_to_bigint(conn, "users", "telegram_id")
    await _upgrade_postgres_column_to_bigint(conn, "channels", "telegram_chat_id")


async def _upgrade_postgres_text_columns(conn) -> None:
    await _upgrade_postgres_column_to_text(conn, "posts", "original_link")


async def _upgrade_postgres_column_to_text(conn, table_name: str, column_name: str) -> None:
    result = await conn.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    data_type = result.scalar_one_or_none()
    if data_type == "text" or data_type is None:
        return

    await conn.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE TEXT"))


async def _upgrade_postgres_column_to_bigint(conn, table_name: str, column_name: str) -> None:
    result = await conn.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    data_type = result.scalar_one_or_none()
    if data_type != "integer":
        return

    await conn.execute(
        text(
            f"ALTER TABLE {table_name} "
            f"ALTER COLUMN {column_name} TYPE BIGINT USING {column_name}::bigint"
        )
    )


async def _ensure_column(
    conn,
    *,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    if conn.dialect.name == "sqlite":
        result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
        existing_columns = {row[1] for row in result.fetchall()}
    else:
        result = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        existing_columns = {row[0] for row in result.fetchall()}

    if column_name in existing_columns:
        return

    await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"))
