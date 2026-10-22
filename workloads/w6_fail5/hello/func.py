# W6 failures and mixed traffic (runbook §38). The four arms differ ONLY in SAQEF_W6:
#   workloads/w6_base:   every call spins the CPU for 5 ms and succeeds (the in-session baseline)
#   workloads/w6_fail5:  as base, but every 20th call of each process answers HTTP 500 (5 %)
#   workloads/w6_fail20: as base, but every 5th call of each process answers HTTP 500 (20 %)
#   workloads/w6_alt:    calls alternate: odd calls spin 5 ms, even calls wait 5 ms (time.sleep);
#                        all succeed. Mixed traffic: half the requests compute, half wait.
# A failing call does the same 5 ms of work first, so the function CPU per call does not
# change; only the reply does. The reply names the arm, so a stale image cannot go unnoticed.
import itertools
import time

SAQEF_W6 = "fail5"
_N = itertools.count(1)
_EVERY = {"fail5": 20, "fail20": 5}.get(SAQEF_W6)


def _w6():
    """One call: (HTTP status, reply text)."""
    k = next(_N)
    t0 = time.perf_counter()
    if SAQEF_W6 == "alt" and k % 2 == 0:
        time.sleep(0.005)
        kind = "sleep"
    else:
        while time.perf_counter() - t0 < 0.005:
            pass
        kind = "spin"
    fail = _EVERY is not None and k % _EVERY == 0
    return (500 if fail else 200), "w6=%s call=%s %s" % (SAQEF_W6, kind, "error" if fail else "ok")


from fdk import response


def handler(ctx, data=None):
    st, text = _w6()
    return response.Response(ctx, response_data=text, status_code=st)
