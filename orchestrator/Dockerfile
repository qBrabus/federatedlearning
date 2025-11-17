FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

RUN apt-get update \ 
    && apt-get install -y --no-install-recommends \ 
        ca-certificates \ 
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

RUN useradd -m flower && chown -R flower:flower ${APP_HOME}
USER flower

ENV GRPC_MAX_MESSAGE_LENGTH=536870912 \
    FLOWER_SERVER_ADDRESS=0.0.0.0 \
    FLOWER_SERVER_PORT=8080

EXPOSE 8080

CMD ["python", "-m", "app.server"]
