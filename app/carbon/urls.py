from django.urls import path

from carbon import views as v

urlpatterns = [
    path('carbon/footprint', v.FootprintView.as_view(), name='carbon-footprint'),
    path('carbon/report.pdf', v.ReportView.as_view(), name='carbon-report'),
]
