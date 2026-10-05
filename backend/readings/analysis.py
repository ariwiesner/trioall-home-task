"""
Read-time analysis over stored readings for one refrigerator: gap episodes,
temperature-breach episodes, gradual-warming episodes, and the Good/Problem/
Needs Review status rollup used by the dashboard.

Nothing here is persisted — see the design plan for why (small data volume,
avoids a whole class of staleness bugs). Every threshold below is an
explicit, documented assumption flagged in NOTES.md for confirmation with
Summer, not a guess buried in code.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

BREACH_THRESHOLD_C = 5.0
BREACH_MIN_DURATION_MINUTES = 30

WARMING_WINDOW_READINGS = 4
WARMING_MIN_RISE_C = 1.5

GAP_TOLERANCE_MULTIPLIER = 1.5  # slack on top of the expected interval before calling it a gap

STATUS_GOOD = "good"
STATUS_PROBLEM = "problem"
STATUS_NEEDS_REVIEW = "needs_review"

GAP_KIND_NEVER_REPORTED = "never_reported"
GAP_KIND_STOPPED_REPORTING = "stopped_reporting"


@dataclass
class Episode:
    start: object  # datetime
    end: object  # datetime
    max_temp: float | None = None

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class RefrigeratorStatus:
    """
    active_breach / active_warming mean "ongoing, or ended within the last
    ACTIVE_WINDOW_MINUTES" — NOT "happening at this exact instant". A
    fridge that spiked and recovered 40 minutes ago still shows active=True
    (and status="problem"/"needs_review") so it stays visible on the
    dashboard for a while after recovering, rather than disappearing the
    moment the last bad reading scrolls out. breach_episodes/gap_episodes/
    warming_episodes are always the FULL history regardless of active_*;
    active_* only gates which ones affect today's badge.
    """

    status: str  # "good" | "problem" | "needs_review"
    last_reading_at: object  # datetime | None
    # The most recent VALID reading's temperature — not necessarily the
    # reading at last_reading_at, which can be an ERR row. None if there's
    # no valid reading yet at all (hence "where available" in the UI).
    last_temperature_c: float | None
    # The *same* reading's original text value and unit as actually stored
    # (TemperatureReading.raw_temperature/temperature_unit) — kept alongside
    # last_temperature_c so the UI can show "Original: 39.2°F" without
    # guessing the unit from whichever logger is currently assigned (a
    # fridge's logger can change; the reading's own stored unit can't).
    last_reading_raw_temperature: str | None
    last_reading_temperature_unit: str
    breach_episodes: list
    gap_episodes: list
    warming_episodes: list
    active_breach: bool
    active_gap: bool
    # None unless active_gap is True, in which case: "never_reported" (no
    # reading has ever been placed on this refrigerator) or
    # "stopped_reporting" (it was reporting, then went quiet) — the
    # dashboard/API can message these very differently.
    active_gap_kind: str | None
    active_warming: bool


def _is_contiguous(t1, t2, expected_interval_minutes):
    return (t2 - t1).total_seconds() / 60 <= expected_interval_minutes * GAP_TOLERANCE_MULTIPLIER


def detect_gaps(readings):
    """
    readings: ascending list of (timestamp, expected_interval_minutes,
    logger_id) for every reading placed on this refrigerator — valid or
    not, since even an ERR reading proves the logger was alive.
    expected_interval_minutes travels with each reading (it's that
    reading's own logger's configured cadence) rather than being one value
    for the refrigerator's whole history: if the logger was swapped for one
    with a different interval, gaps on each side of the swap are judged
    against the cadence that was actually in effect at the time.

    A change in logger_id between consecutive readings always breaks
    continuity, regardless of how little time elapsed: a different
    instrument taking over isn't proof the fridge was watched continuously
    through the handover, so the boundary itself is reported as a gap
    rather than silently bridged.

    Returns every interval between consecutive readings that exceeds the
    (first reading's) expected interval, or crosses a logger change, as a
    historical record — always computed in full, regardless of current
    status (see design plan).
    """
    gaps = []
    for (t1, interval1, logger1), (t2, _interval2, logger2) in zip(readings, readings[1:]):
        if logger1 != logger2 or not _is_contiguous(t1, t2, interval1):
            gaps.append(Episode(start=t1, end=t2))
    return gaps


def detect_breach_episodes(readings):
    """
    readings: ascending list of (timestamp, temperature_c,
    expected_interval_minutes, logger_id), valid only. Interval and logger
    identity travel with each reading for the same reasons as in
    detect_gaps — a logger change always breaks a run, same as a time gap.

    We only ever observe discrete readings, not a continuous signal, so
    "sustained for >= 30 minutes" is an inference, not a measurement: if a
    run of consecutive, contiguous readings from the SAME logger are all
    above BREACH_THRESHOLD_C and its first and last readings are at least
    BREACH_MIN_DURATION_MINUTES apart, we treat the fridge as having been
    above threshold for the whole span between them — we have no way to
    know what happened strictly between two readings. This is what makes a
    single door-open spike (one reading, zero-length run) harmless while a
    genuinely sustained excursion is reported. A data gap, or a logger
    swap, breaks a run outright: we never extend that inference across
    time — or an instrument change — we can't vouch for continuity through,
    even if the readings on both sides are hot.
    """
    episodes = []
    run = []

    def flush():
        if len(run) < 2:
            return
        start, end = run[0][0], run[-1][0]
        if (end - start).total_seconds() / 60 >= BREACH_MIN_DURATION_MINUTES:
            episodes.append(Episode(start=start, end=end, max_temp=max(t for _, t, _, _ in run)))

    prev = None  # (timestamp, interval, logger_id)
    for ts, temp, interval, logger_id in readings:
        breaching = temp > BREACH_THRESHOLD_C
        contiguous = (
            prev is not None and prev[2] == logger_id and _is_contiguous(prev[0], ts, prev[1])
        )
        if breaching and run and contiguous:
            run.append((ts, temp, interval, logger_id))
        elif breaching:
            flush()
            run = [(ts, temp, interval, logger_id)]
        else:
            flush()
            run = []
        prev = (ts, interval, logger_id)
    flush()
    return episodes


def detect_warming_trends(readings):
    """
    readings: ascending list of (timestamp, temperature_c,
    expected_interval_minutes, logger_id), valid only. Flags a window of
    WARMING_WINDOW_READINGS consecutive, contiguous readings — same logger
    throughout, same as detect_gaps/detect_breach_episodes — that are
    non-decreasing throughout and rise by at least WARMING_MIN_RISE_C end
    to end — "a consistent upward trend", not a single increase or noise.
    Windows don't overlap once one is flagged, so one slow afternoon
    doesn't produce a pile of near-duplicate episodes.
    """
    episodes = []
    n = len(readings)
    i = 0
    while i <= n - WARMING_WINDOW_READINGS:
        window = readings[i : i + WARMING_WINDOW_READINGS]
        contiguous = all(
            a[3] == b[3] and _is_contiguous(a[0], b[0], a[2]) for a, b in zip(window, window[1:])
        )
        temps = [t for _, t, _, _ in window]
        non_decreasing = all(b >= a for a, b in zip(temps, temps[1:]))
        total_rise = temps[-1] - temps[0]
        if contiguous and non_decreasing and total_rise >= WARMING_MIN_RISE_C:
            episodes.append(Episode(start=window[0][0], end=window[-1][0], max_temp=temps[-1]))
            i += WARMING_WINDOW_READINGS
        else:
            i += 1
    return episodes


def get_refrigerator_status(refrigerator, now=None) -> RefrigeratorStatus:
    now = now or timezone.now()
    window = timedelta(minutes=settings.ACTIVE_WINDOW_MINUTES)

    # Every reading attached to a specific refrigerator necessarily has a
    # resolved `logger` (refrigerator is only ever set alongside it — see
    # ingestion.py), so `.logger.expected_interval_minutes` is always safe.
    placed_readings = list(
        refrigerator.readings.exclude(timestamp=None).select_related("logger").order_by("timestamp")
    )

    gap_episodes = detect_gaps(
        [(r.timestamp, r.logger.expected_interval_minutes, r.logger_id) for r in placed_readings]
    )
    valid_readings = [r for r in placed_readings if r.is_valid]
    valid_input = [
        (r.timestamp, r.temperature_c, r.logger.expected_interval_minutes, r.logger_id)
        for r in valid_readings
    ]
    breach_episodes = detect_breach_episodes(valid_input)
    warming_episodes = detect_warming_trends(valid_input)

    last_reading = placed_readings[-1] if placed_readings else None
    last_reading_at = last_reading.timestamp if last_reading else None
    last_valid_reading = valid_readings[-1] if valid_readings else None
    last_temperature_c = last_valid_reading.temperature_c if last_valid_reading else None
    last_reading_raw_temperature = last_valid_reading.raw_temperature if last_valid_reading else None
    last_reading_temperature_unit = last_valid_reading.temperature_unit if last_valid_reading else ""

    active_gap_kind = None
    if last_reading is None:
        active_gap = True
        active_gap_kind = GAP_KIND_NEVER_REPORTED
    else:
        active_gap = not _is_contiguous(
            last_reading.timestamp, now, last_reading.logger.expected_interval_minutes
        )
        if active_gap:
            active_gap_kind = GAP_KIND_STOPPED_REPORTING

    active_breach = any(e.end >= now - window for e in breach_episodes)
    active_warming = any(e.end >= now - window for e in warming_episodes)

    if active_breach:
        status = STATUS_PROBLEM
    elif active_gap or active_warming:
        status = STATUS_NEEDS_REVIEW
    else:
        status = STATUS_GOOD

    return RefrigeratorStatus(
        status=status,
        last_reading_at=last_reading_at,
        last_temperature_c=last_temperature_c,
        last_reading_raw_temperature=last_reading_raw_temperature,
        last_reading_temperature_unit=last_reading_temperature_unit,
        breach_episodes=breach_episodes,
        gap_episodes=gap_episodes,
        warming_episodes=warming_episodes,
        active_breach=active_breach,
        active_gap=active_gap,
        active_gap_kind=active_gap_kind,
        active_warming=active_warming,
    )
