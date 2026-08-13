from django.urls import path
from .views import ProviderListAPIView, LLMAnalyticsAPIView

urlpatterns = [
    path('providers/', ProviderListAPIView.as_view(), name='llm-providers'),
    path('analytics/', LLMAnalyticsAPIView.as_view(), name='llm-analytics'),
]
