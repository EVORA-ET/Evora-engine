from django.urls import path

from fleets.views import (
    FleetDetailView,
    FleetListCreateView,
    FleetMembershipView,
)

urlpatterns = [
    path('fleet', FleetListCreateView.as_view(), name='fleet-list-create'),
    path('fleet/<uuid:pk>', FleetDetailView.as_view(), name='fleet-detail'),
    path(
        'fleet/<uuid:pk>/vehicles',
        FleetMembershipView.as_view(),
        name='fleet-vehicles',
    ),
]
