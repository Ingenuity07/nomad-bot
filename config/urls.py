from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from knowledge_base.views import JobParseAPIView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # OpenAPI Schema & API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Legacy Chat API (now inside chat app)
    path('api/', include('chat.urls')),
    
    # LLM Providers status API (now inside llm app)
    path('api/', include('llm.urls')),
    
    # V3 REST Endpoints (Modular Apps)
    path('api/v3/knowledge-base/', include('knowledge_base.urls')),
    path('api/v3/jobs/parse/', JobParseAPIView.as_view(), name='v3-job-parse'),
    path('api/v3/resumes/', include('resume.urls')),
    path('api/v3/prospecting/', include('prospecting.urls')),
    path('api/v3/applications/', include('applications.urls')),
    
    path('', TemplateView.as_view(template_name='chat.html'), name='chat_ui'),
]
