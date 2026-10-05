import io
from datetime import datetime

from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator
from readings import signals as readings_signals
from readings.ingestion import DuplicateFileError, ingest_upload, reprocess_unresolved_readings
from readings.models import TemperatureReading
from readings.parsing import FileParseError

# Fixed, far-past assignment start so these tests don't depend on "today"'s
# real date relative to the hardcoded 2026-09-xx reading timestamps below.
FAR_PAST = timezone.make_aware(datetime(2020, 1, 1))


def csv_file(headers, rows) -> io.BytesIO:
    lines = [",".join(headers)]
    lines += [",".join(str(v) for v in row) for row in rows]
    return io.BytesIO("\n".join(lines).encode("utf-8"))


class IngestUploadTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Jerusalem")
        self.dairy = Refrigerator.objects.create(branch=self.branch, name="Dairy")
        self.logger = Logger.objects.create(external_id="TL-0512", unit=Logger.UNIT_CELSIUS)
        LoggerAssignment.objects.create(logger=self.logger, refrigerator=self.dairy, start_at=FAR_PAST)

    def test_valid_rows_are_stored_with_resolved_refrigerator(self):
        f = csv_file(
            ["Logger", "Branch", "Fridge", "Time", "Temp"],
            [["TL-0512", "Jerusalem", "Dairy", "2026-09-14 06:00", "3.8"]],
        )
        result = ingest_upload(f, "jerusalem.csv")

        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.invalid_count, 0)
        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.refrigerator, self.dairy)
        self.assertEqual(reading.temperature_c, 3.8)

    def test_branch_and_fridge_text_in_the_file_is_never_trusted(self):
        # Wrong/garbled Branch+Fridge text in the file must not affect
        # resolution — only the Logger column (or fallback) + registry do.
        f = csv_file(
            ["Logger", "Branch", "Fridge", "Time", "Temp"],
            [["TL-0512", "tel aviv", "Walk-in", "2026-09-14 06:00", "3.8"]],
        )
        ingest_upload(f, "jerusalem.csv")
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.refrigerator, self.dairy)

    def test_fahrenheit_logger_is_converted_to_celsius(self):
        haifa = Branch.objects.create(name="Haifa")
        haifa_dairy = Refrigerator.objects.create(branch=haifa, name="Dairy")
        haifa_logger = Logger.objects.create(external_id="TL-0231", unit=Logger.UNIT_FAHRENHEIT)
        LoggerAssignment.objects.create(logger=haifa_logger, refrigerator=haifa_dairy, start_at=FAR_PAST)

        f = csv_file(["Logger", "Time", "Temp"], [["TL-0231", "14/09/2026 06:00", "38.3"]])
        ingest_upload(f, "haifa.csv")

        reading = TemperatureReading.objects.get(logger=haifa_logger)
        self.assertTrue(reading.is_valid)
        self.assertAlmostEqual(reading.temperature_c, 3.5, places=1)

    def test_err_reading_is_invalid_but_still_placed_for_gap_purposes(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:30", "ERR"]])
        result = ingest_upload(f, "jerusalem.csv")

        self.assertEqual(result.valid_count, 0)
        self.assertEqual(result.invalid_count, 1)
        reading = TemperatureReading.objects.get()
        self.assertFalse(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_INVALID_TEMPERATURE)
        # Still placed — an ERR reading proves the logger was alive.
        self.assertEqual(reading.refrigerator, self.dairy)

    def test_unparseable_timestamp_is_invalid_and_unplaced(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "not-a-date", "3.8"]])
        ingest_upload(f, "jerusalem.csv")
        reading = TemperatureReading.objects.get()
        self.assertFalse(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_INVALID_TIMESTAMP)
        self.assertIsNone(reading.refrigerator)

    def test_unknown_logger_is_not_rejected_and_stays_valid_but_unplaced(self):
        # A missing LoggerAssignment (or here, a missing Logger registration
        # entirely) doesn't invalidate an otherwise-parseable measurement —
        # it's valid, just unplaced, pending registry configuration.
        f = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        result = ingest_upload(f, "mystery.csv")

        self.assertEqual(result.unknown_logger_codes, ["TL-9999"])
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.invalid_count, 0)
        self.assertEqual(result.unresolved_count, 1)
        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_UNKNOWN_LOGGER)
        self.assertIsNone(reading.refrigerator)
        self.assertIsNone(reading.logger)
        # Unit is unknown until the logger is registered, so no Celsius value yet.
        self.assertIsNone(reading.temperature_c)

    def test_fallback_logger_used_only_when_file_has_no_logger_column(self):
        f = csv_file(["Time", "Temp"], [["2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "raw.csv", fallback_logger=self.logger)
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.logger, self.logger)
        self.assertTrue(reading.is_valid)

    def test_logger_column_takes_precedence_over_fallback(self):
        other_fridge = Refrigerator.objects.create(branch=self.branch, name="Walk-in")
        other_logger = Logger.objects.create(external_id="TL-0417")
        LoggerAssignment.objects.create(logger=other_logger, refrigerator=other_fridge, start_at=FAR_PAST)

        f = csv_file(["Logger", "Time", "Temp"], [["TL-0417", "2026-09-14 06:00", "4.1"]])
        # fallback_logger is TL-0512 (Dairy), but the file names TL-0417 (Walk-in).
        ingest_upload(f, "mixed.csv", fallback_logger=self.logger)
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.logger, other_logger)
        self.assertEqual(reading.refrigerator, other_fridge)

    def test_reading_before_logger_was_ever_assigned_is_valid_but_unplaced(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2000-01-01 06:00", "3.8"]])
        result = ingest_upload(f, "old.csv")

        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.invalid_count, 0)
        self.assertEqual(result.unresolved_count, 1)
        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_NO_ASSIGNMENT)
        self.assertIsNone(reading.refrigerator)
        # The logger itself *is* known, so the unit conversion still happens.
        self.assertEqual(reading.logger, self.logger)
        self.assertEqual(reading.temperature_c, 3.8)

    def test_reassigned_logger_resolves_to_the_fridge_active_at_reading_time(self):
        tel_aviv = Branch.objects.create(name="Tel Aviv")
        walk_in = Refrigerator.objects.create(branch=tel_aviv, name="Walk-in")
        display_2 = Refrigerator.objects.create(branch=tel_aviv, name="Display 2")
        moved_logger = Logger.objects.create(external_id="TL-0417")
        switch_time = timezone.make_aware(datetime(2026, 9, 10))
        LoggerAssignment.objects.create(
            logger=moved_logger, refrigerator=walk_in, start_at=FAR_PAST, end_at=switch_time
        )
        LoggerAssignment.objects.create(logger=moved_logger, refrigerator=display_2, start_at=switch_time)

        f = csv_file(
            ["Logger", "Time", "Temp"],
            [
                ["TL-0417", "2026-09-05 06:00", "4.1"],  # before the switch
                ["TL-0417", "2026-09-15 06:00", "3.7"],  # after the switch
            ],
        )
        ingest_upload(f, "tel_aviv.csv")

        before_reading = TemperatureReading.objects.get(raw_timestamp="2026-09-05 06:00")
        after_reading = TemperatureReading.objects.get(raw_timestamp="2026-09-15 06:00")
        self.assertEqual(before_reading.refrigerator, walk_in)
        self.assertEqual(after_reading.refrigerator, display_2)

    def test_duplicate_file_upload_is_rejected(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        ingest_upload(io.BytesIO(f.getvalue()), "jerusalem.csv")
        with self.assertRaises(DuplicateFileError):
            ingest_upload(io.BytesIO(f.getvalue()), "jerusalem_again.csv")

    def test_duplicate_measurement_across_different_files_is_skipped_not_duplicated(self):
        f1 = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        f2 = csv_file(
            ["Logger", "Time", "Temp"],
            [
                ["TL-0512", "2026-09-14 06:00", "3.8"],
                ["TL-0512", "2026-09-14 06:15", "3.9"],
            ],
        )
        ingest_upload(f1, "week1.csv")
        result = ingest_upload(f2, "week1_resend.csv")

        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(
            TemperatureReading.objects.filter(logger=self.logger, is_valid=True).count(), 2
        )

    def test_unresolved_readings_are_also_deduped_across_files(self):
        # Keying the dedup constraint on raw_logger_code (not the logger FK)
        # means this works even before the logger is registered. The two
        # files differ (an extra unrelated row in f2) so this exercises
        # row-level dedup specifically, not the file-hash check.
        f1 = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        f2 = csv_file(
            ["Logger", "Time", "Temp"],
            [
                ["TL-9999", "2026-09-14 06:00", "3.8"],
                ["TL-0512", "2026-09-14 06:00", "4.0"],
            ],
        )
        ingest_upload(f1, "mystery1.csv")
        result = ingest_upload(f2, "mystery2.csv")

        self.assertEqual(result.duplicate_count, 1)  # the repeated TL-9999 row
        self.assertEqual(result.valid_count, 1)  # the new TL-0512 row
        self.assertEqual(TemperatureReading.objects.filter(raw_logger_code="TL-9999").count(), 1)

    def test_file_with_no_time_or_temperature_column_raises_parse_error(self):
        f = csv_file(["Logger", "Branch", "Fridge"], [["TL-0512", "Jerusalem", "Dairy"]])
        with self.assertRaises(FileParseError):
            ingest_upload(f, "broken.csv")

    def test_temperature_unit_is_recorded_and_immune_to_later_logger_changes(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "jerusalem.csv")
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.temperature_unit, Logger.UNIT_CELSIUS)
        self.assertEqual(reading.temperature_c, 3.8)

        # Someone fixes a (hypothetically wrong) unit on the registry after
        # the fact. The already-resolved reading must not be reinterpreted.
        self.logger.unit = Logger.UNIT_FAHRENHEIT
        self.logger.save()

        reading.refresh_from_db()
        self.assertEqual(reading.temperature_unit, Logger.UNIT_CELSIUS)
        self.assertEqual(reading.temperature_c, 3.8)


