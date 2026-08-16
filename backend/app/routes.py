import hashlib
import secrets
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.database import get_db
from backend.app.models import (
    Hotel,
    HotelEnvironmentPoi,
    HotelEvidenceItem,
    HotelSafetyDimension,
    RoomPhoto,
    RoomReport,
)
from backend.app.schemas import DeepAuditRequest, EnvironmentAssessmentRequest, RoomReportInput
from backend.app.services.audit import deep_audit_hotel
from backend.app.services.catalog import assess_hotel_environment, discover_city_hotels, refresh_environment_pois
from backend.app.services.deepseek import DeepSeekClient, DeepSeekError
from backend.app.services.environment import (
    build_poi_profile,
    calculate_coverage_confidence,
    calculate_rule_baseline,
    combine_environment_assessment,
)

router = APIRouter(prefix="/api")
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_admin(
    settings: SettingsDep,
    x_admin_token: Annotated[Optional[str], Header()] = None,
) -> None:
    if settings.admin_token and not secrets.compare_digest(x_admin_token or "", settings.admin_token):
        raise HTTPException(status_code=401, detail="管理员令牌无效")


def require_user_id(x_user_id: Annotated[Optional[str], Header()] = None) -> str:
    if not x_user_id or len(x_user_id) > 128:
        raise HTTPException(status_code=401, detail="缺少有效的用户身份")
    return x_user_id


def hotel_payload(hotel: Hotel) -> Dict[str, Any]:
    return {
        "id": hotel.id,
        "name": hotel.name,
        "address": hotel.address,
        "phone": hotel.phone,
        "city_code": hotel.city_code,
        "city_name": hotel.city_name,
        "country": hotel.country,
        "longitude": hotel.longitude,
        "latitude": hotel.latitude,
        "booking_url": hotel.booking_url,
        "environment_score": hotel.environment_score,
        "safety_score": hotel.safety_score,
        "risk_level": hotel.risk_level,
        "risk_tag": hotel.risk_tag,
        "conclusion": hotel.conclusion,
        "advice": hotel.advice,
        "negative_comments": hotel.negative_comments,
        "audit_status": hotel.audit_status,
        "audit_confidence": hotel.audit_confidence,
        "updated_at": hotel.updated_at.isoformat() if hotel.updated_at else None,
    }


@router.get("/cities")
def list_cities(session: DbSession) -> List[Dict[str, Any]]:
    rows = session.execute(
        select(
            Hotel.city_code,
            Hotel.city_name,
            Hotel.country,
            func.avg(Hotel.latitude),
            func.avg(Hotel.longitude),
            func.count(Hotel.id),
        )
        .group_by(Hotel.city_code, Hotel.city_name, Hotel.country)
        .order_by(Hotel.city_name)
    ).all()
    return [
        {
            "id": row[0],
            "city_code": row[0],
            "name": row[1] or row[0],
            "country": row[2],
            "latitude": round(float(row[3]), 6),
            "longitude": round(float(row[4]), 6),
            "hotel_count": row[5],
        }
        for row in rows
    ]


@router.get("/hotels")
def list_hotels(
    session: DbSession,
    city_code: str = Query(min_length=1, max_length=32),
    audit_status: Optional[str] = Query(default=None, max_length=40),
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=5000, ge=1, le=10000),
) -> List[Dict[str, Any]]:
    statement = select(Hotel).where(Hotel.city_code == city_code)
    if audit_status:
        statement = statement.where(Hotel.audit_status == audit_status)
    if search:
        pattern = "%{}%".format(search.strip())
        statement = statement.where((Hotel.name.like(pattern)) | (Hotel.address.like(pattern)))
    hotels = session.scalars(statement.order_by(Hotel.safety_score.desc()).limit(limit)).all()
    return [hotel_payload(hotel) for hotel in hotels]


@router.get("/hotels/{hotel_id}")
def get_hotel(hotel_id: int, session: DbSession) -> Dict[str, Any]:
    hotel = session.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="酒店不存在")
    evidence = session.scalars(
        select(HotelEvidenceItem)
        .where(HotelEvidenceItem.hotel_id == hotel_id)
        .order_by(HotelEvidenceItem.weight.desc())
    ).all()
    pois = session.scalars(
        select(HotelEnvironmentPoi)
        .where(HotelEnvironmentPoi.hotel_id == hotel_id)
        .order_by(HotelEnvironmentPoi.distance_meters)
    ).all()
    dimensions = session.scalars(select(HotelSafetyDimension).where(HotelSafetyDimension.hotel_id == hotel_id)).all()
    return {
        "hotel": hotel_payload(hotel),
        "evidence_items": [
            {
                "evidence_id": item.evidence_id,
                "platform": item.platform,
                "source_type": item.source_type,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "claim": item.claim,
                "risk_type": item.risk_type,
                "sentiment": item.sentiment,
                "weight": item.weight,
            }
            for item in evidence
        ],
        "environment_pois": [
            {
                "id": item.id,
                "category": item.category,
                "name": item.name,
                "address": item.address,
                "distance_meters": item.distance_meters,
                "longitude": item.longitude,
                "latitude": item.latitude,
                "source_poi_id": item.source_poi_id,
                "source": item.source,
            }
            for item in pois
        ],
        "dimensions": [
            {
                "dimension": item.dimension,
                "score": item.score,
                "weight": item.weight,
                "reason": item.reason,
                "evidence_ids": item.evidence_ids,
            }
            for item in dimensions
        ],
    }


