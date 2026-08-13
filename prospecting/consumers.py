import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

class ProspectingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.run_id = self.scope['url_route']['kwargs']['run_id']
        self.group_name = f"prospecting_{self.run_id}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"WebSocket client connected to group {self.group_name}")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        logger.info(f"WebSocket client disconnected from group {self.group_name}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            logger.info(f"Received WS message on {self.group_name}: {data}")
        except Exception as e:
            logger.error(f"Error reading WS message: {e}")

    async def progress_update(self, event):
        await self.send(text_data=json.dumps(event.get("data")))


class CampaignConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.campaign_id = self.scope['url_route']['kwargs']['campaign_id']
        self.group_name = f"campaign_{self.campaign_id}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"WebSocket client connected to campaign group {self.group_name}")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        logger.info(f"WebSocket client disconnected from campaign group {self.group_name}")

    async def campaign_event(self, event):
        await self.send(text_data=json.dumps(event.get("data")))


def broadcast_campaign_event(campaign_id: str, event_type: str, metadata: dict):
    """Sends a real-time campaign event notification payload to the campaign group layer."""
    channel_layer = get_channel_layer()
    if channel_layer:
        group_name = f"campaign_{campaign_id}"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "campaign_event",
                "data": {
                    "event_type": event_type,
                    "metadata": metadata
                }
            }
        )
