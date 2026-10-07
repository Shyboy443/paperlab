FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIND_HOST=0.0.0.0 \
    DATA_DIR=/data

WORKDIR /srv/paperlab

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data

EXPOSE 8080
# Railway injects $PORT; /api/health is the healthcheck (see railway.json)
# SSE streams never end on their own: cap how long a redeploy waits for them before shutdown.
CMD ["sh", "-c", "uvicorn app.main:app --host ${BIND_HOST} --port ${PORT:-8080} --timeout-graceful-shutdown 5"]
