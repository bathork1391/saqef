import time

def handler(ctx, data=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.005:
        pass
    return {"message": "Hello World"}