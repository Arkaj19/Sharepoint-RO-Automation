from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    agent_runs, changesets, imports, mappings, outputs, proposals, refresh, rulebook, snapshots, validate,
)
from app.core.config import settings

app = FastAPI(title="GyanSys Migration Tool API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # lets the UI use the server's download file name
)

for module in (refresh, snapshots, changesets, proposals, mappings, rulebook, agent_runs, validate, imports,
               outputs):
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
