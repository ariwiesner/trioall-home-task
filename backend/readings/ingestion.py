"""
Turns a parsed file (see parsing.py) into stored TemperatureReading rows.

This is where the product decisions in NOTES.md actually get enforced:
- a Logger-ID column in the file, when present, is authoritative per row;
  the upload form's fallback logger is only used for files with no such
  column at all.
- refrigerator is resolved from the logger's LoggerAssignment as of the
  reading's own timestamp, and stored — a historical snapshot, never
  revisited just because an assignment changes later.
- a row with an unknown logger code is NOT rejected — it's stored *valid*
  but unplaced (reason=unknown_logger), and gets completed automatically by
  reprocess_unresolved_readings() once the registry is fixed (see
  signals.py — it's wired to fire whenever a Logger or LoggerAssignment is
  saved, i.e. whenever someone configures a logger through the admin). Same
  for a registered logger with no assignment covering the timestamp
  (reason=no_active_assignment). Neither of these makes the row invalid —
  only a genuine parse failure (missing logger code, bad timestamp, bad
  temperature) does. See models.py's TemperatureReading docstring.
- duplicate files (identical bytes) are rejected outright; duplicate
  measurements (same logger code+timestamp, e.g. from overlapping uploads)
  are silently skipped and counted, not stored twice.
"""

from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from fridges.models import Logger, LoggerAssignment

from .models import TemperatureReading, UploadedFile
from .parsing import (
    compute_file_hash,
    parse_temperature,
    parse_timestamp,
    read_rows,
    to_celsius,
)

REASON_MISSING_LOGGER_CODE = TemperatureReading.REASON_MISSING_LOGGER_CODE
REASON_INVALID_TIMESTAMP = TemperatureReading.REASON_INVALID_TIMESTAMP
REASON_INVALID_TEMPERATURE = TemperatureReading.REASON_INVALID_TEMPERATURE
REASON_UNKNOWN_LOGGER = TemperatureReading.REASON_UNKNOWN_LOGGER
REASON_NO_ASSIGNMENT = TemperatureReading.REASON_NO_ASSIGNMENT


class DuplicateFileError(Exception):
    """Raised when this exact file (by content hash) was already uploaded."""

    def __init__(self, existing_upload):
        self.existing_upload = existing_upload
        super().__init__(
            f"This exact file was already uploaded as "
            f"'{existing_upload.original_filename}' on {existing_upload.uploaded_at}."
        )


def build_assignment_index(logger_ids):
    index = {}
    assignments = LoggerAssignment.objects.filter(logger_id__in=logger_ids).order_by(
        "logger_id", "start_at"
    )
    for assignment in assignments:
        index.setdefault(assignment.logger_id, []).append(assignment)
    return index


def resolve_refrigerator(index, logger_id, dt):
    for assignment in index.get(logger_id, []):
        if assignment.start_at <= dt and (assignment.end_at is None or assignment.end_at > dt):
            return assignment.refrigerator
    return None


@dataclass
class IngestResult:
    uploaded_file: UploadedFile
    row_count: int
    valid_count: int
    invalid_count: int
    duplicate_count: int
    unresolved_count: int
    unknown_logger_codes: list


