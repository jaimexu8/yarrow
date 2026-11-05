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

## Manual Setup

### Backend (FastAPI)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

## Database Tasks

Apply migrations:

```bash
cd backend
alembic upgrade head
```

Seed database:

```bash
cd backend
python -m app.db.seed
```

Alternatively, in Docker: `docker compose exec backend python -m app.db.seed`

## Default Accounts

- Admin: `admin@yarrow.local` / `admin123`
- User: `user@yarrow.local` / `user123`

## API Documentation

Once running, accessible at [http://localhost:8000/docs](http://localhost:8000/docs)

## Contributing Workflow

- **Branch naming**: `feat/US-<number>-<short-description>` (e.g., `feat/US-1-create-account`)
- **Commit messages**: `feat(auth): implement user registration API (US-1)`
