"""Settings for AD account creation: UID range, OUs, eligible groups, templates."""
from __future__ import annotations
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section


class AdAccountConfig(BaseModel):
    uid_min: int = Field(
        default=10000,
        description="Lowest UID that the allocator may hand out.",
    )
    uid_max: int = Field(
        default=60000,
        description="Highest UID (inclusive) that the allocator may hand out.",
    )
    default_gid_number: int = Field(
        default=10000,
        description="Default primary GID for new accounts (operator can override).",
    )
    default_login_shell: str = Field(
        default="/bin/bash",
        description="Default loginShell for new accounts.",
    )
    home_directory_template: str = Field(
        default="/home/{sam}",
        description="Template for unixHomeDirectory. {sam} is replaced with the sAMAccountName.",
    )
    mail_template: str = Field(
        default="{first}.{last}@lpp.polytechnique.fr",
        description="Template for the mail attribute. {first}/{last} are normalized (lowercased, accent-stripped).",
    )
    users_ous: list[str] = Field(
        default_factory=list,
        description="Distinguished names of OUs in which new users may be created.",
    )
    eligible_groups: list[str] = Field(
        default_factory=list,
        description="AD group DNs that may be picked in the create form and in step effects.",
    )
    default_groups_by_status: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-employment-status pre-selected groups, e.g. {'Intern': ['CN=...']}.",
    )
    password_length: int = Field(
        default=16,
        description="Length of the auto-generated initial password.",
    )


ad_account_config = section("ad_account", AdAccountConfig, label="AD Accounts")
