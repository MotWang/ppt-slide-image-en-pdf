# Build from workflows/ppt-slide-image-en-pdf (parent of web/)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

COPY scripts /app/scripts
COPY web /app/web

ENV HOST=0.0.0.0
ENV PORT=8787
ENV DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /data/runs

EXPOSE 8787
CMD ["python3", "/app/web/server.py"]
