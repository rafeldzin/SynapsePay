from fastapi import FastAPI
from app.models.schema import Milestone, Deliverable, Verdict
from app.verifier.interface import verify

app = FastAPI(
    title="SynapsePay API",
    description="Deterministic verification and payment rail gateway for autonomous agent contracts.",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "project": "SynapsePay",
        "status": "operational",
        "description": "Deterministic milestone verifier & payment rail",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/verify", response_model=Verdict)
def verify_milestone(milestone: Milestone, deliverable: Deliverable):
    """
    Verify milestone deliverable against criteria.
    """
    return verify(milestone, deliverable)
