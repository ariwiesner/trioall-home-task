from rest_framework import serializers

from fridges.models import Logger

from .models import TemperatureReading, UploadedFile


class LoggerSerializer(serializers.ModelSerializer):
    current_assignment = serializers.SerializerMethodField()

    class Meta:
        model = Logger
        fields = ["id", "external_id", "unit", "current_assignment"]

    def get_current_assignment(self, obj):
        assignment = obj.current_assignment()
        if not assignment:
            return None
        fridge = assignment.refrigerator
        return f"{fridge.branch.name} / {fridge.name}"


class UploadedFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedFile
        fields = [
            "id",
            "original_filename",
            "uploaded_at",
            "fallback_logger",
            "row_count",
            "valid_count",
            "invalid_count",
            "duplicate_count",
            "unresolved_count",
        ]
        read_only_fields = fields


class EpisodeSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    duration_minutes = serializers.FloatField()
    max_temp = serializers.FloatField(allow_null=True)


class RefrigeratorStatusSerializer(serializers.Serializer):
    """Shapes the dict built in views.py from a fridges.Refrigerator + an
    analysis.RefrigeratorStatus — not a ModelSerializer, since the payload
    mixes stored fields with computed-on-read analysis results."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    branch = serializers.CharField()
    status = serializers.CharField()
    last_reading_at = serializers.DateTimeField(allow_null=True)
    last_temperature_c = serializers.FloatField(allow_null=True)
    # The same reading's original stored text value + unit, for "Original:
    # 39.2°F" display — see analysis.py's RefrigeratorStatus docstring.
    last_reading_raw_temperature = serializers.CharField(allow_null=True)
    last_reading_temperature_unit = serializers.CharField(allow_blank=True)
    active_breach = serializers.BooleanField()
    active_gap = serializers.BooleanField()
    active_gap_kind = serializers.CharField(allow_null=True)
    active_warming = serializers.BooleanField()
    breach_episodes = EpisodeSerializer(many=True)
    gap_episodes = EpisodeSerializer(many=True)
    warming_episodes = EpisodeSerializer(many=True)


class TemperatureReadingSerializer(serializers.ModelSerializer):
    # The reading's originating upload, for UI traceability — not a new
    # stored field, just exposing the existing uploaded_file FK.
    source_file = serializers.CharField(source="uploaded_file.original_filename", read_only=True)

    class Meta:
        model = TemperatureReading
        fields = [
            "timestamp",
            "temperature_c",
            "raw_temperature",
            "temperature_unit",
            "is_valid",
            "reason",
            "source_file",
        ]
