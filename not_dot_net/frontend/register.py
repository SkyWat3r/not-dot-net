FRONTEND_LOADERS = []

def register_frontend_loader(loader):
    global FRONTEND_LOADERS
    FRONTEND_LOADERS.append(loader)
    return loader