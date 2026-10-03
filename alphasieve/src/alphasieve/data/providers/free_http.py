"""Small, rate-limited HTTP helper for public market-data endpoints."""

import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class FreeDataError(RuntimeError):
    pass


_LOCK = threading.Lock()
_NEXT_CALL = 0.0
CALL_GAP_S = 0.7
RETRIES = 3
MAX_BYTES = 20_000_000
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlphaSieve/1.0)"}


def get(url: str, params: dict[str, str] | None = None, *, referer: str = "") -> bytes:
    global _NEXT_CALL
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {**HEADERS, **({"Referer": referer} if referer else {})}
    last_error = None
    for attempt in range(RETRIES):
        with _LOCK:
            pause = max(0.0, _NEXT_CALL - time.monotonic())
            if pause:
                time.sleep(pause)
            _NEXT_CALL = time.monotonic() + CALL_GAP_S
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FreeDataError("response exceeds 20 MB")
            return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 429, 500, 502, 503, 504):
                break
        except (OSError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < RETRIES:
            time.sleep(2 ** attempt)
    raise FreeDataError(f"public data request failed: {urllib.parse.urlsplit(url).path}: {last_error}") from last_error
