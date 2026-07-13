from django.urls import path

from organizations.views import OrganizationCreateView, OrganizationDetailView

urlpatterns = [
    path('organization', OrganizationCreateView.as_view(), name='organization-create'),
    path('organization/<uuid:pk>', OrganizationDetailView.as_view(), name='organization-detail'),
]
