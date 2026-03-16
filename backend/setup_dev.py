#!/usr/bin/env python3
"""Set up the GhostEditor dev environment: run migrations and create a dev user.

Usage:
    cd backend
    PYTHONPATH=. .venv/bin/python setup_dev.py

Reads DATABASE_URL from .env via app.config.settings.
"""

import asyncio
import getpass
import subprocess
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.auth.security import hash_password
from app.config import settings
from app.db.models import Base, User


async def run_migrations():
    """Run alembic migrations."""
    print("\n=== Running database migrations ===")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(__import__("pathlib").Path(__file__).parent),
        env={**__import__("os").environ, "PYTHONPATH": "."},
    )
    if result.returncode != 0:
        print("ERROR: Migrations failed. Is the database running?")
        print(f"  DATABASE_URL: {settings.database_url}")
        sys.exit(1)
    print("Migrations complete.")


async def create_dev_user():
    """Create a dev user account interactively."""
    print("\n=== Create dev user ===")

    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Check for existing users
        result = await db.execute(select(User).limit(5))
        existing = result.scalars().all()
        if existing:
            print(f"Found {len(existing)} existing user(s):")
            for u in existing:
                print(f"  - {u.email} (verified={u.email_verified}, provisional={u.is_provisional})")
            answer = input("\nCreate another user? [y/N] ").strip().lower()
            if answer != "y":
                print("Skipping user creation.")
                await engine.dispose()
                return

        email = input("Email [dev@ghosteditor.com]: ").strip() or "dev@ghosteditor.com"

        # Check if email already exists
        result = await db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            print(f"User {email} already exists. Skipping.")
            await engine.dispose()
            return

        password = getpass.getpass("Password [ghosteditor]: ") or "ghosteditor"
        password_confirm = getpass.getpass("Confirm password [ghosteditor]: ") or "ghosteditor"
        if password != password_confirm:
            print("ERROR: Passwords don't match.")
            sys.exit(1)

        hashed = hash_password(password)
        user = User(
            email=email,
            password_hash=hashed,
            email_verified=True,
            is_provisional=False,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"\nDev user created:")
        print(f"  ID:    {user.id}")
        print(f"  Email: {user.email}")
        print(f"  Verified: True, Provisional: False")

    await engine.dispose()


async def main():
    print("GhostEditor Dev Setup")
    print(f"Database: {settings.database_url}")

    await run_migrations()
    await create_dev_user()

    print("\n=== Setup complete ===")
    print("Start the app with:")
    print("  uvicorn app.main:app --reload        # API server")
    print("  PYTHONPATH=. arq app.jobs.worker.WorkerSettings  # Worker")


if __name__ == "__main__":
    asyncio.run(main())
