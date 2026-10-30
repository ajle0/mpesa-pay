# Multi-arch base image: runs the same on Apple Silicon (arm64) and Intel/AMD (x86_64).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY . .

# Static files are collected at build time so the container starts fast.
RUN SECRET_KEY=build-only DEBUG=0 MPESA_MODE=simulate ALLOW_SIMULATE=1 \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# Run via `sh` so the script works even if a Windows checkout lost the executable bit.
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
