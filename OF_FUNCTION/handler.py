import time


def handle(req):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.005:
        pass
    return "Hello"
