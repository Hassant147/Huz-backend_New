import logging
import time

from django.conf import settings
from django.db import connection, reset_queries


logger = logging.getLogger(__name__)


def _perf_metrics_requested(request):
    if not getattr(settings, "ENABLE_PERF_METRICS", False):
        return False

    if request.GET.get("__perf") == "1":
        return True

    header_name = (
        getattr(settings, "PERF_METRICS_REQUEST_HEADER", "X-Perf-Metrics")
        .strip()
        .upper()
        .replace("-", "_")
    )
    header_value = request.META.get(f"HTTP_{header_name}", "")
    return str(header_value).strip() == "1"


class RequestPerformanceMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _perf_metrics_requested(request):
            return self.get_response(request)

        started_at = time.perf_counter()
        previous_force_debug_cursor = connection.force_debug_cursor
        response = None

        try:
            reset_queries()
            connection.force_debug_cursor = True
            response = self.get_response(request)
        finally:
            connection.force_debug_cursor = previous_force_debug_cursor

            duration_ms = (time.perf_counter() - started_at) * 1000.0
            queries = list(getattr(connection, "queries", []) or [])
            query_count = len(queries)
            query_time_ms = 0.0

            for query in queries:
                try:
                    query_time_ms += float(query.get("time", 0.0)) * 1000.0
                except (TypeError, ValueError, AttributeError):
                    continue

            status_code = getattr(response, "status_code", 0) if response is not None else 0

            logger.info(
                "api.performance method=%s path=%s status=%s duration_ms=%.2f query_count=%s query_time_ms=%.2f",
                request.method,
                request.path,
                status_code,
                duration_ms,
                query_count,
                query_time_ms,
            )

            if response is not None:
                response["X-Perf-Duration-Ms"] = f"{duration_ms:.2f}"
                response["X-Perf-Query-Count"] = str(query_count)
                response["X-Perf-Query-Time-Ms"] = f"{query_time_ms:.2f}"

        return response


class LegacyAuthDeprecationHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        legacy_source = getattr(request, "_legacy_token_used", "")
        if legacy_source:
            response["X-Auth-Deprecated"] = legacy_source

        return response
