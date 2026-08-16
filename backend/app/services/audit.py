from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import AuditHistory, Hotel, HotelEvidenceItem, HotelSafetyDimension
from backend.app.services.deepseek import DeepSeekClient, DeepSeekError
from backend.app.services.environment import clamp, clean_text, round_half_up
from backend.app.services.evidence import evidence_sufficiency, normalize_evidence

DIMENSION_WEIGHTS = {
    "hotel_intrinsic_safety": 0.35,
    "guest_review_safety": 0.30,
    "surrounding_environment": 0.20,
    "fire_health_management": 0.10,
    "data_confidence": 0.05,
}


def validate_audit_result(
    raw: Dict[str, Any], allowed_evidence_ids: List[str], evidence_coverage: float
) -> Dict[str, Any]:
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, list):
        raise DeepSeekError("深度审计缺少 dimensions")
    by_dimension = {item.get("dimension"): item for item in dimensions if isinstance(item, dict)}
    if set(by_dimension) != set(DIMENSION_WEIGHTS):
        raise DeepSeekError("深度审计必须返回规定的五个维度")
    allowed = set(allowed_evidence_ids)
    normalized_dimensions = []
    for name, weight in DIMENSION_WEIGHTS.items():
        item = by_dimension[name]
        try:
            score = round_half_up(clamp(float(item["score"]), 0, 5), 1)
        except (KeyError, TypeError, ValueError) as exc:
            raise DeepSeekError("维度 {} 缺少有效分数".format(name)) from exc
        evidence_ids = [value for value in item.get("evidence_ids", []) if value in allowed]
        if not evidence_ids:
            raise DeepSeekError("维度 {} 没有可回溯的 evidence_id".format(name))
        normalized_dimensions.append(
            {
                "dimension": name,
                "score": score,
                "weight": weight,
                "reason": clean_text(item.get("reason"), 1200),
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            }
        )
    weighted_total = sum(Decimal(str(item["score"])) * Decimal(str(item["weight"])) for item in normalized_dimensions)
    total_score = round_half_up(weighted_total, 1)
    try:
        model_confidence = clamp(float(raw.get("audit_confidence", 0.5)), 0, 1)
    except (TypeError, ValueError):
        model_confidence = 0.5
    return {
        "score": total_score,
        "audit_confidence": round_half_up(min(model_confidence, evidence_coverage), 2),
        "risk_level": clean_text(raw.get("risk_level"), 80) or "信息不足",
        "risk_tags": [clean_text(item, 100) for item in raw.get("risk_tags", [])[:8]],
        "conclusion": clean_text(raw.get("conclusion"), 4000),
        "advice": clean_text(raw.get("advice"), 1200),
        "negative_comments": clean_text(raw.get("negative_comments"), 2000),
        "dimensions": normalized_dimensions,
        "audit_status": "deep_audited",
    }


def _environment_payload(session: Session, hotel: Hotel) -> Dict[str, Any]:
    from backend.app.models import HotelEnvironmentPoi

    pois = session.scalars(select(HotelEnvironmentPoi).where(HotelEnvironmentPoi.hotel_id == hotel.id)).all()
    return {
        "environment_score": hotel.environment_score,
        "rule_score": hotel.rule_score,
        "model_score": hotel.model_score,
        "coverage_confidence": hotel.coverage_confidence,
        "pois": [
            {
                "category": item.category,
                "name": item.name,
                "distance_meters": item.distance_meters,
            }
            for item in pois
        ],
    }


def _replace_evidence_rows(session: Session, hotel_id: int, evidence: List[Dict[str, Any]]) -> None:
    session.execute(delete(HotelEvidenceItem).where(HotelEvidenceItem.hotel_id == hotel_id))
    for item in evidence:
        session.add(
            HotelEvidenceItem(
                hotel_id=hotel_id,
                evidence_id=item["evidence_id"],
                platform=clean_text(item.get("platform"), 120),
                source_type=clean_text(item.get("source_type"), 40) or "unknown",
                title=clean_text(item.get("title"), 500),
                url=clean_text(item.get("url"), 1500),
                published_at=clean_text(item.get("published_at"), 80),
                raw_text=item.get("raw_text"),
                claim=item["claim"],
                risk_type=clean_text(item.get("risk_type"), 100) or "信息不足",
                sentiment=clean_text(item.get("sentiment"), 24) or "neutral",
                weight=item["weight"],
            )
        )


