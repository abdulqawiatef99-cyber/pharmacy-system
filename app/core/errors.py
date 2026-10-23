"""Domain errors. The API layer maps them to HTTP codes; messages are Arabic
because they are shown to the pharmacist, codes are stable for clients."""


class DomainError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class ValidationFailed(DomainError):
    status = 422
    code = "validation_failed"


class NotFound(DomainError):
    status = 404
    code = "not_found"


class Unauthorized(DomainError):
    status = 401
    code = "unauthorized"


class Forbidden(DomainError):
    status = 403
    code = "forbidden"


class Conflict(DomainError):
    status = 409
    code = "conflict"
