import json
from typing import Any, Dict

import httpx

from backend.app.config import Settings


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport = None):
        self.settings = settings
        self.transport = transport

    async def json_completion(
        self, system_prompt: str, payload: Dict[str, Any], max_tokens: int = 1800
    ) -> Dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY 未配置")
        request_body = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "请只输出 JSON。输入如下：\n{}".format(json.dumps(payload, ensure_ascii=False)),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        timeout = httpx.Timeout(60.0, connect=15.0)
        async with httpx.AsyncClient(
            base_url=self.settings.deepseek_base_url, timeout=timeout, transport=self.transport
        ) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": "Bearer {}".format(self.settings.deepseek_api_key)},
                json=request_body,
            )
        if response.status_code >= 400:
            raise DeepSeekError("DeepSeek 请求失败（{}）：{}".format(response.status_code, response.text[:300]))
        try:
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise DeepSeekError("DeepSeek 未返回可解析的 JSON") from exc
        if not isinstance(result, dict):
            raise DeepSeekError("DeepSeek JSON 顶层必须是对象")
        return result

    async def environment_assessment(
        self, hotel: Dict[str, Any], profile: Dict[str, Any], rule_score: float
    ) -> Dict[str, Any]:
        prompt = (
            "你是酒店周边环境初评模型。只能依据输入中的酒店基础信息、POI 分类、距离、密度画像和规则基线判断，"
            "不得补写犯罪率、酒店内部管理或未提供的事实。请输出 JSON：model_score(0-5)、confidence(0-1)、"
            "summary、safety_factors(数组)、risk_factors(数组)、limitations(数组)。"
            "没有足够数据时降低 confidence，并明确限制。"
        )
        result = await self.json_completion(
            prompt, {"hotel": hotel, "poi_profile": profile, "rule_baseline": rule_score}, 1200
        )
        try:
            result["model_score"] = round(min(5.0, max(0.0, float(result["model_score"]))), 1)
            result["confidence"] = round(min(1.0, max(0.0, float(result.get("confidence", 0.5)))), 2)
        except (KeyError, TypeError, ValueError) as exc:
            raise DeepSeekError("环境初评缺少有效分数或置信度") from exc
        for key in ("safety_factors", "risk_factors", "limitations"):
            if not isinstance(result.get(key), list):
                result[key] = []
        return result

    async def deep_audit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (
            "你是证据约束型酒店安全审计模型。只能依据输入中的结构化公开证据、环境画像和证据权重判断，"
            "不得补写输入中没有的事实，也不得把普通服务态度、一般噪音或一般卫生差评夸大为人身安全事件。"
            "每个具体判断必须列出输入中存在的 evidence_id。请输出 JSON：audit_confidence(0-1)、risk_level、"
            "risk_tags(数组)、conclusion、advice、negative_comments、dimensions(数组)。dimensions 必须恰好包含 "
            "hotel_intrinsic_safety、guest_review_safety、surrounding_environment、fire_health_management、"
            "data_confidence；每项包含 dimension、score(0-5)、reason、evidence_ids(数组)。"
            "不要输出总分，服务端会按固定权重计算。"
        )
        return await self.json_completion(prompt, payload, 2600)
