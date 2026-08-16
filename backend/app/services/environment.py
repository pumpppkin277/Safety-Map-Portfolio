import math
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List

CATEGORY_LABELS = {
    "police": "公安与治安设施",
    "medical": "医疗设施",
    "transport": "公共交通",
    "convenience": "生活便利设施",
    "nightlife": "夜生活场所",
    "road_risk": "道路与偏僻风险",
    "other": "其他地点",
}

CATEGORY_ALIASES = {
    "公安": "police",
    "派出所": "police",
    "警务": "police",
    "医院": "medical",
    "医疗": "medical",
    "急救": "medical",
    "交通": "transport",
    "地铁": "transport",
    "公交": "transport",
    "便利": "convenience",
    "商超": "convenience",
    "超市": "convenience",
    "夜生活": "nightlife",
    "酒吧": "nightlife",
    "ktv": "nightlife",
    "道路风险": "road_risk",
    "停车场": "road_risk",
    "高速入口": "road_risk",
}

DISTANCE_DELTAS = {
    "police": (0.62, 0.42, 0.20),
    "medical": (0.42, 0.28, 0.12),
    "transport": (0.28, 0.18, 0.08),
    "convenience": (0.22, 0.14, 0.06),
    "nightlife": (-0.52, -0.34, -0.14),
    "road_risk": (-0.68, -0.44, -0.20),
}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def round_half_up(value: float, digits: int = 1) -> float:
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_category(value: Any) -> str:
    raw = clean_text(value, 80).lower()
    if raw in CATEGORY_LABELS:
        return raw
    for alias, category in CATEGORY_ALIASES.items():
        if alias in raw:
            return category
    return "other"


def normalize_pois(items: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for item in list(items or [])[:80]:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        try:
            distance = round(float(item.get("distance_meters", item.get("distance", -1))))
        except (TypeError, ValueError):
            continue
        if not 0 <= distance <= 10000:
            continue
        normalized.append(
            {
                "name": clean_text(item.get("name") or item.get("title") or "未命名地点"),
                "category": normalize_category(item.get("category") or item.get("type")),
                "distance_meters": distance,
            }
        )
    return normalized


def build_poi_profile(items: Iterable[Any]) -> Dict[str, Any]:
    pois = normalize_pois(items)
    categories: Dict[str, Dict[str, Any]] = {}
    for poi in pois:
        category = poi["category"]
        current = categories.setdefault(
            category,
            {
                "category": category,
                "label": CATEGORY_LABELS[category],
                "count": 0,
                "nearest_meters": None,
                "within_300m": 0,
                "within_800m": 0,
                "within_1500m": 0,
                "examples": [],
            },
        )
        distance = poi["distance_meters"]
        current["count"] += 1
        current["nearest_meters"] = (
            distance if current["nearest_meters"] is None else min(current["nearest_meters"], distance)
        )
        current["within_300m"] += int(distance <= 300)
        current["within_800m"] += int(distance <= 800)
        current["within_1500m"] += int(distance <= 1500)
        if len(current["examples"]) < 3:
            current["examples"].append("{}（{}m）".format(poi["name"], distance))
    return {
        "total_pois": len(pois),
        "distinct_categories": len([key for key in categories if key != "other"]),
        "categories": sorted(categories.values(), key=lambda item: item["category"]),
    }


def _proximity_delta(nearest: Any, values: tuple) -> float:
    if nearest is None:
        return 0.0
    if nearest <= 300:
        return values[0]
    if nearest <= 800:
        return values[1]
    if nearest <= 1500:
        return values[2]
    return 0.0


def calculate_rule_baseline(profile: Dict[str, Any]) -> float:
    by_category = {item["category"]: item for item in profile.get("categories", [])}
    score = 2.5
    for category, deltas in DISTANCE_DELTAS.items():
        score += _proximity_delta(by_category.get(category, {}).get("nearest_meters"), deltas)
    if by_category.get("nightlife", {}).get("within_800m", 0) >= 4:
        score -= 0.18
    if by_category.get("road_risk", {}).get("within_800m", 0) >= 2:
        score -= 0.20
    if by_category.get("police", {}).get("within_1500m", 0) >= 2:
        score += 0.10
    if by_category.get("transport", {}).get("within_800m", 0) >= 2:
        score += 0.08
    return round_half_up(clamp(score, 0.5, 4.8), 1)


def calculate_coverage_confidence(profile: Dict[str, Any]) -> float:
    total = float(profile.get("total_pois", 0))
    category_count = float(profile.get("distinct_categories", 0))
    return round_half_up(clamp(0.2 + total * 0.025 + category_count * 0.09, 0.2, 0.92), 2)


def risk_level_for_score(score: float) -> str:
    if score >= 2.8:
        return "相对较安全"
    if score >= 2.3:
        return "需留意"
    return "高风险"


def combine_environment_assessment(rule_score: float, model: Dict[str, Any], coverage: float) -> Dict[str, Any]:
    model_score = clamp(float(model["model_score"]), 0, 5)
    model_confidence = clamp(float(model.get("confidence", 0.5)), 0, 1)
    score = round_half_up(clamp(rule_score * 0.45 + model_score * 0.55, 0, 5), 1)
    return {
        "score": score,
        "risk_level": risk_level_for_score(score),
        "confidence": round_half_up(min(model_confidence, coverage), 2),
        "summary": clean_text(model.get("summary") or "模型未提供摘要。", 600),
        "safety_factors": [clean_text(item, 160) for item in model.get("safety_factors", [])[:4]],
        "risk_factors": [clean_text(item, 160) for item in model.get("risk_factors", [])[:4]],
        "limitations": [clean_text(item, 160) for item in model.get("limitations", [])[:3]],
        "audit_status": "environment_scored",
    }


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    radius = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return round(radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)))
