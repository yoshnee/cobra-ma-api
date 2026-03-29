"""
main.py — FastAPI application for COBRA-MA comparison API.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import cleanup
from extract import router as extract_router
from plans import router as plans_router


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

app.include_router(extract_router)
app.include_router(plans_router)


@app.get("/health")
def health():
    return {"status": "ok"}