def ingest_upload(file_obj, original_filename, fallback_logger=None) -> IngestResult:
    file_hash = compute_file_hash(file_obj)
    existing = UploadedFile.objects.filter(file_hash=file_hash).first()
    if existing:
        raise DuplicateFileError(existing)

    rows = read_rows(file_obj, original_filename)  # raises FileParseError

    with transaction.atomic():
        uploaded = UploadedFile(
            fallback_logger=fallback_logger,
            file_hash=file_hash,
            original_filename=original_filename,
        )
        file_obj.seek(0)
        uploaded.file.save(original_filename, file_obj, save=False)
        # Needs a PK before TemperatureReading rows can reference it via FK
        # in bulk_create below; row-count fields are filled in and re-saved
        # once processing finishes.
        uploaded.save()

        logger_cache = {logger.external_id: logger for logger in Logger.objects.all()}
        assignment_index = build_assignment_index([logger.id for logger in logger_cache.values()])
        existing_keys = set(
            TemperatureReading.objects.filter(is_valid=True).values_list("raw_logger_code", "timestamp")
        )

        fallback_code = fallback_logger.external_id if fallback_logger else None

        to_create = []
        valid_count = invalid_count = duplicate_count = unresolved_count = 0
        unknown_logger_codes = set()

        for row in rows:
            reading = TemperatureReading(uploaded_file=uploaded)

            parse_failure_reason = None
            unresolved_reason = None

            logger_code = row["logger_code"] or fallback_code
            reading.raw_logger_code = logger_code or ""

            logger = None
            if not logger_code:
                parse_failure_reason = REASON_MISSING_LOGGER_CODE
            else:
                logger = logger_cache.get(logger_code)
                if logger is None:
                    unresolved_reason = REASON_UNKNOWN_LOGGER
                    unknown_logger_codes.add(logger_code)
                else:
                    reading.logger = logger

            reading.raw_timestamp = row["timestamp_raw"]
            parsed_ts = parse_timestamp(row["timestamp_raw"])
            if parsed_ts is None:
                parse_failure_reason = parse_failure_reason or REASON_INVALID_TIMESTAMP
            else:
                reading.timestamp = timezone.make_aware(parsed_ts)

            reading.raw_temperature = row["temperature_raw"]
            parsed_temp = parse_temperature(row["temperature_raw"])
            if parsed_temp is None:
                parse_failure_reason = parse_failure_reason or REASON_INVALID_TEMPERATURE

            # Placement only needs a *registered* logger + a parsed timestamp.
            if logger is not None and reading.timestamp is not None:
                refrigerator = resolve_refrigerator(assignment_index, logger.id, reading.timestamp)
                if refrigerator is None:
                    unresolved_reason = unresolved_reason or REASON_NO_ASSIGNMENT
                else:
                    reading.refrigerator = refrigerator

            if parse_failure_reason:
                reading.is_valid = False
                reading.reason = parse_failure_reason
                invalid_count += 1
            else:
                key = (reading.raw_logger_code, reading.timestamp)
                if key in existing_keys:
                    duplicate_count += 1
                    continue
                existing_keys.add(key)

                reading.is_valid = True
                reading.reason = unresolved_reason or ""
                if logger is not None and parsed_temp is not None:
                    reading.temperature_c = to_celsius(parsed_temp, logger.unit)
                    reading.temperature_unit = logger.unit

                valid_count += 1
                if reading.refrigerator is None:
                    unresolved_count += 1

            to_create.append(reading)

        TemperatureReading.objects.bulk_create(to_create)

        uploaded.row_count = len(rows)
        uploaded.valid_count = valid_count
        uploaded.invalid_count = invalid_count
        uploaded.duplicate_count = duplicate_count
        uploaded.unresolved_count = unresolved_count
        uploaded.save()

    return IngestResult(
        uploaded_file=uploaded,
        row_count=len(rows),
        valid_count=valid_count,
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
        unresolved_count=unresolved_count,
        unknown_logger_codes=sorted(unknown_logger_codes),
    )


def reprocess_unresolved_readings(logger_external_id: str | None = None) -> int:
    """
    Re-attempts placement for readings that are valid but unplaced — an
    unknown logger, or a known logger with no covering assignment at
    ingestion time (NOTES.md decision #14: unknown logger isn't rejected,
    it's completed once configured).

    Called automatically by signals.py whenever a Logger or LoggerAssignment
    is saved (i.e. whenever someone configures a logger through the admin —
    see fridges/admin.py) — this function itself doesn't know or care who
    calls it, and remains callable directly (e.g. from a shell) too.

    Only ever touches readings with refrigerator=None. A reading that
    already has a refrigerator is a historical snapshot and is not
    revisited here, regardless of later registry changes.
    """
    queryset = TemperatureReading.objects.filter(is_valid=True, refrigerator__isnull=True)
    if logger_external_id:
        queryset = queryset.filter(raw_logger_code=logger_external_id)

    logger_cache = {logger.external_id: logger for logger in Logger.objects.all()}
    assignment_index = build_assignment_index([logger.id for logger in logger_cache.values()])

    resolved = 0
    resolved_counts_by_upload = Counter()
    with transaction.atomic():
        for reading in queryset:
            logger = logger_cache.get(reading.raw_logger_code)
            if logger is None:
                continue  # still not in the registry

            # The unit used here is the logger's unit *right now*. Once
            # recorded, temperature_unit/temperature_c on this reading are
            # frozen — a later unit correction on the Logger won't reach
            # back and silently reinterpret it (see models.py).
            parsed_temp = parse_temperature(reading.raw_temperature)
            temperature_c = to_celsius(parsed_temp, logger.unit) if parsed_temp is not None else None

            refrigerator = resolve_refrigerator(assignment_index, logger.id, reading.timestamp)
            reading.logger = logger
            reading.temperature_c = temperature_c
            reading.temperature_unit = logger.unit if parsed_temp is not None else ""
            if refrigerator is None:
                # Now a known logger (so temperature_c can be computed), but
                # still nothing covers this timestamp.
                reading.reason = REASON_NO_ASSIGNMENT
                reading.save()
                continue

            reading.refrigerator = refrigerator
            reading.reason = ""
            reading.save()
            resolved += 1
            resolved_counts_by_upload[reading.uploaded_file_id] += 1

        # Keep each upload's historical unresolved_count snapshot accurate —
        # otherwise an old upload's record would keep claiming readings are
        # unresolved long after they've actually been placed.
        for upload_id, count in resolved_counts_by_upload.items():
            UploadedFile.objects.filter(pk=upload_id).update(
                unresolved_count=F("unresolved_count") - count
            )

    return resolved
