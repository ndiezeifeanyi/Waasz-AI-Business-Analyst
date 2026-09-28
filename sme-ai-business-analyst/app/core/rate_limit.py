from time import monotonic


class InMemoryRateLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit_per_minute = limit_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = monotonic()
        window_start = now - 60
        hits = [hit for hit in self._hits.get(key, []) if hit >= window_start]
        if len(hits) >= self.limit_per_minute:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