class ReprocessUnresolvedReadingsTests(TestCase):
    """
    Tests reprocess_unresolved_readings() directly, in isolation from the
    signals that now trigger it automatically on every Logger/
    LoggerAssignment save (see signals.py and test_signals.py for the
    end-to-end "no manual step required" coverage) — otherwise the signal
    would already have resolved everything before the explicit call below
    runs, and these assertions would never see a non-zero result.
    """

    def setUp(self):
        post_save.disconnect(readings_signals.reprocess_on_logger_saved, sender=Logger)
        post_save.disconnect(readings_signals.reprocess_on_assignment_saved, sender=LoggerAssignment)
        self.branch = Branch.objects.create(name="Jerusalem")
        self.dairy = Refrigerator.objects.create(branch=self.branch, name="Dairy")

    def tearDown(self):
        post_save.connect(readings_signals.reprocess_on_logger_saved, sender=Logger)
        post_save.connect(readings_signals.reprocess_on_assignment_saved, sender=LoggerAssignment)

    def test_unknown_logger_reading_is_completed_once_the_registry_is_fixed(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "mystery.csv")
        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertIsNone(reading.refrigerator)

        # Summer (or whoever manages the registry) configures the logger.
        logger = Logger.objects.create(external_id="TL-9999", unit=Logger.UNIT_CELSIUS)
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)

        resolved_count = reprocess_unresolved_readings()
        self.assertEqual(resolved_count, 1)

        reading.refresh_from_db()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.reason, "")
        self.assertEqual(reading.refrigerator, self.dairy)
        self.assertEqual(reading.temperature_c, 3.8)

    def test_known_logger_with_no_assignment_yet_is_completed_once_assigned(self):
        logger = Logger.objects.create(external_id="TL-0512", unit=Logger.UNIT_CELSIUS)
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "jerusalem.csv")
        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_NO_ASSIGNMENT)
        # Unit was already known, so temperature_c was already computed.
        self.assertEqual(reading.temperature_c, 3.8)

        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)
        resolved_count = reprocess_unresolved_readings()
        self.assertEqual(resolved_count, 1)

        reading.refresh_from_db()
        self.assertEqual(reading.refrigerator, self.dairy)
        self.assertEqual(reading.reason, "")

    def test_reprocess_does_not_touch_already_resolved_readings(self):
        logger = Logger.objects.create(external_id="TL-0512")
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "jerusalem.csv")

        resolved_count = reprocess_unresolved_readings()
        self.assertEqual(resolved_count, 0)
