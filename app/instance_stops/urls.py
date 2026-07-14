from django.urls import path

from instance_stops.views import (
    InstanceStopDetailView,
    InstanceStopListCreateView,
)

urlpatterns = [
    path(
        'instance-stop', InstanceStopListCreateView.as_view(),
        name='instance-stop-list-create',
    ),
    path(
        'instance-stop/<uuid:pk>', InstanceStopDetailView.as_view(),
        name='instance-stop-detail',
    ),
]
