from fastapi.testclient import TestClient

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Hotel


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_health_city_hotel_and_room_report_routes():
    with SessionLocal() as session:
        session.add(
            Hotel(
                dedupe_key="api:1",
                city_code="310000",
                city_name="上海",
                name="测试酒店",
                address="测试路 1 号",
                longitude=121.47,
                latitude=31.23,
                safety_score=3.2,
                audit_status="environment_scored",
            )
        )
        session.commit()

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert "KELI_STATIC_SNAPSHOT = false" in client.get("/runtime-config.js").text
        cities = client.get("/api/cities").json()
        assert cities[0]["name"] == "上海"
        hotels = client.get("/api/hotels", params={"city_code": "310000"}).json()
        assert hotels[0]["name"] == "测试酒店"
        detail = client.get("/api/hotels/1").json()
        assert detail["hotel"]["audit_status"] == "environment_scored"

        report = {
            "uid": "user-1",
            "request_id": "request-1",
            "risk_level": "需留意",
            "issues": ["门锁需要复核"],
            "suggestions": ["使用门阻器"],
            "report": {"score": 3},
            "photos": [{"image_url": "https://example.com/lock.jpg", "check_item": "door_lock"}],
        }
        response = client.put("/api/room-reports/request-1", json=report, headers={"X-User-ID": "user-1"})
        assert response.status_code == 200
        saved = client.get("/api/room-reports/request-1", headers={"X-User-ID": "user-1"}).json()
        assert saved["issues"] == ["门锁需要复核"]
        assert len(saved["photos"]) == 1
