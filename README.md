# Dify Python 代码验证工具

这是一个给 Dify Agent 调用的独立 FastAPI 服务，提供静态检查和 pytest 断言测试。公网/内网调用 API 的容器不持有 Docker socket；只有不发布端口的 `sandbox-runner` 控制一次性沙箱容器。

## 工具

### `python_static_check`

`POST /v1/python/check` 接收单个 Python 源码，检查 Python 3.12 语法、Ruff 的常见错误规则，以及导入模块是否安装在固定沙箱镜像中。此检查不会执行提交的源码。模块检查是静态近似：动态导入不会被发现，`try/except ImportError` 中的可选导入也会按普通导入检查。

请求：

```json
{
  "source": "import pandas as pd\n\ndef total(values):\n    return pd.Series(values).sum()\n"
}
```

### `python_assertion_test`

`POST /v1/python/test` 接收源码和 pytest 测试代码。源码保存为 `solution.py`，测试保存为 `test_solution.py`；测试可使用 `from solution import ...`。至少一个测试必须通过才能返回 `passed`。没有收集到测试时返回 `no_tests`。

请求：

```json
{
  "source": "def add(left, right):\n    return left + right\n",
  "test_code": "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
}
```

两个请求的源码与测试合计不能超过 256 KiB。测试结果的 `stdout` 和 `stderr` 合计最多保留 64 KiB，超出时 `output_truncated` 为 `true`。

## 本地开发

要求 Python 3.12。创建虚拟环境并安装 API、runner 和开发依赖：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements-dev.txt
```

本地启动 API 时还需要先运行一个可访问的沙箱 runner 和 Docker Engine：

```bash
export API_KEY='replace-with-a-long-random-api-key'
export RUNNER_SHARED_TOKEN='replace-with-a-different-long-random-token'
export RUNNER_URL='http://127.0.0.1:9000'
uvicorn app.main:app --reload
```

## Docker Compose 部署

1. 从 `.env.example` 复制为 `.env`，分别设置 `API_KEY` 与 `RUNNER_SHARED_TOKEN`。两者都应是独立的高熵随机值。
2. 设置 `DIFY_DOCKER_NETWORK` 为 Dify API/worker 所在的 Docker 网络名。可用 `docker network ls` 查看。工具服务会加入该网络，服务名为 `python-tools-api`。
3. Linux 默认 Docker socket 是 `/var/run/docker.sock`。Docker Desktop 上需要把 `DOCKER_SOCKET_PATH` 设为 Docker Desktop socket 的绝对路径。执行器需要连接 Docker Engine；建议放在专用主机或 rootless Docker daemon 上。
4. 启动：

```bash
docker compose up --build -d
```

API 端口默认只绑定到宿主机 `127.0.0.1:8000`。Dify 工具调用使用 Docker 网络地址 `http://python-tools-api:8000`。

检查健康状态：

```bash
curl http://127.0.0.1:8000/healthz
```

在 Dify 的 **Integrations → Tools → Swagger API** 中导入 `http://python-tools-api:8000/openapi.json`。若 Dify 控制台容器无法直接访问服务名，也可以在 Dify 容器可访问的网络地址下载规范后粘贴其 JSON 内容；运行时 `servers` 地址仍需能被 Dify worker 访问。工具认证选择 HTTP Bearer，并填入 `.env` 中的 `API_KEY`。

## 请求示例

```bash
curl http://127.0.0.1:8000/v1/python/check \
  -H 'Authorization: Bearer replace-with-a-long-random-api-key' \
  -H 'Content-Type: application/json' \
  -d '{"source":"def answer(): return 42"}'
```

测试接口同样需要 Bearer API Key，并提交 `source` 与 `test_code` 两个字段。

## 沙箱限制

每次调用创建并清理一个容器。容器禁网、以 UID 10001 运行、root 文件系统只读，仅有 16 MiB 临时文件系统；CPU 上限为 1 核、内存/交换内存上限 512 MiB、进程上限 64、调用超时 30 秒、并发执行默认 2 个。sandbox 镜像预装 Python 3.12、numpy、pandas、matplotlib、pytest 和 Ruff，运行时不安装依赖。

执行器需要 Docker Engine 控制权限，因此应只让 API 经由私有 Docker 网络访问 runner，并将此服务部署在专用或 rootless Docker 环境。沙箱降低代码访问宿主资源的能力，但不应把 Docker socket 暴露给 Dify 或公网。

## 验证

```bash
pytest
ruff check app runner shared tests
```

需要运行容器端到端场景时，先启动 Docker Engine、构建 `python-code-sandbox:3.12`，再执行：

```bash
RUN_DOCKER_INTEGRATION=1 pytest tests/test_docker_integration.py
```
