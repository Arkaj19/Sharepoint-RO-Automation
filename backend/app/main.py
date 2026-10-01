from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import fetch as fetch_routes
from app.api.routes import validate as validate_routes
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
app.include_router(validate_routes.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )