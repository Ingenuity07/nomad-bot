from django.urls import path

from .views import PublishingProviderHealthAPIView, PublishingProviderWebhookAPIView


urlpatterns = [
    path("publishers/health/", PublishingProviderHealthAPIView.as_view(), name="social-publisher-health"),
    path(
        "publishers/<str:provider>/webhook/",
        PublishingProviderWebhookAPIView.as_view(),
        name="social-publisher-webhook",
    ),
]
