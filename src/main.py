from fastapi import FastAPI
from api.routes import auth, files
from exceptions.handler import ExceptionHandler

def create_application() -> FastAPI:
    app = FastAPI(title="DocFlow API", version="0.3.0")
    app.include_router(auth.router)
    app.include_router(files.router)
    ExceptionHandler(app)
    return app

app = create_application()
