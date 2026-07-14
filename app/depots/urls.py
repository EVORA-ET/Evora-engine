from django.urls import path

from depots.views import (
    BulkUploadStatusView,
    BulkUploadView,
    DepotDetailView,
    DepotListCreateView,
)

urlpatterns = [
    path(
        'depot', DepotListCreateView.as_view(),
        name='depot-list-create',
    ),
    path(
        'depot/<uuid:pk>', DepotDetailView.as_view(),
        name='depot-detail',
    ),
    path(
        'depots/bulk-upload',
        BulkUploadView.as_view(),
        name='depot-bulk-upload',
    ),
    path(
        'depots/bulk-upload/<uuid:upload_id>',
        BulkUploadStatusView.as_view(),
        name='depot-bulk-upload-status',
    ),
]
