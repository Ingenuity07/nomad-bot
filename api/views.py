from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from memory.models import UserProfile, Conversation
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
                from memory.tasks import run_agent_task
                
                # Pre-create conversation if it doesn't exist to reserve UUID
                if not conversation_id:
                    conversation = Conversation.objects.create(user_profile=user_profile)
                    conversation_id = conversation.id
                else:
                    conversation = Conversation.objects.get(id=conversation_id, user_profile=user_profile)
                
                if not conversation.title and message_text:
                    conversation.title = message_text[:50] + ("..." if len(message_text) > 50 else "")
                    conversation.save(update_fields=['title'])

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
            
            selected_provider = serializer.validated_data.get('selected_provider')
            
            orchestrator = SingleAgentOrchestrator()
            try:
                result = orchestrator.handle_request(
                    user_profile=user_profile,
                    conversation_id=conversation_id,
                    message_text=message_text,
                    agent_type=agent_type,
                    selected_provider=selected_provider
                )
                
                response_serializer = ChatResponseSerializer(data=result)
                if response_serializer.is_valid():
                    return Response(response_serializer.data, status=status.HTTP_200_OK)
                return Response(response_serializer.errors, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ApproveAPIView(APIView):
    def post(self, request):
        conversation_id = request.data.get("conversation_id")
        approved = request.data.get("approved")
        
        if not conversation_id or approved is None:
            return Response({"error": "conversation_id and approved fields are required."}, status=status.HTTP_400_BAD_REQUEST)
            
        user_profile, _ = UserProfile.objects.get_or_create(
            username='default_user',
            defaults={'email': 'default@example.com'}
        )
        
        try:
            from core.agents.checkpoint_saver import DjangoCheckpointSaver
            from core.agents.v2_graph import get_v2_agent_graph
            
            config = {"configurable": {"thread_id": str(conversation_id)}}
            saver = DjangoCheckpointSaver()
            graph = get_v2_agent_graph(checkpoint_saver=saver)
            
            checkpoint = saver.get_tuple(config)
            if not checkpoint:
                return Response({"error": f"No active run checkpoint found for conversation {conversation_id}."}, status=status.HTTP_404_NOT_FOUND)
                
            # Update the checkpoint state with approved flag
            graph.update_state(config, {"human_approved": approved})
            
            # If approved, resume the graph execution task
            if approved:
                from memory.tasks import run_agent_task
                task = run_agent_task.delay(
                    username=user_profile.username,
                    conversation_id=str(conversation_id),
                    message_text="",  # Resume triggers None in invoke
                    agent_type="ResearchAgent"
                )
                return Response({
                    "status": "Resumed",
                    "task_id": task.id
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "status": "Rejected"
                }, status=status.HTTP_200_OK)
                
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ConversationListAPIView(APIView):
    def get(self, request):
        user_profile, _ = UserProfile.objects.get_or_create(
            username='default_user',
            defaults={'email': 'default@example.com'}
        )
        conversations = Conversation.objects.filter(user_profile=user_profile).order_by('-updated_at')
        data = []
        for conv in conversations:
            title = conv.title
            if not title:
                first_msg = conv.messages.order_by('created_at').first()
                if first_msg:
                    title = first_msg.content[:50] + ("..." if len(first_msg.content) > 50 else "")
                    conv.title = title
                    conv.save(update_fields=['title'])
                else:
                    title = "Empty Conversation"
            data.append({
                "id": str(conv.id),
                "title": title,
                "selected_provider": conv.selected_provider,
                "selected_model": conv.selected_model,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat()
            })
        return Response(data, status=status.HTTP_200_OK)


class ConversationDetailAPIView(APIView):
    def get(self, request, conversation_id):
        user_profile, _ = UserProfile.objects.get_or_create(
            username='default_user',
            defaults={'email': 'default@example.com'}
        )
        try:
            conversation = Conversation.objects.get(id=conversation_id, user_profile=user_profile)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
            
        messages = conversation.messages.order_by('created_at')
        msgs_data = []
        for msg in messages:
            msgs_data.append({
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "prompt_tokens": msg.prompt_tokens,
                "completion_tokens": msg.completion_tokens,
                "total_tokens": msg.total_tokens,
                "provider": msg.provider,
                "model": msg.model,
                "created_at": msg.created_at.isoformat()
            })
        return Response({
            "id": str(conversation.id),
            "title": conversation.title or "Untitled Conversation",
            "selected_provider": conversation.selected_provider,
            "selected_model": conversation.selected_model,
            "messages": msgs_data
        }, status=status.HTTP_200_OK)


class ProviderListAPIView(APIView):
    def get(self, request):
        from core.llm.router import IntelligentRouter
        router = IntelligentRouter()
        status_data = router.get_providers_status()
        return Response(status_data, status=status.HTTP_200_OK)
