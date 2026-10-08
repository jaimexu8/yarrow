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
COPY backend/pyproject.toml backend/poetry.lock* /app/backend/

WORKDIR /app/backend

ENV PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Copy application code
COPY backend/ /app/backend/

# Applies pending Alembic migrations, then runs the container command.
# Compose passes `uvicorn ...` as the command; it becomes the entrypoint's args.
RUN chmod +x /app/backend/entrypoint.sh
ENTRYPOINT ["/app/backend/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
