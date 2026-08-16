from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Hotel(Base):
    __tablename__ = "hotels"
    __table_args__ = (
        UniqueConstraint("map_poi_id", name="uq_hotels_map_poi_id"),
        UniqueConstraint("dedupe_key", name="uq_hotels_dedupe_key"),
        Index("ix_hotels_city_score", "city_code", "safety_score"),
        Index("ix_hotels_city_status", "city_code", "audit_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_poi_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    city_code: Mapped[str] = mapped_column(String(32), index=True)
    city_name: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(40), default="中国")
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    address: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str] = mapped_column(String(160), default="")
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    booking_url: Mapped[str] = mapped_column(String(1000), default="")
    environment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rule_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    safety_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(80), default="信息不足")
    risk_tag: Mapped[str] = mapped_column(Text, default="待分析 | 基础数据")
    conclusion: Mapped[str] = mapped_column(Text, default="已收录酒店基础位置数据，安全证据和评分待后续分析补充。")
    advice: Mapped[str] = mapped_column(Text, default="信息不足，建议结合现场环境和平台评论自行复核。")
    negative_comments: Mapped[str] = mapped_column(Text, default="")
    audit_status: Mapped[str] = mapped_column(String(40), default="pending")
    audit_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coverage_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HotelEnvironmentPoi(Base):
    __tablename__ = "hotel_environment_pois"
    __table_args__ = (
        UniqueConstraint("hotel_id", "category", "source_poi_id", name="uq_hotel_poi_source"),
        Index("ix_environment_pois_hotel_distance", "hotel_id", "distance_meters"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    address: Mapped[str] = mapped_column(String(500), default="")
    distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_poi_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="tencent_map")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HotelEvidenceItem(Base):
    __tablename__ = "hotel_evidence_items"
    __table_args__ = (
        UniqueConstraint("hotel_id", "evidence_id", name="uq_hotel_evidence_id"),
        Index("ix_evidence_hotel_weight", "hotel_id", "weight"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(120), default="")
    source_type: Mapped[str] = mapped_column(String(40), default="unknown")
    title: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(String(1500), default="")
    published_at: Mapped[str] = mapped_column(String(80), default="")
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    risk_type: Mapped[str] = mapped_column(String(100), default="信息不足")
    sentiment: Mapped[str] = mapped_column(String(24), default="neutral")
    weight: Mapped[float] = mapped_column(Float, default=0.35)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HotelSafetyDimension(Base):
    __tablename__ = "hotel_safety_dimensions"
    __table_args__ = (UniqueConstraint("hotel_id", "dimension", name="uq_hotel_dimension"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[str] = mapped_column(String(80), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoomPhoto(Base):
    __tablename__ = "room_photos"
    __table_args__ = (Index("ix_room_photos_task", "uid", "request_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    image_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    check_item: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoomReport(Base):
    __tablename__ = "room_reports"
    __table_args__ = (UniqueConstraint("uid", "request_id", name="uq_room_report_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(80), default="信息不足")
    issues: Mapped[list] = mapped_column(JSON, default=list)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditHistory(Base):
    __tablename__ = "audit_history"
    __table_args__ = (Index("ix_audit_history_hotel_created", "hotel_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    audit_type: Mapped[str] = mapped_column(String(40), nullable=False)
    audit_status: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_name: Mapped[str] = mapped_column(String(120), default="")
    rule_version: Mapped[str] = mapped_column(String(40), default="v0")
    thresholds_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
