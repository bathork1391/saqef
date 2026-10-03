def handler(ctx, data=None):
    # W1 payload echo (runbook §28): return the request body unchanged.
    return data.getvalue().decode("utf-8") if data is not None else ""
