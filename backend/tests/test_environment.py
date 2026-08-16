from backend.app.services.environment import (
    build_poi_profile,
    calculate_coverage_confidence,
    calculate_rule_baseline,
    combine_environment_assessment,
    risk_level_for_score,
)


def test_rule_baseline_matches_documented_v0_weights():
    profile = build_poi_profile(
        [
            {"name": "派出所", "category": "police", "distance_meters": 260},
            {"name": "地铁站", "category": "transport", "distance_meters": 420},
            {"name": "医院", "category": "medical", "distance_meters": 900},
            {"name": "酒吧", "category": "nightlife", "distance_meters": 1200},
        ]
    )
    assert profile["total_pois"] == 4
    assert profile["distinct_categories"] == 4
    assert calculate_rule_baseline(profile) == 3.3
    assert calculate_coverage_confidence(profile) > 0.5


def test_environment_fusion_and_map_thresholds():
    result = combine_environment_assessment(
        3.2,
        {
            "model_score": 3.8,
            "confidence": 0.81,
            "summary": "交通与治安设施较近。",
            "safety_factors": ["派出所较近"],
            "risk_factors": ["附近有夜生活场所"],
            "limitations": ["只覆盖地图 POI"],
        },
        0.72,
    )
    assert result["score"] == 3.5
    assert result["confidence"] == 0.72
    assert result["audit_status"] == "environment_scored"
    assert risk_level_for_score(2.8) == "相对较安全"
    assert risk_level_for_score(2.3) == "需留意"
    assert risk_level_for_score(2.2) == "高风险"
