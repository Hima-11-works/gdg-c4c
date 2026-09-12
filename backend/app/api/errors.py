"""One consistent error response shape for the whole API.

Every error — a raised HTTPException, a request validation failure, or an
unhandled exception — is rendered as:

    {"error": {"code": "...", "message": "...", "details": [...]?}}

matching app.api.schemas.ErrorResponse.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

# Registering on Starlette's base HTTPException (not fastapi.HTTPException,
# a subclass of it) matters: routing-level errors that never reach a route
# handler — like "no route matched" — are raised as the base class, and a
# handler keyed to the subclass would miss them, falling through to
# FastAPI's own default {"detail": ...} handler instead of this one.

logger = logging.getLogger(__name__)

_CODES_BY_STATUS = {
    400: "bad_request",
    404: "not_found",
    422: "validation_error",
    500: "internal_error",
}


def _error_body(code: str, message: str, details: list | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = _CODES_BY_STATUS.get(exc.status_code, "http_error")
    return JSONResponse(status_code=exc.status_code, content=_error_body(code, str(exc.detail)))


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_error_body(
            "validation_error", "Request validation failed.", jsonable_encoder(exc.errors())
        ),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("internal_error", "An unexpected error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
