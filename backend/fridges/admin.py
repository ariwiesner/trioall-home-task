from django.contrib import admin

from .models import Branch, Logger, LoggerAssignment, Refrigerator


class RefrigeratorInline(admin.TabularInline):
    model = Refrigerator
    extra = 0


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    inlines = [RefrigeratorInline]


@admin.register(Refrigerator)
class RefrigeratorAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "created_at")
    list_filter = ("branch",)
    search_fields = ("name", "branch__name")


class LoggerAssignmentInline(admin.TabularInline):
    model = LoggerAssignment
    extra = 0


@admin.register(Logger)
class LoggerAdmin(admin.ModelAdmin):
    list_display = ("external_id", "unit", "expected_interval_minutes", "current_refrigerator")
    search_fields = ("external_id",)
    inlines = [LoggerAssignmentInline]

    @admin.display(description="Current refrigerator")
    def current_refrigerator(self, obj):
        current = obj.current_assignment()
        return current.refrigerator if current else "—"


@admin.register(LoggerAssignment)
class LoggerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("logger", "refrigerator", "start_at", "end_at")
    list_filter = ("refrigerator__branch",)
