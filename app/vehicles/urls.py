from django.urls import path

from vehicles.views import (
    BulkUploadStatusView,
    BulkUploadView,
    VehicleDetailView,
    VehicleListCreateView,
)

urlpatterns = [
    path(
        'vehicle', VehicleListCreateView.as_view(),
        name='vehicle-list-create',
    ),
    path(
        'vehicle/<uuid:pk>', VehicleDetailView.as_view(),
        name='vehicle-detail',
    ),
    path(
        'vehicles/bulk-upload',
        BulkUploadView.as_view(),
        name='vehicle-bulk-upload',
    ),
    path(
        'vehicles/bulk-upload/<uuid:upload_id>',
        BulkUploadStatusView.as_view(),
        name='vehicle-bulk-upload-status',
    ),
]
