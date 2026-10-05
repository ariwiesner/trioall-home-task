from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator


class LoggerAssignmentOverlapTests(TestCase):
    def setUp(self):
        branch = Branch.objects.create(name="Tel Aviv")
        self.walk_in = Refrigerator.objects.create(branch=branch, name="Walk-in")
        self.display_2 = Refrigerator.objects.create(branch=branch, name="Display 2")
        self.logger = Logger.objects.create(external_id="TL-0417")
        self.now = timezone.now()

    def test_sequential_assignments_are_allowed(self):
        LoggerAssignment.objects.create(
            logger=self.logger,
            refrigerator=self.walk_in,
            start_at=self.now - timedelta(days=30),
            end_at=self.now - timedelta(days=7),
        )
        # Should not raise.
        LoggerAssignment.objects.create(
            logger=self.logger,
            refrigerator=self.display_2,
            start_at=self.now - timedelta(days=7),
        )

    def test_overlapping_assignment_is_rejected(self):
        LoggerAssignment.objects.create(
            logger=self.logger,
            refrigerator=self.walk_in,
            start_at=self.now - timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            LoggerAssignment.objects.create(
                logger=self.logger,
                refrigerator=self.display_2,
                start_at=self.now - timedelta(days=1),
            )

    def test_two_open_ended_assignments_for_same_logger_are_rejected(self):
        LoggerAssignment.objects.create(
            logger=self.logger,
            refrigerator=self.walk_in,
            start_at=self.now - timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            LoggerAssignment.objects.create(
                logger=self.logger,
                refrigerator=self.display_2,
                start_at=self.now - timedelta(days=10),
            )

    def test_end_at_before_start_at_is_rejected(self):
        with self.assertRaises(ValidationError):
            LoggerAssignment.objects.create(
                logger=self.logger,
                refrigerator=self.walk_in,
                start_at=self.now,
                end_at=self.now - timedelta(days=1),
            )


class SeedDemoDataTests(TestCase):
    def test_seed_is_idempotent_and_covers_the_known_edge_cases(self):
        call_command("seed_demo_data")
        branch_count = Branch.objects.count()
        logger_count = Logger.objects.count()

        call_command("seed_demo_data")
        self.assertEqual(Branch.objects.count(), branch_count)
        self.assertEqual(Logger.objects.count(), logger_count)

        haifa_dairy_logger = Logger.objects.get(external_id="TL-0231")
        self.assertEqual(haifa_dairy_logger.unit, Logger.UNIT_FAHRENHEIT)

        moved_logger = Logger.objects.get(external_id="TL-0417")
        self.assertEqual(moved_logger.assignments.count(), 2)
        self.assertEqual(moved_logger.current_assignment().refrigerator.name, "Display 2")
