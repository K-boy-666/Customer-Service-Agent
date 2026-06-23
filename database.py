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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number   TEXT    NOT NULL UNIQUE,
                title           TEXT    NOT NULL,
                type            TEXT    NOT NULL DEFAULT 'incident'
                                CHECK(type IN ('incident','service_request','change_request','problem')),
                priority        TEXT    NOT NULL DEFAULT 'P3'
                                CHECK(priority IN ('P1','P2','P3','P4')),
                status          TEXT    NOT NULL DEFAULT 'new'
                                CHECK(status IN ('new','assigned','in_progress','pending','resolved','closed')),
                description     TEXT    NOT NULL DEFAULT '',
                customer_id     INTEGER REFERENCES customers(id),
                order_id        TEXT    REFERENCES orders(id),
                assignee        TEXT    DEFAULT '',
                department      TEXT    DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ticket_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                content     TEXT    NOT NULL,
                author      TEXT    NOT NULL DEFAULT 'system',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS returns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                return_number   TEXT    NOT NULL UNIQUE,
                order_id        TEXT    NOT NULL REFERENCES orders(id),
                customer_id     INTEGER REFERENCES customers(id),
                type            TEXT    NOT NULL DEFAULT 'return'
                                CHECK(type IN ('return','exchange','refund')),
                reason          TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending'
                                CHECK(status IN (
                                    'pending','approved','rejected','in_transit',
                                    'received','refunded','completed'
                                )),
                refund_amount   REAL    DEFAULT 0.0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS satisfaction_surveys (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_number   TEXT    NOT NULL UNIQUE,
                customer_id     INTEGER REFERENCES customers(id),
                order_id        TEXT    REFERENCES orders(id),
                rating          INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
                feedback_text   TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status   ON tickets(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_order    ON tickets(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_order    ON returns(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_customer ON returns(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_status   ON returns(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surveys_customer ON satisfaction_surveys(customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surveys_order    ON satisfaction_surveys(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surveys_rating   ON satisfaction_surveys(rating)")

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
