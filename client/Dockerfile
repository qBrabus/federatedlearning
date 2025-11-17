FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime

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

RUN useradd -m flclient && chown -R flclient:flclient ${APP_HOME}
USER flclient

ENV SERVER_ADDRESS=fl-superlink.institutimagine.org:443 \
    CLIENT_ID=dgx-client \
    N_LOCAL_EPOCHS=1 \
    BATCH_SIZE=64 \
    LEARNING_RATE=0.01

CMD ["python", "-m", "app.client"]
