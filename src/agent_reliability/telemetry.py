import json
import threading

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class JsonlExporter(SpanExporter):
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def export(self, spans):
        with self.lock, self.path.open("a") as stream:
            for span in spans:
                stream.write(json.dumps(json.loads(span.to_json())) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def provider_for(path):
    provider = TracerProvider(resource=Resource.create({"service.name": "agent-reliability"}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlExporter(path)))
    return provider
