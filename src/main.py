from fastapi import FastAPI
from api.routes import auth, file
from exceptions.handler import ExceptionHandler

def create_application() -> FastAPI:
    app = FastAPI(title="DocFlow API", version="0.2.0")
    app.include_router(auth.router)
    # Legacy upload routes remain mounted during Phase 2. They are not yet
    # tenant-aware and must not be used as the production DocFlow API.
    app.include_router(file.router)
    ExceptionHandler(app)
    return app

app = create_application()
