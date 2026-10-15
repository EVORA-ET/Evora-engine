from django.urls import path

from maintenance import views as v

urlpatterns = [
    path('maintenance/requirements', v.RequirementListCreateView.as_view(),
         name='maintenance-requirements'),
    path('maintenance/requirements/<uuid:pk>/retire',
         v.RequirementRetireView.as_view(),
         name='maintenance-requirement-retire'),
    path('maintenance/events', v.EventListCreateView.as_view(),
         name='maintenance-events'),
    path('maintenance/events/<uuid:pk>/void', v.EventVoidView.as_view(),
         name='maintenance-event-void'),
    path('maintenance/vehicles/<uuid:pk>/status',
         v.VehicleMaintenanceStatusView.as_view(),
         name='maintenance-vehicle-status'),
    path('maintenance/assessments', v.MaintenanceAssessmentCreateView.as_view(),
         name='maintenance-assessments'),
]
