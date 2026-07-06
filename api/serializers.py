from rest_framework import serializers

class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(required=True)
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.CharField(required=False, default="ResearchAgent")
    async_execution = serializers.BooleanField(required=False, default=False)
    
class ChatResponseSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    response = serializers.CharField()
