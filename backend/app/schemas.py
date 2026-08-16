from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

AuditStatus = Literal["pending", "environment_scored", "evidence_insufficient", "deep_audited"]
PoiCategory = Literal["police", "medical", "transport", "convenience", "nightlife", "road_risk", "other"]


class PoiInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    category: PoiCategory
    distance_meters: int = Field(ge=0, le=10000)
    address: str = Field(default="", max_length=500)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    source_poi_id: str = Field(default="", max_length=128)
    source: str = Field(default="tencent_map", max_length=40)


class HotelInput(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1, max_length=240)
    address: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=80)
    city_code: str = Field(default="", max_length=32)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)


class EnvironmentAssessmentRequest(BaseModel):
    hotel: HotelInput
    pois: List[PoiInput] = Field(min_length=1, max_length=80)


class EvidenceInput(BaseModel):
    evidence_id: str = Field(default="", max_length=80)
    platform: str = Field(default="", max_length=120)
    source_type: Literal["official", "ota", "map", "social", "media", "unknown"] = "unknown"
    title: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=1500)
    published_at: str = Field(default="", max_length=80)
    raw_text: Optional[str] = Field(default=None, max_length=12000)
    claim: str = Field(min_length=1, max_length=4000)
    risk_type: str = Field(default="信息不足", max_length=100)
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value:
            return value
        HttpUrl(value)
        return value


class DeepAuditRequest(BaseModel):
    evidence: List[EvidenceInput] = Field(default_factory=list, max_length=80)


class DimensionResult(BaseModel):
    dimension: str
    score: float = Field(ge=0, le=5)
    weight: float = Field(gt=0, le=1)
    reason: str
    evidence_ids: List[str]


class RoomPhotoInput(BaseModel):
    image_url: str = Field(min_length=1, max_length=1500)
    storage_key: str = Field(default="", max_length=500)
    check_item: str = Field(default="", max_length=100)
    status: str = Field(default="completed", max_length=40)


class RoomReportInput(BaseModel):
    uid: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    risk_level: str = Field(default="信息不足", max_length=80)
    issues: List[str] = Field(default_factory=list, max_length=30)
    suggestions: List[str] = Field(default_factory=list, max_length=30)
    report: Dict[str, Any] = Field(default_factory=dict)
    photos: List[RoomPhotoInput] = Field(default_factory=list, max_length=20)
    status: str = Field(default="completed", max_length=40)


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    phone: str
    city_code: str
    city_name: str
    country: str
    longitude: float
    latitude: float
    booking_url: str
    environment_score: Optional[float]
    safety_score: Optional[float]
    risk_level: str
    risk_tag: str
    conclusion: str
    advice: str
    negative_comments: str
    audit_status: str
    audit_confidence: Optional[float]
    updated_at: datetime
