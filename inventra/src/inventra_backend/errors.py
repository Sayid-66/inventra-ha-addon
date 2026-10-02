from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from .idempotency.operations import OperationPayloadMismatch


class StaleVersionError(Exception):
    def __init__(self, entity_type: str, entity_id: str, current: dict):
        super().__init__(f"{entity_type} {entity_id} write based on a stale version")
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current = current


class DuplicateEntityError(Exception):
    def __init__(self, entity_type: str, field: str, value: str):
        super().__init__(f"{entity_type}.{field} already in use: {value}")
        self.entity_type = entity_type
        self.field = field
        self.value = value


class ProductNameNotFoundError(Exception):
    def __init__(self, product_name: str):
        super().__init__(f"no product named {product_name!r} found")
        self.product_name = product_name


class AmbiguousProductNameError(Exception):
    def __init__(self, product_name: str, candidates: list[dict]):
        super().__init__(f"multiple products match {product_name!r}")
        self.product_name = product_name
        self.candidates = candidates


class BusinessRuleViolation(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OperationalError)
    async def _database_busy(request: Request, exc: OperationalError):
        # The lost-update fix can time out waiting for its write lock; expose that as a retryable failure.
        return JSONResponse(
            {"error": {"code": "DATABASE_BUSY", "message": "Database is busy; please retry the request."}},
            status_code=503,
        )

    @app.exception_handler(StaleVersionError)
    async def _stale_version(request: Request, exc: StaleVersionError):
        return JSONResponse(
            {"error": {"code": "STALE_VERSION", "message": str(exc)}, "current": exc.current},
            status_code=409,
        )

    @app.exception_handler(DuplicateEntityError)
    async def _duplicate(request: Request, exc: DuplicateEntityError):
        return JSONResponse({"error": {"code": "DUPLICATE_ENTITY", "message": str(exc)}}, status_code=409)

    @app.exception_handler(ProductNameNotFoundError)
    async def _product_name_not_found(request: Request, exc: ProductNameNotFoundError):
        return JSONResponse(
            {"error": {"code": "PRODUCT_NAME_NOT_FOUND", "message": str(exc)}}, status_code=404,
        )

    @app.exception_handler(AmbiguousProductNameError)
    async def _ambiguous_product_name(request: Request, exc: AmbiguousProductNameError):
        return JSONResponse(
            {"error": {"code": "AMBIGUOUS_PRODUCT_NAME", "message": str(exc)}, "candidates": exc.candidates},
            status_code=409,
        )

    @app.exception_handler(OperationPayloadMismatch)
    async def _op_mismatch(request: Request, exc: OperationPayloadMismatch):
        return JSONResponse(
            {"error": {"code": "OPERATION_ID_PAYLOAD_MISMATCH", "message": str(exc)}}, status_code=409
        )

    @app.exception_handler(BusinessRuleViolation)
    async def _business_rule(request: Request, exc: BusinessRuleViolation):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}, status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}}, status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception):
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}, status_code=500,
        )
