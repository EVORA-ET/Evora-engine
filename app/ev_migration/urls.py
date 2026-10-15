from django.urls import path

from ev_migration import views as v

urlpatterns = [
    path('ev-migration/candidates', v.CandidateListCreateView.as_view(),
         name='ev-candidates'),
    path('ev-migration/candidates/<uuid:pk>', v.CandidateDetailView.as_view(),
         name='ev-candidate-detail'),
    path('ev-migration/assessments/readiness',
         v.ReadinessAssessmentView.as_view(), name='ev-readiness'),
    path('ev-migration/assessments/tco', v.TcoAssessmentView.as_view(),
         name='ev-tco'),
    path('ev-migration/assessments/emissions',
         v.EmissionsAssessmentView.as_view(), name='ev-emissions'),
    path('ev-migration/decisions', v.DecisionListCreateView.as_view(),
         name='ev-decisions'),
]
