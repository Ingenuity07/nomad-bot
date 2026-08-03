from rest_framework import serializers

class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(required=True)
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.CharField(required=False, default="ResearchAgent")
    async_execution = serializers.BooleanField(required=False, default=False)
    selected_provider = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
class ChatResponseSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    response = serializers.CharField()
    selected_provider = serializers.CharField(required=False, allow_null=True)
    selected_model = serializers.CharField(required=False, allow_null=True)
    prompt_tokens = serializers.IntegerField(required=False, default=0)
    completion_tokens = serializers.IntegerField(required=False, default=0)
    total_tokens = serializers.IntegerField(required=False, default=0)
