from django.urls import path
from .views import ChatAPIView, ApproveAPIView, ConversationListAPIView, ConversationDetailAPIView

urlpatterns = [
    path('chat/', ChatAPIView.as_view(), name='chat'),
    path('chat/approve/', ApproveAPIView.as_view(), name='approve'),
    path('conversations/', ConversationListAPIView.as_view(), name='conversation-list'),
    path('conversations/<uuid:conversation_id>/', ConversationDetailAPIView.as_view(), name='conversation-detail'),
]
