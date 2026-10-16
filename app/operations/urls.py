from django.urls import path

from operations.views import AttentionView, OverviewSummaryView

urlpatterns = [
    path(
        'operations/summary',
        OverviewSummaryView.as_view(),
        name='operations-summary',
    ),
    path(
        'operations/attention',
        AttentionView.as_view(),
        name='operations-attention',
    ),
]
