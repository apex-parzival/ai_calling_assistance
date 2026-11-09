from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.main import create_app
from core.settings import SettingsError, load_settings

from .conftest import VALID_ENV


@pytest.fixture
def settings(clean_env, clinics_root):
    for k, v in VALID_ENV.items():
        clean_env.setenv(k, v)
    return load_settings(clinics_root=clinics_root)


def test_health_200_when_db_up(settings, monkeypatch):
    async def ok(_engine):
        return True

    monkeypatch.setattr(db, "ping", ok)
    with TestClient(create_app(settings)) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "clinic": "demo_clinic", "database": "ok", "environment": "development"}


def test_health_503_when_db_down(settings, monkeypatch):
    async def down(_engine):
        return False

    monkeypatch.setattr(db, "ping", down)
    with TestClient(create_app(settings)) as client:
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"


def test_startup_fails_without_backend_keys(clean_env, clinics_root):
    with pytest.raises(SettingsError, match="SESSION_SECRET"):
        create_app(load_settings(clinics_root=clinics_root))


def test_backend_starts_without_vendor_keys(clean_env, clinics_root, monkeypatch):
    # Phase 0: the backend must run before vendor accounts exist.
    for k in ("DATABASE_URL", "SESSION_SECRET", "AGENT_API_TOKEN"):
        clean_env.setenv(k, VALID_ENV[k])

    async def ok(_engine):
        return True

    monkeypatch.setattr(db, "ping", ok)
    with TestClient(create_app(load_settings(clinics_root=clinics_root))) as client:
        assert client.get("/health").status_code == 200
