from django.urls import path
from .views import (
    KnowledgeBaseAPIView, ResumeIngestAPIView, KnowledgeBaseResetAPIView,
    KnowledgeBaseEnrichAPIView
)

urlpatterns = [
    path('', KnowledgeBaseAPIView.as_view(), name='knowledge-base'),
    path('ingest/', ResumeIngestAPIView.as_view(), name='resume-ingest'),
    path('reset/', KnowledgeBaseResetAPIView.as_view(), name='knowledge-base-reset'),
    path('enrich/', KnowledgeBaseEnrichAPIView.as_view(), name='knowledge-base-enrich'),
]
