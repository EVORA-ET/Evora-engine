from django.urls import path

from stops.views import (
    BulkUploadStatusView,
    BulkUploadView,
    StopDetailView,
    StopListCreateView,
)

urlpatterns = [
    path(
        'stop', StopListCreateView.as_view(),
        name='stop-list-create',
    ),
    path(
        'stop/<uuid:pk>', StopDetailView.as_view(),
        name='stop-detail',
    ),
    path(
        'stops/bulk-upload',
        BulkUploadView.as_view(),
        name='stop-bulk-upload',
    ),
    path(
        'stops/bulk-upload/<uuid:upload_id>',
        BulkUploadStatusView.as_view(),
        name='stop-bulk-upload-status',
    ),
]
