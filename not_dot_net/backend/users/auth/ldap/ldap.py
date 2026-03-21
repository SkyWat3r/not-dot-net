# python
import os
from pydantic import BaseModel
from fastapi import HTTPException, Depends, status
from ldap3 import Server, Connection, ALL
from ldap3.core.exceptions import LDAPBindError
from not_dot_net.backend.db import SQLAlchemyUserDatabase
from fastapi_users import BaseUserManager, models
from nicegui import ui
from ..register import register_backend_loader


class LDAPAuthRequest(BaseModel):
    username: str
    password: str


# inside get_backend(db) after you build get_user_manager and get_jwt_strategy
ldap_server_url = os.environ.get("LDAP_SERVER", "ldap://localhost")
ldap_base_dn = os.environ.get("LDAP_BASE_DN", "dc=example,dc=com")


@register_backend_loader
def load(get_user_db, get_user_manager, get_jwt_strategy):
    @ui.page("/auth/ldap")
    async def ldap_login(
            credentials: LDAPAuthRequest,
            user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
            user_manager: BaseUserManager[models.UP, models.ID] = Depends(get_user_manager),
    ):
        # 1) verify against LDAP
        server = Server(ldap_server_url, get_info=ALL)
        try:
            # bind using the user DN pattern - adjust to your directory (uid, cn, userPrincipalName, ...)
            user_dn = f"uid={credentials.username},{ldap_base_dn}"
            conn = Connection(server, user=user_dn, password=credentials.password, auto_bind=True)
        except LDAPBindError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid LDAP credentials")
        except Exception:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="LDAP error")

        # 2) read useful attributes (mail)
        try:
            conn.search(ldap_base_dn, f"(uid={credentials.username})", attributes=["mail"])
            if not conn.entries:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="LDAP user not found")
            entry = conn.entries[0]
            email = getattr(entry, "mail", None)
            email_value = email.value if email is not None else None
        finally:
            conn.unbind()

        if not email_value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LDAP did not return email")

        # 3) map to local user record
        user = await user_db.get_by_email(email_value)
        if not user:
            # Option A: deny access unless local user exists
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No local user mapped to this LDAP account. Implement auto-creation if desired.",
            )

            # Option B (optional): automatically create the local user.
            # Uncomment and implement creation with the correct create model for your fastapi-users version:
            # from fastapi_users import models as fu_models
            # user_create = fu_models.UC(email=email_value, is_active=True)  # adjust to your models
            # user = await user_manager.create(user_create)

        # 4) issue JWT token using existing strategy
        token = get_jwt_strategy().write_token(user)  # type: ignore[arg-type]

        return {"access_token": token}
