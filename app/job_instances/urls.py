from django.urls import path

from job_instances.views import (
    JobInstanceDetailView,
    JobInstanceListCreateView,
)

urlpatterns = [
    path(
        'job-instance', JobInstanceListCreateView.as_view(),
        name='job-instance-list-create',
    ),
    path(
        'job-instance/<uuid:pk>', JobInstanceDetailView.as_view(),
        name='job-instance-detail',
    ),
]
