from datetime import datetime, timedelta

from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator
from readings.analysis import (
    GAP_KIND_NEVER_REPORTED,
    GAP_KIND_STOPPED_REPORTING,
    STATUS_GOOD,
    STATUS_NEEDS_REVIEW,
    STATUS_PROBLEM,
    detect_breach_episodes,
    detect_gaps,
    detect_warming_trends,
    get_refrigerator_status,
)
from readings.models import TemperatureReading, UploadedFile

BASE = timezone.make_aware(datetime(2026, 9, 14, 6, 0))


def minutes(n):
    return BASE + timedelta(minutes=n)


def series(*triples, interval=15, logger_id=1):
    """
    triples: (minute_offset, temp), (minute_offset, temp, interval), or
    (minute_offset, temp, interval, logger_id) to override cadence and/or
    logger identity for that specific reading. Returns the ascending
    (timestamp, temp, expected_interval_minutes, logger_id) list the
    analysis functions expect.
    """
    out = []
    for item in triples:
        if len(item) == 2:
            m, t = item
            out.append((minutes(m), t, interval, logger_id))
        elif len(item) == 3:
            m, t, iv = item
            out.append((minutes(m), t, iv, logger_id))
        else:
            m, t, iv, lid = item
            out.append((minutes(m), t, iv, lid))
    return out


def gap_series(*pairs, interval=15, logger_id=1):
    """
    pairs: (minute_offset, interval_override_or_None), or
    (minute_offset, interval_override_or_None, logger_id_override).
    Returns the ascending (timestamp, expected_interval_minutes, logger_id)
    list detect_gaps expects.
    """
    out = []
    for item in pairs:
        if len(item) == 2:
            m, iv = item
            out.append((minutes(m), iv if iv is not None else interval, logger_id))
        else:
            m, iv, lid = item
            out.append((minutes(m), iv if iv is not None else interval, lid))
    return out


class DetectGapsTests(TestCase):
    def test_no_gap_when_evenly_spaced(self):
        readings = gap_series((0, None), (15, None), (30, None), (45, None))
        self.assertEqual(detect_gaps(readings), [])

    def test_gap_detected_beyond_tolerance(self):
        # 15 * 1.5 = 22.5 min tolerance; a 2-hour jump is clearly a gap.
        readings = gap_series((0, None), (15, None), (135, None))
        gaps = detect_gaps(readings)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].start, minutes(15))
        self.assertEqual(gaps[0].end, minutes(135))

    def test_small_jitter_within_tolerance_is_not_a_gap(self):
        readings = gap_series((0, None), (20, None))  # 20 <= 15*1.5
        self.assertEqual(detect_gaps(readings), [])

    def test_interval_travels_with_each_reading_not_the_whole_series(self):
        # First reading's logger reports every 30 min (slow); the gap check
        # between it and the next reading must use ITS interval (30), not
        # whatever a later/different logger happens to use.
        readings = gap_series((0, 30, 1), (30, 15, 1))  # same logger throughout
        self.assertEqual(detect_gaps(readings), [])

    def test_logger_change_is_a_gap_even_with_no_time_gap(self):
        # Same timing as a normal 15-min interval, but a different logger —
        # a clean instrument swap still isn't proof of continuous coverage.
        readings = gap_series((0, 15, 1), (15, 15, 2))
        gaps = detect_gaps(readings)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].start, minutes(0))
        self.assertEqual(gaps[0].end, minutes(15))


