import uuid
from dataclasses import dataclass
from typing import Any
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from not_dot_net.backend.db import User, DB

SECRET = "SECRET"


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Request | None = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def ensure_default_admin(user_manager: UserManager) -> None:
    user = await user_manager.get_by_email("admin@localhost")
    if not user:
        from not_dot_net.backend.schemas import UserCreate

        admin_create = UserCreate(
            email="admin@localhost",
            password="admin",
            is_active=True,
            is_superuser=True,
        )
        await user_manager.create(admin_create)


@dataclass
class NotDotNetAuthBackend:
    api_auth_backend: Any
    cookie_auth_backend: Any
    current_active_user: Any
    current_active_user_optional: Any
    fastapi_users: Any
    get_jwt_strategy: Any
    get_user_manager: Any
    ensure_default_admin: Any = ensure_default_admin


def get_authentication_backend(db: DB) -> NotDotNetAuthBackend:
    async def get_user_manager(
        user_db: SQLAlchemyUserDatabase = Depends(db.get_user_db),
    ):
        yield UserManager(user_db)

    bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
    cookie_transport = CookieTransport(
        cookie_name="fastapiusersauth", 
        cookie_max_age=3600,
        cookie_httponly=False  # Crucial: allows JS to "see" and set the cookie
    )

    def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
        return JWTStrategy(secret=SECRET, lifetime_seconds=3600)

    api_auth_backend = AuthenticationBackend(
        name="jwt",
        transport=bearer_transport,
        get_strategy=get_jwt_strategy,
    )

    cookie_auth_backend = AuthenticationBackend(
        name="cookie",
        transport=cookie_transport,
        get_strategy=get_jwt_strategy,
    )

    fastapi_users = FastAPIUsers[User, uuid.UUID](
        get_user_manager, [api_auth_backend, cookie_auth_backend]
    )

    current_active_user = fastapi_users.current_user(active=True)
    current_active_user_optional = fastapi_users.current_user(active=True, optional=True)

    return NotDotNetAuthBackend(
        api_auth_backend=api_auth_backend,
        cookie_auth_backend=cookie_auth_backend,
        fastapi_users=fastapi_users,
        get_jwt_strategy=get_jwt_strategy,
        get_user_manager=get_user_manager,
        current_active_user=current_active_user,
        current_active_user_optional=current_active_user_optional
    )
