from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from llm.router import IntelligentRouter

class ProviderListAPIView(APIView):
    def get(self, request):
        router = IntelligentRouter()
        status_data = router.get_providers_status()
        return Response(status_data, status=status.HTTP_200_OK)