class DetectBreachEpisodesTests(TestCase):
    def test_single_spike_that_returns_to_normal_is_not_a_breach(self):
        readings = series((0, 4.0), (15, 9.4), (30, 4.3))
        self.assertEqual(detect_breach_episodes(readings), [])

    def test_two_consecutive_breaching_readings_under_30_min_is_not_a_breach(self):
        readings = series((0, 4.0), (15, 6.0), (30, 6.2), (45, 4.0))
        self.assertEqual(detect_breach_episodes(readings), [])

    def test_sustained_breach_of_exactly_30_minutes_is_flagged(self):
        readings = series((0, 4.0), (15, 6.0), (30, 6.2), (45, 6.1), (60, 4.0))
        episodes = detect_breach_episodes(readings)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].start, minutes(15))
        self.assertEqual(episodes[0].end, minutes(45))
        self.assertEqual(episodes[0].duration_minutes, 30)
        self.assertEqual(episodes[0].max_temp, 6.2)

    def test_exactly_five_degrees_does_not_count_as_breaching(self):
        readings = series((0, 5.0), (15, 5.0), (30, 5.0), (45, 5.0))
        self.assertEqual(detect_breach_episodes(readings), [])

    def test_a_data_gap_breaks_the_breach_run_instead_of_bridging_it(self):
        # Two separate 15-minute-span breaching runs either side of a big
        # gap; neither individually reaches 30 minutes, and we don't claim
        # the fridge was hot for the time in between that we have no
        # reading for.
        readings = series((0, 6.0), (15, 6.1), (200, 6.2), (215, 6.3))
        self.assertEqual(detect_breach_episodes(readings), [])

    def test_matches_the_rishon_incident_shape_in_the_sample_data(self):
        # Cream cakes fridge climbing 4.6 -> 5.4 -> 6.3 -> 7.1 over 45 min.
        readings = series((0, 4.6), (15, 5.4), (30, 6.3), (45, 7.1))
        episodes = detect_breach_episodes(readings)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].duration_minutes, 30)
        self.assertEqual(episodes[0].max_temp, 7.1)

    def test_a_logger_change_breaks_a_breach_run_even_without_a_time_gap(self):
        # Four breaching readings at a normal cadence would otherwise be a
        # 30-minute sustained breach, but a different logger reports the
        # second half — two separate 15-minute-span runs, neither long
        # enough on its own.
        readings = series((0, 6.0, 15, 1), (15, 6.1, 15, 1), (30, 6.2, 15, 2), (45, 6.3, 15, 2))
        self.assertEqual(detect_breach_episodes(readings), [])


class DetectWarmingTrendsTests(TestCase):
    def test_consistent_rise_entirely_below_five_degrees_is_flagged(self):
        readings = series((0, 3.0), (15, 3.5), (30, 4.0), (45, 4.6))
        episodes = detect_warming_trends(readings)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].start, minutes(0))
        self.assertEqual(episodes[0].end, minutes(45))
        self.assertLess(episodes[0].max_temp, 5.0)

    def test_small_fluctuations_are_not_a_trend(self):
        readings = series((0, 3.5), (15, 3.4), (30, 3.6), (45, 3.5))
        self.assertEqual(detect_warming_trends(readings), [])

    def test_rise_below_minimum_delta_is_not_a_trend(self):
        readings = series((0, 3.0), (15, 3.2), (30, 3.4), (45, 3.6))  # total rise 0.6
        self.assertEqual(detect_warming_trends(readings), [])

    def test_non_monotonic_sequence_is_not_a_trend(self):
        # Rises overall but dips partway through — not "consistent".
        readings = series((0, 3.0), (15, 3.8), (30, 3.2), (45, 4.6))
        self.assertEqual(detect_warming_trends(readings), [])

    def test_a_gap_breaks_the_window(self):
        readings = series((0, 3.0), (15, 3.5), (200, 4.0), (215, 4.6))
        self.assertEqual(detect_warming_trends(readings), [])

    def test_a_logger_change_breaks_the_window_even_without_a_time_gap(self):
        # Same rise, same timing as the flagged case above, but the second
        # half comes from a different logger.
        readings = series((0, 3.0, 15, 1), (15, 3.5, 15, 1), (30, 4.0, 15, 2), (45, 4.6, 15, 2))
        self.assertEqual(detect_warming_trends(readings), [])


