FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY run.sh run.sh

# Permissions pour le script
RUN chmod +x run.sh

RUN useradd -m flower && chown -R flower:flower ${APP_HOME}
USER flower

ENV FLOWER_SERVER_PORT=8080 \
    FLOWER_SERVERAPP_PORT=9091

EXPOSE 8080 9091

CMD ["./run.sh"]
