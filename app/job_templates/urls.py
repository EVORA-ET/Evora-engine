from django.urls import path

from job_templates.views import (
    JobTemplateDetailView,
    JobTemplateListCreateView,
)

urlpatterns = [
    path(
        'job-template', JobTemplateListCreateView.as_view(),
        name='job-template-list-create',
    ),
    path(
        'job-template/<uuid:pk>', JobTemplateDetailView.as_view(),
        name='job-template-detail',
    ),
]