class GetRefrigeratorStatusTests(TestCase):
    def setUp(self):
        branch = Branch.objects.create(name="Jerusalem")
        self.fridge = Refrigerator.objects.create(branch=branch, name="Dairy")
        self.logger = Logger.objects.create(external_id="TL-0512", expected_interval_minutes=15)
        LoggerAssignment.objects.create(
            logger=self.logger, refrigerator=self.fridge, start_at=BASE - timedelta(days=1)
        )
        self.upload = UploadedFile.objects.create(original_filename="f.csv", file_hash="h")

    def _add(self, minute_offset, temp_c, is_valid=True, reason="", logger=None):
        logger = logger or self.logger
        TemperatureReading.objects.create(
            uploaded_file=self.upload,
            logger=logger,
            refrigerator=self.fridge,
            raw_logger_code=logger.external_id,
            raw_timestamp=str(minute_offset),
            raw_temperature=str(temp_c),
            timestamp=minutes(minute_offset),
            temperature_c=temp_c,
            is_valid=is_valid,
            reason=reason,
        )

    def test_good_status_for_a_quiet_fridge(self):
        for m, t in [(0, 3.8), (15, 3.9), (30, 4.0), (45, 3.9)]:
            self._add(m, t)
        now = minutes(50)
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertEqual(result.status, STATUS_GOOD)
        self.assertEqual(result.breach_episodes, [])
        self.assertIsNone(result.active_gap_kind)

    def test_problem_status_for_an_active_sustained_breach(self):
        for m, t in [(0, 4.0), (15, 6.0), (30, 6.2), (45, 6.1)]:
            self._add(m, t)
        now = minutes(50)  # within ACTIVE_WINDOW_MINUTES of the breach's end
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertEqual(result.status, STATUS_PROBLEM)
        self.assertTrue(result.active_breach)
        self.assertEqual(len(result.breach_episodes), 1)

    @override_settings(ACTIVE_WINDOW_MINUTES=120)
    def test_an_old_breach_outside_the_active_window_is_history_not_a_problem(self):
        for m, t in [(0, 4.0), (15, 6.0), (30, 6.2), (45, 6.1), (60, 4.0)]:
            self._add(m, t)
        now = minutes(60) + timedelta(minutes=200)  # long after the breach ended
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertNotEqual(result.status, STATUS_PROBLEM)
        self.assertFalse(result.active_breach)
        self.assertEqual(len(result.breach_episodes), 1)  # still visible in history

    def test_needs_review_for_an_active_gap_that_never_reported(self):
        result = get_refrigerator_status(self.fridge, now=minutes(0))
        self.assertEqual(result.status, STATUS_NEEDS_REVIEW)
        self.assertTrue(result.active_gap)
        self.assertEqual(result.active_gap_kind, GAP_KIND_NEVER_REPORTED)
        self.assertIsNone(result.last_reading_at)

    def test_needs_review_for_an_active_gap_that_stopped_reporting(self):
        self._add(0, 3.8)
        now = minutes(0) + timedelta(hours=5)  # nothing reported since, way overdue
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertEqual(result.status, STATUS_NEEDS_REVIEW)
        self.assertTrue(result.active_gap)
        self.assertEqual(result.active_gap_kind, GAP_KIND_STOPPED_REPORTING)

    def test_an_old_historical_gap_does_not_affect_current_status_once_reporting_resumes(self):
        self._add(0, 3.8)
        self._add(200, 3.9)  # big historical gap, but reporting has resumed
        self._add(215, 4.0)
        now = minutes(220)
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertFalse(result.active_gap)
        self.assertIsNone(result.active_gap_kind)
        self.assertEqual(len(result.gap_episodes), 1)  # still visible in history

    def test_needs_review_for_an_active_warming_trend(self):
        for m, t in [(0, 3.0), (15, 3.5), (30, 4.0), (45, 4.6)]:
            self._add(m, t)
        now = minutes(50)
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertEqual(result.status, STATUS_NEEDS_REVIEW)
        self.assertTrue(result.active_warming)

    def test_problem_takes_precedence_over_needs_review(self):
        # Sustained breach *and* a stale-looking last reading at once: 60
        # minutes past the last (breaching) reading is enough to also read
        # as an active gap (> 22.5 min), while still inside the default
        # 120-minute active-breach window.
        for m, t in [(0, 4.0), (15, 6.0), (30, 6.2), (45, 6.1)]:
            self._add(m, t)
        now = minutes(45) + timedelta(minutes=60)
        result = get_refrigerator_status(self.fridge, now=now)
        self.assertTrue(result.active_gap)
        self.assertEqual(result.status, STATUS_PROBLEM)

    def test_last_temperature_c_is_the_latest_valid_reading_not_necessarily_the_latest_reading(self):
        self._add(0, 3.8)
        self._add(15, None, is_valid=False, reason=TemperatureReading.REASON_INVALID_TEMPERATURE)
        result = get_refrigerator_status(self.fridge, now=minutes(20))
        self.assertEqual(result.last_reading_at, minutes(15))  # the ERR reading
        self.assertEqual(result.last_temperature_c, 3.8)  # the last VALID one

    def test_last_temperature_c_is_none_when_there_is_no_valid_reading_yet(self):
        self._add(0, None, is_valid=False, reason=TemperatureReading.REASON_INVALID_TEMPERATURE)
        result = get_refrigerator_status(self.fridge, now=minutes(5))
        self.assertIsNone(result.last_temperature_c)

    def test_last_reading_raw_temperature_and_unit_reflect_the_actual_stored_reading(self):
        # Must come from the reading's own stored fields, not be guessed
        # from whichever logger is currently assigned — see analysis.py's
        # RefrigeratorStatus docstring.
        TemperatureReading.objects.create(
            uploaded_file=self.upload,
            logger=self.logger,
            refrigerator=self.fridge,
            raw_logger_code=self.logger.external_id,
            raw_timestamp="2026-09-14 06:00",
            raw_temperature="39.2",
            temperature_unit=Logger.UNIT_FAHRENHEIT,
            timestamp=minutes(0),
            temperature_c=4.0,
            is_valid=True,
        )
        result = get_refrigerator_status(self.fridge, now=minutes(5))
        self.assertEqual(result.last_reading_raw_temperature, "39.2")
        self.assertEqual(result.last_reading_temperature_unit, Logger.UNIT_FAHRENHEIT)

    def test_last_reading_raw_temperature_is_none_when_there_is_no_reading_yet(self):
        result = get_refrigerator_status(self.fridge, now=minutes(0))
        self.assertIsNone(result.last_reading_raw_temperature)
        self.assertEqual(result.last_reading_temperature_unit, "")

    def test_err_readings_count_for_gap_detection_but_not_breach_detection(self):
        self._add(0, 3.8)
        self._add(15, None, is_valid=False, reason=TemperatureReading.REASON_INVALID_TEMPERATURE)
        self._add(30, 3.9)
        now = minutes(35)
        result = get_refrigerator_status(self.fridge, now=now)
        # No gap: the ERR reading at minute 15 still proves the logger was alive.
        self.assertEqual(result.gap_episodes, [])
        self.assertFalse(result.active_gap)

    def test_gap_detection_across_a_reassignment_uses_each_periods_own_interval_and_flags_the_handover(self):
        # A slow logger (30-min cadence), replaced by a fast one (15-min).
        # Within each period, spacing matching that logger's own cadence
        # must NOT be a false gap (judging the slow period against the fast
        # logger's interval would wrongly flag every one of its readings).
        # The handover itself, though, is always flagged — a different
        # instrument taking over isn't proof of continuous coverage, even
        # though only 15 minutes elapsed (comfortably within either
        # logger's own tolerance).
        slow_logger = Logger.objects.create(external_id="TL-SLOW", expected_interval_minutes=30)
        fast_logger = Logger.objects.create(external_id="TL-FAST", expected_interval_minutes=15)
        switch = minutes(60)
        LoggerAssignment.objects.filter(logger=self.logger, refrigerator=self.fridge).delete()
        LoggerAssignment.objects.create(
            logger=slow_logger, refrigerator=self.fridge, start_at=BASE - timedelta(days=1), end_at=switch
        )
        LoggerAssignment.objects.create(logger=fast_logger, refrigerator=self.fridge, start_at=switch)

        for m in (0, 30, 60):  # slow logger, 30-min spacing
            self._add(m, 3.8, logger=slow_logger)
        for m in (75, 90, 105):  # fast logger, 15-min spacing
            self._add(m, 3.8, logger=fast_logger)

        result = get_refrigerator_status(self.fridge, now=minutes(110))
        self.assertEqual(len(result.gap_episodes), 1)
        self.assertEqual(result.gap_episodes[0].start, minutes(60))
        self.assertEqual(result.gap_episodes[0].end, minutes(75))
