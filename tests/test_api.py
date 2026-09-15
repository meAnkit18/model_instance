import pytest
from fastapi.testclient import TestClient

from controller.config import settings
from controller.main import app
from controller.services import worker_manager as worker_manager_module
from tests.test_worker_manager import FakeDriver


@pytest.fixture
def client():
    original_driver = worker_manager_module.worker_manager.driver
    worker_manager_module.worker_manager.driver = FakeDriver()
    with TestClient(app) as c:
        yield c
    worker_manager_module.worker_manager.driver = original_driver


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_status_shape(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "worker" in body
    assert body["worker"]["state"] == "OFFLINE"


def test_chat_completions_end_to_end(client):
    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "ok"
    assert body["usage"]["total_tokens"] == 2


def test_chat_completions_rejects_malformed_body(client):
    resp = client.post("/v1/chat/completions", json={"messages": "not-a-list"})
    assert resp.status_code == 422


def test_chat_completions_requires_api_key_when_configured(client):
    settings.api_key = "topsecret"
    try:
        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 401

        resp_ok = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer topsecret"},
        )
        assert resp_ok.status_code == 200
    finally:
        settings.api_key = None


def test_internal_register_requires_secret_when_configured(client):
    settings.worker_registration_secret = "wsecret"
    try:
        resp = client.post("/internal/workers/register", json={
            "worker_id": "w1", "status": "READY",
        })
        assert resp.status_code == 401

        resp_ok = client.post(
            "/internal/workers/register",
            json={"worker_id": "w1", "status": "READY"},
            headers={"Authorization": "Bearer wsecret"},
        )
        assert resp_ok.status_code == 200
    finally:
        settings.worker_registration_secret = None


def test_internal_register_rejects_malformed_body(client):
    resp = client.post("/internal/workers/register", json={"status": "READY"})  # missing worker_id
    assert resp.status_code == 422


def test_models_endpoint_lists_configured_model(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == settings.model_id


def test_oversized_body_rejected(client):
    settings.max_request_body_bytes = 100
    try:
        big_content = "x" * 1000
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": big_content}]},
        )
        assert resp.status_code == 413
    finally:
        settings.max_request_body_bytes = 1_000_000
