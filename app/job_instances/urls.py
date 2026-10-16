from django.urls import path

from job_instances.views import (
    JobAssignView,
    JobEligibleResourcesView,
    JobInstanceDetailView,
    JobInstanceListCreateView,
    JobStatusView,
)

urlpatterns = [
    path(
        'job-instance',
        JobInstanceListCreateView.as_view(),
        name='job-instance-list-create',
    ),
    path(
        'job-instance/<uuid:pk>',
        JobInstanceDetailView.as_view(),
        name='job-instance-detail',
    ),
    path(
        'job-instance/<uuid:pk>/assign',
        JobAssignView.as_view(),
        name='job-instance-assign',
    ),
    path(
        'job-instance/<uuid:pk>/status',
        JobStatusView.as_view(),
        name='job-instance-status',
    ),
    path(
        'job-instance/<uuid:pk>/eligible-resources',
        JobEligibleResourcesView.as_view(),
        name='job-instance-eligible-resources',
    ),
]
