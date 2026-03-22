"""RAG SaaS API - Main Application Entry Point."""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 RAG SaaS API starting...")
    yield
    print("👋 RAG SaaS API shutting down...")


app = FastAPI(
    title="RAG SaaS API",
    description="Enterprise RAG Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/")
async def root():
    return {"name": "RAG SaaS API", "version": "1.0.0", "docs": "/docs"}
