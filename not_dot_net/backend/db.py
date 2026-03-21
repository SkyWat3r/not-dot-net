from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Awaitable, Callable
import enum
from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class AuthMethod(str, enum.Enum):
    LOCAL = "local"
    LDAP = "ldap"


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    auth_method: AuthMethod = AuthMethod.LOCAL


@dataclass
class DB:
    async_session_maker: async_sessionmaker[AsyncSession]
    create_db_and_tables: Callable[[], Awaitable]
    get_async_session: Callable[[], AsyncGenerator[AsyncSession, None]]
    get_user_db: Callable[[AsyncSession], AsyncGenerator[SQLAlchemyUserDatabase, None]]


def get_db(path: str = "") -> DB:
    engine = create_async_engine(path)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def create_db_and_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_maker() as session:
            yield session

    async def get_user_db(session: AsyncSession = Depends(get_async_session)):
        yield SQLAlchemyUserDatabase(session, User)

    return DB(
        async_session_maker=async_session_maker,
        create_db_and_tables=create_db_and_tables,
        get_async_session=get_async_session,
        get_user_db=get_user_db,
    )
