import pytest


@pytest.fixture(autouse=True)
def _no_real_sleep_in_db_retries(monkeypatch):
    """Recorder retries failed writes with short sleeps (v3 2G). Tests that
    care about the timing record the sleeps themselves; everything else
    shouldn't wait."""
    import bot.persistence.db as db

    monkeypatch.setattr(db, "_sleep", lambda s: None)
