from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import fetch as fetch_routes
from app.api.routes import preview as preview_routes
from app.api.routes import download as download_routes
from app.api.routes import validate as validate_routes
from app.core.config import settings

app = FastAPI(
    title="GyanSys Migration Tool API",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(fetch_routes.router)
app.include_router(preview_routes.router)
app.include_router(download_routes.router)
app.include_router(validate_routes.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )