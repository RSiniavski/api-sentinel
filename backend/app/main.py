from fastapi import FastAPI

app = FastAPI(
    title="API Sentinel",
    description="Intelligent API Testing & Monitoring",
    version="0.1.0",
)


@app.get("/")
async def root():
    return {
        "name": "API Sentinel",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
    }
