"""Private HTTP interface that owns the Docker Engine connection."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from docker.errors import DockerException
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from runner.config import Settings
from runner.sandbox import SandboxExecutor, SandboxOverloaded, SandboxUnavailable
from shared.limits import MAX_INPUT_BYTES
from shared.request_limiter import RequestBodyLimitMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_size(self) -> CheckRequest:
        if len(self.source.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("source exceeds the configured input limit")
        return self


class TestRequest(CheckRequest):
    test_code: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_size(self) -> TestRequest:
        total_bytes = len(self.source.encode("utf-8")) + len(self.test_code.encode("utf-8"))
        if total_bytes > MAX_INPUT_BYTES:
            raise ValueError("source and test_code exceed the configured input limit")
        return self


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = Settings.from_env()
    application.state.settings = settings
    application.state.executor = None
    try:
        executor = SandboxExecutor(settings)
        executor.ping()
        application.state.executor = executor
        logger.info("Docker sandbox runner is ready")
    except DockerException:
        logger.exception("Docker Engine is unavailable; sandbox calls will fail closed")
    try:
        yield
    finally:
        executor = application.state.executor
        if executor is not None:
            executor.close()


app = FastAPI(
    title="Private Sandbox Runner",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(RequestBodyLimitMiddleware)


async def require_runner_token(
    request: Request,
    token: Annotated[str | None, Header(alias="X-Runner-Token")] = None,
) -> None:
    expected = request.app.state.settings.shared_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The internal runner credential is not configured",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal runner credential",
        )


def get_executor(request: Request) -> SandboxExecutor:
    executor = request.app.state.executor
    if executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Docker sandbox is unavailable",
        )
    return executor


@app.get("/internal/healthz")
async def healthz(request: Request) -> dict[str, str]:
    if request.app.state.executor is None:
        raise HTTPException(status_code=503, detail="Docker sandbox is unavailable")
    return {"status": "ok"}


@app.post("/internal/check", dependencies=[Depends(require_runner_token)])
def check_code(
    body: CheckRequest,
    executor: Annotated[SandboxExecutor, Depends(get_executor)],
) -> dict[str, object]:
    try:
        return executor.check(body.source)
    except SandboxOverloaded as exc:
        raise HTTPException(status_code=429, detail="No sandbox slots are available") from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status_code=503, detail="The sandbox is unavailable") from exc


@app.post("/internal/test", dependencies=[Depends(require_runner_token)])
def test_code(
    body: TestRequest,
    executor: Annotated[SandboxExecutor, Depends(get_executor)],
) -> dict[str, object]:
    try:
        return executor.test(body.source, body.test_code)
    except SandboxOverloaded as exc:
        raise HTTPException(status_code=429, detail="No sandbox slots are available") from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status_code=503, detail="The sandbox is unavailable") from exc
