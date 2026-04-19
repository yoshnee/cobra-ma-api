"""
main.py — FastAPI application for COBRA-MA comparison API.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.clients.db import cleanup
from src.routes.compare import router as compare_router
from src.routes.extract_card import router as extract_card_router
from src.routes.extract_cobra import router as extract_cobra_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    cleanup()


app = FastAPI(title="COBRA MA API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cobravsma.kerjasama.dev",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract_cobra_router)
app.include_router(extract_card_router)
app.include_router(compare_router)


@app.get("/health")
def health():
    return {"status": "ok"}
