from fastapi import FastAPI

app = FastAPI(
    title="SubscriptHQ API",
    description="Subscription Management System",
    version="1.0.0"
)

@app.get("/")
def home():
    return {"message": "SubscriptHQ API is running"}

@app.get("/health")
def health():
    return {"status": "healthy"}
