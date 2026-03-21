import pytest
from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2

from not_dot_net.backend.auth.ldap import ldap_authenticate
from not_dot_net.config import LDAPSettings

LDAP_CFG = LDAPSettings(url="fake", domain="example.com", base_dn="dc=example,dc=com")

FAKE_USERS = {
    "jdoe": {"mail": "jdoe@example.com", "password": "secret"},
    "nomail": {"mail": None, "password": "secret"},
}


def fake_ldap_connect(ldap_cfg: LDAPSettings, username: str, password: str) -> Connection:
    """Build a MOCK_SYNC connection pre-populated with fake AD entries."""
    server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
    conn = Connection(server, user=f"{username}@{ldap_cfg.domain}", password=password, client_strategy=MOCK_SYNC)

    for uid, attrs in FAKE_USERS.items():
        entry_attrs = {
            "sAMAccountName": uid,
            "userPassword": attrs["password"],
            "objectClass": "person",
        }
        if attrs["mail"]:
            entry_attrs["mail"] = attrs["mail"]
        conn.strategy.add_entry(f"cn={uid},ou=users,{ldap_cfg.base_dn}", entry_attrs)

    conn.bind()

    if FAKE_USERS.get(username, {}).get("password") != password:
        from ldap3.core.exceptions import LDAPBindError
        raise LDAPBindError("Invalid credentials")

    return conn


def test_successful_authentication():
    email = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert email == "jdoe@example.com"


def test_wrong_password_returns_none():
    email = ldap_authenticate("jdoe", "wrong", LDAP_CFG, connect=fake_ldap_connect)
    assert email is None


def test_unknown_user_returns_none():
    email = ldap_authenticate("nobody", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert email is None


def test_user_without_mail_returns_none():
    email = ldap_authenticate("nomail", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert email is None
