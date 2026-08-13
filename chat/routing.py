from django.urls import re_path
from chat.consumers import ChatConsumer
from prospecting.consumers import ProspectingConsumer, CampaignConsumer

websocket_urlpatterns = [
    re_path(r'^ws/chat/(?P<conversation_id>[a-fA-F0-9\-]+)/$', ChatConsumer.as_asgi()),
    re_path(r'^ws/prospecting/(?P<run_id>[a-fA-F0-9\-]+)/$', ProspectingConsumer.as_asgi()),
    re_path(r'^ws/campaigns/(?P<campaign_id>[a-fA-F0-9\-]+)/$', CampaignConsumer.as_asgi()),
]
