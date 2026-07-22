"""Retry / circuit-breaker policies for IngestOrchestrator (engine-redesign §4.4
follow-up). Pure value objects extracted from IngestOrchestrator: ErrorClassifier
(auth/quota/retryable), BackoffPolicy (exponential capped), CircuitBreaker
(consecutive-failure threshold). Behavior identical to the inline logic in
classify_error / process_with_retry / run_job_loop.
"""

from __future__ import annotations

from dataclasses import dataclass


class ErrorClassifier:
    """Classify an embedding/provider error as 'auth' (bad key, non-retryable),
    'quota' (exhausted, non-retryable), or 'retryable' (transient). 'auth'/'quota'
    are global: the caller aborts the whole job rather than grinding every object."""

    AUTH_MARKERS = (
        "invalid_api_key",
        "invalid x-api-key",
        "authentication",
        "unauthorized",
        "permission denied",
        "401",
    )

    @staticmethod
    def classify(e: Exception) -> str:
        m = str(e).lower()
        nm = type(e).__name__.lower()
        if (
            any(k in m for k in ErrorClassifier.AUTH_MARKERS)
            or "authentication" in nm
            or "permissiondenied" in nm
        ):
            return "auth"
        # Quota (insufficient_quota / 402) is non-retryable, unlike a transient 429.
        if "insufficient_quota" in m or "402" in m:
            return "quota"
        return "retryable"


@dataclass
class BackoffPolicy:
    """Exponential backoff: initial_ms * 2**attempt, capped at max_ms."""

    initial_ms: int
    max_ms: int

    def delay_ms(self, attempt: int) -> int:
        return min(self.initial_ms * (2**attempt), self.max_ms)


class CircuitBreaker:
    """Counts consecutive failures; trips (record_failure returns True) once the
    count reaches threshold. A success / skip / deferred resets the count so a
    burst of `rm`s doesn't accumulate into a false trip."""

    def __init__(self, threshold: int, initial: int = 0):
        self._threshold = threshold
        self._consec_fail = initial

    @property
    def consec_fail(self) -> int:
        return self._consec_fail

    def record_success(self) -> None:
        self._consec_fail = 0

    def record_failure(self) -> bool:
        """Increment and return True if tripped (consec >= threshold)."""
        self._consec_fail += 1
        return self._consec_fail >= self._threshold
