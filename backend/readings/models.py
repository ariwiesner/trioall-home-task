from django.db import models

from fridges.models import Logger, Refrigerator


class UploadedFile(models.Model):
    """
    A single file upload. The original is kept unchanged on disk for
    traceability/debugging; parsed measurements are stored separately as
    TemperatureReading rows.
    """

    fallback_logger = models.ForeignKey(
        Logger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fallback_uploads",
        help_text="Used for every row only when the file has no Logger-ID column.",
    )
    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    file_hash = models.CharField(max_length=64, unique=True)
    original_filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    row_count = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    # Of valid_count: rows parsed fine but not yet placed in a refrigerator
    # (unknown logger, or a known logger with no covering assignment).
    unresolved_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.original_filename


class TemperatureReading(models.Model):
    """
    is_valid is deliberately narrow: it only asks whether the measurement
    itself could be parsed (a logger code is present, the timestamp parsed,
    the temperature parsed). It says nothing about whether we know where to
    put it. A row can be fully valid and still have refrigerator=None — an
    unregistered logger, or a registered one with no LoggerAssignment
    covering this timestamp — see `reason`. This keeps the duplicate
    constraint (same logger code + timestamp shouldn't produce two "real"
    measurements) meaningful even before a logger is configured, and keeps
    "is this row usable at all" separate from "is this row placed yet".
    """

    # Genuine parse failures — these are the only reasons is_valid=False.
    REASON_MISSING_LOGGER_CODE = "missing_logger_code"
    REASON_INVALID_TIMESTAMP = "invalid_timestamp"
    REASON_INVALID_TEMPERATURE = "invalid_temperature"
    # Valid measurements that just aren't placed yet — is_valid stays True.
    REASON_UNKNOWN_LOGGER = "unknown_logger"
    REASON_NO_ASSIGNMENT = "no_active_assignment"

    PARSE_FAILURE_REASONS = {
        REASON_MISSING_LOGGER_CODE,
        REASON_INVALID_TIMESTAMP,
        REASON_INVALID_TEMPERATURE,
    }
    UNRESOLVED_REASONS = {REASON_UNKNOWN_LOGGER, REASON_NO_ASSIGNMENT}

    REASON_CHOICES = [
        (REASON_MISSING_LOGGER_CODE, "No logger code (no column, no fallback)"),
        (REASON_INVALID_TIMESTAMP, "Invalid timestamp"),
        (REASON_INVALID_TEMPERATURE, "Invalid temperature"),
        (REASON_UNKNOWN_LOGGER, "Unknown logger — not yet in the registry"),
        (REASON_NO_ASSIGNMENT, "No logger assignment covers this time"),
    ]

    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.CASCADE, related_name="readings")

    # The registered logger that reported this row (null if the row's
    # logger code doesn't match any registered Logger yet).
    logger = models.ForeignKey(
        Logger, null=True, blank=True, on_delete=models.SET_NULL, related_name="readings"
    )
    # Resolved from LoggerAssignment as of `timestamp` and stored at
    # ingestion time — a historical snapshot, immune to later assignment
    # edits. Set whenever a registered logger + timestamp are known, even
    # if the temperature itself is invalid (e.g. "ERR") — an unreadable
    # temperature still proves the logger was alive and reporting, which
    # matters for gap detection.
    refrigerator = models.ForeignKey(
        Refrigerator, null=True, blank=True, on_delete=models.SET_NULL, related_name="readings"
    )

    raw_logger_code = models.CharField(max_length=50, blank=True)
    raw_timestamp = models.CharField(max_length=100, blank=True)
    raw_temperature = models.CharField(max_length=50, blank=True)

    timestamp = models.DateTimeField(null=True, blank=True)
    # Null whenever the unit is unknown (logger not registered yet), even
    # though the raw text parsed fine as a number — see raw_temperature.
    temperature_c = models.FloatField(null=True, blank=True)
    # The unit actually used to produce temperature_c, recorded at the
    # moment of conversion — not read live off Logger.unit. If someone
    # later corrects a logger's configured unit, already-resolved readings
    # must not silently reinterpret; this makes temperature_c's provenance
    # a permanent fact about the reading, consistent with refrigerator
    # being a frozen snapshot rather than a live lookup.
    temperature_unit = models.CharField(max_length=1, blank=True, choices=Logger.UNIT_CHOICES)

    is_valid = models.BooleanField(default=True)
    # Populated whenever something is incomplete — a genuine parse failure
    # (is_valid=False) or an unresolved placement on an otherwise-valid row.
    reason = models.CharField(max_length=30, blank=True, choices=REASON_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]
        constraints = [
            # Keyed on the raw code rather than the logger FK so this still
            # catches duplicates for a logger that isn't registered yet.
            models.UniqueConstraint(
                fields=["raw_logger_code", "timestamp"],
                condition=models.Q(is_valid=True),
                name="unique_valid_reading_per_logger_code_timestamp",
            )
        ]

    def __str__(self):
        return f"{self.raw_logger_code or self.logger} @ {self.raw_timestamp}"
