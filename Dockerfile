FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Install entrypoint script (needs root for /usr/local/bin).
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create a non-root user and ensure writable directories.
RUN useradd -m app && \
    mkdir -p /app/data /app/backups && \
    chown -R app:app /app
USER app

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
