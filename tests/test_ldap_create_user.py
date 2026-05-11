import pytest
from ldap3 import MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE


class _Result(dict):
    def __init__(self):
        super().__init__({"description": "success", "message": ""})


class _FakeEntry:
    def __init__(self, attrs):
        from types import SimpleNamespace
        for k, v in attrs.items():
            setattr(self, k, SimpleNamespace(value=v))
        self.entry_dn = attrs.get("_dn", "CN=fake,DC=x")


class _FakeConn:
    def __init__(self, search_returns_entries=None, add_ok=True, modify_ok=True):
        self.search_returns = list(search_returns_entries or [])
        self.entries = []
        self.calls = []  # list of (op_name, args)
        self.add_ok = add_ok
        self.modify_ok = modify_ok
        self.result = _Result()
        self.bound = True

    def search(self, *args, **kwargs):
        self.calls.append(("search", (args, kwargs)))
        self.entries = self.search_returns
        return bool(self.search_returns)

    def add(self, dn, object_class, attributes):
        self.calls.append(("add", (dn, object_class, attributes)))
        self.result = _Result()
        if not self.add_ok:
            self.result["description"] = "alreadyExists"
            self.result["message"] = "exists"
        return self.add_ok

    def modify(self, dn, changes):
        self.calls.append(("modify", (dn, changes)))
        self.result = _Result()
        if not self.modify_ok:
            self.result["description"] = "constraintViolation"
            self.result["message"] = "nope"
        return self.modify_ok

    def unbind(self):
        self.bound = False


def _fake_connect_returning(conn):
    def _connect(cfg, username, password):
        return conn
    return _connect


def test_ldap_user_exists_by_sam_true():
    from not_dot_net.backend.auth.ldap import ldap_user_exists_by_sam, LdapConfig
    conn = _FakeConn(search_returns_entries=[_FakeEntry({"sAMAccountName": "alice"})])
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    assert ldap_user_exists_by_sam("alice", "admin", "pw", cfg, _fake_connect_returning(conn)) is True


def test_ldap_user_exists_by_sam_false():
    from not_dot_net.backend.auth.ldap import ldap_user_exists_by_sam, LdapConfig
    conn = _FakeConn(search_returns_entries=[])
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    assert ldap_user_exists_by_sam("nope", "admin", "pw", cfg, _fake_connect_returning(conn)) is False
