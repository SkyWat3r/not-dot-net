from .register import AUTH_BACKENDS
from pathlib import Path

for module in Path(__file__).parent.iterdir():
    if (
        module.is_file()
        and module.suffix == ".py"
        and module.name not in ("__init__.py", "register.py")
    ):
        __import__(f"{__package__}.{module.stem}")
    if module.is_dir() and (module / "__init__.py").exists():
        __import__(f"{__package__}.{module.name}")


def load(app, get_user_db, get_user_manager, get_jwt_strategy):
    for backend_loader in AUTH_BACKENDS:
        backend_loader(get_user_db, get_user_manager, get_jwt_strategy)
