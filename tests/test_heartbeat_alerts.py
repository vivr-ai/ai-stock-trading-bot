"""Reliability v3: 2E external heartbeat and the alert de-duplicator."""
import main
from bot.reliability.alerts import AlertDeduper
from bot.reliability.heartbeat_ping import ExternalHeartbeat


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_hb(url="https://hc-ping.com/abc-123", status=200, clock=None):
    calls = []

    def post(u, body, timeout):
        calls.append((u, body))
        if isinstance(status, Exception):
            raise status
        return status

    return ExternalHeartbeat(url, post=post, clock=clock or Clock()), calls


def test_disabled_without_url_makes_no_calls():
    hb, calls = make_hb(url="")
    assert not hb.enabled
    assert hb.success() is False and hb.fail("x") is False and calls == []


def test_success_and_fail_urls():
    hb, calls = make_hb()
    assert hb.success("ok")
    assert hb.fail("snapshot failed")
    assert calls[0][0] == "https://hc-ping.com/abc-123"
    assert calls[1][0] == "https://hc-ping.com/abc-123/fail"


def test_ping_errors_never_raise():
    hb, _ = make_hb(status=RuntimeError("network down"))
    assert hb.success() is False and hb.fail("x") is False
    hb2, _ = make_hb(status=500)
    assert hb2.success() is False


def test_repeated_failures_rate_limited_until_recovery():
    clock = Clock()
    hb, calls = make_hb(clock=clock)
    hb.fail("a")
    hb.fail("b")          # suppressed (within 10 min)
    clock.t += 601
    hb.fail("c")          # reminder
    hb.success()
    hb.fail("d")          # new failure streak -> sent at once
    assert [c[1] for c in calls if c[0].endswith("/fail")] == ["a", "c", "d"]


def test_only_genuine_success_pings():
    hb, calls = make_hb()
    for outcome in ("ok", "market_closed"):
        main.report_cycle_outcome(hb, outcome)
    main.report_cycle_outcome(hb, "standby")               # nothing
    main.report_cycle_outcome(hb, "account snapshot failed: boom")
    main.report_cycle_outcome(hb, None)                     # never finished
    urls = [c[0] for c in calls]
    assert urls.count("https://hc-ping.com/abc-123") == 2
    assert urls.count("https://hc-ping.com/abc-123/fail") == 1  # second fail rate-limited


def test_alert_dedup_repeat_and_resolve():
    sent, clock = [], Clock()
    a = AlertDeduper(lambda t, s: sent.append((t, s)), clock=clock)
    assert a.raise_("db", "down", "critical")
    assert not a.raise_("db", "down again", "critical")
    clock.t += 3601
    assert a.raise_("db", "still down", "critical")
    assert a.resolve("db", "recovered")
    assert not a.resolve("db", "recovered twice")   # not active any more
    assert [t for t, _ in sent] == ["down", "still down", "recovered"]


def test_alert_daily_cap_sends_one_storm_notice():
    sent, clock = [], Clock()
    a = AlertDeduper(lambda t, s: sent.append(t), clock=clock, daily_cap=3, new_cap=3)
    for i in range(6):
        a.raise_(f"k{i}", f"msg{i}")                  # non-critical new problems: own cap
    assert sent[:3] == ["msg0", "msg1", "msg2"]
    assert len(sent) == 4 and sent[3].startswith("Alert storm")
    clock.t += 86400
    assert a.raise_("new", "next day")


def test_reminders_hit_the_cap_but_new_critical_and_recovery_still_go_out():
    """Staging 9 Nov: a 15-minute reminder for one stuck position used up the
    whole daily cap, which would have silenced any other emergency."""
    sent, clock = [], Clock()
    a = AlertDeduper(lambda t, s: sent.append(t), clock=clock, daily_cap=3)
    a.raise_("prot_manual:VZ", "VZ manual", "critical", 900)
    for _ in range(6):
        clock.t += 901
        a.raise_("prot_manual:VZ", "VZ manual", "critical", 900)
    assert sent.count("VZ manual") == 4              # first + 3 reminders
    assert sum(1 for t in sent if t.startswith("Alert storm")) == 1
    assert a.raise_("db_down", "DB down", "critical")           # new emergency: sent
    assert a.raise_("prot_missing:AAPL", "AAPL unprotected", "critical")
    assert a.resolve("prot_manual:VZ", "VZ recovered")           # recovery: sent
    assert sent[-3:] == ["DB down", "AAPL unprotected", "VZ recovered"]


def test_alert_send_failure_never_raises():
    a = AlertDeduper(lambda t, s: (_ for _ in ()).throw(RuntimeError("telegram down")))
    assert a.raise_("k", "x") is False
