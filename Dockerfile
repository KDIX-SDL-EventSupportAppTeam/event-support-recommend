# Cloud Run 向け。ステートレス・書き込みなし (docs/specs/11-deployment.md D-5)。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Cloud Run は $PORT を渡す。既定 8080。
ENV PORT=8080

# --workers 1: 並行は Cloud Run の --concurrency で制御する。ワーカー多重化は
#   メモリを食うだけ (D-5)。
# アクセスログは残す（レイテンシ調査に要る・V-12/V-13）。
CMD ["sh", "-c", "uvicorn event_support_recommend.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --access-log"]
