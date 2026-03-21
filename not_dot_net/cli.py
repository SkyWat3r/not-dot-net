from cyclopts import App
from typing import Optional
from yaml import safe_dump

from not_dot_net.app import main, create_user as app_create_user

app = App(name="NotDotNet", version="0.1.0")


@app.command
def serve(host: str = "localhost", port: int = 8000, env_file: Optional[str] = None):
    """Serve the NotDotNet application."""
    print(f"Serving NotDotNet on {host}:{port} in {env_file} environment")
    # Here you would add the code to actually start the server
    # For example, using FastAPI or Flask
    main(host, port, env_file, reload=False)


@app.command
def create_user(username: str, password: str, env_file: Optional[str] = None):
    """Create a new user in the NotDotNet application."""
    print(f"Creating user '{username}' in {env_file} environment")
    # Here you would add the code to create a user in the database
    # For example, using SQLAlchemy or another ORM
    app_create_user(username, password, env_file)


@app.command
def default_config():
    from not_dot_net.config import Settings

    print(safe_dump(Settings().model_dump()))


if __name__ in {"__main__", "__mp_main__"}:
    app()
