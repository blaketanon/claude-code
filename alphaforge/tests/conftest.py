import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_tmp = tempfile.mkdtemp(prefix="alphaforge-test-")
os.environ["ALPHAFORGE_DATA_DIR"] = _tmp
os.environ["ALPHAFORGE_DB_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ALPHAFORGE_DATA_PROVIDER"] = "synthetic"
os.environ["ALPHAFORGE_SYMBOLS"] = "AAA,BBB,CCC"
os.environ["ALPHAFORGE_ADMIN_PASSWORD"] = "test-pass"
os.environ["ALPHAFORGE_SECRET_KEY"] = "test-secret"
os.environ["ANTHROPIC_API_KEY"] = ""


@pytest.fixture(scope="session")
def bars():
    from alphaforge.data import SyntheticProvider

    p = SyntheticProvider()
    end = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)
    start = end - timedelta(days=30)
    return {s: p.fetch_bars(s, "5m", start, end) for s in ["AAA", "BBB", "CCC"]}


@pytest.fixture(scope="session", autouse=True)
def _db():
    from alphaforge.db import init_db

    init_db()
