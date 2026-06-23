FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /src

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/src/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --no-dev --no-install-project

COPY src ./src

RUN uv sync --no-dev

CMD ["uv", "run", "--no-sync", "summary-messages"]
