from django.urls import path
from .v3_views import (
    KnowledgeBaseAPIView, ResumeIngestAPIView, JobParseAPIView,
    ResumeTailorAPIView, ResumeVersionListAPIView, ResumeVersionDetailAPIView,
    ApplicationTrackerAPIView, ResumeDiffAPIView, ResumeDownloadAPIView,
    KnowledgeBaseResetAPIView, KnowledgeBaseEnrichAPIView,
    ProspectingDiscoverAPIView, ProspectingLeadsAPIView, ProspectingResetAPIView,
    ResumeTemplateListAPIView
)

urlpatterns = [
    path('knowledge-base/', KnowledgeBaseAPIView.as_view(), name='v3-knowledge-base'),
    path('knowledge-base/ingest/', ResumeIngestAPIView.as_view(), name='v3-resume-ingest'),
    path('knowledge-base/reset/', KnowledgeBaseResetAPIView.as_view(), name='v3-knowledge-base-reset'),
    path('knowledge-base/enrich/', KnowledgeBaseEnrichAPIView.as_view(), name='v3-knowledge-base-enrich'),
    path('jobs/parse/', JobParseAPIView.as_view(), name='v3-job-parse'),
    path('resumes/tailor/', ResumeTailorAPIView.as_view(), name='v3-resume-tailor'),
    path('resumes/templates/', ResumeTemplateListAPIView.as_view(), name='v3-resume-template-list'),
    path('resumes/diff/', ResumeDiffAPIView.as_view(), name='v3-resume-diff'),
    path('resumes/versions/', ResumeVersionListAPIView.as_view(), name='v3-resume-version-list'),
    path('resumes/versions/<str:version_id>/download/', ResumeDownloadAPIView.as_view(), name='v3-resume-download'),
    path('resumes/versions/<str:version_id>/', ResumeVersionDetailAPIView.as_view(), name='v3-resume-version-detail'),
    path('applications/', ApplicationTrackerAPIView.as_view(), name='v3-application-tracker'),
    path('prospecting/discover/', ProspectingDiscoverAPIView.as_view(), name='v3-prospecting-discover'),
    path('prospecting/leads/', ProspectingLeadsAPIView.as_view(), name='v3-prospecting-leads'),
    path('prospecting/reset/', ProspectingResetAPIView.as_view(), name='v3-prospecting-reset'),
]
