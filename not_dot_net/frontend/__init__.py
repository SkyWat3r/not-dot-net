from pathlib import Path
from .register import FRONTEND_LOADERS
from not_dot_net.backend.app import NotDotNetApp

for module in Path(__file__).parent.iterdir():
    if module.is_file() and module.suffix == ".py" and module.name not in ("__init__.py", "register.py"):
        __import__(f"{__package__}.{module.stem}")
    if module.is_dir() and (module / "__init__.py").exists():
        __import__(f"{__package__}.{module.name}")


def load(ndtapp: NotDotNetApp):
    for loader in FRONTEND_LOADERS:
        loader(ndtapp)
