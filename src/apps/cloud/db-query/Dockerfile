FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd -r -m -u 1001 query
COPY apps/cloud/db-query ./apps/cloud/db-query
RUN pip install --no-cache-dir ./apps/cloud/db-query
USER query
ENTRYPOINT ["python", "-m", "edge_db_query"]
