from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from fridges.models import Branch, Logger, LoggerAssignment, Refrigerator


class Command(BaseCommand):
    """
    Seeds a small, deterministic demo registry: branches, refrigerators,
    loggers, and their assignment history.

    Deliberately a representative subset of Squanchy Bakery's 12 branches,
    not all of them — enough to demonstrate every registry-level edge case
    from the client email (a Fahrenheit logger, a mid-week reassignment)
    without hand-authoring fixtures for branches that don't add anything new.

    No-ops if any Branch already exists, so re-running `docker compose up`
    never duplicates data. Offsets are relative to now() (not hardcoded
    dates) so later stages' "active issue" status logic demos correctly
    whenever this is actually run — but fixed (30 days / 7 days), not
    randomized, so the demo story is reproducible between runs.
    """

    help = "Seeds a small, deterministic demo registry. No-ops if data already exists."

    def handle(self, *args, **options):
        if Branch.objects.exists():
            self.stdout.write("Registry already seeded — skipping.")
            return

        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        thirty_days_ago = now - timedelta(days=30)
        seven_days_ago = now - timedelta(days=7)

        # (branch, [(refrigerator, logger_external_id, unit), ...])
        branch_specs = [
            ("Jerusalem", [("Dairy", "TL-0512", Logger.UNIT_CELSIUS)]),
            ("Tel Aviv", [("Walk-in", "TL-0417", Logger.UNIT_CELSIUS)]),
            (
                "Haifa",
                [
                    ("Dairy", "TL-0231", Logger.UNIT_FAHRENHEIT),
                    ("Walk-in", "TL-0245", Logger.UNIT_CELSIUS),
                ],
            ),
            ("Rishon LeZion", [("Cream cakes", "TL-0388", Logger.UNIT_CELSIUS)]),
            ("Netanya", [("Dairy", "TL-0199", Logger.UNIT_CELSIUS)]),
            ("Beer Sheva", [("Walk-in", "TL-0156", Logger.UNIT_CELSIUS)]),
        ]

        loggers_by_code = {}
        for branch_name, fridge_specs in branch_specs:
            branch = Branch.objects.create(name=branch_name)
            for fridge_name, logger_code, unit in fridge_specs:
                fridge = Refrigerator.objects.create(branch=branch, name=fridge_name)
                logger = Logger.objects.create(external_id=logger_code, unit=unit)
                loggers_by_code[logger_code] = logger
                LoggerAssignment.objects.create(
                    logger=logger, refrigerator=fridge, start_at=thirty_days_ago
                )

        # Mirrors the client email: this Tel Aviv logger was moved into a new
        # display fridge a week ago. Close the old assignment explicitly,
        # then open the new one — never mutate history implicitly.
        tel_aviv = Branch.objects.get(name="Tel Aviv")
        display_2 = Refrigerator.objects.create(branch=tel_aviv, name="Display 2")
        moved_logger = loggers_by_code["TL-0417"]
        old_assignment = moved_logger.assignments.get()
        old_assignment.end_at = seven_days_ago
        old_assignment.save()
        LoggerAssignment.objects.create(
            logger=moved_logger, refrigerator=display_2, start_at=seven_days_ago
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {Branch.objects.count()} branches, "
                f"{Refrigerator.objects.count()} refrigerators, "
                f"{Logger.objects.count()} loggers."
            )
        )
