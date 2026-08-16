const CATEGORY_ALIASES = new Map([
  ['police', 'police'],
  ['公安', 'police'],
  ['派出所', 'police'],
  ['medical', 'medical'],
  ['医院', 'medical'],
  ['医疗', 'medical'],
  ['transport', 'transport'],
  ['交通', 'transport'],
  ['地铁', 'transport'],
  ['convenience', 'convenience'],
  ['便利', 'convenience'],
  ['商超', 'convenience'],
  ['nightlife', 'nightlife'],
  ['夜生活', 'nightlife'],
  ['酒吧', 'nightlife'],
  ['road_risk', 'road_risk'],
  ['道路风险', 'road_risk'],
  ['偏僻道路', 'road_risk'],
]);

const CATEGORY_LABELS = {
  police: '公安与治安设施',
  medical: '医疗设施',
  transport: '公共交通',
  convenience: '生活便利设施',
  nightlife: '夜生活场所',
  road_risk: '道路与偏僻风险',
  other: '其他地点',
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function round(value, digits = 2) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function safeText(value, maxLength = 120) {
  return String(value ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxLength);
}

function normalizeCategory(value) {
  const raw = safeText(value, 40).toLowerCase();
  if (CATEGORY_ALIASES.has(raw)) return CATEGORY_ALIASES.get(raw);
  for (const [alias, category] of CATEGORY_ALIASES.entries()) {
    if (raw.includes(alias)) return category;
  }
  return 'other';
}

export function normalizePois(input) {
  if (!Array.isArray(input)) return [];
  return input.slice(0, 80).flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const distance = Number(item.distance_meters ?? item.distance ?? item.distanceMeters);
    if (!Number.isFinite(distance) || distance < 0 || distance > 10_000) return [];
    return [{
      name: safeText(item.name || item.title || item.category || '未命名地点'),
      category: normalizeCategory(item.category || item.type || ''),
      distance_meters: Math.round(distance),
    }];
  });
}

export function buildPoiProfile(input) {
  const pois = normalizePois(input);
  const categories = {};

  for (const poi of pois) {
    const current = categories[poi.category] || {
      category: poi.category,
      label: CATEGORY_LABELS[poi.category],
      count: 0,
      nearest_meters: null,
      within_300m: 0,
      within_800m: 0,
      within_1500m: 0,
      examples: [],
    };
    current.count += 1;
    current.nearest_meters = current.nearest_meters === null
      ? poi.distance_meters
      : Math.min(current.nearest_meters, poi.distance_meters);
    if (poi.distance_meters <= 300) current.within_300m += 1;
    if (poi.distance_meters <= 800) current.within_800m += 1;
    if (poi.distance_meters <= 1500) current.within_1500m += 1;
    if (current.examples.length < 3) {
      current.examples.push(`${poi.name}（${poi.distance_meters}m）`);
    }
    categories[poi.category] = current;
  }

  return {
    total_pois: pois.length,
    distinct_categories: Object.keys(categories).filter((key) => key !== 'other').length,
    categories: Object.values(categories).sort((a, b) => a.category.localeCompare(b.category)),
  };
}

function proximityDelta(nearest, near, medium, far) {
  if (!Number.isFinite(nearest)) return 0;
  if (nearest <= 300) return near;
  if (nearest <= 800) return medium;
  if (nearest <= 1500) return far;
  return 0;
}

export function calculateRuleBaseline(profile) {
  const byCategory = Object.fromEntries((profile?.categories || []).map((item) => [item.category, item]));
  let score = 2.5;

  score += proximityDelta(byCategory.police?.nearest_meters, 0.62, 0.42, 0.2);
  score += proximityDelta(byCategory.medical?.nearest_meters, 0.42, 0.28, 0.12);
  score += proximityDelta(byCategory.transport?.nearest_meters, 0.28, 0.18, 0.08);
  score += proximityDelta(byCategory.convenience?.nearest_meters, 0.22, 0.14, 0.06);
  score -= proximityDelta(byCategory.nightlife?.nearest_meters, 0.52, 0.34, 0.14);
  score -= proximityDelta(byCategory.road_risk?.nearest_meters, 0.68, 0.44, 0.2);

  if ((byCategory.nightlife?.within_800m || 0) >= 4) score -= 0.18;
  if ((byCategory.road_risk?.within_800m || 0) >= 2) score -= 0.2;
  if ((byCategory.police?.within_1500m || 0) >= 2) score += 0.1;
  if ((byCategory.transport?.within_800m || 0) >= 2) score += 0.08;

  return round(clamp(score, 0.5, 4.8), 1);
}

export function calculateCoverageConfidence(profile) {
  const total = Number(profile?.total_pois || 0);
  const categories = Number(profile?.distinct_categories || 0);
  return round(clamp(0.2 + total * 0.025 + categories * 0.09, 0.2, 0.92), 2);
}

function stringList(value, maxItems = 4) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => safeText(item, 100)).filter(Boolean).slice(0, maxItems);
}

export function normalizeModelAssessment(raw) {
  const modelScore = clamp(Number(raw?.model_score), 0, 5);
  if (!Number.isFinite(modelScore)) throw new Error('模型没有返回有效的 model_score');
  const confidence = clamp(Number(raw?.confidence), 0, 1);
  return {
    model_score: round(modelScore, 1),
    confidence: Number.isFinite(confidence) ? round(confidence, 2) : 0.5,
    summary: safeText(raw?.summary, 260) || '模型未提供摘要。',
    safety_factors: stringList(raw?.safety_factors),
    risk_factors: stringList(raw?.risk_factors),
    limitations: stringList(raw?.limitations, 3),
  };
}

export function riskLevelForScore(score) {
  if (score >= 2.8) return '相对较安全';
  if (score >= 2.3) return '需留意';
  return '高风险';
}

export function combineAssessment(ruleBaseline, modelAssessment, coverageConfidence) {
  const score = round(clamp(ruleBaseline * 0.45 + modelAssessment.model_score * 0.55, 0, 5), 1);
  return {
    score,
    risk_level: riskLevelForScore(score),
    confidence: round(Math.min(modelAssessment.confidence, coverageConfidence), 2),
    summary: modelAssessment.summary,
    safety_factors: modelAssessment.safety_factors,
    risk_factors: modelAssessment.risk_factors,
    limitations: modelAssessment.limitations,
    audit_status: 'environment_scored',
  };
}

export function sanitizeHotel(input) {
  return {
    id: Number.isFinite(Number(input?.id)) ? Number(input.id) : null,
    name: safeText(input?.name || '未命名酒店'),
    address: safeText(input?.address || '', 180),
    city: safeText(input?.city || input?.city_name || '', 80),
  };
}
