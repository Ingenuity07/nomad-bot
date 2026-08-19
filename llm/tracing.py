import logging
import os
from typing import Optional, Dict, Any
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

# Configuration flags matching settings and plan conventions
OTEL_ENABLED = os.environ.get("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
LLM_TRACING_ENABLED = os.environ.get("LLM_TRACING_ENABLED", "true").lower() in ("true", "1", "yes")

_is_initialized = False
_tracer = None

def init_tracer():
    global _is_initialized, _tracer
    if _is_initialized:
        return _tracer

    if not OTEL_ENABLED:
        logger.debug("OpenTelemetry tracing is disabled (OTEL_ENABLED=false). Using No-op tracer.")
        _tracer = trace.get_tracer("nomad-llm-noop")
        _is_initialized = True
        return _tracer

    try:
        service_name = os.environ.get("OTEL_SERVICE_NAME", "nomad-llm")
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        
        # Try to configure OTLP exporter if endpoint is set
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                headers_raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
                headers = {}
                if headers_raw:
                    for item in headers_raw.split(","):
                        if "=" in item:
                            k, v = item.split("=", 1)
                            headers[k.strip()] = v.strip()
                
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint, headers=headers)
                processor = BatchSpanProcessor(exporter)
                provider.add_span_processor(processor)
                logger.info(f"OpenTelemetry OTLP Exporter configured to endpoint: {otlp_endpoint}")
            except Exception as exp_err:
                logger.warning(f"Failed to initialize OTLP Span exporter: {exp_err}. Falling back to default processor.")
        
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("nomad-llm")
        logger.info("OpenTelemetry tracer initialized successfully.")
    except Exception as err:
        logger.error(f"Error initializing OpenTelemetry SDK: {err}. Using No-op tracer fallback.")
        _tracer = trace.get_tracer("nomad-llm-fallback-noop")
        
    _is_initialized = True
    return _tracer

def get_tracer():
    global _tracer
    if _tracer is None:
        return init_tracer()
    return _tracer
