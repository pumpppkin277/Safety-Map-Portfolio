import asyncio

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.database import Base, SessionLocal, engine
from backend.app.models import Hotel, HotelEvidenceItem
from backend.app.services.audit import DIMENSION_WEIGHTS, deep_audit_hotel, validate_audit_result
from backend.app.services.evidence import evidence_sufficiency, normalize_evidence


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_evidence_is_deduplicated_weighted_and_gated():
    evidence = normalize_evidence(
        [
            {
                "platform": "公安通报",
                "source_type": "official",
                "url": "https://example.gov/a",
                "published_at": "2026-01-02",
                "claim": "官方通报记录了一项可核验的消防整改。",
            },
            {
                "platform": "公安通报",
                "source_type": "official",
                "url": "https://example.gov/a",
                "published_at": "2026-01-02",
                "claim": "重复内容",
            },
            {
                "platform": "住客平台",
                "source_type": "ota",
                "url": "https://example.com/review",
                "published_at": "2026-02-03",
                "claim": "多名住客提到夜间入口照明较弱，需要留意。",
            },
        ]
    )
    assert len(evidence) == 2
    assert evidence[0]["evidence_id"].startswith("E-")
    sufficient, metrics = evidence_sufficiency(evidence)
    assert sufficient is True
    assert metrics["source_count"] == 2


def test_validate_audit_calculates_score_from_fixed_dimensions():
    raw = {
        "audit_confidence": 0.9,
        "risk_level": "需留意",
        "risk_tags": ["入口照明"],
        "conclusion": "存在需要复核的夜间出行线索。",
        "advice": "入住前确认入口和门锁。",
        "negative_comments": "入口照明较弱。",
        "dimensions": [
            {
                "dimension": name,
                "score": 4 if name != "data_confidence" else 3,
                "reason": "依据 E1。",
                "evidence_ids": ["E1"],
            }
            for name in DIMENSION_WEIGHTS
        ],
    }
    result = validate_audit_result(raw, ["E1"], 0.72)
    assert result["score"] == 4.0
    assert result["audit_confidence"] == 0.72
    assert [item["weight"] for item in result["dimensions"]] == list(DIMENSION_WEIGHTS.values())


def test_insufficient_deep_audit_preserves_environment_score():
    with SessionLocal() as session:
        hotel = Hotel(
            dedupe_key="test:1",
            city_code="310000",
            city_name="上海",
            name="测试酒店",
            longitude=121.4,
            latitude=31.2,
            environment_score=3.4,
            safety_score=3.4,
            audit_status="environment_scored",
        )
        session.add(hotel)
        session.commit()
        result = asyncio.run(
            deep_audit_hotel(
                session,
                hotel,
                [
                    {
                        "platform": "未知来源",
                        "source_type": "unknown",
                        "claim": "只有一条且没有链接的线索。",
                    }
                ],
                Settings(deepseek_api_key=""),
            )
        )
        assert result["audit_status"] == "evidence_insufficient"
        assert result["score"] is None
        assert hotel.safety_score == 3.4
        assert hotel.audit_status == "evidence_insufficient"
        saved_evidence = session.scalars(select(HotelEvidenceItem).where(HotelEvidenceItem.hotel_id == hotel.id)).all()
        assert len(saved_evidence) == 1
