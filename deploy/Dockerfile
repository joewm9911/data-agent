# data-agent API/worker 镜像（四形态同一套镜像，差异收敛在配置层，10.2）
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY apps/ apps/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# api 与 worker 同镜像，经命令区分
CMD ["uvicorn", "da_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
