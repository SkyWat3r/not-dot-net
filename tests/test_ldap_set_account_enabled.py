"""Tests for ldap_set_account_enabled — the AD enable/disable write helper."""

import pytest
from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2
from ldap3.core.exceptions import LDAPBindError

from not_dot_net.backend.auth.ldap import (
    LdapConfig, LdapModifyError, ldap_set_account_enabled,
)


LDAP_CFG = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com")
USER_DN = "cn=jdoe,ou=users,dc=example,dc=com"


def _make_mutable_fake(initial_uac: int):
    """Shared-state fake connect with userAccountControl reads + writes."""
    state = {"userAccountControl": initial_uac}

    def fake_connect(ldap_cfg, username, password):
        server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
        conn = Connection(server, user=f"{username}@{ldap_cfg.domain}",
                          password=password, client_strategy=MOCK_SYNC)
        conn.strategy.add_entry(USER_DN, {
            "sAMAccountName": "jdoe", "userPassword": "secret",
            "objectClass": "person", "mail": "jdoe@example.com",
            "userAccountControl": state["userAccountControl"],
        })
        conn.bind()
        if password != "secret":
            raise LDAPBindError("Invalid credentials")
        orig_modify = conn.modify
        def tracked_modify(dn, changes, *a, **kw):
            result = orig_modify(dn, changes, *a, **kw)
            for attr, ops in changes.items():
                _op, values = ops[0]
                if attr == "userAccountControl":
                    state["userAccountControl"] = int(values[0]) if values else None
            return result
        conn.modify = tracked_modify
        return conn

    return fake_connect, state


def test_disable_sets_accountdisable_bit_preserving_other_flags():
    # NORMAL_ACCOUNT (0x200) + DONT_EXPIRE_PASSWORD (0x10000) = 0x10200
    fake_connect, state = _make_mutable_fake(0x10200)
    ldap_set_account_enabled(
        dn=USER_DN, enabled=False,
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    # ACCOUNTDISABLE bit (0x2) added; other bits preserved
    assert state["userAccountControl"] == 0x10202


def test_enable_clears_accountdisable_bit_preserving_other_flags():
    fake_connect, state = _make_mutable_fake(0x10202)
    ldap_set_account_enabled(
        dn=USER_DN, enabled=True,
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state["userAccountControl"] == 0x10200


def test_disable_idempotent_when_already_disabled():
    fake_connect, state = _make_mutable_fake(0x202)
    ldap_set_account_enabled(
        dn=USER_DN, enabled=False,
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state["userAccountControl"] == 0x202


def test_enable_idempotent_when_already_enabled():
    fake_connect, state = _make_mutable_fake(0x200)
    ldap_set_account_enabled(
        dn=USER_DN, enabled=True,
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state["userAccountControl"] == 0x200


def test_bind_failure_raises():
    fake_connect, _ = _make_mutable_fake(0x200)
    with pytest.raises(LdapModifyError) as exc:
        ldap_set_account_enabled(
            dn=USER_DN, enabled=False,
            bind_username="jdoe", bind_password="wrong",
            ldap_cfg=LDAP_CFG, connect=fake_connect,
        )
    assert "bind" in str(exc.value).lower()
