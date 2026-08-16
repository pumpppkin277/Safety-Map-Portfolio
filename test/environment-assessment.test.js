import test from 'node:test';
import assert from 'node:assert/strict';

import environmentAssessmentHandler from '../api/environment-assessment.js';
import {
  buildPoiProfile,
  calculateCoverageConfidence,
  calculateRuleBaseline,
  combineAssessment,
  normalizeModelAssessment,
  normalizePois,
  riskLevelForScore,
} from '../lib/environment-assessment.js';

function invokeHandler(body, options = {}) {
  const headers = {};
  const response = {
    statusCode: 200,
    payload: undefined,
    setHeader(name, value) { headers[name] = value; },
    status(code) { this.statusCode = code; return this; },
    json(payload) { this.payload = payload; return this; },
    end() { return this; },
  };
  const request = {
    method: options.method || 'POST',
    headers: options.headers || {},
    body,
  };
  return environmentAssessmentHandler(request, response).then(() => ({ response, headers }));
}

test('normalizes and limits client POI input', () => {
  const pois = normalizePois([
    { name: '静安派出所', category: '派出所', distance_meters: 280 },
    { name: '无效', category: 'other', distance_meters: -1 },
    null,
  ]);
  assert.deepEqual(pois, [{ name: '静安派出所', category: 'police', distance_meters: 280 }]);
});

test('builds an explainable profile and rule baseline', () => {
  const profile = buildPoiProfile([
    { name: '派出所', category: 'police', distance_meters: 260 },
    { name: '地铁站', category: 'transport', distance_meters: 420 },
    { name: '医院', category: 'medical', distance_meters: 900 },
    { name: '酒吧', category: 'nightlife', distance_meters: 1200 },
  ]);
  assert.equal(profile.total_pois, 4);
  assert.equal(profile.distinct_categories, 4);
  assert.equal(calculateRuleBaseline(profile), 3.3);
  assert.ok(calculateCoverageConfidence(profile) > 0.5);
});

test('normalizes DeepSeek output and fuses model with rules', () => {
  const model = normalizeModelAssessment({
    model_score: 3.8,
    confidence: 0.81,
    summary: '交通与治安设施较近，夜生活风险可控。',
    safety_factors: ['派出所较近'],
    risk_factors: ['附近有夜生活场所'],
    limitations: ['只覆盖地图 POI'],
  });
  const result = combineAssessment(3.2, model, 0.72);
  assert.equal(result.score, 3.5);
  assert.equal(result.risk_level, '相对较安全');
  assert.equal(result.confidence, 0.72);
  assert.equal(result.audit_status, 'environment_scored');
});

test('risk labels follow the product score bands', () => {
  assert.equal(riskLevelForScore(2.8), '相对较安全');
  assert.equal(riskLevelForScore(2.3), '需留意');
  assert.equal(riskLevelForScore(2.2), '高风险');
});

test('rejects malformed model scores', () => {
  assert.throws(() => normalizeModelAssessment({ model_score: 'not-a-number' }), /model_score/);
});

test('API rejects an assessment without usable POIs before calling a model', async () => {
  const { response } = await invokeHandler({ hotel: { name: '测试酒店' }, pois: [] });
  assert.equal(response.statusCode, 422);
  assert.equal(response.payload.audit_status, 'evidence_insufficient');
});

test('API keeps the DeepSeek key server-side and supports configured CORS', async () => {
  const previousKey = process.env.DEEPSEEK_API_KEY;
  const previousOrigins = process.env.ALLOWED_ORIGINS;
  delete process.env.DEEPSEEK_API_KEY;
  process.env.ALLOWED_ORIGINS = 'https://example.com';
  try {
    const { response, headers } = await invokeHandler({
      hotel: { name: '测试酒店' },
      pois: [{ name: '派出所', category: 'police', distance_meters: 300 }],
    }, { headers: { origin: 'https://example.com' } });
    assert.equal(response.statusCode, 503);
    assert.match(response.payload.error, /DEEPSEEK_API_KEY/);
    assert.equal(headers['Access-Control-Allow-Origin'], 'https://example.com');
  } finally {
    if (previousKey === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = previousKey;
    if (previousOrigins === undefined) delete process.env.ALLOWED_ORIGINS;
    else process.env.ALLOWED_ORIGINS = previousOrigins;
  }
});

test('API calls DeepSeek JSON mode and returns a fused assessment', async () => {
  const previousKey = process.env.DEEPSEEK_API_KEY;
  const previousFetch = globalThis.fetch;
  let capturedRequest;
  process.env.DEEPSEEK_API_KEY = 'test-key';
  globalThis.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({
      choices: [{
        message: {
          content: JSON.stringify({
            model_score: 3.6,
            confidence: 0.76,
            summary: '公共安全和交通设施可达，附近夜生活场所需要留意。',
            safety_factors: ['派出所距离较近'],
            risk_factors: ['存在夜生活场所'],
            limitations: ['仅覆盖地图 POI'],
          }),
        },
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  try {
    const { response } = await invokeHandler({
      hotel: { id: 7, name: '测试酒店', address: '测试路 1 号' },
      pois: [
        { name: '派出所', category: 'police', distance_meters: 260 },
        { name: '地铁站', category: 'transport', distance_meters: 500 },
        { name: '酒吧', category: 'nightlife', distance_meters: 900 },
      ],
    });
    const outbound = JSON.parse(capturedRequest.options.body);
    assert.equal(response.statusCode, 200);
    assert.equal(response.payload.assessment.audit_status, 'environment_scored');
    assert.equal(response.payload.trace.provider, 'deepseek');
    assert.equal(outbound.response_format.type, 'json_object');
    assert.equal(capturedRequest.options.headers.Authorization, 'Bearer test-key');
  } finally {
    globalThis.fetch = previousFetch;
    if (previousKey === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = previousKey;
  }
});
