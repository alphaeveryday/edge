FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home app
ENV PATH="/app/.venv/bin:$PATH"
USER app
CMD ["python", "-m", "edge_portfolio.worker"]
