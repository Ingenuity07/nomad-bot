from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from llm.router import IntelligentRouter
from django.db.models import Sum, Avg, Count
from django.db.models.functions import TruncDate
from llm.models import PromptRun

class ProviderListAPIView(APIView):
    def get(self, request):
        router = IntelligentRouter()
        status_data = router.get_providers_status()
        return Response(status_data, status=status.HTTP_200_OK)

class LLMAnalyticsAPIView(APIView):
    def get(self, request):
        try:
            runs = PromptRun.objects.all().order_by('-created_at')
            
            aggregates = runs.aggregate(
                total_calls=Count('id'),
                total_input_tokens=Sum('input_tokens'),
                total_output_tokens=Sum('output_tokens'),
                total_cost=Sum('cost_usd'),
                avg_latency=Avg('latency_ms')
            )
            
            total_calls = aggregates['total_calls'] or 0
            total_input_tokens = aggregates['total_input_tokens'] or 0
            total_output_tokens = aggregates['total_output_tokens'] or 0
            total_cost = float(aggregates['total_cost'] or 0.0)
            avg_latency = float(aggregates['avg_latency'] or 0.0)
            
            model_stats = runs.values('model_name').annotate(
                calls=Count('id'),
                input_tokens=Sum('input_tokens'),
                output_tokens=Sum('output_tokens'),
                cost=Sum('cost_usd'),
                latency=Avg('latency_ms')
            ).order_by('-calls')
            
            model_stats_list = []
            for ms in model_stats:
                model_stats_list.append({
                    "model_name": ms["model_name"],
                    "calls": ms["calls"],
                    "input_tokens": ms["input_tokens"] or 0,
                    "output_tokens": ms["output_tokens"] or 0,
                    "cost": float(ms["cost"] or 0.0),
                    "latency": float(ms["latency"] or 0.0)
                })
                
            daily_stats = runs.annotate(date=TruncDate('created_at')).values('date').annotate(
                calls=Count('id'),
                cost=Sum('cost_usd'),
                input_tokens=Sum('input_tokens'),
                output_tokens=Sum('output_tokens')
            ).order_by('date')[:14]
            
            daily_stats_list = []
            for ds in daily_stats:
                daily_stats_list.append({
                    "date": ds["date"].isoformat() if ds["date"] else "",
                    "calls": ds["calls"],
                    "cost": float(ds["cost"] or 0.0),
                    "input_tokens": ds["input_tokens"] or 0,
                    "output_tokens": ds["output_tokens"] or 0
                })
                
            recent_runs_list = []
            for r in runs[:50]:
                recent_runs_list.append({
                    "id": str(r.id),
                    "purpose": r.purpose,
                    "prompt_preview": r.prompt_text[:120] + ("..." if len(r.prompt_text) > 120 else ""),
                    "response_preview": r.response_text[:120] + ("..." if len(r.response_text) > 120 else ""),
                    "model_name": r.model_name,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cost_usd": r.cost_usd,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat()
                })
                
            return Response({
                "overview": {
                    "total_calls": total_calls,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_cost": total_cost,
                    "avg_latency_ms": avg_latency
                },
                "model_breakdown": model_stats_list,
                "daily_trends": daily_stats_list,
                "recent_runs": recent_runs_list
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                "overview": {
                    "total_calls": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cost": 0.0,
                    "avg_latency_ms": 0.0
                },
                "model_breakdown": [],
                "daily_trends": [],
                "recent_runs": [],
                "warning": str(e)
            }, status=status.HTTP_200_OK)
