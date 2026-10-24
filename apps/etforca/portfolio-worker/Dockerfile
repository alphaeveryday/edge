FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home app
# The release stamps its commit so the running service reports which version answered.
ARG PORTFOLIO_VERSION=""
RUN if [ -n "$PORTFOLIO_VERSION" ]; then printf '{"version": "%s"}' "$PORTFOLIO_VERSION" > src/edge_portfolio/data/deployment.json; fi
ENV PATH="/app/.venv/bin:$PATH"
USER app
CMD ["python", "-m", "edge_portfolio.worker"]
