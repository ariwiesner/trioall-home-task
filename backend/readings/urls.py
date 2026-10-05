from django.urls import path

from . import views

urlpatterns = [
    path("loggers/", views.LoggerListView.as_view(), name="logger-list"),
    path("uploads/", views.UploadedFileListCreateView.as_view(), name="upload-list-create"),
    path("uploads/<int:pk>/", views.UploadedFileDetailView.as_view(), name="upload-detail"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path(
        "refrigerators/<int:pk>/",
        views.RefrigeratorDetailView.as_view(),
        name="refrigerator-detail",
    ),
    path(
        "refrigerators/<int:pk>/readings/",
        views.RefrigeratorReadingsView.as_view(),
        name="refrigerator-readings",
    ),
]
