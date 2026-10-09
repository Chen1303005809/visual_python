FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:/usr/bin:/bin"

WORKDIR /app

FROM base AS api
COPY requirements-api.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-api.txt
COPY app /app/app
COPY shared /app/shared
COPY openapi.json /app/openapi.json
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS runner
COPY requirements-runner.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-runner.txt
COPY runner /app/runner
COPY shared /app/shared
EXPOSE 9000
CMD ["uvicorn", "runner.main:app", "--host", "0.0.0.0", "--port", "9000"]

FROM base AS sandbox
COPY requirements-sandbox.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-sandbox.txt
COPY runner/sandbox_tools /opt/dify_validator
COPY 价格接口/external_head.py /opt/dify_validator/external_head.py
COPY 价格接口/external_baseid.py /opt/dify_validator/external_baseid.py
COPY sandbox_stubs/PyEngine.py /opt/dify_validator/PyEngine.py
ENV PYTHONPATH=/opt/dify_validator
USER 10001:10001
