"""Safe public API errors without request payloads or secrets."""

from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "request_id": str(uuid4())},
    )


def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Never echo rejected text or Pydantic input/context into the public error.
    if any("decision_text" in error["loc"] or "performed_actions" in error["loc"]
           for error in exc.errors()):
        return error_response(422, "invalid_decision_text", "Invalid decision text")
    return error_response(422, "invalid_request", "Invalid request")
