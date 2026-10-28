from sqlalchemy import inspect
from sqlalchemy import text

from app.extensions import db


def upgrade_shop_management_schema():
    """Add Task 2 columns and indexes without replacing or dropping existing data."""
    db.create_all()
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        columns = {
            "barber_shop": {
                "description": "TEXT NULL",
                "opening_time": "TIME NULL",
                "closing_time": "TIME NULL",
            },
            "service": {"is_active": "BOOLEAN NOT NULL DEFAULT 1"},
        }
    elif dialect == "mssql":
        columns = {
            "barber_shop": {
                "description": "TEXT NULL",
                "opening_time": "TIME NULL",
                "closing_time": "TIME NULL",
            },
            "service": {"is_active": "BIT NOT NULL CONSTRAINT DF_service_is_active DEFAULT 1"},
        }
    else:
        raise RuntimeError(f"Schema upgrade is not configured for database dialect {dialect!r}.")

    with db.engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, additions in columns.items():
            current = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in additions.items():
                if column_name not in current:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD {column_name} {definition}"
                    )
        inspector = inspect(connection)
        indexes = {item["name"] for item in inspector.get_indexes("barber_shop")}
        if "ix_barber_shop_locality" not in indexes:
            connection.exec_driver_sql(
                "CREATE INDEX ix_barber_shop_locality ON barber_shop (locality)"
            )
        indexes = {item["name"] for item in inspector.get_indexes("barber_shop")}
        if "ix_barber_shop_name" not in indexes:
            connection.exec_driver_sql(
                "CREATE INDEX ix_barber_shop_name ON barber_shop (name)"
            )
        indexes = {item["name"] for item in inspector.get_indexes("service")}
        if "ix_service_is_active" not in indexes:
            connection.exec_driver_sql(
                "CREATE INDEX ix_service_is_active ON service (is_active)"
            )


