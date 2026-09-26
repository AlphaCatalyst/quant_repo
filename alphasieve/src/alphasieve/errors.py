EXIT_CODES = {
    "VALIDATION_ERROR": 2,
    "GATE_FAILED": 3,
    "PERMISSION_DENIED": 4,
    "BUDGET_EXHAUSTED": 5,
    "NOT_FOUND": 6,
    "CONFLICT": 7,
    "STORAGE_UNAVAILABLE": 8,
    "INTERNAL": 10,
}


class AlphaSieveError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        if code not in EXIT_CODES:
            raise ValueError(f"unknown error code {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]


def permission_denied(message: str, **details) -> AlphaSieveError:
    return AlphaSieveError("PERMISSION_DENIED", message, details)


def not_found(message: str, **details) -> AlphaSieveError:
    return AlphaSieveError("NOT_FOUND", message, details)


def validation_error(message: str, **details) -> AlphaSieveError:
    return AlphaSieveError("VALIDATION_ERROR", message, details)
