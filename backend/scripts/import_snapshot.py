import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import delete

from backend.app.config import REPOSITORY_ROOT
from backend.app.database import SessionLocal, init_db
from backend.app.models import Hotel, HotelEnvironmentPoi, HotelEvidenceItem, HotelSafetyDimension


def text(value: Any) -> str:
    return "" if value is None else str(value)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_datetime(value: Any) -> Any:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def import_data(data_dir: Path) -> Dict[str, int]:
    cities = {item["city_code"]: item for item in load_json(data_dir / "cities.json")}
    details_dir = data_dir / "details"
    counts = {"hotels": 0, "pois": 0, "evidence": 0, "dimensions": 0}
    init_db()
    with SessionLocal() as session:
        for hotel_file in sorted(data_dir.glob("hotels-*.json")):
            for item in load_json(hotel_file):
                hotel_id = int(item["id"])
                city = cities.get(text(item.get("city_code")), {})
                hotel = session.get(Hotel, hotel_id)
                if hotel is None:
                    hotel = Hotel(
                        id=hotel_id,
                        dedupe_key="snapshot:{}".format(hotel_id),
                        city_code=text(item.get("city_code")),
                        name=text(item.get("name")),
                        longitude=float(item["longitude"]),
                        latitude=float(item["latitude"]),
                    )
                    session.add(hotel)
                hotel.city_name = text(city.get("name"))
                hotel.country = text(city.get("country") or "中国")
                hotel.address = text(item.get("address"))
                hotel.phone = text(item.get("phone"))
                hotel.booking_url = text(item.get("booking_url"))
                hotel.environment_score = item.get("environment_score")
                hotel.safety_score = item.get("safety_score")
                hotel.risk_level = text(item.get("risk_level") or "信息不足")
                hotel.risk_tag = text(item.get("risk_tag") or "待分析 | 基础数据")
                hotel.conclusion = text(item.get("conclusion"))
                hotel.advice = text(item.get("advice"))
                hotel.negative_comments = text(item.get("negative_comments"))
                hotel.audit_status = text(item.get("audit_status") or "pending")
                hotel.audit_confidence = item.get("audit_confidence")
                imported_updated_at = parse_datetime(item.get("updated_at"))
                if imported_updated_at:
                    hotel.updated_at = imported_updated_at
                counts["hotels"] += 1
        session.commit()

        for detail_file in sorted(details_dir.glob("*.json")):
            payload = load_json(detail_file)
            hotel_id = int(payload["hotel"]["id"])
            if session.get(Hotel, hotel_id) is None:
                continue
            session.execute(delete(HotelEnvironmentPoi).where(HotelEnvironmentPoi.hotel_id == hotel_id))
            session.execute(delete(HotelEvidenceItem).where(HotelEvidenceItem.hotel_id == hotel_id))
            session.execute(delete(HotelSafetyDimension).where(HotelSafetyDimension.hotel_id == hotel_id))
            for item in payload.get("environment_pois", []):
                source_id = text(item.get("map_poi_id") or item.get("amap_poi_id"))
                if not source_id:
                    source_id = hashlib.sha256(
                        "{}|{}|{}".format(item.get("name"), item.get("latitude"), item.get("longitude")).encode("utf-8")
                    ).hexdigest()[:24]
                session.add(
                    HotelEnvironmentPoi(
                        hotel_id=hotel_id,
                        category=text(item.get("category") or "other"),
                        name=text(item.get("name") or "未命名地点"),
                        address=text(item.get("address")),
                        distance_meters=int(item.get("distance_meters") or 0),
                        longitude=item.get("longitude"),
                        latitude=item.get("latitude"),
                        source_poi_id=source_id,
                        source=text(item.get("source") or "snapshot"),
                    )
                )
                counts["pois"] += 1
            for item in payload.get("evidence_items", []):
                session.add(
                    HotelEvidenceItem(
                        hotel_id=hotel_id,
                        evidence_id=text(item.get("evidence_id") or "E{}".format(item.get("id"))),
                        platform=text(item.get("platform")),
                        source_type=text(item.get("source_type") or "unknown"),
                        title=text(item.get("title")),
                        url=text(item.get("url")),
                        published_at=text(item.get("published_at")),
                        raw_text=item.get("raw_text"),
                        claim=text(item.get("claim") or item.get("title") or "公开线索"),
                        risk_type=text(item.get("risk_type") or "信息不足"),
                        sentiment=text(item.get("sentiment") or "neutral"),
                        weight=float(item.get("weight") or 0.35),
                    )
                )
                counts["evidence"] += 1
            for item in payload.get("dimensions", []):
                session.add(
                    HotelSafetyDimension(
                        hotel_id=hotel_id,
                        dimension=text(item.get("dimension")),
                        score=float(item.get("score") or 0),
                        weight=float(item.get("weight") or 0),
                        reason=text(item.get("reason")),
                        evidence_ids=item.get("evidence_ids") or [],
                    )
                )
                counts["dimensions"] += 1
        session.commit()
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import the checked-in static snapshot into SQLAlchemy storage.")
    parser.add_argument("--data-dir", type=Path, default=REPOSITORY_ROOT / "data")
    args = parser.parse_args()
    print(import_data(args.data_dir))