def upgrade_queue_schema():
    """Add daily token scope and active-customer uniqueness, preserving token rows."""
    db.create_all()
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        with db.engine.connect() as connection:
            duplicate = connection.execute(text("""
                SELECT customer_id, shop_id, COUNT(*) AS n FROM queue_token
                WHERE status IN ('waiting', 'in_service')
                GROUP BY customer_id, shop_id HAVING COUNT(*) > 1 LIMIT 1
            """)).first()
            if duplicate:
                raise RuntimeError("Cannot add active queue uniqueness: resolve duplicate active tokens for one customer and shop first.")
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            inspector = inspect(connection)
            counter_columns = {column["name"] for column in inspector.get_columns("queue_counter")}
            if "queue_date" not in counter_columns:
                connection.exec_driver_sql("ALTER TABLE queue_counter ADD COLUMN queue_date DATE")

            token_columns = {column["name"] for column in inspect(connection).get_columns("queue_token")}
            if "queue_date" not in token_columns:
                table = QueueToken.__table__
                connection.exec_driver_sql("""
                    CREATE TABLE queue_token_upgrade (
                        id INTEGER NOT NULL PRIMARY KEY,
                        customer_id INTEGER NOT NULL REFERENCES user (id) ON DELETE RESTRICT,
                        shop_id INTEGER NOT NULL REFERENCES barber_shop (id) ON DELETE RESTRICT,
                        service_id INTEGER NOT NULL REFERENCES service (id) ON DELETE RESTRICT,
                        token_number INTEGER NOT NULL,
                        queue_date DATE NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        created_at DATETIME NOT NULL,
                        estimated_wait_minutes INTEGER NOT NULL,
                        CONSTRAINT uq_token_shop_day_number UNIQUE (shop_id, queue_date, token_number),
                        CONSTRAINT ck_token_status CHECK (status IN ('waiting','in_service','completed','cancelled','skipped')),
                        CONSTRAINT ck_token_values CHECK (token_number > 0 AND estimated_wait_minutes >= 0)
                    )
                """)
                connection.exec_driver_sql("""
                    INSERT INTO queue_token_upgrade
                        (id, customer_id, shop_id, service_id, token_number, queue_date, status, created_at, estimated_wait_minutes)
                    SELECT id, customer_id, shop_id, service_id, token_number,
                           date(created_at), status, created_at, estimated_wait_minutes
                    FROM queue_token
                """)
                connection.exec_driver_sql("DROP TABLE queue_token")
                connection.exec_driver_sql("ALTER TABLE queue_token_upgrade RENAME TO queue_token")
            connection.exec_driver_sql("""
                UPDATE queue_counter
                SET queue_date = COALESCE(
                    (SELECT MAX(queue_date) FROM queue_token WHERE queue_token.shop_id = queue_counter.shop_id),
                    date('now')
                )
                WHERE queue_date IS NULL
            """)
            connection.exec_driver_sql("""
                UPDATE queue_counter
                SET next_token_number = MAX(next_token_number, COALESCE(
                    (SELECT MAX(token_number) + 1 FROM queue_token
                     WHERE queue_token.shop_id = queue_counter.shop_id
                       AND queue_token.queue_date = queue_counter.queue_date), 1
                ))
            """)
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            indexes = {item["name"] for item in inspect(connection).get_indexes("queue_token")}
            desired_indexes = {
                "ix_queue_token_customer_id": "CREATE INDEX ix_queue_token_customer_id ON queue_token (customer_id)",
                "ix_queue_token_shop_id": "CREATE INDEX ix_queue_token_shop_id ON queue_token (shop_id)",
                "ix_queue_token_queue_date": "CREATE INDEX ix_queue_token_queue_date ON queue_token (queue_date)",
                "uq_queue_active_customer_shop": "CREATE UNIQUE INDEX uq_queue_active_customer_shop ON queue_token (customer_id, shop_id) WHERE status IN ('waiting', 'in_service')",
            }
            for name, ddl in desired_indexes.items():
                if name not in indexes:
                    connection.exec_driver_sql(ddl)
            connection.commit()
    elif dialect == "mssql":
        with db.engine.begin() as connection:
            duplicate = connection.execute(text("""
                SELECT TOP 1 customer_id, shop_id, COUNT(*) AS n FROM queue_token
                WHERE status IN ('waiting', 'in_service')
                GROUP BY customer_id, shop_id HAVING COUNT(*) > 1
            """)).first()
            if duplicate:
                raise RuntimeError("Cannot add active queue uniqueness: resolve duplicate active tokens for one customer and shop first.")
            inspector = inspect(connection)
            counter_columns = {column["name"] for column in inspector.get_columns("queue_counter")}
            if "queue_date" not in counter_columns:
                connection.exec_driver_sql("ALTER TABLE queue_counter ADD queue_date DATE NULL")
            token_columns = {column["name"] for column in inspect(connection).get_columns("queue_token")}
            if "queue_date" not in token_columns:
                connection.exec_driver_sql("ALTER TABLE queue_token ADD queue_date DATE NULL")
                connection.exec_driver_sql("UPDATE queue_token SET queue_date = CAST(created_at AS DATE) WHERE queue_date IS NULL")
                connection.exec_driver_sql("ALTER TABLE queue_token ALTER COLUMN queue_date DATE NOT NULL")
            connection.exec_driver_sql("""
                UPDATE queue_counter
                SET queue_date = COALESCE(
                    (SELECT MAX(queue_date) FROM queue_token WHERE queue_token.shop_id = queue_counter.shop_id),
                    CAST(GETUTCDATE() AS DATE)
                )
                WHERE queue_date IS NULL
            """)
            connection.exec_driver_sql("""
                UPDATE queue_counter
                SET next_token_number = CASE
                    WHEN next_token_number < COALESCE(
                        (SELECT MAX(token_number) + 1 FROM queue_token
                         WHERE queue_token.shop_id = queue_counter.shop_id
                           AND queue_token.queue_date = queue_counter.queue_date), 1
                    ) THEN COALESCE(
                        (SELECT MAX(token_number) + 1 FROM queue_token
                         WHERE queue_token.shop_id = queue_counter.shop_id
                           AND queue_token.queue_date = queue_counter.queue_date), 1
                    ) ELSE next_token_number END
            """)
            connection.exec_driver_sql("ALTER TABLE queue_counter ALTER COLUMN queue_date DATE NOT NULL")
            constraints = {item["name"] for item in inspect(connection).get_unique_constraints("queue_token")}
            if "uq_token_shop_number" in constraints:
                connection.exec_driver_sql("ALTER TABLE queue_token DROP CONSTRAINT uq_token_shop_number")
            if "uq_token_shop_day_number" not in constraints:
                connection.exec_driver_sql("ALTER TABLE queue_token ADD CONSTRAINT uq_token_shop_day_number UNIQUE (shop_id, queue_date, token_number)")
            indexes = {item["name"] for item in inspect(connection).get_indexes("queue_token")}
            if "uq_queue_active_customer_shop" not in indexes:
                connection.exec_driver_sql("CREATE UNIQUE INDEX uq_queue_active_customer_shop ON queue_token (customer_id, shop_id) WHERE status IN ('waiting', 'in_service')")
            if "ix_queue_token_queue_date" not in indexes:
                connection.exec_driver_sql("CREATE INDEX ix_queue_token_queue_date ON queue_token (queue_date)")
    else:
        raise RuntimeError(f"Queue schema upgrade is not configured for database dialect {dialect!r}.")
