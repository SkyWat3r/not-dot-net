from unittest.mock import MagicMock


async def test_step_effect_config_round_trip():
    from not_dot_net.backend.workflow_effects import StepEffectConfig
    cfg = StepEffectConfig(
        on_action="approve",
        kind="ad_add_to_groups",
        params={"groups": ["CN=vpn,DC=x"]},
    )
    assert cfg.model_dump()["kind"] == "ad_add_to_groups"


async def test_registry_has_four_handlers():
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    assert set(EFFECT_REGISTRY) == {
        "ad_add_to_groups",
        "ad_remove_from_groups",
        "ad_enable_account",
        "ad_disable_account",
    }


async def test_add_to_groups_validates_against_eligible():
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    import pytest
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=ok,DC=x"]}))

    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    handler.validate_params({"groups": ["CN=ok,DC=x"]})
    # validate_params should NOT raise for in-list groups in the type-check pass.
    # The eligibility re-check is done at run-time. The type-only validate_params is OK to pass {"groups": [strings]}.

    # But for non-string params, validate_params must raise:
    import pytest
    with pytest.raises(ValueError):
        handler.validate_params({"groups": [123]})


async def test_add_to_groups_runs_against_target(monkeypatch):
    import not_dot_net.backend.workflow_effects as effects_mod
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY, EffectResult
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=ok,DC=x"]}))

    captured = {}

    def fake_add(user_dn, group_dns, bu, bp, lc):
        captured["user_dn"] = user_dn
        captured["group_dns"] = group_dns
        return {}

    monkeypatch.setattr(effects_mod, "_ldap_add_to_groups", fake_add)

    from not_dot_net.backend.db import session_scope, User, AuthMethod
    async with session_scope() as session:
        u = User(
            email="target@example.com", full_name="Target", hashed_password="x",
            auth_method=AuthMethod.LDAP, role="", is_active=True,
            ldap_dn="CN=target,DC=x",
        )
        session.add(u)
        await session.commit()

    request = MagicMock(target_email="target@example.com")
    step = MagicMock()
    actor = MagicMock()
    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    result = await handler.run(
        request, step, action="approve",
        params={"groups": ["CN=ok,DC=x"]},
        ad_creds=("admin", "pw"),
        actor=actor,
    )
    assert isinstance(result, EffectResult)
    assert result.succeeded
    assert captured["user_dn"] == "CN=target,DC=x"
    assert captured["group_dns"] == ["CN=ok,DC=x"]


async def test_add_to_groups_partial_failure_returned(monkeypatch):
    import not_dot_net.backend.workflow_effects as effects_mod
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=g1,DC=x", "CN=g2,DC=x"]}))

    def fake_add(user_dn, group_dns, bu, bp, lc):
        return {"CN=g2,DC=x": "no rights"}
    monkeypatch.setattr(effects_mod, "_ldap_add_to_groups", fake_add)

    from not_dot_net.backend.db import session_scope, User, AuthMethod
    async with session_scope() as session:
        u = User(email="t2@example.com", full_name="T", hashed_password="x",
                 auth_method=AuthMethod.LDAP, role="", is_active=True, ldap_dn="CN=t,DC=x")
        session.add(u)
        await session.commit()

    request = MagicMock(target_email="t2@example.com")
    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    result = await handler.run(request, MagicMock(), action="approve",
                                params={"groups": ["CN=g1,DC=x", "CN=g2,DC=x"]},
                                ad_creds=("a", "p"), actor=MagicMock())
    assert not result.succeeded
    assert result.failures == {"CN=g2,DC=x": "no rights"}


import pytest


@pytest.mark.asyncio
async def test_run_effects_skips_non_matching_actions(monkeypatch):
    from not_dot_net.backend.workflow_effects import run_effects, EFFECT_REGISTRY, EffectResult
    from not_dot_net.config import StepEffectConfig

    calls = []

    # Patch each handler instance in the registry so we don't hit LDAP/DB.
    for kind, handler in EFFECT_REGISTRY.items():
        async def fake_run(request, step, action, params, ad_creds, actor, _kind=kind):
            calls.append((_kind, action))
            return EffectResult(kind=_kind, succeeded=True)
        monkeypatch.setattr(handler, "run", fake_run)

    step = MagicMock(effects=[
        StepEffectConfig(on_action="approve", kind="ad_enable_account", params={}),
        StepEffectConfig(on_action="reject", kind="ad_disable_account", params={}),
    ])
    results = await run_effects(
        request=MagicMock(), step=step, action="approve",
        ad_creds=("a", "p"), actor=MagicMock(),
    )
    assert len(results) == 1
    assert results[0].kind == "ad_enable_account"


@pytest.mark.asyncio
async def test_run_effects_raises_when_creds_missing():
    from not_dot_net.backend.workflow_effects import (
        run_effects, AdCredentialsRequired,
    )
    from not_dot_net.config import StepEffectConfig
    step = MagicMock(effects=[
        StepEffectConfig(on_action="approve", kind="ad_enable_account", params={}),
    ])
    with pytest.raises(AdCredentialsRequired):
        await run_effects(
            request=MagicMock(), step=step, action="approve",
            ad_creds=None, actor=MagicMock(),
        )


@pytest.mark.asyncio
async def test_run_effects_no_matching_returns_empty():
    from not_dot_net.backend.workflow_effects import run_effects
    from not_dot_net.config import StepEffectConfig
    step = MagicMock(effects=[
        StepEffectConfig(on_action="reject", kind="ad_disable_account", params={}),
    ])
    results = await run_effects(
        request=MagicMock(), step=step, action="approve",
        ad_creds=("a", "p"), actor=MagicMock(),
    )
    assert results == []
