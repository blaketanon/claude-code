"""NYSE session clock (regular hours, weekday + fixed-holiday aware)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Observed NYSE full-day closures (extend yearly; early closes handled by eod_cutoff override).
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}


class MarketClock:
    def __init__(self, open_t: time = time(9, 30), close_t: time = time(16, 0), eod_minutes_before_close: int = 10):
        self.open_t, self.close_t = open_t, close_t
        self.eod_buffer = timedelta(minutes=eod_minutes_before_close)

    def now(self) -> datetime:
        return datetime.now(NY)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in HOLIDAYS

    def session_bounds(self, d: date) -> tuple[datetime, datetime]:
        return datetime.combine(d, self.open_t, NY), datetime.combine(d, self.close_t, NY)

    def is_open(self, ts: datetime | None = None) -> bool:
        ts = (ts or self.now()).astimezone(NY)
        if not self.is_trading_day(ts.date()):
            return False
        o, c = self.session_bounds(ts.date())
        return o <= ts < c

    def in_eod_window(self, ts: datetime | None = None) -> bool:
        ts = (ts or self.now()).astimezone(NY)
        if not self.is_open(ts):
            return False
        _, c = self.session_bounds(ts.date())
        return ts >= c - self.eod_buffer

    def next_open(self, ts: datetime | None = None) -> datetime:
        ts = (ts or self.now()).astimezone(NY)
        d = ts.date()
        o, _ = self.session_bounds(d)
        if self.is_trading_day(d) and ts < o:
            return o
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return self.session_bounds(d)[0]

    def seconds_to_next_bar(self, timeframe_seconds: int, ts: datetime | None = None) -> float:
        ts = ts or self.now()
        epoch = ts.timestamp()
        return timeframe_seconds - (epoch % timeframe_seconds) + 2.0  # +2s grace for bar finalization
