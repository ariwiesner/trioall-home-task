from datetime import datetime, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator
from readings.models import TemperatureReading, UploadedFile

FAR_PAST = timezone.make_aware(datetime(2020, 1, 1))


def csv_bytes(headers, rows):
    lines = [",".join(headers)]
    lines += [",".join(str(v) for v in row) for row in rows]
    return "\n".join(lines).encode("utf-8")


class UploadEndpointTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Jerusalem")
        self.fridge = Refrigerator.objects.create(branch=self.branch, name="Dairy")
        self.logger = Logger.objects.create(external_id="TL-0512")
        LoggerAssignment.objects.create(logger=self.logger, refrigerator=self.fridge, start_at=FAR_PAST)

    def test_successful_upload_returns_counts(self):
        content = csv_bytes(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        upload = SimpleUploadedFile("week.csv", content, content_type="text/csv")

        response = self.client.post("/api/uploads/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_count"], 1)
        self.assertEqual(response.data["invalid_count"], 0)
        self.assertEqual(response.data["unknown_logger_codes"], [])

    def test_unknown_logger_is_surfaced_for_the_upload_prompt(self):
        content = csv_bytes(["Logger", "Time", "Temp"], [["TL-9999", "2026-09-14 06:00", "3.8"]])
        upload = SimpleUploadedFile("week.csv", content, content_type="text/csv")

        response = self.client.post("/api/uploads/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["unknown_logger_codes"], ["TL-9999"])
        self.assertEqual(response.data["unresolved_count"], 1)

    def test_duplicate_file_returns_conflict(self):
        content = csv_bytes(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        self.client.post(
            "/api/uploads/",
            {"file": SimpleUploadedFile("week.csv", content, content_type="text/csv")},
            format="multipart",
        )
        response = self.client.post(
            "/api/uploads/",
            {"file": SimpleUploadedFile("week_again.csv", content, content_type="text/csv")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_file_with_no_time_or_temperature_column_returns_bad_request(self):
        content = csv_bytes(["Logger", "Branch", "Fridge"], [["TL-0512", "Jerusalem", "Dairy"]])
        upload = SimpleUploadedFile("broken.csv", content, content_type="text/csv")

        response = self.client.post("/api/uploads/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_file_returns_bad_request(self):
        response = self.client.post("/api/uploads/", {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_file_returns_bad_request_not_server_error(self):
        # Regression test: a 0-byte file used to make pandas raise
        # EmptyDataError straight out of the view, resulting in an
        # unhandled 500. Must come back as a clean 400 instead.
        upload = SimpleUploadedFile("empty.csv", b"", content_type="text/csv")
        response = self.client.post("/api/uploads/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_fallback_logger_is_used_for_a_raw_two_column_file(self):
        content = csv_bytes(["Time", "Temp"], [["2026-09-14 06:00", "3.8"]])
        upload = SimpleUploadedFile("raw.csv", content, content_type="text/csv")

        response = self.client.post(
            "/api/uploads/",
            {"file": upload, "fallback_logger_id": self.logger.id},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_count"], 1)
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.logger, self.logger)

    def test_uploads_list_and_detail(self):
        content = csv_bytes(["Logger", "Time", "Temp"], [["TL-0512", "2026-09-14 06:00", "3.8"]])
        upload = SimpleUploadedFile("week.csv", content, content_type="text/csv")
        create_response = self.client.post("/api/uploads/", {"file": upload}, format="multipart")
        upload_id = create_response.data["id"]

        list_response = self.client.get("/api/uploads/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)

        detail_response = self.client.get(f"/api/uploads/{upload_id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["original_filename"], "week.csv")


class LoggerListEndpointTests(APITestCase):
    def test_includes_current_assignment_label(self):
        branch = Branch.objects.create(name="Tel Aviv")
        fridge = Refrigerator.objects.create(branch=branch, name="Walk-in")
        logger = Logger.objects.create(external_id="TL-0417")
        LoggerAssignment.objects.create(logger=logger, refrigerator=fridge, start_at=FAR_PAST)

        response = self.client.get("/api/loggers/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        entry = next(r for r in response.data if r["external_id"] == "TL-0417")
        self.assertEqual(entry["current_assignment"], "Tel Aviv / Walk-in")

    def test_unassigned_logger_has_null_assignment(self):
        Logger.objects.create(external_id="TL-ORPHAN")
        response = self.client.get("/api/loggers/")
        entry = next(r for r in response.data if r["external_id"] == "TL-ORPHAN")
        self.assertIsNone(entry["current_assignment"])


class DashboardEndpointTests(APITestCase):
    def setUp(self):
        self.jerusalem = Branch.objects.create(name="Jerusalem")
        self.haifa = Branch.objects.create(name="Haifa")
        self.good_fridge = Refrigerator.objects.create(branch=self.jerusalem, name="Dairy")
        self.quiet_fridge = Refrigerator.objects.create(branch=self.haifa, name="Dairy")
        # Neither fridge has any readings -> both are "needs_review" (never reported).

    def test_summary_counts_are_unaffected_by_filters(self):
        response = self.client.get("/api/dashboard/?status=problem")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["needs_review"], 2)
        self.assertEqual(response.data["branches"], [])  # no "problem" fridges exist

    def test_search_filters_by_branch_name(self):
        response = self.client.get("/api/dashboard/?search=haifa")
        branch_names = [b["name"] for b in response.data["branches"]]
        self.assertEqual(branch_names, ["Haifa"])

    def test_no_filters_returns_every_branch(self):
        response = self.client.get("/api/dashboard/")
        branch_names = {b["name"] for b in response.data["branches"]}
        self.assertEqual(branch_names, {"Jerusalem", "Haifa"})

    def test_branch_id_filters_to_a_single_branch(self):
        response = self.client.get("/api/dashboard/", {"branch_id": self.haifa.id})
        branch_names = {b["name"] for b in response.data["branches"]}
        self.assertEqual(branch_names, {"Haifa"})

    def test_search_matches_the_currently_assigned_logger_id(self):
        logger = Logger.objects.create(external_id="TL-0512")
        LoggerAssignment.objects.create(logger=logger, refrigerator=self.good_fridge, start_at=FAR_PAST)

        response = self.client.get("/api/dashboard/", {"search": "tl-0512"})
        branch_names = {b["name"] for b in response.data["branches"]}
        self.assertEqual(branch_names, {"Jerusalem"})


class RefrigeratorDetailEndpointTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Rishon LeZion")
        self.fridge = Refrigerator.objects.create(branch=self.branch, name="Cream cakes")
        self.logger = Logger.objects.create(external_id="TL-0388", expected_interval_minutes=15)
        LoggerAssignment.objects.create(logger=self.logger, refrigerator=self.fridge, start_at=FAR_PAST)
        self.upload = UploadedFile.objects.create(original_filename="f.csv", file_hash="h")

    def _add(self, dt, temp_c):
        TemperatureReading.objects.create(
            uploaded_file=self.upload,
            logger=self.logger,
            refrigerator=self.fridge,
            raw_logger_code=self.logger.external_id,
            raw_timestamp=str(dt),
            raw_temperature=str(temp_c),
            timestamp=dt,
            temperature_c=temp_c,
            is_valid=True,
        )

    def test_matches_the_rishon_incident_from_the_sample_data(self):
        base = timezone.now() - timedelta(minutes=50)
        for offset, temp in [(0, 4.6), (15, 5.4), (30, 6.3), (45, 7.1)]:
            self._add(base + timedelta(minutes=offset), temp)

        response = self.client.get(f"/api/refrigerators/{self.fridge.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["branch"], "Rishon LeZion")
        self.assertEqual(len(response.data["breach_episodes"]), 1)
        self.assertEqual(response.data["breach_episodes"][0]["duration_minutes"], 30)

    def test_not_found_returns_404(self):
        response = self.client.get("/api/refrigerators/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_readings_endpoint_is_paginated_and_filterable(self):
        base = timezone.now() - timedelta(hours=2)
        for i in range(5):
            self._add(base + timedelta(minutes=15 * i), 3.8 + i * 0.1)

        response = self.client.get(f"/api/refrigerators/{self.fridge.id}/readings/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)

        since = (base + timedelta(minutes=30)).isoformat()
        # Pass via the `data=` dict (not an f-string) so the client
        # URL-encodes the "+" in the UTC offset — a literal "+" in a query
        # string decodes to a space, which silently breaks parse_datetime.
        filtered = self.client.get(f"/api/refrigerators/{self.fridge.id}/readings/", {"since": since})
        self.assertEqual(filtered.data["count"], 3)
