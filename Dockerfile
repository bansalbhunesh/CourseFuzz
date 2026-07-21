FROM node:24-alpine AS web-build

WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COURSEFUZZ_WEB_DIST=/app/web/dist \
    COURSEFUZZ_DB_PATH=/app/data/coursefuzz.db \
    COURSEFUZZ_ARTIFACT_DIR=/app/data/artifacts

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 coursefuzz \
    && mkdir -p /app/data \
    && chown -R coursefuzz:coursefuzz /app/data
COPY --from=web-build /app/web/dist ./web/dist

USER coursefuzz
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "coursefuzz.main:app", "--host", "0.0.0.0", "--port", "8000"]
