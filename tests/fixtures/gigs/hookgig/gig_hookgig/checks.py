"""The target of the hook hookgig's gig.toml declares; each call is recorded so a test can see it."""

calls = []


def doctor(*args, **kwargs):
    calls.append((args, kwargs))
    return [{"id": "hookgig.ok", "status": "pass"}]
