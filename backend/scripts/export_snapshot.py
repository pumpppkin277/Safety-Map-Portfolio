import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.app.config import REPOSITORY_ROOT
from backend.app.database import SessionLocal
from backend.app.models import Hotel, HotelEnvironmentPoi, HotelEvidenceItem, HotelSafetyDimension
from backend.app.routes import hotel_payload


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))


def export_data(data_dir: Path) -> dict:
    with SessionLocal() as session:
        hotels = session.scalars(select(Hotel).order_by(Hotel.id)).all()
        cities = {}
        city_centers = {}
        status_counts = {}
        for hotel in hotels:
            cities.setdefault(
                hotel.city_code,
                {
                    "id": hotel.city_code,
                    "city_code": hotel.city_code,
                    "name": hotel.city_name or hotel.city_code,
                    "country": hotel.country,
                    "latitude": hotel.latitude,
                    "longitude": hotel.longitude,
                },
            )
            center = city_centers.setdefault(hotel.city_code, {"latitude": 0.0, "longitude": 0.0, "count": 0})
            center["latitude"] += hotel.latitude
            center["longitude"] += hotel.longitude
            center["count"] += 1
            counters = status_counts.setdefault(hotel.city_code, Counter())
            counters[hotel.audit_status] += 1
            counters["total"] += 1
            counters["rated"] += int(hotel.safety_score is not None)
        for city_code, center in city_centers.items():
            cities[city_code]["latitude"] = round(center["latitude"] / center["count"], 6)
            cities[city_code]["longitude"] = round(center["longitude"] / center["count"], 6)
        write_json(data_dir / "cities.json", list(cities.values()))
        for city_code in cities:
            city_hotels = [hotel_payload(item) for item in hotels if item.city_code == city_code]
            write_json(data_dir / "hotels-{}.json".format(city_code), city_hotels)
        for hotel in hotels:
            pois = session.scalars(select(HotelEnvironmentPoi).where(HotelEnvironmentPoi.hotel_id == hotel.id)).all()
            evidence = session.scalars(select(HotelEvidenceItem).where(HotelEvidenceItem.hotel_id == hotel.id)).all()
            dimensions = session.scalars(
                select(HotelSafetyDimension).where(HotelSafetyDimension.hotel_id == hotel.id)
            ).all()
            detail = {
                "hotel": hotel_payload(hotel),
                "environment_pois": [
                    {
                        column.name: getattr(item, column.name)
                        for column in item.__table__.columns
                        if column.name not in {"created_at"}
                    }
                    for item in pois
                ],
                "evidence_items": [
                    {
                        column.name: getattr(item, column.name)
                        for column in item.__table__.columns
                        if column.name not in {"created_at", "raw_text"}
                    }
                    for item in evidence
                ],
                "dimensions": [
                    {
                        column.name: getattr(item, column.name)
                        for column in item.__table__.columns
                        if column.name != "created_at"
                    }
                    for item in dimensions
                ],
            }
            write_json(data_dir / "details" / "{}.json".format(hotel.id), detail)
        write_json(
            data_dir / "snapshot.json", {"cities": {code: dict(values) for code, values in status_counts.items()}}
        )
    return {"hotels": len(hotels), "cities": len(cities)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export SQL storage to the static web snapshot format.")
    parser.add_argument("--data-dir", type=Path, default=REPOSITORY_ROOT / "data")
    args = parser.parse_args()
    print(export_data(args.data_dir))
