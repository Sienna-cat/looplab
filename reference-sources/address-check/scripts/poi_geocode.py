"""高德 Web API 地理编码客户端：地址 → (province, city, district)。

用途：作为 batch_verify.py 的 R8 阶段，对仅含 POI/路名/小区的地址做反查，
补全无法通过 GB/T 2260 行政区划库或镇/街道库定位的区/县。

特点：
- 文件缓存 `data/amap_cache.json`：键为 (addr|city) MD5，避免重复请求。
- 简单 QPS 限流（默认 3 QPS，配合免费版限额）。
- 失败/无结果/key 缺失时返回 None，不影响主流程。

API 文档: https://lbs.amap.com/api/webservice/guide/api/georegeo
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ENDPOINT_GEO = "https://restapi.amap.com/v3/geocode/geo"
DEFAULT_CACHE = Path(__file__).parent.parent / "data" / "amap_cache.json"


class AmapClient:
    def __init__(
        self,
        key: str,
        qps: float = 3.0,
        cache_file: Path | None = DEFAULT_CACHE,
        timeout: float = 8.0,
    ) -> None:
        self.key = key
        self.qps = max(0.5, qps)
        self.cache_file = cache_file
        self.timeout = timeout
        self._last_call: float = 0.0
        self._cache: dict[str, Any] = {}
        self._dirty = False
        if cache_file and cache_file.exists():
            try:
                self._cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    @staticmethod
    def _cache_key(addr: str, city: str) -> str:
        return hashlib.md5(f"{addr}|{city}".encode("utf-8")).hexdigest()[:16]

    def _throttle(self) -> None:
        gap = 1.0 / self.qps
        elapsed = time.time() - self._last_call
        wait = gap - elapsed
        if wait > 0:
            time.sleep(wait)

    def geocode(self, addr: str, hint_city: str = "") -> dict[str, Any] | None:
        """高德 geocode。返回 dict 或 None。

        dict 字段:
          province / city / district / adcode / level / location / formatted_address
        """
        if not addr or not self.key:
            return None
        ck = self._cache_key(addr, hint_city)
        if ck in self._cache:
            return self._cache[ck]

        self._throttle()
        params = {
            "key": self.key,
            "address": addr,
            "output": "json",
        }
        if hint_city:
            params["city"] = hint_city
        url = ENDPOINT_GEO + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "addr-verify/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            self._last_call = time.time()
            return None
        self._last_call = time.time()

        if payload.get("status") != "1" or not payload.get("geocodes"):
            self._cache[ck] = None
            self._dirty = True
            self.flush()
            return None

        g = payload["geocodes"][0]

        def _str(v: Any) -> str:
            return v if isinstance(v, str) else ""

        result = {
            "province": _str(g.get("province")),
            "city": _str(g.get("city")),
            "district": _str(g.get("district")),
            "adcode": _str(g.get("adcode")),
            "level": _str(g.get("level")),
            "location": _str(g.get("location")),
            "formatted_address": _str(g.get("formatted_address")),
        }
        self._cache[ck] = result
        self._dirty = True
        self.flush()
        return result

    def geocode_batch(self, addrs: list[str], hint_city: str = "") -> list[dict[str, Any] | None]:
        """批量 geocode (高德 batch=true)：单次最多 10 个地址，address 用 | 分隔。

        返回与 addrs 同序的结果列表。缓存命中的项不占用配额。
        失败时该项返回 None，不影响其他地址。

        注意: 批量模式下 hint_city 对所有地址共享；混合 city 的请求应分批调用。
        """
        if not addrs or not self.key:
            return [None] * len(addrs)
        results: list[dict[str, Any] | None] = [None] * len(addrs)
        # 先查缓存
        need_indices: list[int] = []
        need_addrs: list[str] = []
        for i, a in enumerate(addrs):
            if not a:
                continue
            ck = self._cache_key(a, hint_city)
            if ck in self._cache:
                results[i] = self._cache[ck]
            else:
                need_indices.append(i)
                need_addrs.append(a)
        # 按 10 个一批调高德
        for batch_start in range(0, len(need_addrs), 10):
            batch_addrs = need_addrs[batch_start : batch_start + 10]
            batch_indices = need_indices[batch_start : batch_start + 10]
            self._throttle()
            params = {
                "key": self.key,
                "address": "|".join(batch_addrs),
                "batch": "true",
                "output": "json",
            }
            if hint_city:
                params["city"] = hint_city
            url = ENDPOINT_GEO + "?" + urllib.parse.urlencode(params)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "addr-verify/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self._last_call = time.time()
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                self._last_call = time.time()
                # 整批失败：留空，下次单条 fallback
                continue
            if payload.get("status") != "1":
                continue
            geocodes_list = payload.get("geocodes") or []

            def _str(v: Any) -> str:
                return v if isinstance(v, str) else ""

            for j, idx in enumerate(batch_indices):
                if j >= len(geocodes_list):
                    break
                g = geocodes_list[j]
                if not g:
                    # 单条无结果：缓存 None
                    ck = self._cache_key(batch_addrs[j], hint_city)
                    self._cache[ck] = None
                    self._dirty = True
                    continue
                r = {
                    "province": _str(g.get("province")),
                    "city": _str(g.get("city")),
                    "district": _str(g.get("district")),
                    "adcode": _str(g.get("adcode")),
                    "level": _str(g.get("level")),
                    "location": _str(g.get("location")),
                    "formatted_address": _str(g.get("formatted_address")),
                }
                ck = self._cache_key(batch_addrs[j], hint_city)
                self._cache[ck] = r
                self._dirty = True
                results[idx] = r
        if self._dirty:
            self.flush()
        return results

    def prefetch(self, addr_hints: list[tuple[str, str]]) -> int:
        """批量预热缓存：addr_hints = [(addr, hint_city), ...]。

        按 hint_city 分组，每组用 geocode_batch 串行调用 (减少 QPS 占用)。
        返回新增缓存条目数。
        """
        if not addr_hints or not self.key:
            return 0
        # 按 hint_city 分组，过滤已缓存
        groups: dict[str, list[str]] = {}
        new_count = 0
        for addr, hint in addr_hints:
            if not addr:
                continue
            ck = self._cache_key(addr, hint)
            if ck in self._cache:
                continue
            groups.setdefault(hint, []).append(addr)
            new_count += 1
        for hint, addrs in groups.items():
            # 去重保持顺序
            seen: set[str] = set()
            unique = [a for a in addrs if not (a in seen or seen.add(a))]
            self.geocode_batch(unique, hint_city=hint)
        return new_count

    def flush(self) -> None:
        if not self._dirty or not self.cache_file:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(self.cache_file)
        self._dirty = False


def from_env(key: str = "", cache_file: Path | None = DEFAULT_CACHE, qps: float = 3.0) -> AmapClient | None:
    """构造客户端：优先用入参 key，否则读 AMAP_KEY 环境变量。无 key 返回 None。"""
    import os

    k = key or os.environ.get("AMAP_KEY", "").strip()
    if not k:
        return None
    return AmapClient(k, qps=qps, cache_file=cache_file)


if __name__ == "__main__":
    import sys

    client = from_env()
    if not client:
        print("缺 AMAP_KEY，无法测试。export AMAP_KEY=xxx 后重试。", file=sys.stderr)
        sys.exit(1)
    for addr in (
        "广西南宁市英华路一品尊府27栋",
        "江苏常州市工人新村59栋B单元",
        "湖南怀化市洪江区前湾税务局",
        "陕西西安市宋日城街港之城小区",
    ):
        r = client.geocode(addr)
        print(f"{addr!r:40s} -> {r}")
