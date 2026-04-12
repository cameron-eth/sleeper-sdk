from __future__ import annotations


class SleeperApiError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Sleeper API error {status_code}: {message}")


class SleeperNotFoundError(SleeperApiError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(404, message)


class SleeperRateLimitError(SleeperApiError):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(429, message)
