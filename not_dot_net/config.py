from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
    PydanticBaseSettingsSource,
)


class LDAPSettings(BaseSettings):
    url: str = "ldap://localhost"
    base_dn: str = "dc=example,dc=com"
    port: int = 389


class AuthSettings(BaseSettings):
    ldap: LDAPSettings = LDAPSettings()


class UsersSettings(BaseSettings):
    auth: AuthSettings = AuthSettings()


class BackendSettings(BaseSettings):
    users: UsersSettings = UsersSettings()
    database_url: str = "sqlite+aiosqlite:///./test.db"


class Settings(BaseSettings):
    app_name: str = "LPP Intranet"
    admin_email: str = ""
    backend: BackendSettings = BackendSettings()

    model_config = SettingsConfigDict(yaml_file="config.yaml")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (YamlConfigSettingsSource(settings_cls),)


def load_settings(config_file: str | None = None) -> Settings:
    from nicegui import app

    app.state.settings = Settings(_yaml_file=config_file)
    return app.state.settings


def get_settings() -> Settings:
    from nicegui import app

    return app.state.settings
