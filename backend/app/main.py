from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import fetch as fetch_routes
from app.core.config import settings

app = FastAPI(title="GyanSys Migration Tool API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fetch_routes.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
