"""Interactive one-time bootstrap for the first dashboard administrator."""

from __future__ import annotations

from getpass import getpass

from sqlalchemy import select

from .auth import create_user
from .database import make_engine, session_factory
from .models import User
from .settings import load_settings


def main() -> None:
    factory = session_factory(make_engine(load_settings()))
    with factory.begin() as session:
        if session.scalar(select(User.id).limit(1)):
            raise SystemExit("A user already exists. Admin bootstrap is disabled.")
        username = input("Admin username: ")
        password = getpass("Admin password (14+ characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords did not match.")
        user = create_user(session, username, password)
        print(f"Created administrator '{user.username}'.")


if __name__ == "__main__":
    main()
