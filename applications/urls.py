from django.urls import path
from .views import ApplicationTrackerAPIView

urlpatterns = [
    path('', ApplicationTrackerAPIView.as_view(), name='applications-tracker'),
]
