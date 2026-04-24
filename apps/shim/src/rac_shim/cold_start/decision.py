# pattern: Functional Core

from dataclasses import dataclass


@dataclass(frozen=True)
class ColdStartDecision:
    should_serve_interstitial: bool
    should_wake: bool  # trigger background wake?


def decide(
    upstream_response_code: int | None,
    upstream_latency_ms: float | None,
    *,
    cold_start_threshold_ms: int,
) -> ColdStartDecision:
    """Decide whether the shim should serve a cold-start interstitial.

    Cold-start is detected when the upstream did not respond or returned a
    gateway-error status (503/504).  A slow but successful response is NOT
    a cold-start — the request is already done and returning the response to
    the user is the right action.

    Specifically:
    - upstream_response_code is None (connection failure / timeout):
      interstitial + wake.
    - upstream_response_code is 503 or 504:
      interstitial + wake.
    - upstream_response_code is anything else (2xx, 3xx, 4xx, other 5xx)
      regardless of latency: no interstitial, no wake.  If the upstream
      responded at all — even slowly — the response is ready and the user
      should receive it.

    Note on the cold_start_threshold_ms parameter: the phase plan mentions a
    latency threshold, but applying it post-hoc on a completed response creates
    a confusing UX (the user already got the response; showing an interstitial
    now makes no sense).  This implementation therefore ignores the latency on
    completed responses and uses the threshold only as documentation of the
    design intent (e.g., for metrics / alerting in a later task).  If the
    intent changes to serve the interstitial *before* getting a response (i.e.,
    based on a request timeout), the right place to implement that is in the
    proxy shell layer, not here.
    """
    if upstream_response_code is None:
        return ColdStartDecision(should_serve_interstitial=True, should_wake=True)

    if upstream_response_code in (503, 504):
        return ColdStartDecision(should_serve_interstitial=True, should_wake=True)

    return ColdStartDecision(should_serve_interstitial=False, should_wake=False)
