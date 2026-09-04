from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.conf.urls.static import static

def health_check_view(request):
    return JsonResponse({"status": "ok", "service": "django-web-health"})

@csrf_exempt
def wake_view(request):
    return JsonResponse({"status": "ok", "service": "django-web-wake", "message": "Service awakened successfully"})

urlpatterns = [
    path('health', health_check_view, name='health-check-short'),
    path('health/', health_check_view, name='health-check'),
    path('wake', wake_view, name='wake-short'),
    path('wake/', wake_view, name='wake'),

    path('admin/', admin.site.urls),
    
    # OpenAPI Schema & API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # LLM Providers status API (now inside llm app)
    path('api/', include('llm.urls')),
    
    # V3 REST Endpoints (Modular Apps)
    path('api/v3/prospecting/', include('prospecting.urls')),
    path('api/v3/linkedin/', include('integrations.linkedin.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
