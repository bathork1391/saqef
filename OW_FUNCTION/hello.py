"""OpenWhisk action mirroring hello/func.py: a genuine ~5 ms CPU spin.

Same workload-anchoring rationale as the Fn/OpenFaaS 'hello' function
(SAQEF_PAPER_DRAFT.md: a no-op handler makes the marginal function CPU ~0 and
the cp_dynamic_share ratio degenerate). Exposed as a WEB ACTION so the GET-only
measurement harness can invoke it without touching the frozen measurement path:
GET /api/v1/web/guest/default/hello returns the result directly (blocking).
"""

import time


def main(args):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.005:
        pass
    return {"ok": True}
