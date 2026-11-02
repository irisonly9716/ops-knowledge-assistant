"""How long one piece of work may still take.

Retries multiply: a request timeout times the retries of the HTTP client times the attempts of the
consumer can add up to far more than anyone intended. A budget is one number for the whole piece of
work, handed down through the layers. Every layer asks how much is left before it waits or sends a
request, so the total stays bounded no matter how the layers combine.
"""
import time
from collections.abc import Callable


class TimeBudget:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + seconds

    def remaining(self) -> float:
        """Seconds left; zero or negative once the budget is used up."""
        return self._deadline - self._clock()
