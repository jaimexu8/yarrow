# Yarrow

Yarrow is a multi-component distributed system for document parsing.

## Quickstart (Docker Compose)

1. Copy environment template:
   ```bash
   cp .env.example .env
   ```
2. Start services:

   ```bash
   docker compose up --build
   ```

   > **Note**: Database migrations are automatically applied on container startup via `entrypoint.sh`.

3. Seed database (optional, creates default admin and user accounts):
   ```bash
   docker compose exec backend python -m app.db.seed
   ```

## Common Operations

### Starting & Stopping Services

- **Start all services in background**:
  ```bash
  docker compose up -d
  ```
- **Rebuild and restart after pulling code or changing dependencies**:
  ```bash
  docker compose up --build -d
  ```
- **Stop services**:
  ```bash
  docker compose down
  ```
- **Restart a specific service** (e.g. `backend`, `worker`, or `frontend`):
  ```bash
  docker compose restart backend
  ```

### Logs & Diagnostics

- **View logs for all services**:
  ```bash
  docker compose logs -f
  ```
- **View logs for a specific service**:
  ```bash
  docker compose logs -f backend
  ```

### Database Management

- **Apply pending database migrations**:
  ```bash
  docker compose exec backend alembic upgrade head
  ```
- **Seed database with default accounts**:
  ```bash
  docker compose exec backend python -m app.db.seed
  ```
- **Clean slate with a fresh database** (removes volumes and resets data):
  ```bash
  docker compose down -v && docker compose up --build -d
  docker compose exec backend python -m app.db.seed
  ```

### Running Tests

- **Run backend tests**:
  ```bash
  docker compose exec backend pytest
  ```
- **Run worker unit tests**:
  ```bash
  docker compose exec worker pytest -m "not integration"
  ```
- **Run frontend tests**:
  ```bash
  docker compose exec frontend npm test
  ```

## Development Container (VS Code)

This repository includes a VS Code Devcontainer configuration (`.devcontainer/`) with Python 3.11, Node.js 20, and all required tooling pre-configured:

1. Open the project in VS Code.
2. Select **Reopen in Container** when prompted (or open the Command Palette and run `Dev Containers: Reopen in Container`).
3. Services and dependencies will install and configure automatically via `.devcontainer/post-install.sh`.

## Service URLs

Once services are running, the following endpoints are accessible:

- **Frontend Application**: [http://localhost:3000](http://localhost:3000)
- **Backend API & Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Mailpit Web UI (Email Inbox)**: [http://localhost:8025](http://localhost:8025) (SMTP port `1025`)
- **RustFS S3 Console**: [http://localhost:9001](http://localhost:9001) (S3 API port `9000`)
- **PostgreSQL / ParadeDB**: `localhost:5432`
- **Valkey (Redis)**: `localhost:6379`

## Default Accounts

When seeded, the following default accounts are available:

- Admin: `admin@yarrow.local` / `admin123`
- User: `user@yarrow.local` / `user123`

## Contributing Workflow

- **Branch naming**: `feat/us-<number>-<short-description>` (e.g., `feat/us-1-create-account`)
- **Commit messages**: `feat(auth): implement user registration api (us-1)`
