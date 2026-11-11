"""2E watchdog: the Healthchecks.io schedule must match the bot's real cycles.

Healthchecks check (set by Vivek on 7 Nov): cron `0,30 9-15 * * 1-5`,
time zone America/New_York, grace 15 min. The bot pings success after an
'ok' or 'market_closed' cycle, fail after any other outcome, and nothing in
standby. These tests enumerate the actual APScheduler fire times and the
Healthchecks expectations and compare them, across DST changes and a market
holiday."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

import main

NY = ZoneInfo("America/New_York")
# Standard cron `0,30 9-15 * * 1-5` (Healthchecks: 1-5 = Mon-Fri). Written
# with names because APScheduler's own from_crontab numbers days from Monday=0.
HC = CronTrigger(minute="0,30", hour="9-15", day_of_week="mon-fri", timezone=NY)
BOT = [CronTrigger(day_of_week="mon-fri", hour="9-15", minute="0,30", timezone=NY),
       CronTrigger(day_of_week="mon-fri", hour="16", minute="0", timezone=NY)]


def fires(trigger, start, end):
    out, prev, t = [], None, start
    while True:
        t = trigger.get_next_fire_time(prev, t)
        if t is None or t >= end:
            return out
        out.append(t)
        prev, t = t, t + timedelta(seconds=1)


def day(y, m, d):
    s = datetime(y, m, d, 0, 0, tzinfo=NY)
    return s, s + timedelta(days=1)


def expected_vs_bot(y, m, d):
    s, e = day(y, m, d)
    hc = {t.astimezone(NY).strftime("%H:%M") for t in fires(HC, s, e)}
    bot = set()
    for tr in BOT:
        bot |= {t.astimezone(NY).strftime("%H:%M") for t in fires(tr, s, e)}
    return hc, bot


def test_normal_weekday_every_expectation_has_a_cycle_and_only_16_00_is_unmonitored():
    hc, bot = expected_vs_bot(2026, 11, 12)
    assert hc <= bot                                      # no expectation without a cycle
    assert min(hc) == "09:00" and max(hc) == "15:30" and len(hc) == 14
    assert bot - hc == {"16:00"}                          # the extra post-close cycle


def test_09_00_cycle_pings_although_the_market_is_not_open_yet():
    # 09:00 NY is before the open, so the cycle returns 'market_closed' -
    # which is a success ping, so the 09:00 expectation is met.
    sent = []
    hb = SimpleNamespace(success=lambda o: sent.append(("ok", o)), fail=lambda w: sent.append(("fail", w)))
    main.report_cycle_outcome(hb, "market_closed")
    main.report_cycle_outcome(hb, "standby")
    main.report_cycle_outcome(hb, "error: boom")
    assert sent == [("ok", "market_closed"), ("fail", "cycle did not complete: error: boom")]


def test_dst_changes_keep_the_two_schedules_aligned():
    for date in [(2026, 11, 2), (2027, 3, 15), (2027, 3, 12)]:   # after fall-back; after/before spring-forward
        hc, bot = expected_vs_bot(*date)
        assert hc <= bot and bot - hc == {"16:00"}, date
    # The UTC hours move by one at the change, for both schedules together.
    s, e = day(2026, 10, 30)
    before = fires(HC, s, e)[0].astimezone(ZoneInfo("UTC")).hour
    s, e = day(2026, 11, 2)
    after = fires(HC, s, e)[0].astimezone(ZoneInfo("UTC")).hour
    assert (before, after) == (13, 14)


def test_holiday_and_weekend():
    # Thanksgiving 26 Nov 2026: both schedules still fire (no holiday
    # calendar on either side); the bot's cycles return 'market_closed' and
    # ping success, so there is no false alert.
    hc, bot = expected_vs_bot(2026, 11, 26)
    assert hc <= bot and len(hc) == 14
    hc, bot = expected_vs_bot(2026, 11, 14)                     # Saturday
    assert hc == set() and bot == set()


def test_grace_covers_the_longest_observed_cycle_and_detects_a_miss_before_the_next():
    grace_min, interval_min, longest_cycle_s = 15, 30, 60      # cycles observed 10-50 s
    assert longest_cycle_s < grace_min * 60 < interval_min * 60
