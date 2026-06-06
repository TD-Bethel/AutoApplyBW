# AutoApply BW — production image for Fly.io
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code.
COPY . .

# waitress listens here; matches internal_port in fly.toml.
EXPOSE 8080

# Production WSGI server (waitress). HOST/PORT/DATABASE_PATH come from env.
CMD ["python", "run.py", "--serve"]
