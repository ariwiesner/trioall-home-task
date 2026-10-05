from django.contrib import admin

from .models import TemperatureReading, UploadedFile


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "uploaded_at",
        "fallback_logger",
        "row_count",
        "valid_count",
        "invalid_count",
        "duplicate_count",
        "unresolved_count",
    )
    readonly_fields = (
        "file_hash",
        "row_count",
        "valid_count",
        "invalid_count",
        "duplicate_count",
        "unresolved_count",
        "uploaded_at",
    )


@admin.register(TemperatureReading)
class TemperatureReadingAdmin(admin.ModelAdmin):
    list_display = (
        "raw_logger_code",
        "refrigerator",
        "timestamp",
        "temperature_c",
        "is_valid",
        "reason",
        "uploaded_file",
    )
    list_filter = ("is_valid", "reason", "refrigerator__branch")
    search_fields = ("raw_logger_code", "raw_timestamp")
