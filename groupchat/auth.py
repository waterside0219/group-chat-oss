from __future__ import annotations

from hmac import compare_digest


def auth_matches(headers: object, shared_secret: str) -> bool:
    if not shared_secret:
        return True
    get = getattr(headers, "get")
    token = get("X-Auth-Token", "") or get("X-Auth", "")
    return compare_digest(str(token), shared_secret)
