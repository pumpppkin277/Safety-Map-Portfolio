import hashlib
import re
from typing import Any, Dict, List

import httpx

from backend.app.config import Settings
from backend.app.services.environment import haversine_meters

HOTEL_KEYWORDS = ["酒店", "宾馆", "旅馆", "民宿", "青年旅舍", "公寓酒店"]
ENVIRONMENT_QUERIES = {
    "police": ["派出所", "公安局", "警务站"],
    "medical": ["医院", "急救中心", "诊所"],
    "transport": ["地铁站", "公交站", "客运站", "火车站"],
    "convenience": ["便利店", "超市", "商场"],
    "nightlife": ["酒吧", "KTV", "夜店"],
    "road_risk": ["停车场", "高速入口", "主干道"],
}


def normalize_identity(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower())


def hotel_dedupe_key(item: Dict[str, Any]) -> str:
    map_id = str(item.get("map_poi_id") or item.get("id") or "").strip()
    if map_id:
        return "map:" + map_id
    raw = "{}|{}".format(normalize_identity(item.get("name", "")), normalize_identity(item.get("address", "")))
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


class TencentMapError(RuntimeError):
    pass


class TencentMapClient:
    base_url = "https://apis.map.qq.com"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport = None):
        self.settings = settings
        self.transport = transport

    async def _search(
        self, keyword: str, boundary: str, page_index: int = 1, page_size: int = 20
    ) -> List[Dict[str, Any]]:
        if not self.settings.tencent_map_key:
            raise TencentMapError("TENCENT_MAP_KEY 未配置")
        params = {
            "keyword": keyword,
            "boundary": boundary,
            "page_size": min(page_size, 20),
            "page_index": page_index,
            "key": self.settings.tencent_map_key,
        }
        if boundary.startswith("nearby("):
            params["orderby"] = "_distance"
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30, transport=self.transport) as client:
            response = await client.get("/ws/place/v1/search", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 0:
            raise TencentMapError("腾讯地图搜索失败：{}".format(payload.get("message") or payload.get("status")))
        return payload.get("data") or []

    async def discover_hotels(self, city_name: str, max_pages: int = 3) -> List[Dict[str, Any]]:
        results = []
        seen = set()
        for keyword in HOTEL_KEYWORDS:
            for page in range(1, max_pages + 1):
                items = await self._search(keyword, "region({},0)".format(city_name), page)
                if not items:
                    break
                for item in items:
                    normalized = self._normalize_place(item)
                    key = hotel_dedupe_key(normalized)
                    if key not in seen and normalized.get("longitude") is not None:
                        seen.add(key)
                        normalized["dedupe_key"] = key
                        results.append(normalized)
        return results

    async def nearby_environment(self, latitude: float, longitude: float) -> List[Dict[str, Any]]:
        results = []
        for category, keywords in ENVIRONMENT_QUERIES.items():
            category_items = {}
            for keyword in keywords:
                items = await self._search(
                    keyword,
                    "nearby({},{},{})".format(latitude, longitude, self.settings.map_radius_meters),
                    page_size=20,
                )
                for item in items:
                    normalized = self._normalize_place(item)
                    source_id = normalized.get("map_poi_id") or hotel_dedupe_key(normalized)
                    if source_id in category_items:
                        continue
                    if normalized.get("latitude") is None:
                        continue
                    normalized.update(
                        {
                            "category": category,
                            "source_poi_id": source_id,
                            "source": "tencent_map",
                            "distance_meters": int(
                                item.get("_distance")
                                or haversine_meters(
                                    latitude, longitude, normalized["latitude"], normalized["longitude"]
                                )
                            ),
                        }
                    )
                    category_items[source_id] = normalized
            ranked = sorted(category_items.values(), key=lambda item: item["distance_meters"])
            results.extend(ranked[: self.settings.poi_limit_per_category])
        return results

    @staticmethod
    def _normalize_place(item: Dict[str, Any]) -> Dict[str, Any]:
        location = item.get("location") or {}
        return {
            "map_poi_id": str(item.get("id") or ""),
            "name": str(item.get("title") or "").strip(),
            "address": str(item.get("address") or "").strip(),
            "phone": str(item.get("tel") or "").strip(),
            "longitude": location.get("lng"),
            "latitude": location.get("lat"),
        }
