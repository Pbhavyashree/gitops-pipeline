import pytest
from fastapi.testclient import TestClient

from app.main import app, store


@pytest.fixture(autouse=True)
def clean_store():
    """Each test starts from an empty store, so ordering cannot matter."""
    store._tasks.clear()
    yield
    store._tasks.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestProbes:
    """
    The probes are what Kubernetes uses to decide whether to restart a pod and
    whether to send it traffic, so they are worth testing properly.
    """

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert body["uptime_seconds"] >= 0

    def test_ready_returns_ready_after_startup(self, client):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    def test_metrics_exposes_prometheus_format(self, client):
        client.get("/health")
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "http_requests_total" in response.text
        assert "http_request_duration_seconds" in response.text

    def test_metrics_uses_route_template_not_raw_path(self, client):
        """
        Guards against unbounded cardinality: a metric label per task ID would
        create a new time series for every request.
        """
        created = client.post("/api/v1/tasks", json={"title": "one"}).json()
        client.get(f"/api/v1/tasks/{created['id']}")

        metrics = client.get("/metrics").text
        assert "/api/v1/tasks/{task_id}" in metrics
        assert created["id"] not in metrics


class TestCreateTask:
    def test_creates_with_defaults(self, client):
        response = client.post("/api/v1/tasks", json={"title": "Write the Helm chart"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Write the Helm chart"
        assert body["priority"] == "medium"
        assert body["done"] is False
        assert body["id"]

    def test_rejects_empty_title(self, client):
        response = client.post("/api/v1/tasks", json={"title": ""})
        assert response.status_code == 422

    def test_rejects_invalid_priority(self, client):
        response = client.post("/api/v1/tasks", json={"title": "x", "priority": "urgent"})
        assert response.status_code == 422

    def test_rejects_overlong_title(self, client):
        response = client.post("/api/v1/tasks", json={"title": "x" * 201})
        assert response.status_code == 422


class TestListTasks:
    def test_lists_empty(self, client):
        assert client.get("/api/v1/tasks").json() == []

    def test_filters_by_done(self, client):
        first = client.post("/api/v1/tasks", json={"title": "a"}).json()
        client.post("/api/v1/tasks", json={"title": "b"})
        client.patch(f"/api/v1/tasks/{first['id']}", json={"done": True})

        done = client.get("/api/v1/tasks?done=true").json()
        pending = client.get("/api/v1/tasks?done=false").json()

        assert len(done) == 1
        assert len(pending) == 1
        assert done[0]["title"] == "a"


class TestGetTask:
    def test_returns_task(self, client):
        created = client.post("/api/v1/tasks", json={"title": "a"}).json()
        response = client.get(f"/api/v1/tasks/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_returns_404_for_unknown(self, client):
        assert client.get("/api/v1/tasks/does-not-exist").status_code == 404


class TestUpdateTask:
    def test_updates_single_field_only(self, client):
        created = client.post("/api/v1/tasks", json={"title": "a", "priority": "high"}).json()

        response = client.patch(f"/api/v1/tasks/{created['id']}", json={"done": True})

        assert response.status_code == 200
        body = response.json()
        assert body["done"] is True
        assert body["priority"] == "high"
        assert body["title"] == "a"

    def test_returns_404_for_unknown(self, client):
        response = client.patch("/api/v1/tasks/nope", json={"done": True})
        assert response.status_code == 404


class TestDeleteTask:
    def test_deletes(self, client):
        created = client.post("/api/v1/tasks", json={"title": "a"}).json()
        assert client.delete(f"/api/v1/tasks/{created['id']}").status_code == 204
        assert client.get(f"/api/v1/tasks/{created['id']}").status_code == 404

    def test_returns_404_for_unknown(self, client):
        assert client.delete("/api/v1/tasks/nope").status_code == 404


class TestRequestTracing:
    def test_echoes_supplied_request_id(self, client):
        response = client.get("/health", headers={"X-Request-ID": "abc-123"})
        assert response.headers["X-Request-ID"] == "abc-123"

    def test_generates_request_id_when_absent(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Request-ID")
