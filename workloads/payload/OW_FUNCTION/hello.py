"""OpenWhisk web action for W1 payload echo (runbook §28).

A text/plain POST to a (non-raw) web action arrives as the string __ow_body;
returning it as "body" sends it back unchanged. GET has no body -> empty reply.
"""


def main(args):
    return {"headers": {"Content-Type": "text/plain"},
            "body": args.get("__ow_body", "")}
