import json
from channels.generic.websocket import AsyncWebsocketConsumer
import logging

logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        self.group_name = f"chat_{self.conversation_id}"

        # Join conversation group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()
        logger.info(f"WebSocket client connected to group {self.group_name}")

    async def disconnect(self, close_code):
        # Leave conversation group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        logger.info(f"WebSocket client disconnected from group {self.group_name}")

    async def receive(self, text_data):
        # Optionally handle incoming WebSocket messages (mostly passive listening for agent runs)
        try:
            data = json.loads(text_data)
            logger.info(f"Received message from client on WS {self.group_name}: {data}")
        except Exception as e:
            logger.error(f"Error parsing incoming WS message: {str(e)}")

    async def chat_message(self, event):
        # Send live event update to WebSocket client
        await self.send(text_data=json.dumps({
            "event_type": event.get("event_type"),
            "data": event.get("data")
        }))
