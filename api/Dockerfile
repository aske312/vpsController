FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 wireguard-tools procps nftables \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
