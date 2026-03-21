AUTH_BACKENDS = []


def register_backend_loader(backend_loader):
    global AUTH_BACKENDS
    AUTH_BACKENDS.append(backend_loader)
    return backend_loader