async def deep_audit_hotel(
    session: Session,
    hotel: Hotel,
    evidence_input: List[Any],
    settings: Settings,
    client: DeepSeekClient = None,
) -> Dict[str, Any]:
    evidence = normalize_evidence(evidence_input)
    sufficient, metrics = evidence_sufficiency(evidence)
    if not sufficient:
        _replace_evidence_rows(session, hotel.id, evidence)
        session.execute(delete(HotelSafetyDimension).where(HotelSafetyDimension.hotel_id == hotel.id))
        hotel.audit_status = "evidence_insufficient"
        hotel.audit_confidence = metrics["coverage_confidence"]
        hotel.safety_score = hotel.environment_score
        hotel.risk_level = "信息不足" if hotel.environment_score is None else hotel.risk_level
        hotel.conclusion = "公开证据尚未达到深度审计最低充分度，当前不生成深审分。"
        if hotel.environment_score is not None:
            hotel.risk_tag = "环境初评 | 深审完成 | 公开证据不足"
        else:
            hotel.risk_tag = "深审完成 | 公开证据不足"
        history = AuditHistory(
            hotel_id=hotel.id,
            audit_type="deep_audit",
            audit_status="evidence_insufficient",
            score=None,
            confidence=metrics["coverage_confidence"],
            model_name=settings.deepseek_model,
            rule_version="deep-audit-v1",
            thresholds_json={"min_evidence": 2, "min_sources": 2, "min_links": 1, "min_weight": 1.2},
            result_json={"metrics": metrics, "preserved_environment_score": hotel.environment_score},
        )
        session.add(history)
        session.commit()
        return {
            "audit_status": "evidence_insufficient",
            "score": None,
            "confidence": metrics["coverage_confidence"],
            "reasons": metrics["reasons"],
            "environment_score": hotel.environment_score,
        }

    deepseek = client or DeepSeekClient(settings)
    model_payload = {
        "hotel": {"id": hotel.id, "name": hotel.name, "address": hotel.address, "city": hotel.city_name},
        "environment": _environment_payload(session, hotel),
        "evidence": evidence,
        "fixed_dimension_weights": DIMENSION_WEIGHTS,
    }
    raw = await deepseek.deep_audit(model_payload)
    result = validate_audit_result(raw, [item["evidence_id"] for item in evidence], metrics["coverage_confidence"])

    _replace_evidence_rows(session, hotel.id, evidence)
    session.execute(delete(HotelSafetyDimension).where(HotelSafetyDimension.hotel_id == hotel.id))
    for item in result["dimensions"]:
        session.add(HotelSafetyDimension(hotel_id=hotel.id, **item))

    hotel.safety_score = result["score"]
    hotel.risk_level = result["risk_level"]
    hotel.risk_tag = " | ".join(result["risk_tags"]) or "已完成深度审计"
    hotel.conclusion = result["conclusion"]
    hotel.advice = result["advice"]
    hotel.negative_comments = result["negative_comments"]
    hotel.audit_confidence = result["audit_confidence"]
    hotel.audit_status = "deep_audited"
    session.add(
        AuditHistory(
            hotel_id=hotel.id,
            audit_type="deep_audit",
            audit_status="deep_audited",
            score=result["score"],
            confidence=result["audit_confidence"],
            model_name=settings.deepseek_model,
            rule_version="deep-audit-v1",
            thresholds_json={"dimension_weights": DIMENSION_WEIGHTS},
            result_json=result,
        )
    )
    session.commit()
    return result
