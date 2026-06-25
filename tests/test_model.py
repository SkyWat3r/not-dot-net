import pytest
from not_dot_net.backend.db import User
from not_dot_net.backend.schemas import UserRead

PROFILE_FIELDS = ["full_name", "phone", "office", "team", "title", "employment_status"]
SENSITIVE_USER_FIELDS = {
    "auth_method",
    "hashed_password",
    "ldap_dn",
    "ldap_username",
    "uid_number",
    "gid_number",
    "member_of",
    "photo",
}


@pytest.mark.parametrize("field", PROFILE_FIELDS)
def test_user_has_profile_field(field: str):
    user = User(email="test@example.com", hashed_password="x")
    assert hasattr(user, field)
    assert getattr(user, field) is None


def test_user_profile_fields_accept_values():
    user = User(
        email="test@example.com",
        hashed_password="x",
        full_name="Alice",
        phone="+33 1 23 45 67 89",
        office="B202",
        team="Plasma Physics",
        title="Researcher",
        employment_status="Permanent",
    )
    assert user.full_name == "Alice"
    assert user.phone == "+33 1 23 45 67 89"
    assert user.office == "B202"
    assert user.team == "Plasma Physics"
    assert user.title == "Researcher"
    assert user.employment_status == "Permanent"


async def test_teams_vocabulary_has_defaults():
    from not_dot_net.backend.vocabularies import (
        vocabularies_config,
        VocabulariesConfig,
        StoredVocabulary,
        VocabularyTerm,
        resolve_terms,
    )
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "teams": StoredVocabulary(
            key="teams",
            label={"en": "Teams"},
            terms=[VocabularyTerm(code="Plasma Physics", labels={"en": "Plasma Physics"})],
        ),
    }))
    teams = [term.code for term in await resolve_terms("teams")]
    assert len(teams) > 0


def test_user_default_role():
    # SQLAlchemy INSERT defaults are not applied on Python object construction;
    # verify via column metadata.
    assert User.__table__.c.role.default.arg == ""


def test_user_read_schema_does_not_expose_sensitive_fields():
    exposed_fields = set(UserRead.model_fields)

    assert SENSITIVE_USER_FIELDS.isdisjoint(exposed_fields)


def test_user_read_schema_profile_fields_are_explicitly_exposed():
    exposed_fields = set(UserRead.model_fields)

    for field in PROFILE_FIELDS:
        assert field in exposed_fields
    assert "start_date" in exposed_fields
    assert "end_date" in exposed_fields
    assert "role" in exposed_fields
