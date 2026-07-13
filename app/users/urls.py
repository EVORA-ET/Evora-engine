from django.urls import path

from users.views import MeView, UserDetailView

urlpatterns = [
    path('me', MeView.as_view(), name='me'),
    path('user/<uuid:pk>', UserDetailView.as_view(), name='user-detail'),
]
