from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator
from readings.ingestion import ingest_upload
from readings.models import TemperatureReading

FAR_PAST = timezone.make_aware(datetime(2020, 1, 1))


def csv_file(headers, rows):
    import io

    lines = [",".join(headers)]
    lines += [",".join(str(v) for v in row) for row in rows]
    return io.BytesIO("\n".join(lines).encode("utf-8"))


class AutomaticReprocessingOnRegistryChangeTests(TestCase):
    """
    The product requirement: no one should have to "ask a developer" to
    re-run anything. Configuring a logger through the normal admin flow
    (fridges/admin.py's LoggerAdmin, with LoggerAssignment inlined) is
    itself what triggers reprocessing — see readings/signals.py.
    """

    def setUp(self):
        self.branch = Branch.objects.create(name="Jerusalem")
        self.dairy = Refrigerator.objects.create(branch=self.branch, name="Dairy")

    def test_creating_the_logger_and_assignment_together_resolves_pending_readings(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        result = ingest_upload(f, "mystery.csv")
        self.assertEqual(result.unresolved_count, 1)

        reading = TemperatureReading.objects.get()
        self.assertTrue(reading.is_valid)
        self.assertIsNone(reading.refrigerator)

        # This is the "normal registry flow": create the Logger, then its
        # assignment — exactly what LoggerAdmin's inline does in one form.
        # No call to reprocess_unresolved_readings() anywhere in this test.
        logger = Logger.objects.create(external_id="TL-9999", unit=Logger.UNIT_CELSIUS)
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)

        reading.refresh_from_db()
        self.assertTrue(reading.is_valid)
        self.assertEqual(reading.reason, "")
        self.assertEqual(reading.refrigerator, self.dairy)
        self.assertEqual(reading.temperature_c, 3.8)

    def test_logger_created_alone_updates_temperature_but_stays_unresolved(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0231", "14/09/2026 06:00", "38.3"]])
        ingest_upload(f, "haifa.csv")
        reading = TemperatureReading.objects.get()
        self.assertIsNone(reading.temperature_c)  # unit unknown at ingestion time

        # Logger created, but no assignment yet — a realistic intermediate
        # state if someone fills in the Logger form and saves before
        # getting to the assignment.
        Logger.objects.create(external_id="TL-0231", unit=Logger.UNIT_FAHRENHEIT)

        reading.refresh_from_db()
        self.assertTrue(reading.is_valid)  # still valid, just unplaced
        self.assertEqual(reading.reason, TemperatureReading.REASON_NO_ASSIGNMENT)
        self.assertIsNone(reading.refrigerator)
        self.assertAlmostEqual(reading.temperature_c, 3.5, places=1)  # unit now known

    def test_adding_the_assignment_afterwards_finishes_the_job(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0231", "14/09/2026 06:00", "38.3"]])
        ingest_upload(f, "haifa.csv")
        logger = Logger.objects.create(external_id="TL-0231", unit=Logger.UNIT_FAHRENHEIT)
        reading = TemperatureReading.objects.get()
        self.assertIsNone(reading.refrigerator)

        haifa = Branch.objects.create(name="Haifa")
        haifa_dairy = Refrigerator.objects.create(branch=haifa, name="Dairy")
        LoggerAssignment.objects.create(logger=logger, refrigerator=haifa_dairy, start_at=FAR_PAST)

        reading.refresh_from_db()
        self.assertEqual(reading.refrigerator, haifa_dairy)
        self.assertEqual(reading.reason, "")

    def test_uploaded_file_unresolved_count_is_kept_accurate(self):
        f = csv_file(
            ["Logger", "Time", "Temp"],
            [
                ["TL-9999", "2026-09-14 06:00", "3.8"],
                ["TL-9999", "2026-09-14 06:15", "3.9"],
                ["TL-0512", "2026-09-14 06:00", "4.0"],
            ],
        )
        known = Logger.objects.create(external_id="TL-0512")
        LoggerAssignment.objects.create(logger=known, refrigerator=self.dairy, start_at=FAR_PAST)

        result = ingest_upload(f, "mixed.csv")
        self.assertEqual(result.unresolved_count, 2)
        upload = result.uploaded_file

        logger = Logger.objects.create(external_id="TL-9999")
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)

        upload.refresh_from_db()
        self.assertEqual(upload.unresolved_count, 0)

    def test_editing_an_existing_assignment_also_retriggers_reprocessing(self):
        # Not just creation — fixing a mistake should also unstick readings.
        f = csv_file(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        logger = Logger.objects.create(external_id="TL-0512")
        # Wrong start_at at first — doesn't cover the reading.
        assignment = LoggerAssignment.objects.create(
            logger=logger, refrigerator=self.dairy, start_at=timezone.make_aware(datetime(2026, 10, 1))
        )
        ingest_upload(f, "jerusalem.csv")
        reading = TemperatureReading.objects.get()
        self.assertIsNone(reading.refrigerator)
        self.assertEqual(reading.reason, TemperatureReading.REASON_NO_ASSIGNMENT)

        assignment.start_at = FAR_PAST
        assignment.save()

        reading.refresh_from_db()
        self.assertEqual(reading.refrigerator, self.dairy)

    def test_unrelated_logger_registration_does_not_touch_other_loggers_readings(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        ingest_upload(f, "mystery.csv")
        reading = TemperatureReading.objects.get()

        other_fridge = Refrigerator.objects.create(branch=self.branch, name="Walk-in")
        other_logger = Logger.objects.create(external_id="TL-0001")
        LoggerAssignment.objects.create(logger=other_logger, refrigerator=other_fridge, start_at=FAR_PAST)

        reading.refresh_from_db()
        self.assertIsNone(reading.refrigerator)  # still untouched

    def test_genuinely_invalid_readings_are_never_touched_by_registry_changes(self):
        f = csv_file(["Logger", "Time", "Temp"], [["TL-9999", "not-a-date", "3.8"]])
        ingest_upload(f, "broken.csv")
        reading = TemperatureReading.objects.get()
        self.assertFalse(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_INVALID_TIMESTAMP)

        logger = Logger.objects.create(external_id="TL-9999")
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.dairy, start_at=FAR_PAST)

        reading.refresh_from_db()
        self.assertFalse(reading.is_valid)
        self.assertEqual(reading.reason, TemperatureReading.REASON_INVALID_TIMESTAMP)
        self.assertIsNone(reading.refrigerator)
