FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY run.sh run.sh
RUN chmod +x run.sh

RUN useradd -m flclient && chown -R flclient:flclient ${APP_HOME}
USER flclient

CMD ["./run.sh"]
