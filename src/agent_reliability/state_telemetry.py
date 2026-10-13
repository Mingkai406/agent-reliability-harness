"""Checkpoint I/O spans and cross-process invocation links (no state payload logging)."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from opentelemetry import trace


class TracedSqliteSaver(SqliteSaver):
    tracer = None

    def get_tuple(self, config):
        with self.tracer.start_as_current_span("checkpoint.read") as span:
            result = super().get_tuple(config)
            span.set_attribute("checkpoint.found", result is not None)
            if result:
                span.set_attribute("checkpoint.id", result.checkpoint["id"])
            return result

    def put(self, config, checkpoint, metadata, new_versions):
        with self.tracer.start_as_current_span("checkpoint.write") as span:
            span.set_attribute("checkpoint.id", checkpoint["id"])
            span.set_attribute(
                "checkpoint.serialized_bytes", len(self.serde.dumps_typed(checkpoint)[1])
            )
            return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        writes = list(writes)
        with self.tracer.start_as_current_span("checkpoint.pending_writes") as span:
            span.set_attribute("checkpoint.task_id", task_id)
            span.set_attribute("checkpoint.write_count", len(writes))
            return super().put_writes(config, writes, task_id, task_path)


@contextmanager
def invocation_span(directory, tracer, case_id, layout):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "invocation_context.json"
    links = []
    if path.exists():
        try:
            prior = json.loads(path.read_text())
            context = trace.SpanContext(
                int(prior["trace_id"], 16),
                int(prior["span_id"], 16),
                is_remote=True,
                trace_flags=trace.TraceFlags(1),
            )
            if context.is_valid:
                links.append(trace.Link(context, {"relationship": "previous_invocation"}))
        except (ValueError, KeyError, TypeError):
            pass  # Observability metadata must not break business-state recovery.
    with tracer.start_as_current_span(
        "workflow.invoke",
        links=links,
        attributes={
            "case.id": case_id,
            "state.layout": layout,
        },
    ) as span:
        context = span.get_span_context()
        fd, temporary = tempfile.mkstemp(prefix=".invocation-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "trace_id": format(context.trace_id, "032x"),
                        "span_id": format(context.span_id, "016x"),
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        yield span
