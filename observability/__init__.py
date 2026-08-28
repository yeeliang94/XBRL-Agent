"""Application observability helpers.

The public seam is intentionally small: request context lives in
``observability.context`` and durable run failures are captured through
``observability.incidents.capture_run_incident``.
"""

