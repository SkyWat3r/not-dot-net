import pytest
from unittest.mock import MagicMock


def test_derive_sam_cascade():
    from not_dot_net.backend.workflow_service import derive_sam_candidates
    assert derive_sam_candidates("Alice", "Smith")[:3] == ["smith", "smitha", "smithal"]


def test_derive_sam_strips_accents():
    from not_dot_net.backend.workflow_service import derive_sam_candidates
    assert derive_sam_candidates("Éloïse", "Béranger")[0] == "beranger"


def test_render_mail_uses_template():
    from not_dot_net.backend.workflow_service import render_mail
    assert render_mail("{first}.{last}@x.y", "Alice", "Smith") == "alice.smith@x.y"


def test_generate_initial_password_meets_complexity():
    from not_dot_net.backend.workflow_service import generate_initial_password
    pw = generate_initial_password(16)
    assert len(pw) == 16
    assert any(c.islower() for c in pw)
    assert any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw)


@pytest.mark.asyncio
async def test_ad_account_creation_happy_submit(monkeypatch):
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    from sqlalchemy import select

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": ["CN=g1,DC=x,DC=y"],
    }))

    import not_dot_net.backend.workflow_service as ws
    monkeypatch.setattr(ws, "ldap_user_exists_by_sam", lambda *a, **kw: False, raising=False)
    monkeypatch.setattr(ws, "ldap_create_user",
                        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},{new_user.ou_dn}",
                        raising=False)
    monkeypatch.setattr(ws, "ldap_add_to_groups", lambda *a, **kw: {}, raising=False)

    async with session_scope() as session:
        target = User(
            email="t@example.com", full_name="T", hashed_password="x",
            auth_method=AuthMethod.LOCAL, role="", is_active=False,
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        target_id = target.id

    request = MagicMock(target_email="t@example.com", id="req-1")
    form = {
        "first_name": "Alice", "last_name": "Smith",
        "sam_account": "smith", "ou_dn": "OU=Users,DC=x,DC=y",
        "mail": "alice.smith@x.y", "home_directory": "/home/smith",
        "groups": ["CN=g1,DC=x,DC=y"],
    }
    actor = MagicMock(id="actor-1")

    result = await _handle_ad_account_creation(request, form, ("admin", "pw"), actor)
    assert result.sam_account == "smith"
    assert result.uid == 10000
    assert result.group_failures == {}

    async with session_scope() as session:
        u = await session.get(User, target_id)
    assert u.uid_number == 10000
    assert u.ldap_dn == "CN=Alice Smith,OU=Users,DC=x,DC=y"
    assert u.is_active is True


@pytest.mark.asyncio
async def test_ad_account_creation_rejects_existing_sam(monkeypatch):
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    import not_dot_net.backend.workflow_service as ws

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"], "eligible_groups": [],
    }))
    monkeypatch.setattr(ws, "ldap_user_exists_by_sam", lambda *a, **kw: True, raising=False)

    async with session_scope() as session:
        session.add(User(email="t2@example.com", full_name="T2", hashed_password="x",
                         auth_method=AuthMethod.LOCAL, role="", is_active=False))
        await session.commit()

    request = MagicMock(target_email="t2@example.com", id="req-2")
    form = {"first_name": "A", "last_name": "S", "sam_account": "taken",
            "ou_dn": "OU=Users,DC=x,DC=y", "mail": "a@b.c", "home_directory": "/h"}
    with pytest.raises(ValueError, match="already exists"):
        await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())


@pytest.mark.asyncio
async def test_ad_account_creation_group_failures_returned_not_raised(monkeypatch):
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    import not_dot_net.backend.workflow_service as ws

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x"], "eligible_groups": ["CN=g,DC=x"],
    }))
    monkeypatch.setattr(ws, "ldap_user_exists_by_sam", lambda *a, **kw: False, raising=False)
    monkeypatch.setattr(ws, "ldap_create_user",
                        lambda new, *a, **kw: f"CN=x,{new.ou_dn}", raising=False)
    monkeypatch.setattr(ws, "ldap_add_to_groups",
                        lambda *a, **kw: {"CN=g,DC=x": "denied"}, raising=False)

    async with session_scope() as session:
        session.add(User(email="t3@example.com", full_name="T3", hashed_password="x",
                         auth_method=AuthMethod.LOCAL, role="", is_active=False))
        await session.commit()

    request = MagicMock(target_email="t3@example.com", id="req-3")
    form = {"first_name": "A", "last_name": "S", "sam_account": "as",
            "ou_dn": "OU=Users,DC=x", "mail": "a@b.c", "home_directory": "/h",
            "groups": ["CN=g,DC=x"]}
    result = await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())
    assert result.group_failures == {"CN=g,DC=x": "denied"}
