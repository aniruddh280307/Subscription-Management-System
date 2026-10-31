from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db, settings
from app.routers import auth, customers, products, plans, subscriptions, invoices, dashboard, guardian, har, commercial, system
from app.services.scheduler_service import start_automation, stop_automation


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = await start_automation()
    yield
    await stop_automation(tasks)

app = FastAPI(
    title="SubscriptHQ API",
    description="Subscription and Billing Management Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include routers
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(products.router)
app.include_router(plans.router)
app.include_router(subscriptions.router)
app.include_router(invoices.router)
app.include_router(dashboard.router)
app.include_router(guardian.router)
app.include_router(har.router)
app.include_router(commercial.router)
app.include_router(system.router)


@app.get("/")
def home():
    return {
        "message": "SubscriptHQ API is running",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
    }


@app.get("/health")
@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """Health check verifying API operational status and live database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connectivity failed: {str(e)}",
        )
