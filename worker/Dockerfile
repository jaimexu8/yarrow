FROM python:3.11-slim

# Build context is the repository root so that the shared ../packages/yarrow-db
# path dependency resolves. The repo layout is preserved inside the image.
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install poetry==1.8.2

# Copy the shared package first, then the service's dependency manifests
COPY packages/ /app/packages/
COPY worker/pyproject.toml worker/poetry.lock* /app/worker/

WORKDIR /app/worker

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Copy application code
COPY worker/ /app/worker/

CMD ["celery", "-A", "app.celery_app", "worker", "--loglevel=info"]
