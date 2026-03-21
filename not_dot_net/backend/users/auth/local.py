from pydantic import BaseModel
from fastapi import HTTPException, Depends, status
from passlib.context import CryptContext
from nicegui import ui

from not_dot_net.backend.db import SQLAlchemyUserDatabase
from not_dot_net.backend.schemas import UserCreate
from .register import register_backend_loader


# simple request models
class LocalAuthRequest(BaseModel):
    email: str
    password: str


class LocalRegisterRequest(BaseModel):
    email: str
    password: str


# passlib context - fastapi-users uses bcrypt by default
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@register_backend_loader
def load(get_user_db, get_user_manager, get_jwt_strategy):
    @ui.page("/auth/local")
    async def local_login(
            credentials: LocalAuthRequest,
            user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
            user_manager=Depends(get_user_manager),
    ):
        # 1) fetch user by email
        user = await user_db.get_by_email(credentials.email)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        # 2) obtain hashed password attribute commonly used by fastapi-users
        hashed = getattr(user, "hashed_password", None) or getattr(user, "password", None)
        if not hashed:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User has no password hash")

        # 3) verify password
        try:
            valid = pwd_context.verify(credentials.password, hashed)
        except Exception:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Password verification failed")

        if not valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        # 4) issue JWT token
        token = get_jwt_strategy().write_token(user)  # type: ignore[arg-type]
        return {"access_token": token}

    @ui.page("/auth/register")
    async def local_register(
            credentials: LocalRegisterRequest,
            user_manager=Depends(get_user_manager),
    ):
        # create a local user via the user manager (fastapi-users will hash the password)
        user_create = UserCreate(email=credentials.email, password=credentials.password)
        try:
            user = await user_manager.create(user_create)
        except Exception as e:
            # basic error mapping; you can refine for duplicate email, etc.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        token = get_jwt_strategy().write_token(user)  # type: ignore[arg-type]
        return {"access_token": token}