@router.post("/environment-assessment")
async def environment_assessment(
    request: EnvironmentAssessmentRequest,
    settings: SettingsDep,
) -> Dict[str, Any]:
    pois = [item.model_dump() for item in request.pois]
    profile = build_poi_profile(pois)
    if profile["total_pois"] == 0:
        raise HTTPException(status_code=422, detail="缺少可用的环境 POI")
    rule_score = calculate_rule_baseline(profile)
    coverage = calculate_coverage_confidence(profile)
    try:
        model = await DeepSeekClient(settings).environment_assessment(request.hotel.model_dump(), profile, rule_score)
    except DeepSeekError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    assessment = combine_environment_assessment(rule_score, model, coverage)
    return {
        "assessment": assessment,
        "trace": {
            "provider": "deepseek",
            "model": settings.deepseek_model,
            "rule_version": "environment-v0",
            "rule_baseline": rule_score,
            "model_score": model["model_score"],
            "coverage_confidence": coverage,
            "weights": {"rule": 0.45, "model": 0.55},
        },
        "disclaimer": "环境初评只描述周边地图环境，不代表酒店内部管理水平，也不替代现场判断。",
    }


@router.post("/admin/cities/discover", dependencies=[Depends(require_admin)])
async def discover_city(
    session: DbSession,
    settings: SettingsDep,
    city_name: str = Query(min_length=1, max_length=80),
    city_code: str = Query(min_length=1, max_length=32),
    max_pages: int = Query(default=3, ge=1, le=10),
) -> Dict[str, int]:
    return await discover_city_hotels(session, city_name, city_code, settings, max_pages=max_pages)


@router.post("/admin/hotels/{hotel_id}/environment", dependencies=[Depends(require_admin)])
async def refresh_and_assess_environment(
    hotel_id: int,
    session: DbSession,
    settings: SettingsDep,
    refresh_pois: bool = True,
) -> Dict[str, Any]:
    hotel = session.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="酒店不存在")
    try:
        if refresh_pois:
            await refresh_environment_pois(session, hotel, settings)
        return await assess_hotel_environment(session, hotel, settings)
    except (ValueError, DeepSeekError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/admin/hotels/{hotel_id}/audit", dependencies=[Depends(require_admin)])
async def audit_hotel(
    hotel_id: int,
    request: DeepAuditRequest,
    session: DbSession,
    settings: SettingsDep,
) -> Dict[str, Any]:
    hotel = session.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="酒店不存在")
    try:
        return await deep_audit_hotel(session, hotel, request.evidence, settings)
    except DeepSeekError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/room-reports/{request_id}")
def upsert_room_report(
    request_id: str,
    request: RoomReportInput,
    session: DbSession,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Dict[str, Any]:
    if request_id != request.request_id:
        raise HTTPException(status_code=422, detail="request_id 不一致")
    if user_id != request.uid:
        raise HTTPException(status_code=403, detail="不能写入其他用户的报告")
    report = session.scalar(
        select(RoomReport).where(RoomReport.uid == request.uid, RoomReport.request_id == request_id)
    )
    if report is None:
        report = RoomReport(uid=request.uid, request_id=request_id)
        session.add(report)
    report.risk_level = request.risk_level
    report.issues = request.issues
    report.suggestions = request.suggestions
    report.report_json = request.report
    report.status = request.status
    existing_photos = session.scalars(
        select(RoomPhoto).where(RoomPhoto.uid == request.uid, RoomPhoto.request_id == request_id)
    ).all()
    for photo in existing_photos:
        session.delete(photo)
    for photo in request.photos:
        storage_key = photo.storage_key or "room-checks/{}/{}/{}".format(
            hashlib.sha256(request.uid.encode("utf-8")).hexdigest()[:16],
            request_id,
            hashlib.sha256(photo.image_url.encode("utf-8")).hexdigest()[:20],
        )
        session.add(
            RoomPhoto(
                uid=request.uid,
                request_id=request_id,
                storage_key=storage_key,
                **photo.model_dump(exclude={"storage_key"}),
            )
        )
    session.commit()
    return {"uid": request.uid, "request_id": request_id, "status": report.status, "photo_count": len(request.photos)}


@router.get("/room-reports/{request_id}")
def get_room_report(
    request_id: str,
    session: DbSession,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Dict[str, Any]:
    report = session.scalar(select(RoomReport).where(RoomReport.uid == user_id, RoomReport.request_id == request_id))
    if report is None:
        raise HTTPException(status_code=404, detail="房间检查报告不存在")
    photos = session.scalars(
        select(RoomPhoto).where(RoomPhoto.uid == user_id, RoomPhoto.request_id == request_id)
    ).all()
    return {
        "uid": user_id,
        "request_id": request_id,
        "risk_level": report.risk_level,
        "issues": report.issues,
        "suggestions": report.suggestions,
        "report": report.report_json,
        "status": report.status,
        "photos": [
            {
                "image_url": item.image_url,
                "storage_key": item.storage_key,
                "check_item": item.check_item,
                "status": item.status,
            }
            for item in photos
        ],
    }
