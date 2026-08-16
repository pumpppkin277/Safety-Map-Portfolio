import hashlib
import re
from typing import Any, Dict, Iterable, List, Tuple

from backend.app.services.environment import clamp, clean_text

SOURCE_BASE_WEIGHT = {
    "official": 0.95,
    "media": 0.80,
    "ota": 0.72,
    "map": 0.68,
    "social": 0.52,
    "unknown": 0.35,
}


def _fingerprint(item: Dict[str, Any]) -> str:
    url = clean_text(item.get("url"), 1500).lower().rstrip("/")
    if url:
        return "url:" + url
    claim = re.sub(r"\W+", "", clean_text(item.get("claim"), 4000).lower())
    return "claim:" + hashlib.sha256(claim.encode("utf-8")).hexdigest()


def _evidence_id(item: Dict[str, Any]) -> str:
    supplied = clean_text(item.get("evidence_id"), 80)
    if supplied:
        return supplied
    return "E-" + hashlib.sha256(_fingerprint(item).encode("utf-8")).hexdigest()[:12].upper()


def evidence_weight(item: Dict[str, Any]) -> float:
    source_type = clean_text(item.get("source_type"), 40).lower() or "unknown"
    base = SOURCE_BASE_WEIGHT.get(source_type, SOURCE_BASE_WEIGHT["unknown"])
    link_factor = 1.0 if clean_text(item.get("url"), 1500) else 0.78
    time_factor = 1.0 if clean_text(item.get("published_at"), 80) else 0.88
    claim_factor = 1.0 if len(clean_text(item.get("claim"), 4000)) >= 24 else 0.82
    return round(clamp(base * link_factor * time_factor * claim_factor, 0.2, 0.98), 2)


def normalize_evidence(items: Iterable[Any]) -> List[Dict[str, Any]]:
    deduplicated: Dict[str, Dict[str, Any]] = {}
    for raw in list(items or [])[:80]:
        item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        claim = clean_text(item.get("claim"), 4000)
        if not claim:
            continue
        item["evidence_id"] = _evidence_id(item)
        item["claim"] = claim
        item["weight"] = evidence_weight(item)
        key = _fingerprint(item)
        previous = deduplicated.get(key)
        if previous is None or item["weight"] > previous["weight"]:
            deduplicated[key] = item
    return list(deduplicated.values())


def evidence_sufficiency(items: List[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    source_count = len({clean_text(item.get("platform") or item.get("source_type"), 120).lower() for item in items})
    verifiable_count = sum(bool(clean_text(item.get("url"), 1500)) for item in items)
    weighted_total = round(sum(float(item.get("weight", 0)) for item in items), 2)
    coverage = round(
        clamp(
            0.12 + len(items) * 0.10 + source_count * 0.10 + verifiable_count * 0.08 + weighted_total * 0.06, 0.12, 0.92
        ),
        2,
    )
    reasons = []
    if len(items) < 2:
        reasons.append("有效证据少于 2 条")
    if source_count < 2:
        reasons.append("独立来源少于 2 个")
    if verifiable_count < 1:
        reasons.append("缺少可访问的来源链接")
    if weighted_total < 1.2:
        reasons.append("加权证据总量不足")
    return not reasons, {
        "evidence_count": len(items),
        "source_count": source_count,
        "verifiable_count": verifiable_count,
        "weighted_total": weighted_total,
        "coverage_confidence": coverage,
        "reasons": reasons,
    }
