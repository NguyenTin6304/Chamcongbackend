"""Global pytest fixtures — run before every test in the suite.

MAIL_ENABLED is forced to False so tests never call Resend (or any other
real mail provider) even when the local .env has MAIL_ENABLED=True.

IMPORTANT: do NOT import from `app.*` at module level here.
Each test file sets os.environ["DATABASE_URL"] = "sqlite:///..." at line 1
before importing app modules. If conftest.py imports app at module level,
pytest processes conftest.py first, creating the engine with the real
PostgreSQL URL from .env — which breaks all tests.
The delayed import inside the fixture body runs AFTER test files have already
set their DATABASE_URL, so the SQLite engine is already in place.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _disable_mail():
    """Force NoopMailSender for the entire test session."""
    from app.core.config import settings  # delayed — must stay inside function
    original = settings.MAIL_ENABLED
    settings.MAIL_ENABLED = False
    yield
    settings.MAIL_ENABLED = original
