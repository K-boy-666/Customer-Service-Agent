"""
SQLite persistence layer for the order system.

Zero new dependencies — sqlite3 is part of the Python standard library.
Uses WAL mode for concurrent reads and enforces foreign keys.
"""

import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "orders.db")


def get_db() -> sqlite3.Connection:
    """Return a connection with WAL mode, foreign keys, and Row factory.

    check_same_thread=False is safe here because FastAPI ``def`` (sync) endpoints
    run in the thread pool — each request gets its own connection lifecycle.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all tables and indexes if they don't already exist.

    Idempotent — safe to call on every startup.
    """
    conn = get_db()
    try:
        # -----------------------------------------------------------------
        # Tables
        # -----------------------------------------------------------------

        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                email           TEXT    NOT NULL UNIQUE,
                phone           TEXT    NOT NULL,
                membership_tier TEXT    NOT NULL DEFAULT 'standard'
                                CHECK(membership_tier IN ('standard','silver','gold','platinum')),
                points          INTEGER NOT NULL DEFAULT 0,
                joined_at       TEXT    NOT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku         TEXT PRIMARY KEY,
                name        TEXT    NOT NULL,
                category    TEXT    NOT NULL,
                unit_price  REAL    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id                TEXT PRIMARY KEY,
                order_number      TEXT    NOT NULL UNIQUE,
                customer_id       INTEGER NOT NULL REFERENCES customers(id),
                status            TEXT    NOT NULL DEFAULT 'pending'
                                  CHECK(status IN ('pending','shipped','delivered','cancelled')),
                total_amount      REAL    NOT NULL,
                currency          TEXT    NOT NULL DEFAULT 'CNY',
                shipping_address  TEXT    NOT NULL,
                created_at        TEXT    NOT NULL,
                updated_at        TEXT    NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT    NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                sku      TEXT    NOT NULL REFERENCES products(sku),
                name     TEXT    NOT NULL,
                qty      INTEGER NOT NULL,
                price    REAL    NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id            TEXT    NOT NULL UNIQUE REFERENCES orders(id),
                carrier             TEXT    NOT NULL,
                tracking_number     TEXT    NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK(status IN (
                                        'pending','picked_up','in_transit',
                                        'out_for_delivery','delivered','failed','returned'
                                    )),
                estimated_delivery  TEXT,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipment_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id  INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
                status       TEXT    NOT NULL,
                location     TEXT    NOT NULL,
                description  TEXT    NOT NULL,
                event_time   TEXT    NOT NULL
            )
        """)

        # -----------------------------------------------------------------
        # Indexes
        # -----------------------------------------------------------------

        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer  ON orders(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created   ON orders(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_order      ON order_items(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shipments_order  ON shipments(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shipments_track  ON shipments(tracking_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_events_ship ON shipment_events(shipment_id, event_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_name   ON customers(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_email  ON customers(email)")

        conn.commit()
    finally:
        conn.close()


def is_db_empty() -> bool:
    """Return True if the orders table has zero rows."""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM orders").fetchone()
        return row["cnt"] == 0
    finally:
        conn.close()
