from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from memory.models import UserProfile
from orchestrator.single_agent import SingleAgentOrchestrator
from .serializers import ChatRequestSerializer, ChatResponseSerializer

class ChatAPIView(APIView):
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if serializer.is_valid():
            user_profile, _ = UserProfile.objects.get_or_create(
                username='default_user',
                defaults={'email': 'default@example.com'}
            )
            
            message_text = serializer.validated_data['message']
            conversation_id = serializer.validated_data.get('conversation_id')
            agent_type = serializer.validated_data.get('agent_type', 'ResearchAgent')
            async_execution = serializer.validated_data.get('async_execution', False)
            
            if async_execution:
                from memory.models import Conversation
                from memory.tasks import run_agent_task
                
                # Pre-create conversation if it doesn't exist to reserve UUID
                if not conversation_id:
                    conversation = Conversation.objects.create(user_profile=user_profile)
                    conversation_id = conversation.id
                else:
                    conversation = Conversation.objects.get(id=conversation_id, user_profile=user_profile)
                
                # Create Message object for the user input
                conversation.messages.create(role='user', content=message_text)
                
                task = run_agent_task.delay(
                    username=user_profile.username,
                    conversation_id=str(conversation_id),
                    message_text=message_text,
                    agent_type=agent_type
                )
                return Response({
                    "conversation_id": str(conversation_id),
                    "task_id": task.id,
                    "status": "Accepted"
                }, status=status.HTTP_202_ACCEPTED)
            
            orchestrator = SingleAgentOrchestrator()
            try:
                result = orchestrator.handle_request(
                    user_profile=user_profile,
                    conversation_id=conversation_id,
                    message_text=message_text,
                    agent_type=agent_type
                )
                
                response_serializer = ChatResponseSerializer(data=result)
                if response_serializer.is_valid():
                    return Response(response_serializer.data, status=status.HTTP_200_OK)
                return Response(response_serializer.errors, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
