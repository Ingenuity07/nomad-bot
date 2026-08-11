from django.urls import path
from .views import (
    ResumeTailorAPIView, ResumeTemplateListAPIView, ResumeVersionListAPIView,
    ResumeVersionDetailAPIView, ResumeDiffAPIView, ResumeDownloadAPIView
)

urlpatterns = [
    path('tailor/', ResumeTailorAPIView.as_view(), name='resume-tailor'),
    path('templates/', ResumeTemplateListAPIView.as_view(), name='resume-template-list'),
    path('diff/', ResumeDiffAPIView.as_view(), name='resume-diff'),
    path('versions/', ResumeVersionListAPIView.as_view(), name='resume-version-list'),
    path('versions/<str:version_id>/download/', ResumeDownloadAPIView.as_view(), name='resume-download'),
    path('versions/<str:version_id>/', ResumeVersionDetailAPIView.as_view(), name='resume-version-detail'),
]
