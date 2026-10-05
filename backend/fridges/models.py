from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import models

# Sentinel for "open-ended" when comparing assignment intervals that may have
# a null end_at (still active).
_FAR_FUTURE = datetime.max.replace(tzinfo=dt_timezone.utc)


class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "branches"

    def __str__(self):
        return self.name


class Refrigerator(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="refrigerators")
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("branch", "name")
        ordering = ["branch__name", "name"]

    def __str__(self):
        return f"{self.branch.name} / {self.name}"


class Logger(models.Model):
    UNIT_CELSIUS = "C"
    UNIT_FAHRENHEIT = "F"
    UNIT_CHOICES = [
        (UNIT_CELSIUS, "Celsius"),
        (UNIT_FAHRENHEIT, "Fahrenheit"),
    ]

    external_id = models.CharField(max_length=50, unique=True)
    unit = models.CharField(max_length=1, choices=UNIT_CHOICES, default=UNIT_CELSIUS)
    expected_interval_minutes = models.PositiveIntegerField(default=15)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["external_id"]

    def __str__(self):
        return self.external_id

    def current_assignment(self):
        return self.assignments.filter(end_at__isnull=True).first()


class LoggerAssignment(models.Model):
    """
    Time-based mapping of a logger to the refrigerator it was clipped into.

    Deliberately not a "move" operation: ending the outgoing assignment and
    creating the new one are two explicit saves. Overlapping assignments for
    the same logger are rejected, not silently resolved — see design plan.
    """

    logger = models.ForeignKey(Logger, on_delete=models.CASCADE, related_name="assignments")
    refrigerator = models.ForeignKey(
        Refrigerator, on_delete=models.CASCADE, related_name="logger_assignments"
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True, help_text="Blank = currently active.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["logger__external_id", "start_at"]

    def __str__(self):
        end = self.end_at.isoformat() if self.end_at else "present"
        return f"{self.logger.external_id} -> {self.refrigerator} [{self.start_at.isoformat()} - {end}]"

    def clean(self):
        super().clean()

        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError("end_at must be after start_at.")

        if not self.logger_id or not self.start_at:
            return

        this_end = self.end_at or _FAR_FUTURE
        others = LoggerAssignment.objects.filter(logger_id=self.logger_id)
        if self.pk:
            others = others.exclude(pk=self.pk)

        for other in others:
            other_end = other.end_at or _FAR_FUTURE
            if self.start_at < other_end and other.start_at < this_end:
                raise ValidationError(
                    f"Overlaps existing assignment for {self.logger}: {other}. "
                    "Close the outgoing assignment (set its end_at) before "
                    "creating a new one."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
