"""版本号与 GitHub 更新检查（带缓存，避免频繁请求 GitHub API）。"""
from __future__ import annotations

import json
import time

APP_VERSION = "1.12.256"
VERSION_CHECK_TTL = 6 * 3600  # 6 小时
GITHUB_REPO = "icekale/vpush"


def _version_key(version: str) -> list[int]:
    try:
        return [int(x) for x in (version or "").split(".") if x.isdigit()]
    except (ValueError, TypeError):
        return []


def is_newer(version: str, base: str) -> bool:
    return _version_key(version) > _version_key(base)


def latest_github_version(db) -> tuple[str, bool]:
    """查询 GitHub 最新 v* 标签；带缓存，失败返回 (None, False)。"""
    cached = db.get_setting("version_check_cache")
    now = time.time()
    if cached:
        try:
            data = json.loads(cached)
            if now - float(data.get("checked_at") or 0) < VERSION_CHECK_TTL:
                latest = data.get("latest") or ""
                return latest, bool(latest)
        except (TypeError, ValueError):
            pass
    latest = ""
    try:
        import httpx

        resp = httpx.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/tags",
            timeout=10,
            headers={"User-Agent": "vpush", "Accept": "application/vnd.github+json"},
        )
        if resp.status_code == 200:
            versions = []
            for tag in resp.json() or []:
                name = (tag.get("name") or "").strip().lstrip("v")
                if name and name[0].isdigit():
                    versions.append(name)
            if versions:
                latest = max(versions, key=_version_key)
    except Exception:  # noqa: BLE001, S110 - 更新检查失败不影响使用
        pass
    db.set_setting("version_check_cache", json.dumps({"latest": latest, "checked_at": now}))
    return latest, bool(latest)
