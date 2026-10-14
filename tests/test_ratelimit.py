from ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_cap_and_sliding_window():
    clock = Clock()
    limiter = RateLimiter(limit=2, window=3600, clock=clock)
    assert limiter.acquire(1) == 0
    clock.now += 600
    assert limiter.acquire(1) == 0
    assert limiter.acquire(1) == 3000  # first slot frees 3600 s after the first hit
    assert limiter.acquire(2) == 0  # per user
    clock.now += 3000
    assert limiter.acquire(1) == 0


def test_blocked_attempts_are_not_counted():
    clock = Clock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    limiter.acquire(1)
    for _ in range(5):
        assert limiter.acquire(1) > 0
    clock.now += 60
    assert limiter.acquire(1) == 0

