from django.urls import path

from drivers.views import (
    BulkUploadStatusView,
    BulkUploadView,
    DriverDetailView,
    DriverListCreateView,
)

urlpatterns = [
    path(
        'driver', DriverListCreateView.as_view(),
        name='driver-list-create',
    ),
    path(
        'driver/<uuid:pk>', DriverDetailView.as_view(),
        name='driver-detail',
    ),
    path(
        'drivers/bulk-upload',
        BulkUploadView.as_view(),
        name='driver-bulk-upload',
    ),
    path(
        'drivers/bulk-upload/<uuid:upload_id>',
        BulkUploadStatusView.as_view(),
        name='driver-bulk-upload-status',
    ),
]
