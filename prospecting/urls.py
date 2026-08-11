from django.urls import path
from .views import ProspectingDiscoverAPIView, ProspectingLeadsAPIView, ProspectingResetAPIView

urlpatterns = [
    path('discover/', ProspectingDiscoverAPIView.as_view(), name='prospecting-discover'),
    path('leads/', ProspectingLeadsAPIView.as_view(), name='prospecting-leads'),
    path('reset/', ProspectingResetAPIView.as_view(), name='prospecting-reset'),
]
