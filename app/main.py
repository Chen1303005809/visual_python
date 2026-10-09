"""Dify-facing FastAPI application."""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.models import CheckResponse, CodeRequest, TestRequest, TestResponse
from app.runner_client import RunnerClient, RunnerOverloaded, RunnerUnavailable
from shared.request_limiter import RequestBodyLimitMiddleware

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"
bearer_auth = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.settings = Settings.from_env()
    application.state.runner_client = RunnerClient(application.state.settings)
    yield


app = FastAPI(
    title="Dify Python Code Validator",
    description="静态检查并在一次性隔离容器中测试 Agent 生成的 Python 代码。",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(RequestBodyLimitMiddleware)


def _openapi_schema() -> dict[str, Any]:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema is None:
        app.openapi_schema = _openapi_schema()
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


async def require_api_key(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_auth),
    ],
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_KEY is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Bearer API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The API key is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_runner_client(request: Request) -> RunnerClient:
    return request.app.state.runner_client


def _raise_runner_error(exc: RuntimeError) -> None:
    if isinstance(exc, RunnerOverloaded):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The code execution queue is full; retry later",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The sandbox runner is temporarily unavailable",
    ) from exc


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/python/check",
    response_model=CheckResponse,
    dependencies=[Depends(require_api_key)],
)
async def check_python_code(
    body: CodeRequest,
    client: Annotated[RunnerClient, Depends(get_runner_client)],
) -> dict[str, object]:
    """Check syntax, Ruff diagnostics, and availability of imported modules."""
    try:
        return await client.check(body.source)
    except (RunnerUnavailable, RunnerOverloaded) as exc:
        _raise_runner_error(exc)


@app.post(
    "/v1/python/test",
    response_model=TestResponse,
    dependencies=[Depends(require_api_key)],
)
async def test_python_code(
    body: TestRequest,
    client: Annotated[RunnerClient, Depends(get_runner_client)],
) -> dict[str, object]:
    """Run source and at least one supplied pytest assertion in a disposable sandbox."""
    try:
        return await client.test(body.source, body.test_code)
    except (RunnerUnavailable, RunnerOverloaded) as exc:
        _raise_runner_error(exc)
