import asyncio

from yarrow_db.models import User
from yarrow_db.session import get_async_session_maker

from app.core.security import get_password_hash


async def seed():
    async_session = get_async_session_maker()

    async with async_session() as session:
        # Create Admin
        admin = User(
            email="admin@yarrow.local",
            hashed_password=get_password_hash("admin123"),
            is_admin=True,
            is_active=True,
            is_verified=True,
        )
        session.add(admin)

        # Create User
        user = User(
            email="user@yarrow.local",
            hashed_password=get_password_hash("user123"),
            is_admin=False,
            is_active=True,
            is_verified=True,
        )
        session.add(user)

        await session.commit()
        print("Database seeded with default users.")


if __name__ == "__main__":
    asyncio.run(seed())
