from django.urls import path
from .views import ProviderListAPIView

urlpatterns = [
    path('providers/', ProviderListAPIView.as_view(), name='llm-providers'),
]
