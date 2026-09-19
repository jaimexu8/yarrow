# Yarrow DB

This is the shared db model definition for yarrow. Any party that requires accessing the db would reference the definition here.

## Directory Structure

```text
.
└── src
    └── yarrow_db
        ├── config.py                       # Database configurations
        ├── models/                         # Database model definitions
        └── session.py                      # Database session helpers         
```

## Example Usage

```python
from app.core.security import get_password_hash
from yarrow_db.models import User
from yarrow_db.session import get_async_session_maker

async def seed():
    async_session = get_async_session_maker()

    async with async_session() as session:
        # Create Admin
        admin = User(
            email="admin@yarrow.local",
            hashed_password=get_password_hash("admin123"),
            is_admin=True,
            is_active=True,
            is_verified=True
        )
        session.add(admin)

        # Create User
        user = User(
            email="user@yarrow.local",
            hashed_password=get_password_hash("user123"),
            is_admin=False,
            is_active=True,
            is_verified=True
        )
        session.add(user)

        await session.commit()
        print("Database seeded with default users.")
```