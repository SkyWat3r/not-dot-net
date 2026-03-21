from .db import get_db
from .users import get_authentication_backend, load_auth
from .schemas import UserRead, UserUpdate


class NotDotNetApp:
    def __init__(self, app, db_path: str | None = None):
        self.db = db = get_db(db_path)
        self.auth_backends = auth_backends = get_authentication_backend(db)
        load_auth(
            app=app,
            get_user_db=db.get_user_db,
            get_user_manager=auth_backends.get_user_manager,
            get_jwt_strategy=auth_backends.get_jwt_strategy,
        )

    def register_routes(self, app):
        auth_backends = self.auth_backends
        print("Registering auth and user routes...")
        app.include_router(
            auth_backends.fastapi_users.get_auth_router(auth_backends.api_auth_backend),
            prefix="/auth/jwt",
            tags=["auth"],
        )

        app.include_router(
            auth_backends.fastapi_users.get_users_router(UserRead, UserUpdate),
            prefix="/users",
            tags=["users"],
        )
        
        app.include_router(
            auth_backends.fastapi_users.get_auth_router(auth_backends.cookie_auth_backend),
            prefix="/auth/cookie",
            tags=["auth"],
        )