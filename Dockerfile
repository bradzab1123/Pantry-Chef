FROM python:3.12-slim

# Don't write .pyc files; flush logs straight to `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user. uid 1000 usually matches the first Linux user on
# the host, so the bind-mounted ./data folder stays writable for both.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER app

# The SQLite file lives at /app/data/pantry.db (mounted as a volume).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
