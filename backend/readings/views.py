from django.utils.dateparse import parse_datetime
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from fridges.models import Logger, LoggerAssignment, Refrigerator

from .analysis import get_refrigerator_status
from .ingestion import DuplicateFileError, ingest_upload
from .models import TemperatureReading, UploadedFile
from .parsing import FileParseError
from .serializers import (
    LoggerSerializer,
    RefrigeratorStatusSerializer,
    TemperatureReadingSerializer,
    UploadedFileSerializer,
)


class LoggerListView(generics.ListAPIView):
    # Unpaginated: this exists to fully populate the upload form's logger
    # dropdown, not to be browsed page by page.
    queryset = Logger.objects.all()
    serializer_class = LoggerSerializer
    pagination_class = None


class UploadedFileListCreateView(generics.ListCreateAPIView):
    queryset = UploadedFile.objects.all()
    serializer_class = UploadedFileSerializer

    def create(self, request, *args, **kwargs):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

        fallback_logger = None
        fallback_logger_id = request.data.get("fallback_logger_id")
        if fallback_logger_id:
            try:
                fallback_logger = Logger.objects.get(pk=fallback_logger_id)
            except (Logger.DoesNotExist, ValueError, TypeError):
                return Response(
                    {"detail": "Unknown fallback_logger_id."}, status=status.HTTP_400_BAD_REQUEST
                )

        try:
            result = ingest_upload(file_obj, file_obj.name, fallback_logger=fallback_logger)
        except DuplicateFileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except FileParseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        data = UploadedFileSerializer(result.uploaded_file).data
        # Surfaced separately from the stored UploadedFile fields so the
        # upload form can immediately prompt to configure them (NOTES.md
        # decision #14), without a second lookup.
        data["unknown_logger_codes"] = result.unknown_logger_codes
        return Response(data, status=status.HTTP_201_CREATED)


class UploadedFileDetailView(generics.RetrieveAPIView):
    queryset = UploadedFile.objects.all()
    serializer_class = UploadedFileSerializer


def _episode_dict(episode):
    return {
        "start": episode.start,
        "end": episode.end,
        "duration_minutes": episode.duration_minutes,
        "max_temp": episode.max_temp,
    }


def _status_dict(fridge, result):
    return {
        "id": fridge.id,
        "name": fridge.name,
        "branch": fridge.branch.name,
        "status": result.status,
        "last_reading_at": result.last_reading_at,
        "last_temperature_c": result.last_temperature_c,
        "last_reading_raw_temperature": result.last_reading_raw_temperature,
        "last_reading_temperature_unit": result.last_reading_temperature_unit,
        "active_breach": result.active_breach,
        "active_gap": result.active_gap,
        "active_gap_kind": result.active_gap_kind,
        "active_warming": result.active_warming,
        "breach_episodes": [_episode_dict(e) for e in result.breach_episodes],
        "gap_episodes": [_episode_dict(e) for e in result.gap_episodes],
        "warming_episodes": [_episode_dict(e) for e in result.warming_episodes],
    }


class DashboardView(APIView):
    """
    GET /api/dashboard/?search=&status=&branch_id=

    The top-level summary counts are always computed across ALL
    refrigerators, regardless of search/status/branch_id filters — only the
    branch listing below it is filtered. That matches NOTES.md decision #9:
    the summary is the at-a-glance total, search/filter is for drilling in.

    search matches branch name, refrigerator name (there's no separate
    "fridge ID" in the data model — its name, e.g. "Dairy", is the
    identifier NOTES.md decision #10 means), or the external_id of the
    refrigerator's CURRENTLY assigned logger.
    """

    def get(self, request):
        search = request.query_params.get("search", "").strip().lower()
        status_filter = request.query_params.get("status")
        branch_id = request.query_params.get("branch_id")

        current_logger_codes = {
            assignment.refrigerator_id: assignment.logger.external_id
            for assignment in LoggerAssignment.objects.filter(
                end_at__isnull=True
            ).select_related("logger")
        }

        counts = {"good": 0, "problem": 0, "needs_review": 0}
        by_branch = {}

        refrigerators = Refrigerator.objects.select_related("branch").order_by("branch__name", "name")
        for fridge in refrigerators:
            result = get_refrigerator_status(fridge)
            counts[result.status] += 1

            if status_filter and result.status != status_filter:
                continue
            if branch_id and str(fridge.branch_id) != branch_id:
                continue
            if search:
                logger_code = current_logger_codes.get(fridge.id, "")
                haystacks = (fridge.branch.name, fridge.name, logger_code)
                if not any(search in haystack.lower() for haystack in haystacks):
                    continue

            by_branch.setdefault(fridge.branch, []).append(
                {
                    "id": fridge.id,
                    "name": fridge.name,
                    "status": result.status,
                    "last_reading_at": result.last_reading_at,
                }
            )

        branches = [
            {"id": branch.id, "name": branch.name, "refrigerators": fridges}
            for branch, fridges in by_branch.items()
        ]
        return Response({"summary": counts, "branches": branches})


class RefrigeratorDetailView(APIView):
    def get(self, request, pk):
        try:
            fridge = Refrigerator.objects.select_related("branch").get(pk=pk)
        except Refrigerator.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        result = get_refrigerator_status(fridge)
        serializer = RefrigeratorStatusSerializer(_status_dict(fridge, result))
        return Response(serializer.data)


class RefrigeratorReadingsView(generics.ListAPIView):
    """GET /api/refrigerators/{id}/readings/?since=&until= — paginated,
    for the detail screen's timeline chart."""

    serializer_class = TemperatureReadingSerializer

    def get_queryset(self):
        queryset = (
            TemperatureReading.objects.filter(refrigerator_id=self.kwargs["pk"])
            # source_file on the serializer reads uploaded_file.original_filename —
            # select_related avoids one extra query per row for it.
            .select_related("uploaded_file")
            .order_by("timestamp")
        )

        since = parse_datetime(self.request.query_params.get("since", "") or "")
        if since:
            queryset = queryset.filter(timestamp__gte=since)

        until = parse_datetime(self.request.query_params.get("until", "") or "")
        if until:
            queryset = queryset.filter(timestamp__lte=until)

        return queryset
