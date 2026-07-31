import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from agentguard.config import AppSettings

API_KEY_HEADER = "AgentGuard-API-Key"
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: AppSettings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._api_key = settings.server.api_key

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if self._api_key is None or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        supplied = request.headers.get(API_KEY_HEADER)
        if supplied and hmac.compare_digest(supplied, self._api_key):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid AgentGuard API key"},
        )
