"""The Telegram bot token must never reach the logs (20 Nov finding: a
`requests` connection error printed the full /bot<token>/sendMessage URL)."""
import logging
import sys
import types

from bot.notifications.telegram_client import TelegramNotifier, redact

FAKE = "1234567890:AAFakeTokenForTestsOnly_abcdefghijkl"


def _fake_requests(monkeypatch, post):
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))


def test_connection_error_text_is_redacted(monkeypatch, caplog):
    def post(url, json=None, timeout=None):
        raise ConnectionError(f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
                              f"Max retries exceeded with url: {url.split('.org')[1]}")
    _fake_requests(monkeypatch, post)
    caplog.set_level(logging.WARNING, logger="bot.notifications.telegram")
    assert TelegramNotifier(FAKE, "42").send("hello") is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Telegram send error" in text and "/bot<redacted>/sendMessage" in text
    assert FAKE not in text and "AAFakeToken" not in text


def test_http_error_body_is_redacted(monkeypatch, caplog):
    _fake_requests(monkeypatch, lambda url, json=None, timeout=None: types.SimpleNamespace(
        status_code=401, text=f"Unauthorized for {url}"))
    caplog.set_level(logging.WARNING, logger="bot.notifications.telegram")
    TelegramNotifier(FAKE, "42").send("hello")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP 401" in text and FAKE not in text


def test_success_path_still_sends_to_the_real_url(monkeypatch):
    seen = []
    _fake_requests(monkeypatch, lambda url, json=None, timeout=None: (
        seen.append(url), types.SimpleNamespace(status_code=200, text="ok"))[1])
    assert TelegramNotifier(FAKE, "42").send("hello") is True
    assert seen == [f"https://api.telegram.org/bot{FAKE}/sendMessage"]   # behaviour unchanged


def test_redact_handles_unknown_token_shapes_and_leaves_normal_text():
    assert redact(f"url /bot{FAKE}/x") == "url /bot<redacted>/x"
    assert redact(f"token {FAKE} leaked") == "token <redacted> leaked"
    assert redact("SPY down -2.57% at 12:30, VZ 45.33") == "SPY down -2.57% at 12:30, VZ 45.33"
