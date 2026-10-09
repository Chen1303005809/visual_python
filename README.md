# Dify Python 代码验证工具

这是一个给 Dify Agent 调用的独立 FastAPI 服务，提供 Python 静态检查和 pytest 沙箱测试。API 通过宿主机端口直接暴露，不要求 Dify 与服务加入同一个 Docker 网络，也不要求外部 API Key。

## 工具

### `python_static_check`

`POST /v1/python/check` 接收单个 Python 源码，检查 Python 3.12 语法、Ruff 常见错误规则，以及导入模块是否安装在固定沙箱镜像中。静态检查不会执行提交的源码。模块检查是静态近似：动态导入不会被发现，`try/except ImportError` 中的可选导入也会按普通导入检查。

### `python_assertion_test`

`POST /v1/python/test` 接收源码和 pytest 测试代码。源码保存为 `solution.py`，测试保存为 `test_solution.py`；测试可使用 `from solution import ...`。至少一个测试必须通过才会返回 `passed`。没有收集到测试时返回 `no_tests`。

两个请求的源码与测试合计不能超过 256 KiB。测试结果的 `stdout` 和 `stderr` 合计最多保留 64 KiB，超出时 `output_truncated` 为 `true`。

## Docker Compose 部署

1. 从 `.env.example` 复制为 `.env`，设置 `RUNNER_SHARED_TOKEN`，并把 `API_PUBLIC_URL` 改成 Dify Worker 可以访问的地址，例如 `http://192.168.1.20:8000`。
2. Linux 默认 Docker socket 是 `/var/run/docker.sock`。Docker Desktop 上把 `DOCKER_SOCKET_PATH` 设为 Docker Desktop socket 的绝对路径。
3. 启动：

```bash
docker compose up --build -d
```

API 默认发布到宿主机所有 IPv4 网卡的 `8000` 端口，即 `0.0.0.0:8000`。可用 `API_PORT` 修改端口。Dify 不需要加入本项目的 Docker 网络，但它所在的主机或容器必须能访问 `API_PUBLIC_URL` 指向的地址；将该地址设置为 Dify Worker 可路由到的宿主机 IP 或域名。不要在 Dify 容器内把 `localhost` 当成工具服务所在的主机。

在 Dify 的 Swagger/OpenAPI 工具中导入：

```text
http://<API_PUBLIC_URL对应的主机>:<API_PORT>/openapi.json
```

服务返回的 `/openapi.json` 会使用 `API_PUBLIC_URL` 作为 OpenAPI `servers` 地址。合并版规范包含两个工具；单工具规范在 `openapi-check.json` 和 `openapi-test.json`，若单独导入静态文件，请将其中的 `servers[0].url` 改成 Dify 可访问的地址。

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

## 请求示例

外部 API 不要求 API Key 或 Bearer Token：

```bash
curl http://127.0.0.1:8000/v1/python/check \
  -H 'Content-Type: application/json' \
  -d '{"source":"def answer(): return 42"}'
```

测试接口提交 `source` 与 `test_code`：

```bash
curl http://127.0.0.1:8000/v1/python/test \
  -H 'Content-Type: application/json' \
  -d '{"source":"def add(a, b): return a + b","test_code":"from solution import add\n\ndef test_add(): assert add(2, 3) == 5"}'
```

## Token 说明

`RUNNER_SHARED_TOKEN` 是 API 与内部 `sandbox-runner` 之间的共享密钥。API 调用 Runner 时放在 `X-Runner-Token` 请求头中；它不是 Dify 调用 API 的 Key，不需要配置到 Dify 工具。Runner 不发布宿主机端口，只连接内部 Docker 网络。

API 容器和 Runner 之间通过 `runner-private` 网络通信；API 另接 `api-ingress` 网络以发布宿主机端口。只有 Runner 挂载 Docker socket，API 容器没有 Docker socket。

API 没有外部认证，任何能访问发布端口的客户端都可以请求执行代码。此配置适用于调研或可信网络环境；沙箱限制了代码的网络、CPU、内存、进程数、运行时间和输出，但并不替代访问控制。

## 沙箱限制

每次调用创建并清理一个容器。容器禁网、以 UID 10001 运行、根文件系统只读，仅有 16 MiB 临时文件系统；CPU 上限为 1 核、内存/交换内存上限 512 MiB、进程上限 64、调用超时 30 秒、并发执行默认 2 个。沙箱镜像预装 Python 3.12、numpy、pandas、matplotlib、pytest 和 Ruff，运行时不安装依赖。

沙箱镜像还包含 `价格接口/external_head.py`、`external_baseid.py` 和模拟 `PyEngine`，可用于解析策略接口并验证策略逻辑。模拟引擎只记录 `sendMsg` 消息，不连接真实交易客户端；测试通过不代表真实宿主集成通过。

Runner 使用 Docker Engine 创建沙箱容器，因此仍需妥善保护 Runner 和 Docker socket。不要把 Docker socket 挂载给 Dify 或 API 容器。

## 本地开发与验证

要求 Python 3.12。创建虚拟环境并安装 API、Runner 和开发依赖：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements-dev.txt
```

配置 `RUNNER_SHARED_TOKEN`、`API_PUBLIC_URL` 和 `RUNNER_URL` 后分别启动 Runner 与 API。常规检查：

```bash
pytest
ruff check app runner shared tests
```

需要运行 Docker 端到端场景时，先启动 Docker Engine 并构建沙箱镜像，再运行：

```bash
RUN_DOCKER_INTEGRATION=1 pytest tests/test_docker_integration.py
```
