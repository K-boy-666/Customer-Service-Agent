"""Shared pytest fixtures for concurrency and load tests.

Existing unittest-style tests keep their own setUp/tearDown. This conftest
provides fixtures for the new concurrency test files to reduce boilerplate.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# Disable rate limiting by default in tests to avoid interfering with existing tests.
# Rate limit tests re-enable it via their own fixture.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

# Ensure src/ is on the path for all test modules.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import database
import seed_data
from numbering import reset_for_tests
from orchestrator_runtime import reset_conversation_states_for_tests
from security import Actor, request_otp, verify_otp


@pytest.fixture()
def temp_db():
    """Create a fresh SQLite temp database, seed it, and clean up after."""
    fd, db_path = tempfile.mkstemp(prefix="concurrency-test-", suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + db_path.replace("\\", "/")
    os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
    database.reset_engine_for_tests()
    reset_for_tests()
    reset_conversation_states_for_tests()
    database.init_db()
    session = database.get_session()
    try:
        seed_data.seed(session)
    finally:
        session.close()
    yield db_path
    database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
    for suffix in ("", "-wal", "-shm"):
        path = f"{db_path}{suffix}"
        if os.path.exists(path):
            os.remove(path)


@pytest.fixture()
def actor():
    return Actor("test-orchestrator", "orchestrator", {})


@pytest.fixture()
def verification_token(temp_db):
    """Return a verification token for customer 1 / order SO20260601001."""
    from models import Order

    session = database.get_session()
    try:
        order = session.query(Order).filter_by(status="delivered").order_by(Order.created_at.desc()).first()
        assert order is not None
        customer_id = order.customer_id
        order_id = order.id
        challenge = request_otp(session, "customer_identity", "email", "test@example.com", customer_id, order_id)
        verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
        token = verified["token"]
    finally:
        session.close()
    return token
