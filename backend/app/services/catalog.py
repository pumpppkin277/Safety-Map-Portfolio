from typing import Any, Dict, List

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import AuditHistory, Hotel, HotelEnvironmentPoi
from backend.app.services.deepseek import DeepSeekClient
from backend.app.services.environment import (
    build_poi_profile,
    calculate_coverage_confidence,
    calculate_rule_baseline,
    combine_environment_assessment,
)
from backend.app.services.tencent_maps import TencentMapClient, hotel_dedupe_key


async def discover_city_hotels(
    session: Session,
    city_name: str,
    city_code: str,
    settings: Settings,
    max_pages: int = 3,
    map_client: TencentMapClient = None,
) -> Dict[str, int]:
    client = map_client or TencentMapClient(settings)
    discovered = await client.discover_hotels(city_name, max_pages=max_pages)
    created = 0
    updated = 0
    for item in discovered:
        key = item.get("dedupe_key") or hotel_dedupe_key(item)
        map_id = item.get("map_poi_id") or None
        hotel = (
            session.scalar(select(Hotel).where(or_(Hotel.dedupe_key == key, Hotel.map_poi_id == map_id)))
            if map_id
            else session.scalar(select(Hotel).where(Hotel.dedupe_key == key))
        )
        if hotel is None:
            hotel = Hotel(
                dedupe_key=key,
                map_poi_id=map_id,
                city_code=city_code,
                city_name=city_name,
                name=item["name"],
                address=item.get("address", ""),
                phone=item.get("phone", ""),
                longitude=item["longitude"],
                latitude=item["latitude"],
            )
            session.add(hotel)
            created += 1
        else:
            hotel.name = item["name"]
            hotel.address = item.get("address", hotel.address)
            hotel.phone = item.get("phone", hotel.phone)
            hotel.longitude = item["longitude"]
            hotel.latitude = item["latitude"]
            hotel.city_code = city_code
            hotel.city_name = city_name
            updated += 1
    session.commit()
    return {"discovered": len(discovered), "created": created, "updated": updated}


async def refresh_environment_pois(
    session: Session,
    hotel: Hotel,
    settings: Settings,
    map_client: TencentMapClient = None,
) -> List[HotelEnvironmentPoi]:
    client = map_client or TencentMapClient(settings)
    pois = await client.nearby_environment(hotel.latitude, hotel.longitude)
    session.execute(delete(HotelEnvironmentPoi).where(HotelEnvironmentPoi.hotel_id == hotel.id))
    records = []
    for item in pois:
        record = HotelEnvironmentPoi(
            hotel_id=hotel.id,
            category=item["category"],
            name=item["name"],
            address=item.get("address", ""),
            distance_meters=item["distance_meters"],
            longitude=item.get("longitude"),
            latitude=item.get("latitude"),
            source_poi_id=item["source_poi_id"],
            source=item.get("source", "tencent_map"),
        )
        session.add(record)
        records.append(record)
    session.commit()
    return records


async def assess_hotel_environment(
    session: Session,
    hotel: Hotel,
    settings: Settings,
    deepseek_client: DeepSeekClient = None,
) -> Dict[str, Any]:
    records = session.scalars(select(HotelEnvironmentPoi).where(HotelEnvironmentPoi.hotel_id == hotel.id)).all()
    poi_payload = [
        {
            "name": item.name,
            "category": item.category,
            "distance_meters": item.distance_meters,
        }
        for item in records
    ]
    if not poi_payload:
        raise ValueError("酒店尚无可用的环境 POI")
    profile = build_poi_profile(poi_payload)
    rule_score = calculate_rule_baseline(profile)
    coverage = calculate_coverage_confidence(profile)
    client = deepseek_client or DeepSeekClient(settings)
    model = await client.environment_assessment(
        {
            "id": hotel.id,
            "name": hotel.name,
            "address": hotel.address,
            "city": hotel.city_name,
        },
        profile,
        rule_score,
    )
    assessment = combine_environment_assessment(rule_score, model, coverage)
    hotel.rule_score = rule_score
    hotel.model_score = model["model_score"]
    hotel.environment_score = assessment["score"]
    hotel.safety_score = assessment["score"]
    hotel.risk_level = assessment["risk_level"]
    hotel.risk_tag = "环境初评" + (" | " + " | ".join(assessment["risk_factors"]) if assessment["risk_factors"] else "")
    hotel.conclusion = assessment["summary"] + " 该结果只代表周边环境层，不等同于完整酒店安全审计。"
    hotel.advice = "周边环境存在可留意因素，建议继续查看酒店内部安全证据和近期住客评价。"
    hotel.audit_status = "environment_scored"
    hotel.audit_confidence = assessment["confidence"]
    hotel.coverage_confidence = coverage
    session.add(
        AuditHistory(
            hotel_id=hotel.id,
            audit_type="environment_assessment",
            audit_status="environment_scored",
            score=assessment["score"],
            confidence=assessment["confidence"],
            model_name=settings.deepseek_model,
            rule_version="environment-v0",
            thresholds_json={"rule_weight": 0.45, "model_weight": 0.55, "distance_bands": [300, 800, 1500]},
            result_json={"assessment": assessment, "profile": profile, "rule_score": rule_score, "model": model},
        )
    )
    session.commit()
    return {
        "assessment": assessment,
        "trace": {"rule_baseline": rule_score, "model_score": model["model_score"], "coverage_confidence": coverage},
    }
