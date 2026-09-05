from django.urls import path

from .views import (
    ApprovePostAPIView,
    BriefDetailAPIView,
    BriefListCreateAPIView,
    CancelPostAPIView,
    DashboardAPIView,
    GeneratePostsAPIView,
    PostDetailAPIView,
    PostImageAPIView,
    PostListAPIView,
    PublisherCallbackAPIView,
    RegeneratePostImageAPIView,
    PublishNowAPIView,
    SettingsAPIView,
)

urlpatterns = [
    path("dashboard/", DashboardAPIView.as_view(), name="linkedin-dashboard"),
    path("settings/", SettingsAPIView.as_view(), name="linkedin-settings"),
    path("briefs/", BriefListCreateAPIView.as_view(), name="linkedin-briefs"),
    path("briefs/<uuid:pk>/", BriefDetailAPIView.as_view(), name="linkedin-brief-detail"),
    path("posts/", PostListAPIView.as_view(), name="linkedin-posts"),
    path("posts/generate/", GeneratePostsAPIView.as_view(), name="linkedin-generate"),
    path("posts/<uuid:pk>/", PostDetailAPIView.as_view(), name="linkedin-post-detail"),
    path("posts/<uuid:pk>/image/", PostImageAPIView.as_view(), name="linkedin-post-image"),
    path("posts/<uuid:pk>/regenerate-image/", RegeneratePostImageAPIView.as_view(), name="linkedin-regenerate-image"),
    path("posts/<uuid:pk>/approve/", ApprovePostAPIView.as_view(), name="linkedin-approve"),
    path("posts/<uuid:pk>/cancel/", CancelPostAPIView.as_view(), name="linkedin-cancel"),
    path("posts/<uuid:pk>/publish-now/", PublishNowAPIView.as_view(), name="linkedin-publish-now"),
    path("publisher-callback/", PublisherCallbackAPIView.as_view(), name="linkedin-publisher-callback"),
]
