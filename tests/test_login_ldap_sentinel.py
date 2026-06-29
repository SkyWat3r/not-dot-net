"""Reproducer: local-first login must not crash on an LDAP user's sentinel hash.

LDAP-provisioned users store hashed_password="!ldap-no-local-password" (they
have no local password). handle_login tries local auth FIRST; newer pwdlib
raises UnknownHashError on a hash it can't identify instead of returning
"not verified". That exception escaped handle_login, 500-ing the request and
never reaching the LDAP fallback — so AD users could not log in at all.
"""
from contextlib import asynccontextmanager

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.frontend.login import handle_login

from tests.test_auth_endpoints import _FakeRequest


async def test_login_with_ldap_sentinel_hash_falls_through_not_crashes():
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                u = await manager.create(
                    UserCreate(email="ldapuser@test.com", password="placeholder123")
                )
        u.hashed_password = "!ldap-no-local-password"
        session.add(u)
        await session.commit()

    # No LDAP configured in tests → fallback fails gracefully (no 500).
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                response = await handle_login(
                    _FakeRequest({"username": "ldapuser@test.com", "password": "anypw"}),
                    user_manager=manager,
                )

    assert response.status_code == 303
    assert "/login?error=1" in response.headers["location"]
