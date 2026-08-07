"""
Task API — the workload this pipeline deploys.

The application is deliberately small. The point of this repository is the
delivery path around it: build, test, package, and reconcile into a cluster
without anyone running kubectl by hand. But it is not a toy either — it has
the endpoints an operator actually needs to run something in Kubernetes:
separate liveness and readiness probes, Prometheus metrics, and structured
logs that a log aggregator can parse.
"""

import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.config import settings
from app.store import TaskStore

# --- Logging -----------------------------------------------------------------
# JSON-ish single-line logs. Kubernetes captures stdout, and anything
# multi-line (a stack trace, a pretty-printed dict) gets split into separate
# log entries by the collector, which makes it useless for searching.

logging.basicConfig(
    level=settings.log_level,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    stream=sys.stdout,
)
logger = logging.getLogger("task-api")

# --- Metrics -----------------------------------------------------------------
# Counters and histograms are what Prometheus scrapes from /metrics. The
# histogram matters more than the counter: an average latency hides the tail,
# and the tail is what users complain about.

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
TASKS_TOTAL = Gauge("tasks_total", "Number of tasks currently stored")
APP_INFO = Gauge("app_info", "Application metadata", ["version", "environment"])

# --- Lifecycle ---------------------------------------------------------------

store = TaskStore()
_ready = False
_started_at = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Readiness is set after startup work completes, not when the process boots.

    Kubernetes will route traffic to a pod as soon as it is ready, so reporting
    ready too early means requests hit a pod that cannot serve them yet. In a
    real service this is where connection pools warm up and migrations are
    checked.
    """
    global _ready
    logger.info("starting up, version=%s environment=%s", settings.version, settings.environment)
    APP_INFO.labels(version=settings.version, environment=settings.environment).set(1)
    _ready = True
    logger.info("ready to serve traffic")
    yield
    _ready = False
    logger.info("shutting down")


app = FastAPI(
    title="Task API",
    description="Reference workload for a GitOps delivery pipeline",
    version=settings.version,
    lifespan=lifespan,
)


# --- Middleware --------------------------------------------------------------


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """
    Attaches a request ID and records metrics.

    The path label uses the route template rather than the raw URL. Using the
    raw path would create a new time series per task ID, which is how you
    accidentally take down a Prometheus instance.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.perf_counter()

    response = await call_next(request)

    duration = time.perf_counter() - start
    route = request.scope.get("route")
    path = route.path if route else "unmatched"

    REQUEST_COUNT.labels(method=request.method, path=path, status=str(response.status_code)).inc()
    REQUEST_LATENCY.labels(method=request.method, path=path).observe(duration)

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s %s %.1fms request_id=%s",
        request.method,
        path,
        response.status_code,
        duration * 1000,
        request_id,
    )
    return response


# --- Schemas -----------------------------------------------------------------


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["low", "medium", "high"] = "medium"


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: Literal["low", "medium", "high"] | None = None
    done: bool | None = None


class Task(BaseModel):
    id: str
    title: str
    priority: str
    done: bool
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    uptime_seconds: float


# --- Probes ------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """
    Liveness. Answers "is this process wedged and in need of a restart?"

    Deliberately checks nothing external. If this called the database, a
    database blip would make Kubernetes restart every healthy pod at once,
    turning a partial outage into a total one.
    """
    return HealthResponse(
        status="ok",
        version=settings.version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - _started_at, 2),
    )


@app.get("/ready", tags=["operations"])
async def ready(response: Response):
    """
    Readiness. Answers "should this pod receive traffic right now?"

    This is where dependency checks belong. A failing readiness probe removes
    the pod from the Service endpoints without restarting it, so it can
    recover and rejoin.
    """
    if not _ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready"}
    return {"status": "ready"}


@app.get("/metrics", tags=["operations"], include_in_schema=False)
async def metrics() -> Response:
    TASKS_TOTAL.set(store.count())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- Tasks -------------------------------------------------------------------


@app.post("/api/v1/tasks", response_model=Task, status_code=201, tags=["tasks"])
async def create_task(payload: TaskCreate) -> Task:
    task = store.create(title=payload.title, priority=payload.priority)
    logger.info("created task id=%s", task["id"])
    return Task(**task)


@app.get("/api/v1/tasks", response_model=list[Task], tags=["tasks"])
async def list_tasks(done: bool | None = None) -> list[Task]:
    return [Task(**t) for t in store.list(done=done)]


@app.get("/api/v1/tasks/{task_id}", response_model=Task, tags=["tasks"])
async def get_task(task_id: str) -> Task:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return Task(**task)


@app.patch("/api/v1/tasks/{task_id}", response_model=Task, tags=["tasks"])
async def update_task(task_id: str, payload: TaskUpdate) -> Task:
    task = store.update(task_id, **payload.model_dump(exclude_unset=True))
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return Task(**task)


@app.delete("/api/v1/tasks/{task_id}", status_code=204, tags=["tasks"])
async def delete_task(task_id: str) -> Response:
    if not store.delete(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return Response(status_code=204)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to a client; log it instead."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
