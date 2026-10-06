"""LLM endpoint availability (stub; lane LLM implements)."""


def state(settings):
    return {"available": None, "paused_since": None, "consecutive_failures": 0, "last_probe": None}
