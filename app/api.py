"""REST API：认证、订阅目录、我的动态、KOL/分类管理。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx

logger = logging.getLogger(__name__)

TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TOKEN_MAX_LEN = 2048


def verify_turnstile(*, secret: str, token: str, action: str, hostnames: set[str], ip: str = "") -> bool:
    """Canonical siteverify. Tokens are single-use; caller must reset the widget after each attempt."""
    if not isinstance(token, str) or not token or len(token) > TURNSTILE_TOKEN_MAX_LEN:
        return False
    payload = {"secret": secret, "response": token}
    if ip and ip != "unknown":
        payload["remoteip"] = ip
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(TURNSTILE_SITEVERIFY_URL, data=payload)
            resp.raise_for_status()
            result = resp.json()
    except Exception:
        return False
    return (
        result.get("success") is True
        and result.get("action") == action
        and result.get("hostname") in hostnames
    )


from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import auth, kol_requests, user_quota, wechat
from .avatar_cache import cache_avatar
from .bot_core import BIND_CODE_TTL, new_bind_code
from .db import _UNSET, ALLOWED_PLATFORMS, DB, days_until_purge, user_plain_secret
from .feishu_documents import (
    FeishuDocumentError,
    FeishuDocumentSyncService,
    parse_feishu_document_url,
)
from .fetchers.base import CN_TZ, PLATFORM_LABELS, apply_twitter_feed, strip_html
from .fetchers.combination import extract_cube_symbol, resolve_combination_profile
from .fetchers.ima import (
    IMA_API_KEY_KEY,
    IMA_CLIENT_ID_KEY,
    IMA_COOKIE_KEY,
    IMA_COOKIE_TIME_KEY,
)
from .fetchers.twitter import (
    TWITTER_COOKIE_KEY,
    TWITTER_COOKIE_TIME_KEY,
    resolve_x_profile,
)
from .fetchers.weibo import WEIBO_COOKIE_KEY, resolve_weibo_profile
from .fetchers.xueqiu import (
    XUEQIU_COOKIE_KEY,
    XUEQIU_COOKIE_TIME_KEY,
    resolve_profile,
    write_xueqiu_seed_cookie,
)
from .fetchers.zsxq import (
    DEFAULT_DELAY,
    DEFAULT_FILE_DELAY,
    ZSXQ_COOKIE_KEY,
    ZSXQ_COOKIE_TIME_KEY,
    _app_channel_enabled,
    _app_device,
    _comment_budget,
    _comments_enabled,
    _delay,
    _max_comment_pages,
    _max_pages,
    _ws_address,
    _ws_enabled,
    prefetch_enabled,
    purge_unreferenced_zsxq_files,
    resolve_zsxq_file_url,
    resolve_zsxq_profile,
    zsxq_cache_stats,
)
from .ima_digest import digest_view
from .ima_documents import (
    IMA_FOLDER_LIST_MAX_PAGES,
    IMA_MOUNT_FOLDER_ID_MAX,
    IMA_PURE_GROUPS_KEY,
    IMA_PURE_INTERVAL_KEY,
    IMA_PURE_INTERVAL_MAX,
    IMA_PURE_INTERVAL_MIN,
    IMA_PURE_KB_ID_KEY,
    IMA_PURE_REFRESH_TOKEN_KEY,
    IMA_PURE_ROOT_FOLDER_KEY,
    IMA_PURE_UID_KEY,
    ImaDocumentService,
    ImaPureClient,
    LocalLibraryInvalidMeta,
    _clamp_group_interval,
    _safe_error,
    ima_kb_valid_tags,
    normalize_ima_folder_item,
    purge_ima_document_tags,
)
from .ima_kb import (
    attach_catalog_acl,
    attach_catalog_summary,
    is_open_group,
    readable_group_ids,
)
from .ima_kb import catalog as ima_kb_catalog
from .market import MarketQuotes
from .news import (
    NewsInputError,
    NewsNotFound,
    NewsService,
    NewsUpstreamError,
    normalize_feed_url,
)
from .plaza import (
    filter_plaza_rows,
    is_plaza_hidden,
    plaza_hidden_platforms,
    plaza_source_rows,
    plaza_visible_platforms,
    set_plaza_visibility,
    user_timeline_platforms,
)
from .proxy import (
    ProxyRouter,
    extract_pool,
    import_proxies,
    probe_proxy,
    public_pool,
    public_proxy,
)
from .weibo_qr import create_qr, poll_qr

# 关键词提醒规则上限（每个用户）与单关键词长度上限
KEYWORDS_MAX_COUNT = 20
KEYWORDS_MAX_LENGTH = 50
REGISTER_NOTE_MAX = 40
REGISTER_EXPIRE_DAYS = frozenset({1, 7, 30})


def _cube_holdings_response(snap) -> dict:
    """兼容旧 list 快照与新 {holdings, cash} 快照。"""
    if not snap:
        return {"holdings": [], "cash": None, "updated_at": ""}
    payload = snap["payload"]
    if isinstance(payload, dict):
        return {
            "holdings": payload.get("holdings") or [],
            "cash": payload.get("cash"),
            "updated_at": snap["fetched_at"],
        }
    return {"holdings": payload or [], "cash": None, "updated_at": snap["fetched_at"]}


def _cube_nav_response(snap) -> dict:
    """兼容旧 list 快照与新 {series, benchmark} 快照。"""
    if not snap:
        return {"series": [], "benchmark": [], "updated_at": ""}
    payload = snap["payload"]
    if isinstance(payload, dict):
        return {
            "series": payload.get("series") or [],
            "benchmark": payload.get("benchmark") or [],
            "updated_at": snap["fetched_at"],
        }
    return {"series": payload or [], "benchmark": [], "updated_at": snap["fetched_at"]}


def _normalize_weibo_id(external_id: str) -> str:
    """微博主页链接（https://weibo.com/u/<uid>）提取 UID。"""
    match = re.search(r"weibo\.com/u/(\d+)", external_id)
    return match.group(1) if match else external_id


def _parse_batch_kol_line(line: str) -> tuple[str, str, str, str | None]:
    """批量导入单行解析：返回 (platform, external_id, nickname, error)。

    error 非空时本行失败。只认链接/组合码能识别出的平台（雪球主页/组合/微博/X/
    知识星球/ima）；纯数字 UID 或无法识别的 URL 不再回退默认平台。X 统一存 screen name。
    """
    nickname = ""
    external_id = ""
    platform = ""
    parse_error = None
    unrecognized = False
    for token in line.split():
        detected = kol_requests.detect_platform_from_link(token)
        if detected:
            ext, err = kol_requests.normalize_kol_request_input(detected, token)
            if err:
                parse_error = err
                continue
            platform, external_id, parse_error = detected, ext, None
            unrecognized = False
            continue
        if token.startswith(("http://", "https://")):
            unrecognized = True
            continue
        if token.isdigit():
            unrecognized = True
            continue
        nickname = f"{nickname} {token}".strip()
    if not external_id:
        if unrecognized:
            return "", "", nickname, "无法识别平台，请粘贴雪球/微博/X/知识星球主页链接"
        return "", "", nickname, parse_error or "未识别到链接或ID"
    if not platform:
        return "", "", nickname, parse_error or "无法识别平台，请粘贴主页链接"
    ext, err = kol_requests.normalize_kol_request_input(platform, external_id)
    if err:
        return platform, "", nickname, err
    return platform, ext, nickname, None


def _account_key(username: str) -> str:
    """账号锁定/失败计数的统一键：去空白 + casefold。

    登录大小写不敏感（COLLATE NOCASE），锁定键必须同样规范化，
    否则 Alice/alice 会用不同字典键分散失败计数、绕过账号锁定。
    """
    return (username or "").strip().casefold()


def _resolve_telegram_bot(token: str) -> tuple[str, str, str]:
    """验证用户自建 bot token：返回 (bot_username, chat_id, error)。

    自动通过 getUpdates 识别用户给自己 bot 发消息时的 chat_id，
    用户无需手动填写 chat_id。
    """

    try:
        with httpx.Client(timeout=15) as client:
            me = client.get(f"https://api.telegram.org/bot{token}/getMe")
            me.raise_for_status()
            me_data = me.json()
            if not me_data.get("ok"):
                return "", "", f"token 无效：{me_data.get('description', '未知错误')}"
            bot_username = (me_data.get("result") or {}).get("username") or ""
            updates = client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"limit": 5},
            )
            updates.raise_for_status()
            up_data = updates.json()
            if not up_data.get("ok"):
                return bot_username, "", (
                    f"获取会话失败：{up_data.get('description', '未知错误')}"
                )
            chat_ids = []
            for update in up_data.get("result") or []:
                msg = update.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id:
                    chat_ids.append(str(chat_id))
            if not chat_ids:
                return bot_username, "", "请先给你的机器人发一条消息（如 /start），再点保存"
            return bot_username, chat_ids[-1], ""
    except Exception as exc:  # noqa: BLE001
        return "", "", f"无法连接 Telegram：{exc}"


class RegisterIn(BaseModel):
    username: str
    password: str
    code: str = ""
    cf_turnstile_response: Annotated[str, Field(max_length=2048, alias="cf-turnstile-response")] = ""


class LoginIn(BaseModel):
    username: str
    password: str
    cf_turnstile_response: Annotated[str, Field(max_length=2048, alias="cf-turnstile-response")] = ""


class WechatLoginIn(BaseModel):
    code: str
    invite_code: str = ""


class WebPushKeys(BaseModel):
    p256dh: str
    auth: str


class WebPushIn(BaseModel):
    endpoint: str
    keys: WebPushKeys


class AndroidDeviceIn(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=4096)]
    provider: Literal["fcm", "huawei", "xiaomi", "oppo", "vivo", "meizu", "other"]
    device_model: Annotated[str, Field(max_length=128)] = ""
    app_version: Annotated[str, Field(max_length=64)] = ""


class NewsSeenIn(BaseModel):
    view_started_at: str


class NewsSettingsIn(BaseModel):
    enabled: bool | None = None
    visible: bool | None = None
    refresh_interval_minutes: int | None = None


class NewsSourceCreateIn(BaseModel):
    name: str


class NewsSourceUpdateIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class NewsFeedCreateIn(BaseModel):
    name: str
    url: str


class NewsFeedUpdateIn(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None


class NewsFeedValidateIn(BaseModel):
    url: str


class MeUpdate(BaseModel):
    telegram_chat_id: str | None = None
    telegram_bot_token: str | None = None
    feishu_open_id: str | None = None
    feishu_chat_id: str | None = None
    wecom_webhook: str | None = None
    bark_key: str | None = None
    notify_enabled: bool | None = None
    daily_report_enabled: bool | None = None
    translate_twitter: bool | None = None
    push_channels: str | None = None
    dnd_start: str | None = None
    dnd_end: str | None = None
    dnd_allow_favorite: bool | None = None
    keywords: list[str] | None = None
    keywords_match_reports: bool | None = None
    llm_api_base: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_api_format: str | None = None
    news_source_ids: list[int] | None = None


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


class KolIn(BaseModel):
    platform: str
    name: str
    external_id: str
    category_id: int | None = None
    priority: bool = False
    secondary: bool = False
    original_only: bool = False


class KolBatchIn(BaseModel):
    platform: str | None = None  # 兼容旧客户端；平台只从每行链接识别
    lines: str
    category_id: int | None = None
    priority: bool = False
    secondary: bool = False
    original_only: bool = False


class KolUpdate(BaseModel):
    name: str | None = None
    external_id: str | None = None
    enabled: bool | None = None
    category_id: int | None = None
    priority: bool | None = None
    secondary: bool | None = None
    is_private: bool | None = None
    visible_users: list[str] | None = None
    original_only: bool | None = None
    silent: bool | None = None
    recommend_weight: int | None = None


class KolBatchAction(BaseModel):
    ids: list[int]
    action: str  # enable|disable|priority|secondary|normal|category|delete
    value: bool | int | None = None


class UserBatchAction(BaseModel):
    ids: list[int]
    action: str  # enable_notify|disable_notify|delete


class InactiveUsersPolicyIn(BaseModel):
    inactive_after_days: int
    inactive_purge_after_days: int


class RegisterCodeBatchAction(BaseModel):
    codes: list[str]
    action: str  # revoke|delete


class CategoryIn(BaseModel):
    name: str


class TagRuleIn(BaseModel):
    tag: str
    keywords: list[str] = []


class TagAliasIn(BaseModel):
    alias: str
    stock: str


class TagVocabularyIn(BaseModel):
    tags: list[TagRuleIn] | None = None
    stock_names: list[str] | None = None
    stock_aliases: list[TagAliasIn] | None = None


class TagBackfillIn(BaseModel):
    mode: Literal["pending", "all"] = "pending"


class TagMaintainIn(BaseModel):
    backfill: Literal["none", "pending", "all"] = "none"


class KolRequestIn(BaseModel):
    platform: str
    external_id: str
    name: str = ""
    category_id: int | None = None


class RegisterCodeGenIn(BaseModel):
    count: int = 5
    note: str = ""
    expires_in_days: int | None = 7


class RegisterCodeNoteIn(BaseModel):
    note: str = ""


class PollingConfigIn(BaseModel):
    interval_seconds: int | None = None
    priority_interval_seconds: int | None = None
    # Truth 专属轮询间隔（0 = 跟随优先档）
    truth_interval_seconds: int | None = None
    digest_interval_seconds: int | None = None
    source_probe_interval_seconds: int | None = None
    cookie_keepalive_interval_seconds: int | None = None
    daily_report_hour: int | None = None
    translate_twitter_content: bool | None = None
    telegram_rich_messages: bool | None = None
    # 采集频率档位（无新帖自适应降频参数，后台可调即时生效）
    combination_base_seconds: int | None = None
    combination_idle_cap_seconds: int | None = None
    normal_idle_cap_seconds: int | None = None
    priority_idle_cap_seconds: int | None = None
    x_fallback_cap_seconds: int | None = None
    # 次要大V档位：降频采集 + 长周期合并推送
    secondary_interval_seconds: int | None = None
    secondary_idle_cap_seconds: int | None = None
    secondary_digest_interval_seconds: int | None = None
    secondary_min_digest_count: int | None = None
    zsxq_max_pages: int | None = None
    zsxq_fetch_delay_seconds: float | None = None
    zsxq_file_delay_seconds: float | None = None
    zsxq_prefetch_files: bool | None = None
    zsxq_fetch_comments: bool | None = None
    zsxq_max_comment_pages: int | None = None
    zsxq_comment_budget: int | None = None
    zsxq_app_channel: bool | None = None
    zsxq_app_device: str | None = None
    zsxq_ws_enabled: bool | None = None
    zsxq_ws_address: str | None = None


class PlazaSourcesIn(BaseModel):
    visibility: dict[str, str]


class CookieIn(BaseModel):
    cookie: str


class ImgbedIn(BaseModel):
    base_url: str = ""
    token: str = ""
    channel: str = ""
    channel_name: str = ""
    folder: str = ""
    retention_days: int | None = None


class TurnstileIn(BaseModel):
    enabled: bool = True
    sitekey: Annotated[str, Field(max_length=128)] = ""
    secret: Annotated[str, Field(max_length=256)] = ""
    hostnames: Annotated[str, Field(max_length=500)] = ""


class ImaCredentialsIn(BaseModel):
    cookie: str | None = None
    openapi_clientid: str | None = None
    openapi_apikey: str | None = None


class ImaGroupIn(BaseModel):
    id: str | None = None
    name: str
    knowledge_base_id: str
    root_folder_id: str
    enabled: bool = True
    folder_ids: list[object] | None = None
    interval_seconds: int | None = None


class ImaCollectorIn(BaseModel):
    uid: str | None = None
    refresh_token: str | None = None
    knowledge_base_id: str | None = None
    root_folder_id: str | None = None
    interval_seconds: int | None = None
    groups: list[ImaGroupIn] | None = None


class ImaCollectorSyncIn(BaseModel):
    group_id: str = ""


class FeishuDocumentSourceIn(BaseModel):
    url: str


class FeishuDocumentConfigIn(BaseModel):
    app_id: str | None = None
    app_secret: str | None = None
    redirect_uri: str | None = None
    scopes: str | None = None
    interval_seconds: int | None = None


class FeishuDocumentSourceUpdateIn(BaseModel):
    enabled: bool | None = None
    display_mode: str | None = None
    display_name: str | None = None


class FeishuDocumentOauthCallbackIn(BaseModel):
    state: str
    code: str


class CiccTriggerIn(BaseModel):
    mode: str


class CiccScheduleIn(BaseModel):
    enabled: bool
    time: str | None = None  # HH:mm，None=不改时间


class CiccCategoriesIn(BaseModel):
    categories: list[str] = []  # 空数组=采集全部品类
    keywords: list[str] = []    # 标题关键词白名单（空=不过滤）


class ImaKbAclIn(BaseModel):
    usernames: list[str]


class ImaKbUserAclIn(BaseModel):
    group_ids: list[str]


class LocalLibraryEnabledIn(BaseModel):
    enabled: bool


class LocalLibraryMetaIn(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


class LocalLibraryCreateIn(BaseModel):
    slug: str
    name: str
    tags: list[str] = []


class ProxyPoolIn(BaseModel):
    name: str
    kind: str = "static"
    extract_url: str = ""
    protocol: str = "http"
    expire_seconds: int = 0
    refresh_interval_seconds: int = 0
    enabled: bool = True


class ProxyPoolUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    extract_url: str | None = None
    protocol: str | None = None
    expire_seconds: int | None = None
    refresh_interval_seconds: int | None = None
    enabled: bool | None = None


class ProxyImportIn(BaseModel):
    text: str
    protocol: str | None = None


class ProxyIn(BaseModel):
    pool_id: int
    text: str
    protocol: str | None = None


class BackupWebDAVIn(BaseModel):
    url: str | None = None
    username: str | None = None
    password: str | None = None
    path: str | None = None
    hour: int | None = None
    keep: int | None = None


class SubscriptionIn(BaseModel):
    kol_id: int
    type: str = "post"


class SubscriptionTypeIn(BaseModel):
    type: str


class SubscriptionFavoriteIn(BaseModel):
    favorite: bool


class SubscriptionSecondaryIn(BaseModel):
    secondary: bool


class SubscriptionHideImagesIn(BaseModel):
    hide_images: bool


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    password: str | None = None
    username: str | None = None


class TestPushIn(BaseModel):
    user_id: int
    message: str = "这是一条测试推送 ✅"


_SECRET_MASK_GLUE = "……"


def mask_secret(value: str | None) -> str:
    """凭据脱敏展示：首尾各留 4 位，短值整体打码。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return _SECRET_MASK_GLUE * 2
    return f"{value[:4]}{_SECRET_MASK_GLUE}{value[-4:]}"


def _is_masked_secret(value: str | None) -> bool:
    """用户把回填的掩码原样提交回来时视为「未修改」，不得覆盖真实凭据。

    真实 webhook URL / API key 不会含中文省略号。
    """
    return _SECRET_MASK_GLUE in (value or "")


def _me_llm_runtime(body: MeUpdate, user: dict, request: Request, db):
    from types import SimpleNamespace

    from .llm import normalize_llm_api_format
    from .url_safety import is_allowed_trusted_llm_base, is_allowed_user_llm_base

    user = db.get_user(user["id"]) or user
    base = (body.llm_api_base if body.llm_api_base is not None else user.get("llm_api_base") or "").strip()
    key = (body.llm_api_key or "").strip()
    if not key or _is_masked_secret(key):
        key = user_plain_secret(user, "llm_api_key", db)
    model = (body.llm_model if body.llm_model is not None else user.get("llm_model") or "").strip()
    api_format = normalize_llm_api_format(
        body.llm_api_format if body.llm_api_format is not None else user.get("llm_api_format")
    )
    if not base or not key:
        return None
    allowed = (
        is_allowed_trusted_llm_base(base)
        if user.get("is_admin")
        else is_allowed_user_llm_base(base)
    )
    if not allowed:
        raise HTTPException(status_code=400, detail="LLM 地址须为 http(s) URL")
    return SimpleNamespace(
        api_base=base,
        api_key=key,
        model=model,
        api_format=api_format,
        user_supplied=not bool(user.get("is_admin")),
    )


def public_user(user: dict, db=None) -> dict:
    # 凭据列已改密文存储，掩码展示必须先解出明文再取首尾 4 位
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "telegram_chat_id": user["telegram_chat_id"],
        "custom_telegram_bot": bool(user.get("telegram_bot_token")),
        "feishu_open_id": user["feishu_open_id"],
        "feishu_chat_id": user["feishu_chat_id"],
        "wecom_webhook": mask_secret(user_plain_secret(user, "wecom_webhook", db)),
        "bark_key": mask_secret(user_plain_secret(user, "bark_key", db)),
        "notify_enabled": bool(user["notify_enabled"]),
        "daily_report_enabled": bool(user.get("daily_report")),
        "translate_twitter": bool(user.get("translate_twitter", 1)),
        "push_channels": user.get("push_channels") or "",
        "dnd_start": user.get("dnd_start") or "",
        "dnd_end": user.get("dnd_end") or "",
        "dnd_allow_favorite": bool(user.get("dnd_allow_favorite")),
        "keywords_match_reports": bool(user.get("keywords_match_reports")),
        "llm_api_base": user.get("llm_api_base") or "",
        "llm_api_key": mask_secret(user_plain_secret(user, "llm_api_key", db)),
        "llm_model": user.get("llm_model") or "",
        "llm_api_format": (user.get("llm_api_format") or "chat"),
        "llm_last_status": user.get("llm_last_status") or "",
        "created_at": user["created_at"],
    }


# 图片代理白名单图床：这些域名在部分家庭/公司网络会被 DNS 劫持到透明代理网段
# （198.18/15 保留段），SSRF 的 IP 网段校验会误拒。白名单内放宽网段校验，
# 但仍强制图片类型与大小限制，避免被当作任意内容代理。
IMAGE_PROXY_HOSTS = frozenset({
    "pbs.twimg.com", "video.twimg.com", "abs.twimg.com",
    "xqimg.imedao.com", "xueqiuimg.com",
    "wx1.sinaimg.cn", "wx2.sinaimg.cn", "wx3.sinaimg.cn", "wx4.sinaimg.cn",
    "static-assets-1.truthsocial.com",
})
# <img src> 不能带 Authorization，所以此接口保持匿名；按 IP 卡住带宽放大。
# 60/分钟：图床正常时几乎打不满；图床故障时一页 100 帖约 46 张，仍有余量。
IMAGE_PROXY_MAX_PER_WINDOW = 60
IMAGE_PROXY_WINDOW_SECONDS = 60
IMAGE_PROXY_VIDEO_MAX_PER_WINDOW = 180  # 播放器会打多次 Range，单独放宽
IMAGE_PROXY_VIDEO_MAX_BYTES = 60 * 1024 * 1024
IMAGE_PROXY_VIDEO_TYPES = frozenset({"video/mp4", "video/quicktime", "video/webm"})


ACCOUNT_ORIGIN_LABELS = {
    "invite": "邀请码",
    "telegram": "Telegram",
    "feishu": "飞书",
    "wechat": "微信",
    "web": "网页",
}


def account_origin(user: dict, invite: dict | None = None) -> str:
    """推断账号从哪来：邀请码 > 自动建号 > 网页。后来绑定的渠道不改来源。"""
    invite = invite or {}
    if invite.get("code"):
        return "invite"
    # 微信 openid 只在小程序自动建号时写入，/me 改不了
    if user.get("wechat_openid"):
        return "wechat"
    # 机器人自动建号没有密码；网页号后来绑定 Telegram/飞书仍算网页
    has_password = bool(user.get("password_hash"))
    if user.get("telegram_chat_id") and not has_password:
        return "telegram"
    if (user.get("feishu_open_id") or user.get("feishu_chat_id")) and not has_password:
        return "feishu"
    return "web"


def delete_user_block_reason(target: dict | None, admin: dict) -> str | None:
    if target is None:
        return "用户不存在"
    if target["id"] == admin["id"]:
        return "不能删除自己的账号"
    if target.get("is_admin"):
        return "不能删除管理员"
    return None


def admin_user_summary(
    user: dict,
    invite: dict | None = None,
    *,
    feishu_personal_active: bool = False,
    webpush_bound: bool = False,
    subscription_count: int = 0,
    inactive: bool = False,
    days_until_purge: int | None = None,
    ima_kb_groups: list[str] | None = None,
    ima_kb_subscribed: list[str] | None = None,
) -> dict:
    """管理员用户列表摘要：只暴露管理所需字段，不含 feed_token/bark_key/wecom_webhook/llm_api_key 等凭证。"""
    invite = invite or {}
    origin = account_origin(user, invite)
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "created_at": user["created_at"],
        "notify_enabled": bool(user["notify_enabled"]),
        "daily_report_enabled": bool(user.get("daily_report")),
        "dnd_enabled": bool(user.get("dnd_start")),
        "push_channels": user.get("push_channels") or "",
        "subscription_count": int(subscription_count or 0),
        "telegram_bound": bool(user.get("telegram_chat_id")),
        "feishu_bound": bool(user.get("feishu_open_id") or user.get("feishu_chat_id") or feishu_personal_active),
        "wecom_bound": bool(user.get("wecom_webhook")),
        "bark_bound": bool(user.get("bark_key")),
        "webpush_bound": bool(webpush_bound),
        "custom_telegram_bot": bool(user.get("telegram_bot_token")),
        "register_code": invite.get("code") or "",
        "register_note": invite.get("note") or "",
        "inactive": bool(inactive),
        "days_until_purge": days_until_purge,
        "origin": origin,
        "origin_label": ACCOUNT_ORIGIN_LABELS[origin],
        "has_password": bool(user.get("password_hash")),
        "last_login_at": user.get("last_login_at") or "",
        "username_valid": auth.is_valid_username(user.get("username") or ""),
        "ima_kb_groups": list(ima_kb_groups or []),
        "ima_kb_subscribed": list(ima_kb_subscribed or []),
    }


IMA_DOCUMENT_LIST_MAX_OFFSET = 2000


def bounded_limit(value: int, default: int = 100) -> int:
    """分页 limit 统一钳制：负数/0 按 1 处理（SQLite 的 LIMIT -1 表示不限制），上限 500。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 500))


_WSCN_LIVES_URL = "https://api-one-wscn.awtmt.com/apiv1/content/lives"
_WSCN_CACHE: dict[str, tuple[float, dict]] = {}
_WSCN_CACHE_TTL = 15
_WSCN_CACHE_MAX = 64
_WSCN_LOCK = threading.Lock()
_WSCN_REFRESHING: set[str] = set()
_WSCN_CLIENT: httpx.Client | None = None
_WSCN_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _wscn_evict_locked() -> None:
    """缓存条目超上限时按写入时间淘汰最旧（cursor 键空间经路由校验已有界）。"""
    if len(_WSCN_CACHE) <= _WSCN_CACHE_MAX:
        return
    overflow = len(_WSCN_CACHE) - _WSCN_CACHE_MAX
    for key, _ in sorted(_WSCN_CACHE.items(), key=lambda kv: kv[1][0])[:overflow]:
        _WSCN_CACHE.pop(key, None)


def warmup_wscn_live() -> None:
    try:
        _fetch_wscn_lives(limit=30)
    except Exception:
        logger.warning("wscn warmup failed", exc_info=True)


def start_wscn_live_refresh() -> None:
    """预热首屏后每 TTL 秒刷一次，请求只读这份缓存。"""
    warmup_wscn_live()
    threading.Thread(target=_wscn_home_loop, daemon=True, name="wscn-refresh").start()


def _wscn_client() -> httpx.Client:
    global _WSCN_CLIENT
    if _WSCN_CLIENT is None:
        _WSCN_CLIENT = httpx.Client(timeout=8, headers=_WSCN_HEADERS)
    return _WSCN_CLIENT


def _wscn_plain_body(content: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", content or "", flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    return strip_html(text).strip()


def _normalize_wscn_item(raw: dict) -> dict:
    item_id = int(raw["id"])
    ts = int(raw.get("display_time") or 0)
    published_at = datetime.fromtimestamp(ts, tz=CN_TZ).isoformat() if ts > 0 else ""
    content = raw.get("content") or raw.get("content_text") or ""
    return {
        "id": item_id,
        "score": int(raw.get("score") or 1),
        "highlight_title": (raw.get("highlight_title") or "").strip(),
        "body": _wscn_plain_body(content),
        "published_at": published_at,
        "url": (raw.get("uri") or f"https://wallstreetcn.com/livenews/{item_id}").strip(),
    }


def _wscn_load(cursor: str, limit: int) -> dict:
    params: dict[str, str | int] = {"channel": "global-channel", "limit": limit}
    if cursor:
        params["cursor"] = cursor
    resp = _wscn_client().get(_WSCN_LIVES_URL, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 20000:
        raise RuntimeError(payload.get("message") or "WSCN API 错误")
    data = payload.get("data") or {}
    items = [_normalize_wscn_item(row) for row in (data.get("items") or [])]
    return {
        "items": items,
        "next_cursor": (data.get("next_cursor") or "").strip(),
        "polling_cursor": int(data.get("polling_cursor") or (items[0]["id"] if items else 0)),
    }


def _wscn_refresh(cursor: str, limit: int, cache_key: str) -> None:
    try:
        result = _wscn_load(cursor, limit)
        with _WSCN_LOCK:
            _WSCN_CACHE[cache_key] = (time.time(), result)
            _wscn_evict_locked()
    except Exception:
        logger.warning("wscn live refresh failed", exc_info=True)
    finally:
        with _WSCN_LOCK:
            _WSCN_REFRESHING.discard(cache_key)


def _wscn_refresh_home() -> None:
    cache_key = ":30"
    with _WSCN_LOCK:
        if cache_key in _WSCN_REFRESHING:
            return
        _WSCN_REFRESHING.add(cache_key)
    _wscn_refresh("", 30, cache_key)


def _wscn_home_loop() -> None:
    while True:
        time.sleep(_WSCN_CACHE_TTL)
        try:
            _wscn_refresh_home()
        except Exception:
            logger.warning("wscn home loop failed", exc_info=True)


def _fetch_wscn_lives(*, cursor: str = "", limit: int = 30) -> dict:
    limit = max(1, min(int(limit), 50))
    cursor = (cursor or "").strip()
    cache_key = f"{cursor}:{limit}"
    now = time.time()
    with _WSCN_LOCK:
        cached = _WSCN_CACHE.get(cache_key)
        if cached and now - cached[0] <= _WSCN_CACHE_TTL:
            return cached[1]
    if cached is not None:
        # 过期值：后台刷新（_wscn_refresh 在锁外加载），请求立即拿旧值
        with _WSCN_LOCK:
            if cache_key not in _WSCN_REFRESHING:
                _WSCN_REFRESHING.add(cache_key)
                start_refresh = True
            else:
                start_refresh = False
        if start_refresh:
            threading.Thread(
                target=_wscn_refresh, args=(cursor, limit, cache_key), daemon=True
            ).start()
        return cached[1]
    # 冷 key：先占位再在锁外加载——外网请求持全局锁会把所有 wscn 请求
    # 串行化在一个线程上；并发同 key 的后来者短暂等待首个线程的结果
    with _WSCN_LOCK:
        cached = _WSCN_CACHE.get(cache_key)
        if cached:
            return cached[1]
        busy = cache_key in _WSCN_REFRESHING
        if not busy:
            _WSCN_REFRESHING.add(cache_key)
    if busy:
        for _ in range(90):
            time.sleep(0.1)
            with _WSCN_LOCK:
                filled = _WSCN_CACHE.get(cache_key)
                if filled:
                    return filled[1]
        raise RuntimeError("快讯源刷新超时")
    try:
        result = _wscn_load(cursor, limit)
        with _WSCN_LOCK:
            _WSCN_CACHE[cache_key] = (time.time(), result)
            _wscn_evict_locked()
        return result
    finally:
        with _WSCN_LOCK:
            _WSCN_REFRESHING.discard(cache_key)


def _prune_window_dict(
    entries: dict[str, list[float]],
    window: float,
    now: float,
    max_entries: int,
) -> None:
    """限流字典清理：删除全部已过期条目（无论列表是否为空），仍超上限时删最旧。

    旧的清理只删 `not v` 的空列表键，而窗口外记录的列表本身非空，导致过期条目
    永不清理、字典可持续增长。这里按当前时间过滤每个键的所有时间戳。
    """
    expired = [
        k for k, ts_list in entries.items()
        if not any(now - t < window for t in ts_list)
    ]
    for k in expired:
        entries.pop(k, None)
    if len(entries) > max_entries:
        # 删最旧：按每条记录的最后失败时间排序，只保留最近的 max_entries 条
        oldest = sorted(entries.keys(), key=lambda k: max(entries[k]))
        for k in oldest[: len(entries) - max_entries]:
            entries.pop(k, None)


def _feishu_timeline_entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("timestamp") or ""), str(entry.get("id") or ""))


def _feishu_timeline_cursor(entry: dict[str, Any]) -> str:
    raw = json.dumps(
        {"timestamp": _feishu_timeline_entry_key(entry)[0], "id": _feishu_timeline_entry_key(entry)[1]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _feishu_timeline_cursor_key(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        )
        timestamp = str(payload["timestamp"])
        entry_id = str(payload["id"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("时间线游标无效") from exc
    if not timestamp or not entry_id:
        raise ValueError("时间线游标无效")
    return timestamp, entry_id


def _feishu_timeline_page(
    entries: list[dict[str, Any]],
    order: Literal["latest", "original"],
    window_days: int | None,
    before: str,
) -> tuple[list[dict[str, Any]], bool, str]:
    ordered = sorted(entries, key=_feishu_timeline_entry_key, reverse=order == "latest")
    if not window_days:
        return ordered, False, ""
    cursor_key = _feishu_timeline_cursor_key(before) if before else None
    candidates = [
        item for item in ordered
        if cursor_key is None
        or (
            _feishu_timeline_entry_key(item) < cursor_key
            if order == "latest"
            else _feishu_timeline_entry_key(item) > cursor_key
        )
    ]
    if not candidates:
        return [], False, ""
    anchor = datetime.fromisoformat(
        str(candidates[0].get("day") or candidates[0]["timestamp"][:10])
    ).date()
    if order == "latest":
        lower = anchor - timedelta(days=window_days - 1)
        page = [
            item for item in candidates
            if lower <= datetime.fromisoformat(
                str(item.get("day") or item["timestamp"][:10])
            ).date() <= anchor
        ]
    else:
        upper = anchor + timedelta(days=window_days - 1)
        page = [
            item for item in candidates
            if anchor <= datetime.fromisoformat(
                str(item.get("day") or item["timestamp"][:10])
            ).date() <= upper
        ]
    if not page:
        return [], bool(candidates), ""
    has_more = len(page) < len(candidates)
    return page, has_more, _feishu_timeline_cursor(page[-1]) if has_more else ""


def create_api_router(
    db: DB,
    secret: str,
    allow_register: bool = True,
    wechat_config=None,
    notifiers_config=None,
    trust_proxy: bool = False,
    ima_documents: ImaDocumentService | None = None,
    feishu_documents: FeishuDocumentSyncService | None = None,
    news_service: NewsService | None = None,
    turnstile_site_key: str = "",
    turnstile_secret: str = "",
    turnstile_hostnames: str = "",
) -> APIRouter:
    router = APIRouter(prefix="/api")
    market_quotes = MarketQuotes()
    # 登录/注册与 img-proxy 限流落在 bind_quota，多进程共享同一 SQLite
    ima_quota_alerts: set[tuple] = set()
    LOGIN_MAX_FAILURES = 8
    LOGIN_WINDOW = 300
    # 账号级失败锁定（防 IP 轮换爆破，独立于上面的 IP 限流）：
    # 1 小时滚动窗口内连续失败超阈值即锁定该账号，锁定期内即使密码正确也拒绝；
    # 管理员账号更敏感（3 次锁 30 分钟），普通账号 10 次锁 15 分钟；成功登录立即解锁。
    ACCOUNT_FAILURE_WINDOW = 3600
    LOGIN_ACCOUNT_LOCK_THRESHOLD = 10
    LOGIN_ACCOUNT_LOCK_WINDOW = 900
    ADMIN_LOGIN_LOCK_THRESHOLD = 3
    ADMIN_LOGIN_LOCK_WINDOW = 1800
    MIN_PASSWORD_LEN = 10
    MAX_PASSWORD_LEN = 128
    _ts_secret = (turnstile_secret or "").strip()
    _ts_sitekey = (turnstile_site_key or "").strip()
    _ts_hosts_raw = (turnstile_hostnames or "").strip()
    # 微博扫码登录会话：qrid -> {client, created_at}
    weibo_qr_sessions: dict[str, dict] = {}

    def _client_ip(request: Request) -> str:
        """优先取 X-Forwarded-For 首段（仅当位于可信反代之后），否则用直连 IP。

        未配置 trust_proxy 时若直接信任该头，攻击者改 header 即可绕过登录/注册限流。
        """
        if trust_proxy:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                first = forwarded.split(",")[0].strip()
                if first:
                    return first
        return request.client.host if request.client else "unknown"

    def _check_login_limit(ip: str) -> None:
        now = time.time()
        period = user_quota.window_start(now, LOGIN_WINDOW)
        if db.get_quota_count(f"login_fail:{ip}", period) >= LOGIN_MAX_FAILURES:
            raise HTTPException(status_code=429, detail="尝试次数过多，请 5 分钟后再试")

    def _check_img_proxy_limit(ip: str) -> None:
        now = time.time()
        allowed, retry_after = db.consume_bind_quota(
            f"img_proxy:{ip}",
            user_quota.window_start(now, IMAGE_PROXY_WINDOW_SECONDS),
            IMAGE_PROXY_MAX_PER_WINDOW,
            IMAGE_PROXY_WINDOW_SECONDS,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="图片加载过于频繁，请稍后再试",
                headers={"Retry-After": str(max(int(retry_after), 1))},
            )

    def _check_img_proxy_video_limit(ip: str) -> None:
        now = time.time()
        allowed, retry_after = db.consume_bind_quota(
            f"img_proxy_video:{ip}",
            user_quota.window_start(now, IMAGE_PROXY_WINDOW_SECONDS),
            IMAGE_PROXY_VIDEO_MAX_PER_WINDOW,
            IMAGE_PROXY_WINDOW_SECONDS,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="视频加载过于频繁，请稍后再试",
                headers={"Retry-After": str(max(int(retry_after), 1))},
            )

    def _img_proxy_video(url: str, request: Request):
        """流式代理视频并透传 Range，让 <video> 能拖进度。"""
        _check_img_proxy_video_limit(_client_ip(request))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header
        client = httpx.Client(timeout=30, follow_redirects=False)
        stream_ctx = client.stream("GET", url, headers=headers, follow_redirects=False)
        try:
            resp = stream_ctx.__enter__()
        except Exception as exc:
            client.close()
            raise HTTPException(status_code=502, detail="视频源请求失败") from exc
        try:
            if resp.status_code not in (200, 206):
                raise HTTPException(status_code=502, detail="视频源请求失败")
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type not in IMAGE_PROXY_VIDEO_TYPES:
                raise HTTPException(status_code=400, detail="非视频内容")
            content_length = resp.headers.get("content-length")
            if (
                resp.status_code == 200
                and content_length
                and int(content_length) > IMAGE_PROXY_VIDEO_MAX_BYTES
            ):
                raise HTTPException(status_code=400, detail="视频过大")
        except HTTPException:
            stream_ctx.__exit__(None, None, None)
            client.close()
            raise

        def iter_bytes():
            sent = 0
            try:
                for chunk in resp.iter_bytes():
                    sent += len(chunk)
                    if sent > IMAGE_PROXY_VIDEO_MAX_BYTES:
                        break
                    yield chunk
            finally:
                stream_ctx.__exit__(None, None, None)
                client.close()

        out_headers = {
            # 禁止边缘缓存：CF 默认按 URL 缓存，会把第一次 206 片段当成整段
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
        }
        if resp.headers.get("content-range"):
            out_headers["Content-Range"] = resp.headers["content-range"]
        if content_length:
            out_headers["Content-Length"] = content_length
        media_type = "video/mp4" if content_type == "video/quicktime" else content_type
        return StreamingResponse(
            iter_bytes(),
            status_code=resp.status_code,
            media_type=media_type,
            headers=out_headers,
        )

    def _turnstile_runtime() -> dict:
        stored_enabled = db.get_setting("turnstile_enabled")
        stored_site = (db.get_setting("turnstile_site_key") or "").strip()
        stored_secret = (db.get_setting("turnstile_secret") or "").strip()
        stored_hosts = (db.get_setting("turnstile_hostnames") or "").strip()
        sitekey = stored_site or _ts_sitekey
        secret = stored_secret or _ts_secret
        hosts_raw = stored_hosts or _ts_hosts_raw
        hosts = {h.strip().lower() for h in hosts_raw.split(",") if h.strip()}
        if stored_enabled in ("0", "1"):
            wanted = stored_enabled == "1"
        else:
            wanted = bool(secret)
        return {
            "enabled": wanted,
            "active": wanted and bool(secret) and bool(sitekey),
            "sitekey": sitekey,
            "secret": secret,
            "secret_set": bool(secret),
            "secret_from_env": bool(_ts_secret) and not stored_secret,
            "hostnames": hosts_raw,
            "hosts": hosts,
        }

    def _turnstile_admin_status() -> dict:
        rt = _turnstile_runtime()
        return {
            "enabled": rt["enabled"],
            "active": rt["active"],
            "sitekey": rt["sitekey"],
            "secret_set": rt["secret_set"],
            "secret_from_env": rt["secret_from_env"],
            "hostnames": rt["hostnames"],
        }

    def _require_turnstile(token: str, action: str, ip: str) -> None:
        rt = _turnstile_runtime()
        if not rt["active"]:
            return
        if not verify_turnstile(
            secret=rt["secret"], token=token, action=action, hostnames=rt["hosts"], ip=ip
        ):
            raise HTTPException(status_code=403, detail="人机验证失败，请重试")

    def _record_login_failure(ip: str) -> None:
        now = time.time()
        db.consume_bind_quota(
            f"login_fail:{ip}",
            user_quota.window_start(now, LOGIN_WINDOW),
            LOGIN_MAX_FAILURES,
            LOGIN_WINDOW,
        )

    def _account_lock_key(username: str) -> str:
        return f"login_lock:{_account_key(username)}"

    def _account_fail_key(username: str) -> str:
        return f"acct_fail:{_account_key(username)}"

    def _account_lock_seconds_left(username: str) -> int:
        """账号剩余锁定秒数；未锁定返回 0（过期记录自动清理）。"""
        raw = db.get_setting(_account_lock_key(username))
        if not raw:
            return 0
        try:
            until = float(raw)
        except (TypeError, ValueError):
            db.set_setting(_account_lock_key(username), "")
            return 0
        left = int(until - time.time())
        if left <= 0:
            db.set_setting(_account_lock_key(username), "")
            return 0
        return left

    def _record_account_failure(username: str, is_admin: bool, ip: str) -> None:
        """账号级失败计数（1 小时滚动窗口）；超阈值锁定账号并写操作日志。"""
        now = time.time()
        threshold = ADMIN_LOGIN_LOCK_THRESHOLD if is_admin else LOGIN_ACCOUNT_LOCK_THRESHOLD
        fail_key = _account_fail_key(username)
        allowed, _retry = db.consume_bind_quota(
            fail_key,
            user_quota.window_start(now, ACCOUNT_FAILURE_WINDOW),
            threshold,
            ACCOUNT_FAILURE_WINDOW,
        )
        count = db.get_quota_count(
            fail_key, user_quota.window_start(now, ACCOUNT_FAILURE_WINDOW)
        )
        if not allowed or count >= threshold:
            window = ADMIN_LOGIN_LOCK_WINDOW if is_admin else LOGIN_ACCOUNT_LOCK_WINDOW
            db.set_setting(_account_lock_key(username), str(now + window))
            db.log_admin_action(
                None,
                "login_locked",
                username,
                f"ip={ip} role={'admin' if is_admin else 'user'} 1小时内失败{count}次，锁定{window // 60}分钟",
            )

    def _audit(admin: dict, action: str, target: str = "", detail: str = "") -> None:
        db.log_admin_action(admin["id"], action, target, detail)

    def news_audit_url(url: str) -> str:
        try:
            parsed = httpx.URL(url)
            host = parsed.host or ""
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            return f"{parsed.scheme}://{host}{parsed.path or '/'}"[:240]
        except Exception:
            return "invalid-url"

    def _invite_by_user_id() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for row in db.list_register_codes():
            if row.get("used_by"):
                out[row["used_by"]] = row
        return out

    def _notify_admins_new_request(platform: str, ref: str, requester: dict, request_id: int) -> None:
        """新的大V添加申请：优先 TG 带审批按钮；未绑 TG 的管理员走其他渠道。"""
        if notifiers_config is None:
            return

        from .channels import CHANNELS, build_channel_notifier, channel_bound

        label = PLATFORM_LABELS.get(platform, platform)
        message = (
            f"🆕 新的大V添加申请：{label}「{ref}」\n"
            f"申请人：{requester['username']}\n"
            "点击下方按钮直接审批，或到管理后台「添加审批」处理。"
        )
        keyboard = [
            [
                {"text": "✅ 通过", "callback_data": f"approve:{request_id}"},
                {"text": "❌ 拒绝", "callback_data": f"reject:{request_id}"},
            ]
        ]
        client = httpx.Client(timeout=15)
        try:
            for user in db.list_users():
                if not user.get("is_admin"):
                    continue
                # TG 是唯一带审批按钮的渠道：已绑 TG 的管理员只发 TG，避免多渠道重复推送；
                # TG 发送失败时回退其他渠道，避免管理员收不到通知
                tg_ok = False
                if channel_bound(user, "telegram", notifiers_config, db):
                    try:
                        notifier = build_channel_notifier(
                            "telegram", user, notifiers_config, client=client, db=db
                        )
                        notifier.send_text(message, reply_markup=keyboard)
                        tg_ok = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("大V申请 TG 通知失败 user=%s err=%s", user["username"], exc)
                if tg_ok:
                    continue
                for channel in CHANNELS:
                    if channel == "telegram" or not channel_bound(user, channel, notifiers_config, db):
                        continue
                    try:
                        notifier = build_channel_notifier(channel, user, notifiers_config, client=client, db=db)
                        notifier.send_text(message)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "大V申请通知失败 user=%s channel=%s err=%s",
                            user["username"],
                            channel,
                            exc,
                        )
        finally:
            client.close()

    POLLING_FIELDS = [
        ("interval_seconds", "config_interval_seconds", "stats_polling_interval", 1, 3600),
        (
            "priority_interval_seconds",
            "config_priority_interval_seconds",
            "stats_priority_interval_seconds",
            1,
            600,
        ),
        (
            "truth_interval_seconds",
            "config_truth_interval_seconds",
            "config_truth_interval_seconds",
            0,
            600,
        ),
        ("digest_interval_seconds", "config_digest_interval_seconds", "stats_digest_interval_seconds", 0, 86400),
        (
            "source_probe_interval_seconds",
            "config_source_probe_interval_seconds",
            "stats_source_probe_interval_seconds",
            0,
            86400,
        ),
        (
            "cookie_keepalive_interval_seconds",
            "config_cookie_keepalive_interval_seconds",
            "stats_keepalive_interval",
            0,
            86400,
        ),
        ("daily_report_hour", "config_daily_report_hour", "stats_daily_report_hour", 0, 23),
        # 采集频率档位：无新帖自适应降频参数（scheduler._effective_interval 读取）
        (
            "combination_base_seconds",
            "config_combination_base_seconds",
            "config_combination_base_seconds",
            5,
            3600,
        ),
        (
            "combination_idle_cap_seconds",
            "config_combination_idle_cap_seconds",
            "config_combination_idle_cap_seconds",
            5,
            86400,
        ),
        (
            "normal_idle_cap_seconds",
            "config_normal_idle_cap_seconds",
            "config_normal_idle_cap_seconds",
            5,
            86400,
        ),
        (
            "priority_idle_cap_seconds",
            "config_priority_idle_cap_seconds",
            "config_priority_idle_cap_seconds",
            5,
            86400,
        ),
        (
            "x_fallback_cap_seconds",
            "config_x_fallback_cap_seconds",
            "config_x_fallback_cap_seconds",
            5,
            86400,
        ),
        (
            "secondary_interval_seconds",
            "config_secondary_base_seconds",
            "config_secondary_base_seconds",
            60,
            86400,
        ),
        (
            "secondary_idle_cap_seconds",
            "config_secondary_idle_cap_seconds",
            "config_secondary_idle_cap_seconds",
            60,
            86400,
        ),
        (
            "secondary_digest_interval_seconds",
            "config_secondary_digest_interval_seconds",
            "config_secondary_digest_interval_seconds",
            0,
            86400,
        ),
        (
            "secondary_min_digest_count",
            "config_secondary_min_digest_count",
            "config_secondary_min_digest_count",
            1,
            100,
        ),
    ]

    def _effective_polling() -> dict:
        out = {}
        for name, cfg_key, stat_key, _lo, _hi in POLLING_FIELDS:
            raw = db.get_setting(cfg_key) or db.get_setting(stat_key)
            try:
                out[name] = int(raw)
            except (TypeError, ValueError):
                out[name] = 0
        out["translate_twitter_content"] = (
            db.get_setting("config_translate_twitter_content") == "1"
        )
        from .telegram_rich_flag import get_telegram_rich_messages

        yaml_rich = True
        if notifiers_config is not None and getattr(notifiers_config, "telegram", None):
            yaml_rich = bool(getattr(notifiers_config.telegram, "rich_messages", True))
        out["telegram_rich_messages"] = get_telegram_rich_messages(db, yaml_rich)
        out["zsxq_max_pages"] = _max_pages(db)
        out["zsxq_fetch_delay_seconds"] = _delay(db, "zsxq_fetch_delay_seconds", DEFAULT_DELAY)
        out["zsxq_file_delay_seconds"] = _delay(db, "zsxq_file_delay_seconds", DEFAULT_FILE_DELAY)
        out["zsxq_prefetch_files"] = prefetch_enabled(db)
        out["zsxq_fetch_comments"] = _comments_enabled(db)
        out["zsxq_max_comment_pages"] = _max_comment_pages(db)
        out["zsxq_comment_budget"] = _comment_budget(db)
        out["zsxq_app_channel"] = _app_channel_enabled(db)
        out["zsxq_app_device"] = _app_device(db)
        out["zsxq_ws_enabled"] = _ws_enabled(db)
        out["zsxq_ws_address"] = _ws_address(db)
        return out

    def _user_from_bearer(token: str) -> dict:
        payload = auth.verify_token(token, secret)
        if not payload:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        user = db.get_authenticated_user(payload.get("uid"))
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        if int(payload.get("ver", 0)) != int(user.get("token_version") or 0):
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return user

    def get_current_user(authorization: str | None = Header(None)):
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        return _user_from_bearer(token)

    def get_download_user(
        authorization: str | None = Header(None),
        token: str | None = Query(None),
    ):
        # 文件下载可能是 <a href> / <img>，允许一次性 query；JSON API 只认 Authorization
        if not (authorization and authorization.startswith("Bearer ")) and token:
            authorization = f"Bearer {token}"
        bearer = ""
        if authorization and authorization.startswith("Bearer "):
            bearer = authorization[7:]
        return _user_from_bearer(bearer)

    def require_admin(user: dict = Depends(get_current_user)):
        if not user["is_admin"]:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user

    def _quota_or_429(
        user: dict,
        bucket: str,
        period_start: int,
        limit: int,
        window_seconds: int,
        detail: str,
        notice: str,
    ) -> None:
        allowed, retry_after = db.consume_user_quota(
            int(user["id"]), bucket, period_start, limit, window_seconds
        )
        if allowed:
            return
        key = (int(user["id"]), bucket, int(period_start))
        if key not in ima_quota_alerts:
            ima_quota_alerts.add(key)
            if len(ima_quota_alerts) > 2000:
                ima_quota_alerts.clear()
                ima_quota_alerts.add(key)
            db.log_admin_action(
                None,
                "ima_quota",
                str(user.get("username") or user["id"]),
                notice,
            )
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(max(int(retry_after), 1))},
        )

    def _enforce_ima_list_quota(user: dict) -> None:
        if user.get("is_admin"):
            return
        now = time.time()
        _quota_or_429(
            user,
            user_quota.BUCKET_LIST_BURST,
            user_quota.window_start(now, user_quota.IMA_LIST_BURST_SEC),
            user_quota.IMA_LIST_BURST,
            user_quota.IMA_LIST_BURST_SEC,
            "刷新过于频繁，请稍后再试",
            "知识库列表 10 分钟超限",
        )

    def _enforce_ima_file_quota(user: dict) -> None:
        if user.get("is_admin"):
            return
        now = time.time()
        _quota_or_429(
            user,
            user_quota.BUCKET_PDF_BURST,
            user_quota.window_start(now, user_quota.IMA_PDF_BURST_SEC),
            user_quota.IMA_PDF_BURST,
            user_quota.IMA_PDF_BURST_SEC,
            "阅读过于频繁，请稍后再试",
            "知识库 PDF 10 分钟超限",
        )
        day_start = user_quota.shanghai_day_start(now)
        day_seconds = 24 * 3600
        _quota_or_429(
            user,
            user_quota.BUCKET_PDF_DAY,
            day_start,
            user_quota.IMA_PDF_DAY,
            day_seconds,
            "今日阅读已达上限，明天再看",
            "知识库 PDF 今日达上限",
        )

    # ---- 认证 ----
    @router.get("/version")
    def version_info():
        """当前版本与 GitHub 最新版本（带缓存），用于前端更新提示。"""
        from .version import APP_VERSION, is_newer, latest_github_version

        latest, has = latest_github_version(db)
        return {
            "current": APP_VERSION,
            "latest": latest,
            "update_available": bool(has and is_newer(latest, APP_VERSION)),
            "url": "https://github.com/icekale/vpush/releases",
        }

    @router.get("/auth/turnstile")
    def turnstile_public():
        rt = _turnstile_runtime()
        # 开关开且密钥齐全才下发 sitekey，避免前端出框、后端却跳过校验
        return {"sitekey": rt["sitekey"] if rt["active"] else ""}

    @router.post("/auth/register")
    def register(body: RegisterIn, request: Request):
        if not allow_register:
            raise HTTPException(status_code=403, detail="暂未开放注册")
        ip = _client_ip(request)
        _check_login_limit(ip)
        _require_turnstile(body.cf_turnstile_response, "register", ip)
        try:
            try:
                username = auth.validate_username(body.username)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            if len(body.password) < MIN_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail=f"密码至少{MIN_PASSWORD_LEN}位")
            if len(body.password) > MAX_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail=f"密码最长{MAX_PASSWORD_LEN}位")
            if not body.code.strip():
                raise HTTPException(status_code=400, detail="注册需要邀请码，请向管理员索取")
            try:
                # 管理员只能在网页后台指定，注册用户一律为普通用户
                uid = db.register_with_code(body.code, username, auth.hash_password(body.password))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        except HTTPException:
            # 注册失败同样计入限流，避免邀请码爆破
            _record_login_failure(ip)
            raise
        user = db.get_user(uid)
        return {
            "token": auth.create_token(uid, username, secret, user.get("token_version") or 0),
            "user": public_user(user, db),
        }

    @router.post("/auth/login")
    def login(body: LoginIn, request: Request):
        ip = _client_ip(request)
        _check_login_limit(ip)
        _require_turnstile(body.cf_turnstile_response, "login", ip)
        username = body.username.strip()
        locked_left = _account_lock_seconds_left(username)
        if locked_left > 0:
            # 锁定期内一律拒绝（即使密码正确），不泄露密码有效性，也不再累计计数
            minutes = max(1, (locked_left + 59) // 60)
            raise HTTPException(
                status_code=429,
                detail=f"该账号因多次失败登录被临时锁定，请约 {minutes} 分钟后再试",
            )
        # 注册按 COLLATE NOCASE 判重，登录同样大小写不敏感，避免同名不同大小写无法登录
        user = db.get_user_by_username_ci(username)
        if user is None:
            auth.verify_password(body.password, auth.DUMMY_HASH)
            _record_login_failure(ip)
            _record_account_failure(username, False, ip)
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user["password_hash"]:
            # 机器人/微信自动创建的账号没有密码，不能通过账号密码登录
            auth.verify_password(body.password, auth.DUMMY_HASH)
            _record_login_failure(ip)
            _record_account_failure(username, bool(user["is_admin"]), ip)
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not body.password or len(body.password) > MAX_PASSWORD_LEN:
            _record_login_failure(ip)
            _record_account_failure(username, bool(user["is_admin"]), ip)
            raise HTTPException(status_code=400, detail=f"密码长度需在 1-{MAX_PASSWORD_LEN} 位之间")
        if not auth.verify_password(body.password, user["password_hash"]):
            _record_login_failure(ip)
            _record_account_failure(username, bool(user["is_admin"]), ip)
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        db.clear_quota(f"login_fail:{ip}")
        db.clear_quota(_account_fail_key(username))
        db.set_setting(_account_lock_key(username), "")
        db.touch_last_login(user["id"])
        return {
            "token": auth.create_token(
                user["id"], user["username"], secret, user.get("token_version") or 0
            ),
            "user": public_user(user, db),
        }

    @router.post("/auth/wechat")
    def wechat_login(body: WechatLoginIn, request: Request):
        if wechat_config is None or not wechat_config.app_id or not wechat_config.app_secret:
            raise HTTPException(status_code=400, detail="未配置微信小程序 app_id/app_secret")
        ip = _client_ip(request)
        _check_login_limit(ip)
        try:
            data = wechat.code2session(body.code, wechat_config.app_id, wechat_config.app_secret)
        except Exception as exc:  # noqa: BLE001
            _record_login_failure(ip)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        openid = data["openid"]
        user = db.get_user_by_openid(openid)
        if user is None:
            if not allow_register:
                _record_login_failure(ip)
                raise HTTPException(status_code=403, detail="暂未开放注册")
            invite = (body.invite_code or "").strip()
            if not invite:
                _record_login_failure(ip)
                raise HTTPException(status_code=400, detail="注册需要邀请码，请向管理员索取")
            base = f"wx_{openid[:10]}"
            username, i = base, 1
            while db.get_user_by_username_ci(username) is not None:
                username = f"{base}{i}"
                i += 1
            try:
                uid = db.register_wechat_with_code(invite, username, openid)
            except ValueError as exc:
                raced = db.get_user_by_openid(openid)
                if raced is not None:
                    user = raced
                else:
                    _record_login_failure(ip)
                    raise HTTPException(status_code=400, detail=str(exc)) from None
            else:
                user = db.get_user(uid)
        if user is None:
            raise HTTPException(status_code=400, detail="微信登录失败")
        db.clear_quota(f"login_fail:{ip}")
        db.touch_last_login(user["id"])
        return {
            "token": auth.create_token(
                user["id"], user["username"], secret, user.get("token_version") or 0
            ),
            "user": public_user(user, db),
        }

    # ---- 我的 ----
    @router.get("/me")
    def me(user: dict = Depends(get_current_user)):
        db.touch_last_login(user["id"])
        user = db.get_user(user["id"])
        profile = public_user(user, db)
        profile["news_visible"] = db.get_setting("news_visible") != "0"
        profile["subscription_count"] = db.count_subscriptions(user["id"])
        profile["keywords"] = db.get_user_keywords(user["id"])
        if notifiers_config is not None:
            profile["push_guide"] = {
                "telegram_bot_username": notifiers_config.telegram.bot_username,
                "feishu_bot_name": notifiers_config.feishu.bot_name,
            }
        from .feishu_personal import FeishuPersonalManager, mask_app_id

        fs_personal_mgr = FeishuPersonalManager(
            db, notifiers_config.feishu if notifiers_config is not None else None
        )
        personal_bot = db.get_feishu_personal_bot(user["id"])
        profile["feishu_personal"] = {
            "available": fs_personal_mgr.available(),
            "status": personal_bot["status"] if personal_bot else "",
            "app_id_masked": mask_app_id(personal_bot["app_id"]) if personal_bot else "",
        }
        from .notifiers.webpush import ensure_vapid_keys

        webpush_cfg = getattr(notifiers_config, "webpush", None) if notifiers_config is not None else None
        _priv, vapid_pub = ensure_vapid_keys(db, webpush_cfg)
        profile["vapid_public_key"] = vapid_pub
        profile["webpush_count"] = db.count_webpush_subscriptions(user["id"])
        profile["webpush_bound"] = profile["webpush_count"] > 0
        profile["android_device_count"] = db.count_android_devices(user["id"])
        profile["plaza_platforms"] = plaza_visible_platforms(db)
        profile["timeline_platforms"] = user_timeline_platforms(
            db, user["id"], bool(user.get("is_admin"))
        )
        return profile

    @router.put("/me")
    def update_me(body: MeUpdate, user: dict = Depends(get_current_user)):
        updates = {}
        keywords = _UNSET
        if "telegram_chat_id" in body.model_fields_set:
            value = (body.telegram_chat_id or "").strip()
            if value:
                owner = db.get_user_by_telegram(value)
                if owner is not None and owner["id"] != user["id"]:
                    raise HTTPException(status_code=400, detail="该 Telegram 已绑定其他账号")
            updates["telegram_chat_id"] = value
        if "telegram_bot_token" in body.model_fields_set:
            value = (body.telegram_bot_token or "").strip()
            if value:
                owner = db.get_user_by_telegram_bot(value)
                if owner is not None and owner["id"] != user["id"]:
                    raise HTTPException(status_code=400, detail="该机器人 token 已被其他账号使用")
                _bot_username, chat_id, error = _resolve_telegram_bot(value)
                if not chat_id:
                    raise HTTPException(status_code=400, detail=f"自建机器人绑定失败：{error}")
                updates["telegram_chat_id"] = chat_id
            updates["telegram_bot_token"] = value
        for field, getter, error in (
            ("feishu_open_id", db.get_user_by_feishu, "该飞书账号已绑定其他账号"),
            ("feishu_chat_id", db.get_user_by_feishu_chat, "该飞书会话已绑定其他账号"),
        ):
            if field in body.model_fields_set:
                value = (getattr(body, field) or "").strip()
                if value:
                    owner = getter(value)
                    if owner is not None and owner["id"] != user["id"]:
                        raise HTTPException(status_code=400, detail=error)
                updates[field] = value
        if "wecom_webhook" in body.model_fields_set:
            value = (body.wecom_webhook or "").strip()
            if _is_masked_secret(value):
                pass  # 掩码原样提交 = 未修改，保留旧值
            else:
                if value:
                    from .notifiers.wecom import is_valid_wecom_webhook

                    if not is_valid_wecom_webhook(value):
                        raise HTTPException(
                            status_code=400,
                            detail="企业微信 webhook 地址无效，应为 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=... 格式",
                        )
                    owner = db.get_user_by_wecom_webhook(value)
                    if owner is not None and owner["id"] != user["id"]:
                        raise HTTPException(status_code=400, detail="该企业微信群机器人已绑定其他账号")
                updates["wecom_webhook"] = value
        if "bark_key" in body.model_fields_set:
            value = (body.bark_key or "").strip()
            if _is_masked_secret(value):
                pass
            else:
                if value:
                    from .notifiers.bark import is_valid_bark_key

                    if not is_valid_bark_key(value):
                        raise HTTPException(
                            status_code=400,
                            detail="Bark key 无效：应为手机 Bark App 里的推送 key（形如 AaBbCcDdEeFf...）",
                        )
                    owner = db.get_user_by_bark_key(value)
                    if owner is not None and owner["id"] != user["id"]:
                        raise HTTPException(status_code=400, detail="该 Bark key 已绑定其他账号")
                updates["bark_key"] = value
        if "keywords" in body.model_fields_set:
            keywords = [k.strip() for k in (body.keywords or []) if k.strip()]
            if len(keywords) > KEYWORDS_MAX_COUNT:
                raise HTTPException(status_code=400, detail=f"关键词最多 {KEYWORDS_MAX_COUNT} 个")
            for keyword in keywords:
                if len(keyword) > KEYWORDS_MAX_LENGTH:
                    raise HTTPException(
                        status_code=400,
                        detail=f"单个关键词最长 {KEYWORDS_MAX_LENGTH} 字：{keyword}",
                    )
        if "keywords_match_reports" in body.model_fields_set and body.keywords_match_reports is not None:
            want = bool(body.keywords_match_reports)
            updates["keywords_match_reports"] = want
            current = db.get_user(user["id"]) or {}
            if want and not current.get("keywords_match_reports"):
                updates["keywords_match_reports_since"] = datetime.now(UTC).isoformat()
        if "notify_enabled" in body.model_fields_set:
            updates["notify_enabled"] = body.notify_enabled
        if "daily_report_enabled" in body.model_fields_set and body.daily_report_enabled is not None:
            updates["daily_report"] = body.daily_report_enabled
        if "translate_twitter" in body.model_fields_set and body.translate_twitter is not None:
            updates["translate_twitter"] = body.translate_twitter
        if "push_channels" in body.model_fields_set:
            value = (body.push_channels or "").strip()
            channels = [c.strip() for c in value.split(",") if c.strip()] if value else []
            invalid = [c for c in channels if c not in ("telegram", "feishu", "wecom", "bark", "webpush")]
            if invalid:
                raise HTTPException(status_code=400, detail=f"无效的推送渠道: {', '.join(invalid)}")
            updates["push_channels"] = ",".join(channels)
        for field, label in (("dnd_start", "开始"), ("dnd_end", "结束")):
            if field in body.model_fields_set:
                value = (getattr(body, field) or "").strip()
                if value and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                    raise HTTPException(
                        status_code=400,
                        detail=f"免打扰{label}时间需为 HH:MM 格式（00:00-23:59）",
                    )
                updates[field] = value
        if "dnd_allow_favorite" in body.model_fields_set:
            updates["dnd_allow_favorite"] = body.dnd_allow_favorite
        if "llm_api_key" in body.model_fields_set:
            value = (body.llm_api_key or "").strip()
            if not _is_masked_secret(value):  # 掩码原样提交 = 未修改
                updates["llm_api_key"] = value
        if "llm_api_base" in body.model_fields_set:
            value = (body.llm_api_base or "").strip()
            if value:
                from .url_safety import (
                    is_allowed_trusted_llm_base,
                    is_allowed_user_llm_base,
                )

                allowed = (
                    is_allowed_trusted_llm_base(value)
                    if user.get("is_admin")
                    else is_allowed_user_llm_base(value)
                )
                if not allowed:
                    raise HTTPException(status_code=400, detail="LLM 地址须为 http(s) URL")
            updates["llm_api_base"] = value
        if "llm_model" in body.model_fields_set:
            updates["llm_model"] = (body.llm_model or "").strip()
        if "llm_api_format" in body.model_fields_set:
            from .llm import normalize_llm_api_format

            updates["llm_api_format"] = normalize_llm_api_format(body.llm_api_format)
        news_source_ids = _UNSET
        if "news_source_ids" in body.model_fields_set:
            if body.news_source_ids is None:
                raise HTTPException(status_code=400, detail="新闻来源必须是来源 ID 数组")
            news_source_ids = body.news_source_ids
        try:
            db.update_user_atomic(
                user["id"], updates, keywords=keywords, news_source_ids=news_source_ids
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return public_user(db.get_user(user["id"]), db)

    @router.post("/me/llm-models")
    def list_my_llm_models(body: MeUpdate, request: Request, user: dict = Depends(get_current_user)):
        """按 OpenAI 兼容 GET /models 拉取模型 id 列表。"""
        from .llm import list_models

        cfg = _me_llm_runtime(body, user, request, db)
        if cfg is None:
            raise HTTPException(status_code=400, detail="请先填写 API 地址和 Key")
        models = list_models(cfg)
        if models is None:
            raise HTTPException(status_code=502, detail="无法获取模型列表，请检查地址和 Key")
        return {"models": models}

    @router.post("/me/llm-test")
    def test_my_llm(body: MeUpdate, request: Request, user: dict = Depends(get_current_user)):
        """用当前表单打一条最短请求，返回耗时和用量。"""
        from .llm import probe_llm

        cfg = _me_llm_runtime(body, user, request, db)
        if cfg is None:
            raise HTTPException(status_code=400, detail="请先填写 API 地址和 Key")
        return probe_llm(cfg)

    @router.post("/me/webpush")
    def subscribe_webpush(body: WebPushIn, request: Request, user: dict = Depends(get_current_user)):
        from .notifiers.webpush import (
            is_valid_push_endpoint,
            is_valid_subscription_keys,
        )

        endpoint = (body.endpoint or "").strip()
        p256dh = (body.keys.p256dh or "").strip()
        auth = (body.keys.auth or "").strip()
        if not is_valid_push_endpoint(endpoint):
            raise HTTPException(status_code=400, detail="推送端点无效")
        if not is_valid_subscription_keys(p256dh, auth):
            raise HTTPException(status_code=400, detail="推送密钥无效")
        ua = (request.headers.get("user-agent") or "")[:200]
        db.upsert_webpush_subscription(user["id"], endpoint, p256dh, auth, ua)
        return {
            "ok": True,
            "webpush_bound": True,
            "webpush_count": db.count_webpush_subscriptions(user["id"]),
        }

    @router.delete("/me/webpush")
    def unsubscribe_webpush(user: dict = Depends(get_current_user)):
        db.delete_webpush_subscriptions(user["id"])
        return {"ok": True, "webpush_bound": False, "webpush_count": 0}

    @router.put("/me/android-devices/{installation_id}")
    def register_android_device(
        installation_id: str,
        body: AndroidDeviceIn,
        user: dict = Depends(get_current_user),
    ):
        installation_id = installation_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", installation_id):
            raise HTTPException(status_code=400, detail="设备安装标识无效")
        token = body.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="设备 token 不能为空")
        try:
            db.upsert_android_device(
                installation_id,
                user["id"],
                token,
                body.provider,
                body.device_model.strip(),
                body.app_version.strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {
            "ok": True,
            "installation_id": installation_id,
            "provider": body.provider,
            "device_count": db.count_android_devices(user["id"]),
        }

    @router.delete("/me/android-devices/{installation_id}")
    def unregister_android_device(
        installation_id: str, user: dict = Depends(get_current_user)
    ):
        installation_id = installation_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", installation_id):
            raise HTTPException(status_code=400, detail="设备安装标识无效")
        db.delete_android_device(installation_id, user["id"])
        return {"ok": True, "device_count": db.count_android_devices(user["id"])}

    @router.post("/me/bind-code")
    def create_bind_code(user: dict = Depends(get_current_user)):
        from .db import BIND_ISSUE_LIMIT, BIND_ISSUE_WINDOW
        from .user_quota import window_start

        now = time.time()
        allowed, _retry = db.consume_bind_quota(
            f"issue:{user['id']}",
            window_start(now, BIND_ISSUE_WINDOW),
            BIND_ISSUE_LIMIT,
            BIND_ISSUE_WINDOW,
        )
        if not allowed:
            raise HTTPException(status_code=429, detail="绑定码生成过于频繁，请稍后再试")
        db.delete_expired_bind_codes()
        code = new_bind_code()
        for _ in range(8):
            try:
                db.create_bind_code(code, user["id"], int(time.time()) + BIND_CODE_TTL)
                break
            except sqlite3.IntegrityError:
                code = new_bind_code()
        else:
            raise HTTPException(status_code=500, detail="绑定码生成失败，请重试")
        return {"code": code, "expires_in_seconds": BIND_CODE_TTL}

    # ---- 飞书个人机器人（扫码注册） ----
    _fs_personal_mgr = {}

    def _feishu_personal_manager():
        # 单例：轮询线程/临时监听器/绑定码明文都挂在这个实例上，不能每次 new
        if "mgr" not in _fs_personal_mgr:
            from .feishu_personal import FeishuPersonalManager

            _fs_personal_mgr["mgr"] = FeishuPersonalManager(
                db, notifiers_config.feishu if notifiers_config is not None else None
            )
        return _fs_personal_mgr["mgr"]

    def _require_feishu_personal():
        manager = _feishu_personal_manager()
        if not manager.available():
            raise HTTPException(
                status_code=400,
                detail="个人机器人功能未启用（服务端未配置 FEISHU_CREDENTIAL_KEY）",
            )
        return manager

    def _personal_session_payload(session: dict) -> dict:
        """注册会话状态接口：只暴露展示字段，不返回密钥/设备码。

        绑定码明文只在本进程内存（DB 只存哈希），仅当前用户能通过本人 session 查询；
        过期/重启后为空，前端可点「重新生成绑定码」。
        """
        from .feishu_personal import mask_app_id, qr_data_uri

        personal_bot = db.get_feishu_personal_bot(session["user_id"])
        bind_command = ""
        bind_code_expires_at = session.get("bind_code_expires_at")
        if session["status"] == "awaiting_bind":
            entry = _feishu_personal_manager().get_bind_command(session["session_id"])
            if entry:
                code, expires_at = entry
                bind_command = f"/bind {code}"
                bind_code_expires_at = expires_at
        return {
            "session_id": session["session_id"],
            "status": session["status"],
            "verification_uri": session["verification_uri"],
            "qr_uri": qr_data_uri(session["verification_uri"]),
            "session_expires_at": session["session_expires_at"],
            "bind_command": bind_command,
            "bind_code_expires_at": bind_code_expires_at,
            "last_error": session.get("last_error") or "",
            "candidate_app_id_masked": mask_app_id(session["candidate_app_id"])
            if session.get("candidate_app_id") else "",
            "personal_bot_status": personal_bot["status"] if personal_bot else "",
            "personal_bot_app_id_masked": mask_app_id(personal_bot["app_id"])
            if personal_bot else "",
        }

    @router.post("/me/feishu-personal/register")
    def feishu_personal_register(user: dict = Depends(get_current_user)):
        manager = _require_feishu_personal()
        try:
            session = manager.begin_session(user["id"])
        except Exception as exc:  # noqa: BLE001 - 飞书协议异常，展示给用户
            raise HTTPException(status_code=502, detail=f"发起注册失败：{exc}") from exc
        return _personal_session_payload(session)

    @router.get("/me/feishu-personal/register/{session_id}")
    def feishu_personal_register_status(session_id: str, user: dict = Depends(get_current_user)):
        session = db.get_feishu_registration_session(session_id)
        if session is None or session["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="注册会话不存在")
        return _personal_session_payload(session)

    @router.post("/me/feishu-personal/register/{session_id}/refresh-code")
    def feishu_personal_refresh_code(session_id: str, user: dict = Depends(get_current_user)):
        session = db.get_feishu_registration_session(session_id)
        if session is None or session["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="注册会话不存在")
        if session["status"] != "awaiting_bind":
            raise HTTPException(status_code=400, detail="当前状态无需刷新绑定码")
        manager = _feishu_personal_manager()
        try:
            issued = manager.issue_bind_code(session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session = db.get_feishu_registration_session(session_id)
        payload = _personal_session_payload(session)
        payload["bind_command"] = issued["bind_command"]
        payload["bind_code_expires_at"] = issued["bind_code_expires_at"]
        return payload

    @router.post("/me/feishu-personal/register/{session_id}/cancel")
    def feishu_personal_cancel(session_id: str, user: dict = Depends(get_current_user)):
        session = db.get_feishu_registration_session(session_id)
        if session is None or session["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="注册会话不存在")
        _feishu_personal_manager().cancel_session(session_id)
        return {"ok": True}

    @router.delete("/me/feishu-personal")
    def feishu_personal_delete(user: dict = Depends(get_current_user)):
        """解绑个人机器人：擦除个人凭据与身份；共享飞书字段保持原值。"""
        _feishu_personal_manager().disable(user["id"])
        return {"ok": True}

    @router.post("/auth/logout")
    def logout(user: dict = Depends(get_current_user)):
        db.update_user_atomic(user["id"], {}, revoke_tokens=True)
        return {"ok": True}

    @router.post("/me/password")
    def change_password(body: PasswordChangeIn, user: dict = Depends(get_current_user)):
        if len(body.new_password) < MIN_PASSWORD_LEN:
            raise HTTPException(status_code=400, detail=f"新密码至少{MIN_PASSWORD_LEN}位")
        if len(body.new_password) > MAX_PASSWORD_LEN:
            raise HTTPException(status_code=400, detail=f"新密码最长{MAX_PASSWORD_LEN}位")
        # 微信/机器人自动创建的账号没有密码：已持有会话即可首次设密
        if user["password_hash"] and not auth.verify_password(body.old_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="原密码错误")
        db.update_user_password(user["id"], auth.hash_password(body.new_password))
        fresh = db.get_user(user["id"])
        return {
            "ok": True,
            "token": auth.create_token(
                fresh["id"], fresh["username"], secret, fresh.get("token_version") or 0
            ),
        }

    def _plaza_kol_visible(user: dict, kol: dict | None) -> bool:
        if kol is None:
            return False
        if user["is_admin"]:
            return True
        if kol["id"] not in db.visible_kol_ids(user["id"]):
            return False
        return not is_plaza_hidden(db, kol["platform"])

    # ---- 目录与订阅 ----
    @router.get("/catalog")
    def catalog(platform: str | None = None, category_id: int | None = None, user: dict = Depends(get_current_user)):
        if is_plaza_hidden(db, platform):
            return []
        kols = filter_plaza_rows(db, db.list_kols(platform, category_id, status=1))
        if not user["is_admin"]:
            visible = db.visible_kol_ids(user["id"])
            kols = [k for k in kols if k["id"] in visible]
        # 已订阅置顶 → 优先大V → 最近活跃：组内已订阅的靠前，其余保持原排序
        subscribed_types = db.subscribed_kol_types(user["id"])
        last_post_at = db.last_post_time_by_kol()
        kols.sort(
            key=lambda k: (
                k["id"] in subscribed_types,
                bool(k.get("priority")),
                last_post_at.get(k["id"]) or "",
            ),
            reverse=True,
        )
        favorite_ids = db.subscribed_favorite_ids(user["id"])
        secondary_ids = db.subscribed_secondary_ids(user["id"])
        combo_ids = [k["id"] for k in kols if k["platform"] == "combination"]
        quotes = db.list_cube_snapshots(combo_ids, "quote")
        rows = []
        for kol in kols:
            row = {
                **kol,
                "subscribed": kol["id"] in subscribed_types,
                "subscribe_type": subscribed_types.get(kol["id"], "post"),
                "favorite": kol["id"] in favorite_ids,
                "secondary": kol["id"] in secondary_ids,
            }
            if kol["platform"] == "combination":
                snap = quotes.get(kol["id"])
                row["quote"] = snap["payload"] if snap else None
            rows.append(row)
        return rows

    @router.get("/recommendations")
    def recommendations(user: dict = Depends(get_current_user), unsubscribed: bool = False):
        """按订阅人数推荐大V。unsubscribed=1 供动态页右侧栏，排除已订。"""
        rows = filter_plaza_rows(db, db.recommended_kols(user["id"], 16 if unsubscribed else 4))
        if unsubscribed:
            rows = [k for k in rows if not k["subscribed"]][:4]
        return [
            {
                "id": k["id"],
                "name": k["name"],
                "platform": k["platform"],
                "avatar_url": k["avatar_url"],
                "category_name": k["category_name"],
                "subscriber_count": int(k["subscriber_count"] or 0),
                "subscribed": bool(k["subscribed"]),
            }
            for k in rows
        ]

    @router.post("/subscriptions")
    def subscribe(body: SubscriptionIn, user: dict = Depends(get_current_user)):
        kol = db.get_kol(body.kol_id)
        if kol is None or not kol.get("enabled") or (
            not user["is_admin"]
            and (
                kol["id"] not in db.visible_kol_ids(user["id"])
                or is_plaza_hidden(db, kol["platform"])
            )
        ):
            raise HTTPException(status_code=404, detail="大V不存在")
        if body.type not in ("post", "reply", "both"):
            raise HTTPException(status_code=400, detail="订阅类型需为 post / reply / both")
        if not db.add_subscription(user["id"], body.kol_id, type=body.type):
            db.update_subscription_type(user["id"], body.kol_id, body.type)
        return {"ok": True}

    @router.put("/subscriptions/{kol_id}")
    def update_subscription_type(kol_id: int, body: SubscriptionTypeIn, user: dict = Depends(get_current_user)):
        kol = db.get_kol(kol_id)
        if kol is None:
            raise HTTPException(status_code=404, detail="大V不存在")
        if body.type not in ("post", "reply", "both"):
            raise HTTPException(status_code=400, detail="订阅类型需为 post / reply / both")
        if not db.update_subscription_type(user["id"], kol_id, body.type):
            raise HTTPException(status_code=404, detail="尚未订阅该大V")
        return {"ok": True}

    @router.put("/subscriptions/{kol_id}/favorite")
    def set_subscription_favorite(kol_id: int, body: SubscriptionFavoriteIn, user: dict = Depends(get_current_user)):
        if db.get_kol(kol_id) is None:
            raise HTTPException(status_code=404, detail="大V不存在")
        if not db.set_subscription_favorite(user["id"], kol_id, body.favorite):
            raise HTTPException(status_code=404, detail="尚未订阅该大V")
        return {"ok": True}

    @router.put("/subscriptions/{kol_id}/secondary")
    def set_subscription_secondary(kol_id: int, body: SubscriptionSecondaryIn, user: dict = Depends(get_current_user)):
        if db.get_kol(kol_id) is None:
            raise HTTPException(status_code=404, detail="大V不存在")
        if not db.set_subscription_secondary(user["id"], kol_id, body.secondary):
            raise HTTPException(status_code=404, detail="尚未订阅该大V")
        return {"ok": True}

    @router.put("/subscriptions/{kol_id}/hide-images")
    def set_subscription_hide_images(
        kol_id: int,
        body: SubscriptionHideImagesIn,
        user: dict = Depends(get_current_user),
    ):
        if db.get_kol(kol_id) is None:
            raise HTTPException(status_code=404, detail="大V不存在")
        if not db.set_subscription_hide_images(user["id"], kol_id, body.hide_images):
            raise HTTPException(status_code=404, detail="尚未订阅该大V")
        return {"ok": True}

    @router.delete("/subscriptions/{kol_id}")
    def unsubscribe(kol_id: int, user: dict = Depends(get_current_user)):
        db.remove_subscription(user["id"], kol_id)
        return {"ok": True}

    # ---- 财经新闻 ----
    def _news_service_or_503() -> NewsService:
        if news_service is None:
            raise HTTPException(status_code=503, detail="财经新闻服务不可用")
        return news_service

    @router.get("/news/sources")
    def news_sources(user: dict = Depends(get_current_user)):
        selected_ids = set(db.list_user_news_source_ids(user["id"]))
        statuses = {row["id"]: row for row in db.news_source_statuses(user["id"])}
        items = []
        for source in db.list_news_sources():
            status = statuses.get(source["id"], {"code": "paused", "last_success_at": None})
            items.append({
                "id": source["id"],
                "slug": source["slug"],
                "name": source["name"],
                "enabled": bool(source["enabled"]),
                "selected": source["id"] in selected_ids,
                "status": status["code"],
                "last_success_at": status["last_success_at"],
            })
        return {
            "items": items,
            "collection_enabled": db.get_setting("news_enabled") == "1",
        }

    @router.get("/news")
    def list_news(
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
        source_id: int | None = Query(None),
        q: str = Query("", max_length=200),
        user: dict = Depends(get_current_user),
    ):
        selected_ids = set(db.list_user_news_source_ids(user["id"]))
        if source_id is not None and source_id not in selected_ids:
            raise HTTPException(status_code=400, detail="只能筛选已选择的新闻来源")
        view_started_at = datetime.now(UTC).isoformat()
        anchor = (db.get_user(user["id"]) or {}).get("news_last_seen_at")
        rows = db.list_news_articles(
            user["id"], source_id=source_id, q=q, limit=limit, offset=offset
        )
        items = []
        for row in rows:
            row.pop("images", None)
            row["is_new"] = bool(anchor and row["published_at"] > anchor)
            items.append(row)
        total = db.count_news_articles(user["id"], source_id=source_id, q=q)
        return {
            "items": items,
            "offset": offset,
            "next_offset": offset + len(items),
            "has_more": offset + len(items) < total,
            "view_started_at": view_started_at,
            "source_statuses": db.news_source_statuses(user["id"]),
        }

    @router.post("/news/seen")
    def mark_news_seen(body: NewsSeenIn, user: dict = Depends(get_current_user)):
        raw = (body.view_started_at or "").strip()
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="时间必须是带时区的 ISO 8601 时间") from None
        if value.tzinfo is None or value.utcoffset() is None:
            raise HTTPException(status_code=400, detail="时间必须是带时区的 ISO 8601 时间")
        if value > datetime.now(UTC):
            raise HTTPException(status_code=400, detail="时间不能晚于当前时间")
        normalized = value.astimezone(UTC).isoformat()
        db.advance_news_seen(user["id"], normalized)
        return {"ok": True, "news_last_seen_at": normalized}

    @router.get("/news/{article_id}")
    def news_article(article_id: int, user: dict = Depends(get_current_user)):
        article = db.get_news_article(article_id, user_id=user["id"])
        if article is None:
            raise HTTPException(status_code=404, detail="文章不存在")
        article.pop("images", None)
        article.pop("has_image", None)
        return article

    @router.get("/news/{article_id}/images/{index}")
    def news_article_image(
        article_id: int, index: int, user: dict = Depends(get_download_user)
    ):
        try:
            body, content_type = _news_service_or_503().fetch_image(
                article_id, index, user["id"]
            )
        except NewsNotFound:
            raise HTTPException(status_code=404, detail="图片不存在") from None
        except NewsInputError:
            raise HTTPException(status_code=400, detail="图片地址不安全或类型不受支持") from None
        except NewsUpstreamError:
            raise HTTPException(status_code=502, detail="图片暂时无法加载") from None
        return Response(
            content=body,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ---- 管理员财经新闻 ----
    def _admin_news_source_row(source: dict, include_archived: bool = True) -> dict:
        feeds = db.list_news_feeds(source["id"], include_archived=include_archived)
        return {
            **source,
            "enabled": bool(source["enabled"]),
            "feeds": feeds,
            "article_count": db.count_news_articles_for_source(source["id"]),
        }

    def _admin_news_source(source_id: int) -> dict:
        source = db.get_news_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="媒体不存在")
        return source

    def _news_url_or_400(url: str) -> tuple[str, str]:
        raw = (url or "").strip()
        if not raw or len(raw) > 2048:
            raise HTTPException(status_code=400, detail="Feed URL 长度必须为 1-2048 个字符")
        try:
            return raw, normalize_feed_url(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Feed URL 必须是安全的 HTTP(S) 地址") from None

    def _news_validate_or_error(url: str) -> dict:
        try:
            return _news_service_or_503().validate_feed(url)
        except NewsInputError:
            raise HTTPException(status_code=400, detail="Feed 地址无法验证") from None
        except NewsUpstreamError:
            raise HTTPException(status_code=502, detail="Feed 暂时无法访问") from None

    def _news_refresh_ids(feed_ids: list[int], response: Response) -> dict:
        if db.get_setting("news_enabled") != "1":
            raise HTTPException(status_code=409, detail="财经新闻采集已关闭")
        accepted, busy = [], []
        service = _news_service_or_503()
        for feed_id in feed_ids:
            if service.submit_feed(feed_id):
                accepted.append(feed_id)
            else:
                busy.append(feed_id)
        response.status_code = 202
        return {"accepted_feed_ids": accepted, "busy_feed_ids": busy}

    @router.get("/admin/news/settings")
    def admin_news_settings(admin: dict = Depends(require_admin)):
        del admin
        try:
            interval = int(db.get_setting("news_refresh_interval_seconds") or 600)
        except ValueError:
            interval = 600
        return {
            "enabled": db.get_setting("news_enabled") == "1",
            "visible": db.get_setting("news_visible") != "0",
            "refresh_interval_minutes": max(5, min(1440, interval // 60)),
        }

    @router.patch("/admin/news/settings")
    def update_admin_news_settings(
        body: NewsSettingsIn, admin: dict = Depends(require_admin)
    ):
        values = {}
        if "enabled" in body.model_fields_set:
            values["news_enabled"] = "1" if body.enabled else "0"
        if "visible" in body.model_fields_set:
            values["news_visible"] = "1" if body.visible else "0"
        if "refresh_interval_minutes" in body.model_fields_set:
            if body.refresh_interval_minutes is None or not 5 <= body.refresh_interval_minutes <= 1440:
                raise HTTPException(status_code=400, detail="刷新周期必须为 5-1440 分钟")
            values["news_refresh_interval_seconds"] = str(body.refresh_interval_minutes * 60)
        if values:
            db.set_settings_atomic(values)
            _audit(admin, "news_settings_update", "", json.dumps(values, ensure_ascii=False))
        return admin_news_settings(admin)

    @router.get("/admin/news/sources")
    def admin_news_sources(
        include_archived: bool = Query(False), admin: dict = Depends(require_admin)
    ):
        del admin
        return {
            "items": [
                _admin_news_source_row(source, include_archived)
                for source in db.list_news_sources(include_archived=include_archived)
            ]
        }

    @router.post("/admin/news/sources")
    def create_admin_news_source(
        body: NewsSourceCreateIn, admin: dict = Depends(require_admin)
    ):
        name = (body.name or "").strip()
        if not 1 <= len(name) <= 60:
            raise HTTPException(status_code=400, detail="媒体名称长度必须为 1-60 个字符")
        try:
            source_id = db.add_news_source(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "news_source_create", str(source_id), name)
        return _admin_news_source_row(_admin_news_source(source_id))

    @router.patch("/admin/news/sources/{source_id}")
    def update_admin_news_source(
        source_id: int, body: NewsSourceUpdateIn, admin: dict = Depends(require_admin)
    ):
        _admin_news_source(source_id)
        kwargs = {}
        if "name" in body.model_fields_set:
            kwargs["name"] = body.name
        if "enabled" in body.model_fields_set:
            kwargs["enabled"] = body.enabled
        try:
            source = db.update_news_source(source_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "news_source_update", str(source_id), json.dumps(kwargs, ensure_ascii=False))
        return _admin_news_source_row(source)

    @router.post("/admin/news/sources/{source_id}/archive")
    def archive_admin_news_source(source_id: int, admin: dict = Depends(require_admin)):
        _admin_news_source(source_id)
        db.set_news_source_archived(source_id, True)
        _audit(admin, "news_source_archive", str(source_id))
        return {"ok": True}

    @router.post("/admin/news/sources/{source_id}/restore")
    def restore_admin_news_source(source_id: int, admin: dict = Depends(require_admin)):
        _admin_news_source(source_id)
        db.set_news_source_archived(source_id, False)
        _audit(admin, "news_source_restore", str(source_id))
        return {"ok": True}

    @router.delete("/admin/news/sources/{source_id}")
    def delete_admin_news_source(source_id: int, admin: dict = Depends(require_admin)):
        source = _admin_news_source(source_id)
        db.delete_news_source(source_id)
        _audit(admin, "news_source_delete", str(source_id), source["name"])
        return {"ok": True}

    @router.post("/admin/news/sources/{source_id}/refresh")
    def refresh_admin_news_source(
        source_id: int, response: Response, admin: dict = Depends(require_admin)
    ):
        source = _admin_news_source(source_id)
        if source["archived_at"] or not source["enabled"]:
            raise HTTPException(status_code=400, detail="媒体已停用或归档")
        feed_ids = [
            feed["id"] for feed in db.list_news_feeds(source_id)
            if feed["enabled"]
        ]
        result = _news_refresh_ids(feed_ids, response)
        _audit(admin, "news_source_refresh", str(source_id), str(result))
        return result

    @router.post("/admin/news/feeds/validate")
    def validate_admin_news_feed(
        body: NewsFeedValidateIn, admin: dict = Depends(require_admin)
    ):
        del admin
        _, normalized = _news_url_or_400(body.url)
        return _news_validate_or_error(normalized)

    @router.post("/admin/news/sources/{source_id}/feeds")
    def create_admin_news_feed(
        source_id: int, body: NewsFeedCreateIn, admin: dict = Depends(require_admin)
    ):
        source = _admin_news_source(source_id)
        if source["archived_at"]:
            raise HTTPException(status_code=400, detail="归档媒体不能新增 Feed")
        name = (body.name or "").strip()
        if not 1 <= len(name) <= 80:
            raise HTTPException(status_code=400, detail="Feed 名称长度必须为 1-80 个字符")
        raw_url, normalized = _news_url_or_400(body.url)
        if db.get_news_feed_by_normalized_url(normalized):
            raise HTTPException(status_code=400, detail="Feed URL 已存在")
        _news_validate_or_error(normalized)
        try:
            feed_id = db.add_news_feed(source_id, name, raw_url, normalized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "news_feed_create", str(feed_id), news_audit_url(raw_url))
        return db.get_news_feed(feed_id)

    @router.patch("/admin/news/feeds/{feed_id}")
    def update_admin_news_feed(
        feed_id: int, body: NewsFeedUpdateIn, admin: dict = Depends(require_admin)
    ):
        feed = db.get_news_feed(feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed 不存在")
        kwargs = {}
        if "name" in body.model_fields_set:
            name = (body.name or "").strip()
            if not 1 <= len(name) <= 80:
                raise HTTPException(status_code=400, detail="Feed 名称长度必须为 1-80 个字符")
            kwargs["name"] = name
        if "enabled" in body.model_fields_set:
            kwargs["enabled"] = body.enabled
        if "url" in body.model_fields_set and body.url is not None:
            raw_url, normalized = _news_url_or_400(body.url)
            duplicate = db.get_news_feed_by_normalized_url(normalized)
            if duplicate and duplicate["id"] != feed_id:
                raise HTTPException(status_code=400, detail="Feed URL 已存在")
            if raw_url != feed["url"] or normalized != feed["normalized_url"]:
                _news_validate_or_error(normalized)
            kwargs.update(url=raw_url, normalized_url=normalized)
        try:
            saved = db.update_news_feed(feed_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "news_feed_update", str(feed_id), news_audit_url(saved["url"]))
        return saved

    @router.post("/admin/news/feeds/{feed_id}/archive")
    def archive_admin_news_feed(feed_id: int, admin: dict = Depends(require_admin)):
        if db.get_news_feed(feed_id) is None:
            raise HTTPException(status_code=404, detail="Feed 不存在")
        db.set_news_feed_archived(feed_id, True)
        _audit(admin, "news_feed_archive", str(feed_id))
        return {"ok": True}

    @router.post("/admin/news/feeds/{feed_id}/restore")
    def restore_admin_news_feed(feed_id: int, admin: dict = Depends(require_admin)):
        if db.get_news_feed(feed_id) is None:
            raise HTTPException(status_code=404, detail="Feed 不存在")
        db.set_news_feed_archived(feed_id, False)
        _audit(admin, "news_feed_restore", str(feed_id))
        return {"ok": True}

    @router.post("/admin/news/feeds/{feed_id}/refresh")
    def refresh_admin_news_feed(
        feed_id: int, response: Response, admin: dict = Depends(require_admin)
    ):
        feed = db.get_news_feed(feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed 不存在")
        if feed["archived_at"] or not feed["enabled"]:
            raise HTTPException(status_code=400, detail="Feed 已停用或归档")
        result = _news_refresh_ids([feed_id], response)
        _audit(admin, "news_feed_refresh", str(feed_id), str(result))
        return result

    @router.post("/admin/news/refresh")
    def refresh_all_admin_news(
        response: Response, admin: dict = Depends(require_admin)
    ):
        feed_ids = []
        for source in db.list_news_sources():
            if source["enabled"] and not source["archived_at"]:
                feed_ids.extend(
                    feed["id"] for feed in db.list_news_feeds(source["id"])
                    if feed["enabled"]
                )
        result = _news_refresh_ids(feed_ids, response)
        _audit(admin, "news_refresh", "", str(result))
        return result

    @router.get("/my/subscriptions")
    def my_subscriptions(user: dict = Depends(get_current_user)):
        return filter_plaza_rows(db, db.list_subscriptions(user["id"]))

    @router.get("/market/indices")
    def market_indices(group: Literal["auto", "day", "night"] = "auto", user: dict = Depends(get_current_user)):
        return market_quotes.snapshot(group)

    @router.get("/my/feed")
    def my_feed(
        limit: int = 100,
        offset: int = 0,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        favorite: int = 0,
        tag: str | None = None,
        include_secondary: int = 0,
        since_id: int | None = None,  # 仅返回 id 大于该值的帖子（新帖检测/计数，配合现有筛选）
        user: dict = Depends(get_current_user),
    ):
        kol_ids = sorted(db.readable_subscribed_kol_ids(user["id"], user["is_admin"]))
        return apply_twitter_feed(
            db.list_feed_posts(
                kol_ids,
                limit=bounded_limit(limit),
                user_id=user["id"],
                offset=max(offset, 0),
                platform=platform,
                category_id=category_id,
                q=q,
                favorite=bool(favorite),
                tag=tag,
                include_secondary=bool(include_secondary),
                since_id=since_id,
                exclude_platforms=plaza_hidden_platforms(db),
            ),
            user,
        )

    @router.get("/live/wscn")
    def wscn_live(
        cursor: str = "",
        limit: int = 30,
        since_id: int | None = None,
        user: dict = Depends(get_current_user),
    ):
        """华尔街见闻 7x24 全球直播快讯（代理 + 短缓存，不入库）。"""
        del user  # 仅要求登录，不做 per-user 状态
        # cursor 进缓存键与上游查询串：不校验的话可被任意字符串撑爆缓存
        if cursor and (not cursor.isdigit() or len(cursor) > 16):
            raise HTTPException(status_code=400, detail="无效的 cursor")
        try:
            data = _fetch_wscn_lives(cursor=cursor, limit=bounded_limit(limit, default=30))
        except httpx.HTTPError as exc:
            logger.warning("wscn live fetch failed: %s", exc)
            raise HTTPException(status_code=502, detail="快讯源暂时不可用") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if since_id is not None and since_id > 0:
            newer = [row for row in data["items"] if row["id"] > since_id]
            data = {**data, "items": newer}
        return data

    @router.get("/kols/{kol_id}")
    def get_kol(kol_id: int, user: dict = Depends(get_current_user)):
        kol = db.get_kol(kol_id)
        if not _plaza_kol_visible(user, kol):
            raise HTTPException(status_code=404, detail="大V不存在")
        sub = db.get_subscription(user["id"], kol_id)
        kol["subscribed"] = sub is not None
        kol["subscribe_type"] = (sub or {}).get("type") or "post"
        kol["favorite"] = bool(sub and sub.get("favorite"))
        kol["secondary"] = bool(sub and sub.get("secondary"))
        if user["is_admin"]:
            kol["visible_users"] = db.acl_usernames(kol_id)
        # 组合详情附带实时净值/涨跌快照（抓取端定时写入，无则前端隐藏）
        if kol["platform"] == "combination":
            snap = db.get_cube_snapshot(kol_id, "quote")
            kol["quote"] = snap["payload"] if snap else None
            kol["quote_at"] = snap["fetched_at"] if snap else ""
        return kol

    @router.get("/kols/{kol_id}/posts")
    def kol_posts(kol_id: int, limit: int = 100, user: dict = Depends(get_current_user)):
        kol = db.get_kol(kol_id)
        if not _plaza_kol_visible(user, kol):
            raise HTTPException(status_code=404, detail="大V不存在")
        posts = apply_twitter_feed(
            db.list_posts(limit=bounded_limit(limit), kol_id=kol_id, order_published=True),
            user,
        )
        subscription = db.get_subscription(user["id"], kol_id)
        if subscription and subscription["hide_images"]:
            return [{**post, "images": []} for post in posts]
        return posts

    @router.get("/kols/{kol_id}/holdings")
    def kol_holdings(kol_id: int, user: dict = Depends(get_current_user)):
        """组合当前持仓快照（抓取端定时写入 cube_snapshots，页面不依赖雪球在线）。"""
        kol = db.get_kol(kol_id)
        if not _plaza_kol_visible(user, kol):
            raise HTTPException(status_code=404, detail="大V不存在")
        snap = db.get_cube_snapshot(kol_id, "holdings")
        return _cube_holdings_response(snap)

    @router.get("/kols/{kol_id}/nav")
    def kol_nav(kol_id: int, user: dict = Depends(get_current_user)):
        """组合净值序列 [{date, value}]（抓取端定时写入，页面画曲线用）。"""
        kol = db.get_kol(kol_id)
        if not _plaza_kol_visible(user, kol):
            raise HTTPException(status_code=404, detail="大V不存在")
        snap = db.get_cube_snapshot(kol_id, "nav")
        return _cube_nav_response(snap)

    @router.post("/kol-requests")
    def create_kol_request(
        body: KolRequestIn,
        background_tasks: BackgroundTasks,
        user: dict = Depends(get_current_user),
    ):
        """用户申请添加大V，管理员审批后入库。"""
        if body.platform not in ALLOWED_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {body.platform}")
        external_id, err = kol_requests.normalize_kol_request_input(body.platform, body.external_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if body.category_id is None:
            raise HTTPException(status_code=400, detail="请选择分类")
        try:
            request_id = db.add_kol_request(
                body.platform, external_id, user["id"], name=body.name, category_id=body.category_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # 通知管理员有新申请：放后台执行（多管理员×多渠道串行可达数十秒，
        # 不能让申请人干等）；响应发出后仍在线程池里跑，不影响用户侧延迟
        def _notify_bg():
            try:
                _notify_admins_new_request(
                    body.platform, body.name or external_id, user, request_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("大V申请通知管理员失败 err=%s", exc)

        background_tasks.add_task(_notify_bg)
        return {"ok": True}

    @router.get("/my/kol-requests")
    def my_kol_requests(user: dict = Depends(get_current_user)):
        return db.list_kol_requests(user_id=user["id"])

    @router.get("/admin/kol-requests", dependencies=[Depends(require_admin)])
    def admin_kol_requests(status: str | None = None):
        return db.list_kol_requests(status)

    @router.post("/admin/kol-requests/{request_id}/approve", dependencies=[Depends(require_admin)])
    def approve_kol_request(request_id: int, admin: dict = Depends(require_admin)):
        try:
            return kol_requests.approve_kol_request(db, request_id, admin, notifiers_config)
        except kol_requests.KolRequestNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except kol_requests.KolRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @router.post("/admin/kol-requests/{request_id}/reject", dependencies=[Depends(require_admin)])
    def reject_kol_request(request_id: int, admin: dict = Depends(require_admin)):
        try:
            kol_requests.reject_kol_request(db, request_id, admin)
        except kol_requests.KolRequestNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return {"ok": True}

    @router.post("/admin/register-codes", dependencies=[Depends(require_admin)])
    def generate_register_codes(body: RegisterCodeGenIn, admin: dict = Depends(require_admin)):
        """批量生成一次性注册码。"""
        count = max(1, min(body.count, 100))
        note = (body.note or "").strip()
        if len(note) > REGISTER_NOTE_MAX:
            raise HTTPException(status_code=400, detail=f"备注最长{REGISTER_NOTE_MAX}字")
        if body.expires_in_days is not None and body.expires_in_days not in REGISTER_EXPIRE_DAYS:
            raise HTTPException(status_code=400, detail="有效期需为 1、7、30 天或永不过期")
        expires_at = None
        if body.expires_in_days is not None:
            expires_at = (
                datetime.now(UTC) + timedelta(days=body.expires_in_days)
            ).strftime("%Y-%m-%d %H:%M:%S")
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        existing = {r["code"] for r in db.list_register_codes()}
        batch_id = secrets.token_hex(8)
        codes = []
        while len(codes) < count:
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            if code in existing:
                continue
            existing.add(code)
            db.add_register_code(
                code,
                note=note,
                batch_id=batch_id,
                expires_at=expires_at,
                created_by=admin["id"],
            )
            codes.append(code)
        _audit(
            admin,
            "generate_register_codes",
            batch_id,
            f"count={len(codes)} note={note} expires_in_days={body.expires_in_days}",
        )
        return {
            "codes": codes,
            "count": len(codes),
            "batch_id": batch_id,
            "expires_at": expires_at or "",
            "note": note,
        }

    @router.get("/admin/register-codes", dependencies=[Depends(require_admin)])
    def list_register_codes():
        return db.list_register_codes()

    def _cred_preview(value: str) -> str:
        return (value[:8] + "…") if len(value or "") > 8 else (value or "")

    def _cookie_status(key: str, time_key: str) -> dict:
        cookie = db.get_setting(key) or ""
        return {
            "set": bool(cookie),
            "updated_at": db.get_setting(time_key) or "",
            "preview": "已配置" if cookie else "",
        }

    @router.get("/admin/xueqiu-cookie", dependencies=[Depends(require_admin)])
    def get_xueqiu_cookie():
        return _cookie_status(XUEQIU_COOKIE_KEY, XUEQIU_COOKIE_TIME_KEY)

    @router.get("/admin/twitter-cookie", dependencies=[Depends(require_admin)])
    def get_twitter_cookie():
        status = _cookie_status(TWITTER_COOKIE_KEY, TWITTER_COOKIE_TIME_KEY)
        if not status["set"]:
            env = os.environ.get("TWITTER_COOKIE", "")
            if env:
                status = {
                    "set": True,
                    "updated_at": "",
                    "preview": "已配置",
                    "from_env": True,
                }
        return status

    @router.get("/admin/plaza-sources", dependencies=[Depends(require_admin)])
    def get_plaza_sources():
        return {"sources": plaza_source_rows(db)}

    @router.put("/admin/plaza-sources")
    def update_plaza_sources(body: PlazaSourcesIn, admin: dict = Depends(require_admin)):
        try:
            sources = set_plaza_visibility(db, body.visibility or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        changed = ",".join(f"{p}={m}" for p, m in sorted((body.visibility or {}).items()))
        _audit(admin, "update_plaza_sources", "", changed[:200])
        return {"sources": sources}

    @router.get("/admin/polling-config", dependencies=[Depends(require_admin)])
    def get_polling_config():
        return _effective_polling()

    @router.put("/admin/polling-config", dependencies=[Depends(require_admin)])
    def update_polling_config(body: PollingConfigIn, admin: dict = Depends(require_admin)):
        changed = []
        for name, cfg_key, _stat, lo, hi in POLLING_FIELDS:
            value = getattr(body, name)
            if value is None:
                continue
            if not (lo <= value <= hi):
                raise HTTPException(status_code=400, detail=f"{name} 需在 {lo}-{hi} 之间")
            db.set_setting(cfg_key, str(value))
            changed.append(name)
        if body.translate_twitter_content is not None:
            db.set_setting(
                "config_translate_twitter_content",
                "1" if body.translate_twitter_content else "0",
            )
            changed.append("translate_twitter_content")
        if body.telegram_rich_messages is not None:
            from .telegram_rich_flag import set_telegram_rich_messages

            set_telegram_rich_messages(db, body.telegram_rich_messages)
            changed.append("telegram_rich_messages")
        if body.zsxq_max_pages is not None:
            if not (1 <= body.zsxq_max_pages <= 20):
                raise HTTPException(status_code=400, detail="zsxq_max_pages 需在 1-20 之间")
            db.set_setting("zsxq_max_pages", str(body.zsxq_max_pages))
            changed.append("zsxq_max_pages")
        for name, lo, hi in (
            ("zsxq_fetch_delay_seconds", 0.2, 10.0),
            ("zsxq_file_delay_seconds", 0.2, 10.0),
        ):
            value = getattr(body, name)
            if value is None:
                continue
            if not (lo <= value <= hi):
                raise HTTPException(status_code=400, detail=f"{name} 需在 {lo}-{hi} 之间")
            db.set_setting(name, str(value))
            changed.append(name)
        if body.zsxq_prefetch_files is not None:
            db.set_setting("zsxq_prefetch_files", "1" if body.zsxq_prefetch_files else "0")
            changed.append("zsxq_prefetch_files")
        if body.zsxq_fetch_comments is not None:
            db.set_setting("zsxq_fetch_comments", "1" if body.zsxq_fetch_comments else "0")
            changed.append("zsxq_fetch_comments")
        if body.zsxq_max_comment_pages is not None:
            if not (1 <= body.zsxq_max_comment_pages <= 10):
                raise HTTPException(status_code=400, detail="zsxq_max_comment_pages 需在 1-10 之间")
            db.set_setting("zsxq_max_comment_pages", str(body.zsxq_max_comment_pages))
            changed.append("zsxq_max_comment_pages")
        if body.zsxq_comment_budget is not None:
            if not (1 <= body.zsxq_comment_budget <= 200):
                raise HTTPException(status_code=400, detail="zsxq_comment_budget 需在 1-200 之间")
            db.set_setting("zsxq_comment_budget", str(body.zsxq_comment_budget))
            changed.append("zsxq_comment_budget")
        if body.zsxq_app_channel is not None:
            db.set_setting("zsxq_app_channel", "1" if body.zsxq_app_channel else "0")
            changed.append("zsxq_app_channel")
        if body.zsxq_app_device is not None:
            dev = body.zsxq_app_device.strip()
            if not dev or len(dev) > 64:
                raise HTTPException(status_code=400, detail="zsxq_app_device 需 1-64 字符")
            db.set_setting("zsxq_app_device", dev)
            changed.append("zsxq_app_device")
        if body.zsxq_ws_enabled is not None:
            db.set_setting("zsxq_ws_enabled", "1" if body.zsxq_ws_enabled else "0")
            changed.append("zsxq_ws_enabled")
        if body.zsxq_ws_address is not None:
            addr = body.zsxq_ws_address.strip()
            if addr and not addr.startswith(("ws://", "wss://")):
                raise HTTPException(status_code=400, detail="zsxq_ws_address 需以 ws:// 或 wss:// 开头")
            db.set_setting("zsxq_ws_address", addr)
            changed.append("zsxq_ws_address")
        _audit(admin, "update_polling_config", "", ",".join(changed))
        return _effective_polling()

    @router.post("/admin/xueqiu-cookie", dependencies=[Depends(require_admin)])
    def set_xueqiu_cookie(body: CookieIn, admin: dict = Depends(require_admin)):
        cookie = body.cookie.strip()
        if not cookie:
            raise HTTPException(status_code=400, detail="cookie 不能为空")
        db.set_setting(XUEQIU_COOKIE_KEY, cookie)
        db.set_setting(XUEQIU_COOKIE_TIME_KEY, str(int(time.time())))
        try:
            write_xueqiu_seed_cookie(cookie)
        except Exception:  # noqa: BLE001 - sidecar sync must not fail the admin request
            logger.warning("雪球 sidecar seed cookie 写入失败")
        _audit(admin, "set_xueqiu_cookie", "", f"len={len(cookie)}")
        return {"ok": True}

    @router.post("/admin/twitter-cookie", dependencies=[Depends(require_admin)])
    def set_twitter_cookie(body: CookieIn, admin: dict = Depends(require_admin)):
        cookie = body.cookie.strip()
        if not cookie:
            raise HTTPException(status_code=400, detail="cookie 不能为空")
        if "auth_token=" not in cookie or "ct0=" not in cookie:
            raise HTTPException(status_code=400, detail="X Cookie 需包含 auth_token 与 ct0")
        db.set_setting(TWITTER_COOKIE_KEY, cookie)
        db.set_setting(TWITTER_COOKIE_TIME_KEY, str(int(time.time())))
        _audit(admin, "set_twitter_cookie", "", f"len={len(cookie)}")
        return {"ok": True}

    def _imgbed_status() -> dict:
        from . import imgbed as imgbed_mod

        stored_url = (db.get_setting("imgbed_base_url") or "").strip()
        stored_token = (db.get_setting("imgbed_token") or "").strip()
        stored_channel = (db.get_setting("imgbed_channel") or "").strip()
        stored_name = (db.get_setting("imgbed_channel_name") or "").strip()
        stored_folder = (db.get_setting("imgbed_folder") or "").strip()
        env_url = os.environ.get("IMGBED_BASE_URL", "").strip()
        env_token = os.environ.get("IMGBED_TOKEN", "").strip()
        runtime = imgbed_mod.current_config()
        base_url = stored_url or env_url or (runtime.base_url if runtime else "")
        token = stored_token or env_token or (runtime.token if runtime else "")
        channel = stored_channel or (runtime.channel if runtime else "telegram") or "telegram"
        channel_name = stored_name or (runtime.channel_name if runtime else "vpush-imgbed") or "vpush-imgbed"
        folder = stored_folder or (runtime.folder if runtime else "vpush") or "vpush"
        counts = {
            row["status"]: row["n"]
            for row in db._rows("SELECT status, COUNT(*) AS n FROM hosted_images GROUP BY status")
        }
        return {
            "project": "CloudFlare-ImgBed",
            "project_url": "https://github.com/MarSeventh/CloudFlare-ImgBed",
            "base_url": base_url,
            "token_set": bool(token),
            "token_from_env": bool(env_token) and not stored_token,
            "updated_at": db.get_setting("imgbed_updated_at") or "",
            "channel": channel,
            "channel_name": channel_name,
            "folder": folder,
            "enabled": bool(base_url and token),
            "ready_count": int(counts.get("ready") or 0),
            "pending_count": int(counts.get("pending") or 0),
            "failed_count": int(counts.get("failed") or 0),
            "last_check_error": db.get_setting("imgbed_last_check_error") or "",
            "retention_days": imgbed_mod.retention_days(db),
        }

    @router.get("/admin/imgbed", dependencies=[Depends(require_admin)])
    def get_imgbed():
        return _imgbed_status()

    @router.delete("/admin/imgbed")
    def clear_imgbed(admin: dict = Depends(require_admin)):
        from . import imgbed as imgbed_mod

        for key in (
            "imgbed_base_url", "imgbed_token", "imgbed_channel", "imgbed_channel_name",
            "imgbed_folder", "imgbed_updated_at", "imgbed_last_check_error",
        ):
            db.set_setting(key, "")
        imgbed_mod.reset_to_env()
        _audit(admin, "clear_imgbed", "", "")
        return _imgbed_status()

    @router.put("/admin/imgbed")
    def set_imgbed(body: ImgbedIn, admin: dict = Depends(require_admin)):
        from urllib.parse import urlparse

        from . import imgbed as imgbed_mod

        base_url = (body.base_url or "").strip().rstrip("/")
        token = (body.token or "").strip()
        current = _imgbed_status()
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise HTTPException(status_code=400, detail="图床地址须为 https 域名，不要带账号密码")
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base_url = current["base_url"]
        if not token:
            if not current["token_set"]:
                raise HTTPException(status_code=400, detail="请填写 API 密钥")
            token = (db.get_setting("imgbed_token") or "").strip() or os.environ.get("IMGBED_TOKEN", "").strip()
        channel = (body.channel or current["channel"] or "telegram").strip() or "telegram"
        channel_name = (body.channel_name or current["channel_name"] or "vpush-imgbed").strip() or "vpush-imgbed"
        folder = (body.folder or current["folder"] or "vpush").strip().strip("/") or "vpush"
        db.set_setting("imgbed_base_url", base_url)
        db.set_setting("imgbed_token", token)
        db.set_setting("imgbed_channel", channel)
        db.set_setting("imgbed_channel_name", channel_name)
        db.set_setting("imgbed_folder", folder)
        if body.retention_days is not None:
            if body.retention_days < 0 or body.retention_days > imgbed_mod.MAX_RETENTION_DAYS:
                raise HTTPException(status_code=400, detail="图片保留天数须在 0–3650")
            db.set_setting("imgbed_retention_days", str(int(body.retention_days)))
        db.set_setting("imgbed_updated_at", str(int(time.time())))
        imgbed_mod.apply_runtime(base_url, token, channel, channel_name, folder)
        _, check_error = imgbed_mod.probe(base_url)
        db.set_setting("imgbed_last_check_error", check_error)
        _audit(admin, "set_imgbed", "", base_url)
        return _imgbed_status()

    def _parse_turnstile_hostnames(raw: str) -> str:
        parts = [h.strip().lower().rstrip(".") for h in (raw or "").split(",") if h.strip()]
        if not parts:
            raise HTTPException(status_code=400, detail="请填写允许域名")
        for host in parts:
            if "/" in host or ":" in host or not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
                raise HTTPException(status_code=400, detail=f"域名无效: {host}")
        return ",".join(parts)

    @router.get("/admin/turnstile", dependencies=[Depends(require_admin)])
    def get_turnstile():
        return _turnstile_admin_status()

    @router.put("/admin/turnstile")
    def set_turnstile(body: TurnstileIn, admin: dict = Depends(require_admin)):
        current = _turnstile_admin_status()
        sitekey = (body.sitekey or "").strip() or current["sitekey"]
        secret = (body.secret or "").strip()
        if not secret:
            secret = (db.get_setting("turnstile_secret") or "").strip() or _ts_secret
        hosts_raw = (body.hostnames or "").strip() or current["hostnames"]
        if body.enabled:
            hostnames = _parse_turnstile_hostnames(hosts_raw)
        elif (body.hostnames or "").strip():
            hostnames = _parse_turnstile_hostnames(body.hostnames)
        else:
            hostnames = current["hostnames"] or ""
        if body.enabled and not sitekey:
            raise HTTPException(status_code=400, detail="请填写站点密钥")
        if body.enabled and not secret:
            raise HTTPException(status_code=400, detail="请填写密钥")
        db.set_setting("turnstile_enabled", "1" if body.enabled else "0")
        if (body.sitekey or "").strip():
            db.set_setting("turnstile_site_key", sitekey)
        if (body.secret or "").strip():
            db.set_setting("turnstile_secret", secret)
        db.set_setting("turnstile_hostnames", hostnames)
        _audit(
            admin,
            "set_turnstile",
            "",
            f"enabled={int(body.enabled)} sitekey={sitekey} hostnames={hostnames} secret={'set' if secret else 'missing'}",
        )
        return _turnstile_admin_status()

    @router.get("/admin/zsxq-cookie", dependencies=[Depends(require_admin)])
    def get_zsxq_cookie():
        status = _cookie_status(ZSXQ_COOKIE_KEY, ZSXQ_COOKIE_TIME_KEY)
        if not status["set"]:
            env = os.environ.get("ZSXQ_COOKIE") or os.environ.get("ZSXQ_ACCESS_TOKEN", "")
            if env:
                status = {
                    "set": True,
                    "updated_at": "",
                    "preview": "已配置",
                    "from_env": True,
                }
        return status

    @router.post("/admin/zsxq-cookie", dependencies=[Depends(require_admin)])
    def set_zsxq_cookie(body: CookieIn, admin: dict = Depends(require_admin)):
        raw = body.cookie.strip()
        if not raw:
            raise HTTPException(status_code=400, detail="cookie 不能为空")
        cookie = raw.split("=")[-1].strip() if ("=" in raw or ";" in raw) else raw.strip()
        if not cookie:
            raise HTTPException(status_code=400, detail="cookie 不能为空")
        db.set_setting(ZSXQ_COOKIE_KEY, cookie)
        db.set_setting(ZSXQ_COOKIE_TIME_KEY, str(int(time.time())))
        _audit(admin, "set_zsxq_cookie", "", f"len={len(cookie)}")
        return {"ok": True}

    _COOKIE_CLEAR = {
        "xueqiu": (XUEQIU_COOKIE_KEY, XUEQIU_COOKIE_TIME_KEY),
        "weibo": (WEIBO_COOKIE_KEY, "weibo_cookie_updated_at"),
        "twitter": (TWITTER_COOKIE_KEY, TWITTER_COOKIE_TIME_KEY),
        "ima": (IMA_COOKIE_KEY, IMA_COOKIE_TIME_KEY),
        "zsxq": (ZSXQ_COOKIE_KEY, ZSXQ_COOKIE_TIME_KEY),
    }

    @router.delete("/admin/cookies/{kind}", dependencies=[Depends(require_admin)])
    def clear_saved_cookie(kind: str, admin: dict = Depends(require_admin)):
        keys = _COOKIE_CLEAR.get(kind)
        if not keys:
            raise HTTPException(status_code=400, detail="未知 Cookie 源")
        db.set_setting(keys[0], "")
        db.set_setting(keys[1], "")
        if kind == "xueqiu":
            try:
                write_xueqiu_seed_cookie("")
            except Exception:  # noqa: BLE001 - sidecar sync must not fail the admin request
                logger.warning("雪球 sidecar seed cookie 清空失败")
        _audit(admin, "clear_cookie", kind, "")
        return {"ok": True}

    @router.post("/admin/zsxq-cache/purge", dependencies=[Depends(require_admin)])
    def purge_zsxq_cache(admin: dict = Depends(require_admin)):
        result = purge_unreferenced_zsxq_files(db)
        _audit(admin, "purge_zsxq_cache", "", f"deleted={result['deleted']}")
        return result

    def _zsxq_file_hits(file_id: str) -> list[dict]:
        return db.find_zsxq_file_posts(file_id)

    def _zsxq_file_name(file_id: str) -> str:
        for row in _zsxq_file_hits(file_id):
            detail = row.get("detail") or ""
            try:
                files = (json.loads(detail) if isinstance(detail, str) else detail).get("files") or []
            except Exception:
                files = []
            for f in files:
                if str(f.get("file_id")) == file_id and f.get("name"):
                    return str(f["name"])
        return ""

    def _stored_zsxq_url(file_id: str):
        """库里已有的未过期签名 URL；缺失/过期返回空串。签名 URL e= 为过期时间戳。"""
        now = int(time.time())
        for row in _zsxq_file_hits(file_id):
            detail = row.get("detail") or ""
            try:
                files = (json.loads(detail) if isinstance(detail, str) else detail).get("files") or []
            except Exception:
                files = []
            for f in files:
                if str(f.get("file_id")) != file_id:
                    continue
                u = str(f.get("url") or "")
                if not u:
                    continue
                m = re.search(r"[?&]e=(\d+)", u)
                if not m or int(m.group(1)) > now:
                    return u
        return ""

    def _zsxq_cd(name: str, fallback: str) -> str:
        """ASCII 安全的 RFC5987 Content-Disposition，避免 UTF-8 撞 Starlette latin-1 头。"""
        from urllib.parse import quote

        n = name or fallback
        ascii_name = "".join(
            c for c in n if c.isascii() and c not in '"\\\r\n' and ord(c) >= 32
        ) or "download"
        return (
            f"attachment; filename=\"{ascii_name}\"; "
            f"filename*=UTF-8''{quote(n)}"
        )

    def _write_back_zsxq_url(file_id: str, name: str, local_url: str) -> None:
        """把落盘后的本地 URL 写回库里该文件的 files[].url，供列表直接展示。"""
        updates = []
        for row in _zsxq_file_hits(file_id):
            detail = row.get("detail") or ""
            try:
                d = json.loads(detail) if isinstance(detail, str) else dict(detail)
            except Exception:  # noqa: S112 - 坏 JSON 行跳过，行为同原有实现
                continue
            if not isinstance(d, dict):
                continue
            files = d.get("files") or []
            changed = False
            for f in files:
                if str(f.get("file_id")) == file_id:
                    f["url"] = local_url
                    changed = True
            if changed:
                updates.append((row["id"], d))
        db.update_post_details(updates)

    @router.get("/media/zsxq-file/{file_id}")
    def download_zsxq_file(file_id: str, user: dict = Depends(get_download_user)):
        if not file_id.isdigit() or len(file_id) > 32:
            raise HTTPException(status_code=400, detail="无效附件")
        hits = _zsxq_file_hits(file_id)
        if not user.get("is_admin"):
            readable = db.readable_subscribed_kol_ids(user["id"], False)
            if not any(h["kol_id"] in readable for h in hits):
                raise HTTPException(status_code=404, detail="附件不存在")
        from pathlib import Path as _Path

        name = _zsxq_file_name(file_id)
        db_path = str(getattr(db, "path", "") or "")
        # 1) 本地已缓存 → 直接读本地，永久可用、不碰配额/签名过期
        files_dir = _Path(db_path).parent / "zsxq_files" if db_path and db_path != ":memory:" else None
        if files_dir and files_dir.exists():
            hits = sorted(files_dir.glob(f"{file_id}.*"))
            if hits:
                return FileResponse(
                    str(hits[0]),
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": _zsxq_cd(name, hits[0].name)},
                )
        # 2) 无本地缓存 → 拿签名 URL（优先库中未过期，避免烧配额）并落盘
        remote = _stored_zsxq_url(file_id)
        if not remote:
            try:
                remote = resolve_zsxq_file_url(file_id, db=db)
            except Exception as exc:
                # 13607/20601 下载量异常/日限：把真实原因告诉用户而不是裸 502
                raise HTTPException(status_code=429, detail=f"附件下载受限：{exc}") from exc
        if not remote:
            raise HTTPException(status_code=502, detail="附件暂时无法下载")
        from .url_safety import is_safe_http_url

        if not is_safe_http_url(remote):
            raise HTTPException(status_code=502, detail="附件地址不安全")
        from .fetchers.zsxq import cache_zsxq_file

        local = cache_zsxq_file(db, file_id, name, remote)
        if local:
            _write_back_zsxq_url(file_id, name, local)
            fp = files_dir / _Path(local).name
            return FileResponse(
                str(fp),
                media_type="application/octet-stream",
                headers={"Content-Disposition": _zsxq_cd(name, _Path(local).name)},
            )
        raise HTTPException(status_code=502, detail="附件下载失败")

    def _configured_groups():
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        # 本地库（local-<slug>，已启用）与 IMA 组共用同一条授权/阅读通路
        return ima_documents.product_groups()

    def _acl_known_group_ids() -> set[str]:
        """ACL 可配置的组：可见组 + 全部已扫描本地库。

        本地库未启用/报错时不在 product_groups 里，但仍应能先授权后启用。
        """
        ids = {group.id for group in _configured_groups()}
        ids |= {
            str(item.get("group_id") or "")
            for item in ima_documents.local_scan_status()["libraries"]
        }
        ids |= {
            str(item.get("group_id") or "")
            for item in db.list_feishu_document_sources()
        }
        return ids

    def _readable_groups(user: dict):
        groups = _configured_groups()
        allowed = readable_group_ids(db, user, groups)
        return tuple(group for group in groups if group.id in allowed)

    def _require_readable_group(user: dict, group_id: str):
        readable = _readable_groups(user)
        if group_id and group_id not in {group.id for group in readable}:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return readable

    @router.get("/ima-documents")
    def list_ima_documents(
        q: str = Query("", max_length=200),
        day: str = Query("", max_length=64),
        group: str = Query("", max_length=128),
        tag: str = Query("", max_length=64),
        rating: str = Query("", max_length=24),
        ticker: str = Query("", max_length=24),
        limit: int = 50,
        offset: int = Query(0, ge=0, le=IMA_DOCUMENT_LIST_MAX_OFFSET),
        # 首屏默认只取列表（include_facets=0），分面随后用 facets_only=1 单独取
        include_facets: int = Query(1, ge=0, le=1),
        facets_only: int = Query(0, ge=0, le=1),
        user: dict = Depends(get_current_user),
    ):
        _enforce_ima_list_quota(user)
        groups = _readable_groups(user)
        group = group.strip()
        if group and group not in {group_config.id for group_config in groups}:
            raise HTTPException(status_code=404, detail="知识库不存在")
        query = q.strip()
        tag = tag.strip()
        requested = day.strip()
        search_mode = bool(query or tag)
        effective_day = "" if search_mode or not requested else requested
        list_kwargs = {
            "groups": groups,
            "query": query,
            "day": effective_day,
            "group": group,
            "tag": tag,
            "rating": rating.strip(),
            "ticker": ticker.strip(),
        }
        if facets_only:
            # 分面单独一次请求：不带列表条目（首屏先画列表，计数/日期/标签随后补齐）
            payload = ima_documents.list_documents(**list_kwargs, limit=1, offset=0)
            items = []
        else:
            payload = ima_documents.list_documents(
                **list_kwargs,
                limit=bounded_limit(limit, default=50),
                offset=max(offset, 0),
                facets=include_facets == 1,
            )
            items = db.attach_report_extractions(payload["items"])
        return {
            "groups": payload.get("groups") if payload.get("groups") is not None else [],
            "items": items,
            "days": payload["days"],
            "tags": payload["tags"],
            "tag_counts": payload.get("tag_counts") or {},
            "document_count": int(payload.get("document_count") or 0),
            "day": payload.get("day") or effective_day,
            "has_more": bool(payload.get("has_more")) and not facets_only,
            "offset": 0 if facets_only else int(payload.get("offset") or 0),
        }

    @router.get("/ima-documents/catalog")
    def ima_documents_catalog(user: dict = Depends(get_current_user)):
        with ima_documents.config_lock:
            groups = _configured_groups()
        listed = ima_kb_catalog(db, user, groups)
        listed = attach_catalog_summary(listed, ima_documents.catalog_stats(groups))
        return attach_catalog_acl(listed, db, user)

    def _ima_document(user: dict, media_id: str, group: str = "") -> dict:
        group = group.strip()
        groups = _require_readable_group(user, group)
        try:
            document = ima_documents.document(media_id, groups, group=group)
        except ValueError:
            document = None
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        db.attach_report_extractions([document])
        return document

    @router.post("/ima-documents/groups/{group_id}/subscribe")
    def subscribe_ima_kb(group_id: str, user: dict = Depends(get_current_user)):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", group_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        if group_id not in {g.id for g in _configured_groups()}:
            raise HTTPException(status_code=404, detail="知识库不存在")
        if user.get("is_admin"):
            db.ima_kb_subscribe(user["id"], group_id)
            return {"ok": True}
        if not db.ima_kb_can_subscribe(user["id"], group_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        db.ima_kb_subscribe(user["id"], group_id)
        _audit(user, "subscribe_ima_kb", group_id)
        return {"ok": True}

    @router.delete("/ima-documents/groups/{group_id}/subscribe")
    def unsubscribe_ima_kb(group_id: str, user: dict = Depends(get_current_user)):
        db.ima_kb_unsubscribe(user["id"], group_id)
        _audit(user, "unsubscribe_ima_kb", group_id)
        return {"ok": True}

    @router.get("/ima-documents/tickers/{code}")
    def ima_ticker_page(
        code: str,
        group: str = Query("", max_length=128),
        limit: int = 100,
        user: dict = Depends(get_current_user),
    ):
        """标的页：该标的的研报时间线 + 已编译的跨文档综述（未编译则只有时间线）。"""
        groups = _require_readable_group(user, group)
        group_ids = [group_config.id for group_config in groups]
        ticker = db.ima_ticker_code(code[:64])
        rows = db.ima_ticker_reports(
            ticker, group_ids, limit=bounded_limit(limit, default=100)
        )
        keys = [(row["group_id"], row["media_id"]) for row in rows]
        items = db.attach_report_extractions(db.ima_documents_by_keys(keys, group_ids))
        order = {key: index for index, key in enumerate(keys)}
        items.sort(key=lambda item: order.get((item.get("group_id"), item.get("media_id")), 0))
        cached = db.ima_ticker_digest(ticker)
        # 综述是按全部库编译的：只授了部分库的人看到它，就拿到了其他库里的要点。
        # 读不全来源库就整块隐藏（时间线仍按可见库过滤返回）。只比已配置的库：
        # 库里会残留旧配置/已下线的 group_id，不该因此把综述藏给所有人。
        source_groups = set(db.ima_ticker_groups(ticker)) & {
            group_config.id for group_config in _configured_groups()
        }
        if cached and not source_groups <= set(group_ids):
            cached = {}
        return {
            "code": ticker,
            "name": str(cached.get("name") or (rows[0]["ticker_name"] if rows else "")),
            "digest": digest_view(cached) if cached else {},
            "items": items,
            "count": len(items),
        }

    @router.get("/ima-documents/{media_id}")
    def get_ima_document(
        media_id: str,
        group: str = Query("", max_length=128),
        user: dict = Depends(get_current_user),
    ):
        document = _ima_document(user, media_id, group)
        document_type = "feishu_timeline" if document.get("group_id", "").startswith("feishu-") else "document"
        source_url = ""
        feishu_display = "timeline"
        if document_type == "feishu_timeline":
            source = db.get_feishu_document_source_by_group(str(document.get("group_id") or ""))
            if source and not source.get("deleted_at") and source.get("enabled"):
                source_url = str(source.get("canonical_url") or "")
                feishu_display = str(source.get("display_mode") or "timeline")
        return {
            "media_id": document["media_id"],
            "name": document["name"],
            "day": document["day"],
            "size": document["size"],
            "chars": document["chars"],
            "downloaded_at": document["downloaded_at"],
            "group_id": document.get("group_id", ""),
            "group_name": document.get("group_name", ""),
            "abstract": document.get("abstract") or "",
            "abstract_zh": document.get("abstract_zh") or "",
            "needs_translation": bool(document.get("needs_translation")),
            "cover_url": document.get("cover_url") or "",
            "tags": document.get("tags") or [],
            "has_pdf": bool(document.get("has_pdf")),
            "has_txt": bool(document.get("has_txt")),
            "type": document_type,
            "source_url": source_url,
            "feishu_display": feishu_display,
        }

    @router.post("/ima-documents/{media_id}/translate")
    def translate_ima_document(
        media_id: str,
        group: str = Query("", max_length=128),
        user: dict = Depends(get_current_user),
    ):
        document = _ima_document(user, media_id, group)
        if not document.get("needs_translation"):
            return {"abstract_zh": document.get("abstract_zh") or document.get("abstract") or ""}
        from .scheduler import translate_text
        source = document.get("abstract") or ""
        try:
            zh = translate_text(source)
        except Exception:
            zh = source
        if zh and zh != source:
            try:
                ima_documents.store.write_abstract_zh(
                    media_id,
                    group,
                    groups=_require_readable_group(user, group),
                    text_zh=zh,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="文档不存在") from exc
        return {"abstract_zh": zh}

    def _ima_archive_file(document: dict, field: str):
        if not ima_documents.store.archive_readable():
            raise HTTPException(status_code=503, detail="知识库存储暂不可用")
        return ima_documents.store.authorized_archive_file(document.get(f"{field}_path"))

    @router.get("/ima-documents/{media_id}/timeline")
    def get_feishu_document_timeline(
        media_id: str,
        group: str = Query("", max_length=128),
        order: Literal["latest", "original"] = "latest",
        user: dict = Depends(get_current_user),
    ):
        if feishu_documents is None:
            raise HTTPException(status_code=503, detail="飞书文档服务未启用")
        document = _ima_document(user, media_id, group)
        source = db.get_feishu_document_source_by_group(str(document.get("group_id") or ""))
        if source is None or source.get("deleted_at") or not source.get("enabled"):
            raise HTTPException(status_code=404, detail="飞书文档不存在")
        try:
            timeline = feishu_documents.timeline(source)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=404, detail="时间线内容不存在") from exc
        entries = list(timeline.get("entries") or [])
        if order == "latest":
            entries.reverse()
        return {
            "source": {
                "id": source["id"],
                "group_id": source["group_id"],
                "media_id": source["media_id"],
                "title": source.get("display_name") or source.get("title") or document["name"],
                "canonical_url": source["canonical_url"],
                "revision_id": source.get("revision_id") or "",
                "last_success_at": source.get("last_success_at") or "",
            },
            "notices": timeline.get("notices") or [],
            "entries": entries,
            "order": order,
        }

    @router.get("/ima-documents/timeline/all")
    def get_all_feishu_document_timelines(
        order: Literal["latest", "original"] = "latest",
        group: str = Query("", max_length=128),
        window_days: int | None = Query(None, ge=1, le=31),
        before: str = Query("", max_length=512),
        user: dict = Depends(get_current_user),
    ):
        if feishu_documents is None:
            raise HTTPException(status_code=503, detail="飞书文档服务未启用")
        if before and not window_days:
            raise HTTPException(status_code=400, detail="时间线游标需要窗口参数")
        readable = {group.id for group in _readable_groups(user)}
        sources = [
            item for item in db.list_feishu_document_sources(active_only=True)
            if item.get("timeline_path") and item.get("group_id") in readable
        ]
        if group and not any(item.get("group_id") == group for item in sources):
            raise HTTPException(status_code=404, detail="文档不存在")
        public_sources = [
            {
                "id": source["id"],
                "group_id": source["group_id"],
                "media_id": source["media_id"],
                "title": source.get("display_name") or source.get("title") or "飞书文档",
                "canonical_url": source.get("canonical_url") or "",
                "revision_id": source.get("revision_id") or "",
                "last_success_at": source.get("last_success_at") or "",
            }
            for source in sources
        ]
        source_by_group = {item["group_id"]: item for item in public_sources}
        entries = []
        notices = []
        for source in sources:
            if group and source.get("group_id") != group:
                continue
            try:
                timeline = feishu_documents.timeline(source)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                continue
            public_source = source_by_group[source["group_id"]]
            notices.extend({**item, "source": public_source} for item in timeline.get("notices") or [])
            entries.extend({**item, "source": public_source} for item in timeline.get("entries") or [])
        try:
            entries, has_more, next_cursor = _feishu_timeline_page(entries, order, window_days, before)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="时间线游标无效") from exc
        return {
            "sources": public_sources,
            "notices": notices,
            "entries": entries,
            "order": order,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    @router.get("/ima-documents/{media_id}/assets/{asset_id}")
    def get_feishu_document_asset(
        media_id: str,
        asset_id: str,
        group: str = Query("", max_length=128),
        user: dict = Depends(get_download_user),
    ):
        _enforce_ima_file_quota(user)
        if feishu_documents is None:
            raise HTTPException(status_code=503, detail="飞书文档服务未启用")
        document = _ima_document(user, media_id, group)
        source = db.get_feishu_document_source_by_group(str(document.get("group_id") or ""))
        if source is None or source.get("deleted_at") or not source.get("enabled"):
            raise HTTPException(status_code=404, detail="飞书文档不存在")
        try:
            asset = feishu_documents.asset(source, asset_id)
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404, detail="飞书文档资源不存在") from None
        return FileResponse(str(asset), media_type=mimetypes.guess_type(asset.name)[0] or "application/octet-stream")

    @router.get("/ima-documents/{media_id}/text")
    def get_ima_document_text(
        media_id: str,
        group: str = Query("", max_length=128),
        user: dict = Depends(get_download_user),
    ):
        _enforce_ima_file_quota(user)
        document = _ima_document(user, media_id, group)
        txt = _ima_archive_file(document, "txt")
        if txt is None or not txt.is_file():
            raise HTTPException(status_code=404, detail="TXT 文件不存在")
        try:
            content = txt.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=404, detail="TXT 文件不存在") from exc
        return Response(content=content, media_type="text/plain")

    @router.get("/ima-documents/{media_id}/pdf")
    def get_ima_document_pdf(
        media_id: str,
        group: str = Query("", max_length=128),
        download: int = Query(0, ge=0, le=1),
        user: dict = Depends(get_download_user),
    ):
        _enforce_ima_file_quota(user)
        document = _ima_document(user, media_id, group)
        pdf = _ima_archive_file(document, "pdf")
        if pdf is None or not pdf.is_file():
            raise HTTPException(status_code=404, detail="PDF 文件不存在")
        from urllib.parse import quote

        disposition = "attachment" if download else "inline"
        filename = quote(str(document["name"] or "document.pdf"))
        return FileResponse(
            str(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}"},
        )

    def _ima_collector_status():
        if ima_documents is None:
            return None
        payload = ima_documents.status()
        payload["storage"] = ima_documents.storage_status.public()
        for group in payload.get("config", {}).get("groups", []):
            group["acl_usernames"] = db.ima_kb_acl_usernames(group["id"])
        return payload

    def _public_feishu_source(source: dict) -> dict:
        interval = max(int(getattr(feishu_documents.config, "interval_seconds", 60)), 15)
        last_checked = str(source.get("last_checked_at") or "")
        next_check_at = ""
        if last_checked and source.get("enabled") and not source.get("deleted_at"):
            try:
                next_check_at = (
                    datetime.fromisoformat(last_checked)
                    + timedelta(seconds=interval)
                ).isoformat()
            except ValueError:
                pass
        return {
            "id": int(source["id"]),
            "source_type": source["source_type"],
            "canonical_url": source["canonical_url"],
            "group_id": source["group_id"],
            "media_id": source["media_id"],
            "title": source.get("title") or "待首次同步",
            "display_name": source.get("display_name") or "",
            "revision_id": source.get("revision_id") or "",
            "entry_count": int(source.get("entry_count") or 0),
            "enabled": bool(source.get("enabled")),
            "display_mode": str(source.get("display_mode") or "timeline"),
            "sync_status": source.get("sync_status") or "pending",
            "last_checked_at": source.get("last_checked_at") or "",
            "last_success_at": source.get("last_success_at") or "",
            "next_check_at": next_check_at,
            "last_error": str(source.get("last_error") or "")[:300],
        }

    def _require_feishu_documents():
        if feishu_documents is None:
            raise HTTPException(status_code=503, detail="飞书文档服务未启用")
        return feishu_documents

    def _public_feishu_config(service) -> dict:
        cfg = service.config
        from urllib.parse import urlparse

        path = urlparse(cfg.redirect_uri).path
        stored = db.get_feishu_docs_settings()
        has_db = any(
            str(stored.get(key) or "").strip()
            for key in ("app_id", "app_secret", "redirect_uri", "scopes", "interval_seconds")
        )
        base = service.base_config
        source = "db" if has_db else (
            "env" if (base.app_id and base.app_secret and base.redirect_uri) else ""
        )
        return {
            "app_id": cfg.app_id,
            "app_secret_set": bool(cfg.app_secret),
            "redirect_uri": cfg.redirect_uri,
            "redirect_path_ok": path == "/api/admin/feishu-documents/oauth/callback",
            "scopes": cfg.scopes,
            "interval_seconds": max(int(cfg.interval_seconds), 15),
            "config_source": source,
        }

    @router.get("/admin/feishu-documents", dependencies=[Depends(require_admin)])
    def list_feishu_document_sources():
        service = _require_feishu_documents()
        credential = db.get_feishu_oauth_credential()
        return {
            "configured": service.configured,
            "authorized": bool(credential),
            "interval_seconds": max(int(service.config.interval_seconds), 15),
            "config": _public_feishu_config(service),
            "sources": [
                _public_feishu_source(source)
                for source in db.list_feishu_document_sources()
            ],
        }

    @router.put("/admin/feishu-documents/config")
    def update_feishu_documents_config(body: FeishuDocumentConfigIn, admin: dict = Depends(require_admin)):
        service = _require_feishu_documents()
        if not callable(getattr(db, "set_feishu_docs_settings", None)):
            raise HTTPException(status_code=503, detail="飞书文档配置存储不可用")
        updates: dict[str, Any] = {}
        if body.app_id is not None:
            value = body.app_id.strip()
            if len(value) > 128:
                raise HTTPException(status_code=400, detail="App ID 过长")
            updates["app_id"] = value
        if body.app_secret is not None and body.app_secret.strip():
            value = body.app_secret.strip()
            if len(value) > 256:
                raise HTTPException(status_code=400, detail="App Secret 过长")
            updates["app_secret"] = value
        if body.redirect_uri is not None:
            value = body.redirect_uri.strip()
            if value and not value.startswith("https://"):
                raise HTTPException(status_code=400, detail="回调地址必须是 HTTPS")
            if len(value) > 512:
                raise HTTPException(status_code=400, detail="回调地址过长")
            updates["redirect_uri"] = value
        if body.scopes is not None:
            value = " ".join(body.scopes.split())
            if not value:
                raise HTTPException(status_code=400, detail="授权权限不能为空")
            if len(value) > 500:
                raise HTTPException(status_code=400, detail="授权权限列表过长")
            updates["scopes"] = value
        if body.interval_seconds is not None:
            if not 15 <= int(body.interval_seconds) <= 86400:
                raise HTTPException(status_code=400, detail="检查间隔需在 15–86400 秒之间")
            updates["interval_seconds"] = str(int(body.interval_seconds))
        if not updates:
            raise HTTPException(status_code=400, detail="没有要保存的配置")
        previous_app_id = service.config.app_id
        try:
            db.set_feishu_docs_settings(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        service.reload_config()
        service.start()
        credentials_changed = ("app_secret" in updates) or (
            updates.get("app_id") is not None and updates["app_id"] != previous_app_id
        )
        _audit(admin, "update_feishu_documents_config", "", ",".join(sorted(updates)))
        return {
            "ok": True,
            "config": _public_feishu_config(service),
            "reauth_required": bool(credentials_changed and db.get_feishu_oauth_credential()),
        }

    @router.post("/admin/feishu-documents/oauth/start")
    def start_feishu_documents_oauth(
        request: Request,
        response: Response,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        try:
            url, state_hash = service.oauth_begin(int(admin["id"]))
        except FeishuDocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response.set_cookie(
            "feishu_oauth",
            state_hash,
            max_age=300,
            httponly=True,
            samesite="lax",
            path="/api/admin/feishu-documents/oauth/callback",
            secure=request.url.scheme == "https",
        )
        _audit(admin, "start_feishu_documents_oauth")
        return {"url": url}

    @router.post("/admin/feishu-documents/oauth/callback")
    def finish_feishu_documents_oauth(
        body: FeishuDocumentOauthCallbackIn,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        try:
            admin_id = service.oauth_callback(body.state.strip(), body.code.strip())
        except FeishuDocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if int(admin_id) != int(admin["id"]):
            raise HTTPException(status_code=403, detail="授权会话不属于当前管理员")
        started_by = db.get_user(admin_id)
        if started_by is None or not started_by.get("is_admin"):
            raise HTTPException(status_code=403, detail="授权发起账号不是管理员")
        _audit(admin, "finish_feishu_documents_oauth")
        return {"ok": True}

    @router.get("/admin/feishu-documents/oauth/callback")
    def finish_feishu_documents_oauth_redirect(
        request: Request,
        state: str = Query("", max_length=256),
        code: str = Query("", max_length=4096),
    ):
        expected = hashlib.sha256((state or "").strip().encode()).hexdigest()
        if (request.cookies.get("feishu_oauth") or "") != expected:
            from fastapi.responses import RedirectResponse

            logger.warning("Feishu OAuth callback rejected: missing or mismatched cookie")
            return RedirectResponse(
                url="/admin/knowledge?tab=feishu&oauth=failed",
                status_code=303,
            )
        service = _require_feishu_documents()
        try:
            admin_id = service.oauth_callback(state.strip(), code.strip())
        except FeishuDocumentError as exc:
            logger.warning(
                "Feishu OAuth callback failed code=%s detail=%s",
                getattr(exc, "code", 0), str(exc)[:120],
            )
            from fastapi.responses import RedirectResponse

            return RedirectResponse(
                url="/admin/knowledge?tab=feishu&oauth=failed",
                status_code=303,
            )
        admin = db.get_user(admin_id)
        if admin is None or not admin.get("is_admin"):
            raise HTTPException(status_code=403, detail="授权发起账号不是管理员")
        _audit(admin, "finish_feishu_documents_oauth")
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/admin/knowledge?tab=feishu&oauth=success", status_code=303)

    def _queue_feishu_sync(background_tasks: BackgroundTasks, service, source_id: int, force: bool) -> None:
        def _run() -> None:
            try:
                service.sync_source(source_id, force)
            except Exception:  # noqa: BLE001 - 失败状态已落库，后台不得二次抛出
                logger.warning(
                    "Feishu document background sync failed source_id=%s", source_id,
                    exc_info=True,
                )

        background_tasks.add_task(_run)

    @router.post("/admin/feishu-documents/preview")
    def preview_feishu_document_source(
        body: FeishuDocumentSourceIn,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        try:
            preview = service.preview_document_url(body.url)
        except FeishuDocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit(admin, "preview_feishu_document_source")
        return preview

    @router.post("/admin/feishu-documents")
    def add_feishu_document_source(
        body: FeishuDocumentSourceIn,
        background_tasks: BackgroundTasks,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        try:
            parsed = parse_feishu_document_url(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source = db.upsert_feishu_document_source(parsed)
        _queue_feishu_sync(background_tasks, service, int(source["id"]), True)
        _audit(admin, "add_feishu_document_source", str(source["id"]), parsed["canonical_url"])
        return _public_feishu_source(source)

    @router.patch("/admin/feishu-documents/{source_id}")
    def update_feishu_document_source(
        source_id: int,
        body: FeishuDocumentSourceUpdateIn,
        background_tasks: BackgroundTasks,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        source = db.get_feishu_document_source(source_id)
        if source is None or source.get("deleted_at"):
            raise HTTPException(status_code=404, detail="飞书文档来源不存在")
        if body.enabled is None and body.display_mode is None and body.display_name is None:
            raise HTTPException(status_code=400, detail="没有要更新的字段")
        if body.display_mode is not None and body.display_mode not in {"timeline", "document"}:
            raise HTTPException(status_code=400, detail="展示方式必须是 timeline 或 document")
        updates: dict[str, Any] = {}
        if body.display_mode is not None:
            updates["display_mode"] = body.display_mode
        if body.display_name is not None:
            name = body.display_name.strip()
            if len(name) > 200:
                raise HTTPException(status_code=400, detail="展示名过长（≤200 字）")
            updates["display_name"] = name
        if body.enabled is not None:
            updates.update(enabled=body.enabled, sync_status="pending" if body.enabled else "disabled", last_error="")
        db.update_feishu_document_source(source_id, **updates)
        if body.enabled is True:
            _queue_feishu_sync(background_tasks, service, source_id, False)
        elif body.enabled is False:
            ima_documents.remove_external_document(source["group_id"], source["media_id"])
        updated = db.get_feishu_document_source(source_id)
        if (
            "display_name" in updates
            and updates["display_name"] != str(source.get("display_name") or "")
            and body.enabled is not False
            and source.get("txt_path")
        ):
            # ponytail: 与周期同步线程无锁并发，窗口极小且同步本身也会带新展示名重发
            background_tasks.add_task(service.republish_from_archive, source_id)
        _audit(admin, "update_feishu_document_source", str(source_id), json.dumps({k: v for k, v in updates.items()}, ensure_ascii=False))
        return _public_feishu_source(updated)

    @router.post("/admin/feishu-documents/{source_id}/sync")
    def sync_feishu_document_source(
        source_id: int,
        background_tasks: BackgroundTasks,
        admin: dict = Depends(require_admin),
    ):
        service = _require_feishu_documents()
        source = db.get_feishu_document_source(source_id)
        if source is None or source.get("deleted_at"):
            raise HTTPException(status_code=404, detail="飞书文档来源不存在")
        if not source.get("enabled"):
            raise HTTPException(status_code=400, detail="请先启用该来源")
        _queue_feishu_sync(background_tasks, service, source_id, True)
        _audit(admin, "sync_feishu_document_source", str(source_id))
        return {"ok": True, "status": "queued"}

    @router.delete("/admin/feishu-documents/{source_id}")
    def delete_feishu_document_source(
        source_id: int,
        admin: dict = Depends(require_admin),
    ):
        _require_feishu_documents()
        source = db.soft_delete_feishu_document_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="飞书文档来源不存在")
        ima_documents.remove_external_document(source["group_id"], source["media_id"])
        _audit(admin, "delete_feishu_document_source", str(source_id))
        return {"ok": True}

    @router.get("/admin/ima-collector", dependencies=[Depends(require_admin)])
    def get_ima_collector():
        payload = _ima_collector_status()
        if payload is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        return payload

    @router.post("/admin/ima-collector/discover", dependencies=[Depends(require_admin)])
    def discover_ima_groups(admin: dict = Depends(require_admin)):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        result = ima_documents.discover()
        if result.get("status") == "not_configured":
            raise HTTPException(status_code=400, detail="请先配置 IMA UID 和 Refresh Token")
        _audit(admin, "discover_ima_groups", "", str(result.get("status") or ""))
        return result

    @router.get("/admin/ima-collector/groups/{group_id}/folders", dependencies=[Depends(require_admin)])
    def list_ima_group_folders(
        group_id: str,
        parent_id: str = Query("", max_length=128),
    ):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", group_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        parent_id = parent_id.strip()
        if parent_id and not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", parent_id):
            raise HTTPException(status_code=400, detail="父文件夹 ID 格式无效")
        group = next((item for item in _configured_groups() if item.id == group_id), None)
        if group is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        actual_parent_id = parent_id or group.root_folder_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", actual_parent_id):
            raise HTTPException(status_code=400, detail="根文件夹 ID 格式无效")
        try:
            client = ImaPureClient(ima_documents.config(), group=group)
            raw_items = client.list_items(
                actual_parent_id,
                folders_only=True,
                max_pages=IMA_FOLDER_LIST_MAX_PAGES,
            )
        except Exception as exc:  # noqa: BLE001 - folder endpoint must return a safe error
            logger.exception(
                "IMA folder list failed group=%s parent=%s",
                group_id,
                actual_parent_id,
            )
            detail = _safe_error(exc)
            raise HTTPException(status_code=502, detail=f"IMA 文件夹读取失败: {detail}") from None
        items = []
        seen: set[str] = set()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_ima_folder_item(raw_item, actual_parent_id)
            if item is None or item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
        return {"group_id": group_id, "parent_id": actual_parent_id, "items": items}

    @router.put("/admin/ima-collector/groups/{group_id}/acl", dependencies=[Depends(require_admin)])
    def set_ima_kb_acl(group_id: str, body: ImaKbAclIn, admin: dict = Depends(require_admin)):
        if group_id not in _acl_known_group_ids():
            raise HTTPException(status_code=404, detail="知识库不存在")
        user_ids = []
        for username in body.usernames:
            target = db.get_user_by_username_ci(username.strip())
            if target is None:
                raise HTTPException(status_code=400, detail=f"用户不存在: {username}")
            user_ids.append(target["id"])
        db.set_ima_kb_acl(group_id, user_ids)
        _audit(admin, "set_ima_kb_acl", group_id, ",".join(body.usernames))
        return {"ok": True, "acl_usernames": db.ima_kb_acl_usernames(group_id)}

    @router.put("/admin/users/{user_id}/ima-kb", dependencies=[Depends(require_admin)])
    def set_user_ima_kb(user_id: int, body: ImaKbUserAclIn, admin: dict = Depends(require_admin)):
        target = db.get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target.get("is_admin"):
            raise HTTPException(status_code=400, detail="管理员可直接打开全部知识库")
        known = _acl_known_group_ids()
        group_ids = []
        seen: set[str] = set()
        for group_id in body.group_ids:
            value = str(group_id or "").strip()
            if not value or value in seen or is_open_group(value):
                continue
            if value not in known:
                raise HTTPException(status_code=404, detail="知识库不存在")
            seen.add(value)
            group_ids.append(value)
        db.set_ima_kb_acl_for_user(user_id, group_ids)
        _audit(admin, "set_user_ima_kb", str(user_id), ",".join(group_ids))
        return {
            "ok": True,
            "ima_kb_groups": db.ima_kb_group_ids_for_user(user_id),
            "ima_kb_subscribed": db.ima_kb_subscribed_group_ids_for_user(user_id),
        }

    @router.put("/admin/ima-collector", dependencies=[Depends(require_admin)])
    def set_ima_collector(body: ImaCollectorIn, admin: dict = Depends(require_admin)):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        updates: dict[str, str] = {}
        audit_parts: list[str] = []
        if body.uid is not None:
            value = body.uid.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                raise HTTPException(status_code=400, detail="IMA UID 格式无效")
            updates[IMA_PURE_UID_KEY] = value
        if body.refresh_token is not None and body.refresh_token.strip():
            value = body.refresh_token.strip()
            if len(value) > 4096:
                raise HTTPException(status_code=400, detail="Refresh Token 过长")
            updates[IMA_PURE_REFRESH_TOKEN_KEY] = value
        if body.knowledge_base_id is not None:
            value = body.knowledge_base_id.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                raise HTTPException(status_code=400, detail="知识库 ID 格式无效")
            updates[IMA_PURE_KB_ID_KEY] = value
        if body.root_folder_id is not None:
            value = body.root_folder_id.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise HTTPException(status_code=400, detail="根文件夹 ID 格式无效")
            updates[IMA_PURE_ROOT_FOLDER_KEY] = value
        if body.interval_seconds is not None:
            if not IMA_PURE_INTERVAL_MIN <= body.interval_seconds <= IMA_PURE_INTERVAL_MAX:
                raise HTTPException(status_code=400, detail="同步间隔须在 1800–604800 秒")
            updates[IMA_PURE_INTERVAL_KEY] = str(body.interval_seconds)
        if body.groups is not None:
            with ima_documents.config_lock:
                existing = {group.id: group for group in ima_documents.config().groups}
                groups: list[dict[str, object]] = []
                group_ids: list[str] = []
                clear_group_ids: list[str] = []
                for group in body.groups:
                    name = group.name.strip()
                    knowledge_base_id = group.knowledge_base_id.strip()
                    root_folder_id = group.root_folder_id.strip()
                    if not name or len(name) > 100:
                        raise HTTPException(status_code=400, detail="IMA 群组名称不能为空且最多 100 个字符")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", knowledge_base_id):
                        raise HTTPException(status_code=400, detail="知识库 ID 格式无效")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", root_folder_id):
                        raise HTTPException(status_code=400, detail="根文件夹 ID 格式无效")
                    if group.id is None:
                        group_id = "manual-" + hashlib.sha256(
                            f"{knowledge_base_id}\0{root_folder_id}".encode()
                        ).hexdigest()[:16]
                    else:
                        group_id = group.id.strip()
                        if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", group_id):
                            raise HTTPException(status_code=400, detail="IMA 群组 ID 格式无效")
                        if group_id.startswith("local-"):
                            raise HTTPException(status_code=400, detail="local- 前缀专供本地库，IMA 群组不得使用")
                    if group_id in group_ids:
                        raise HTTPException(status_code=400, detail="IMA 群组 ID 不能重复")
                    group_ids.append(group_id)
                    previous = existing.get(group_id)
                    if group.folder_ids is None:
                        folder_ids = list(
                            previous.mount_folder_ids
                            if previous is not None
                            else ((root_folder_id,) if group.enabled else ())
                        )
                    else:
                        if len(group.folder_ids) > IMA_MOUNT_FOLDER_ID_MAX:
                            raise HTTPException(status_code=400, detail="每个 IMA 群组最多挂载 256 个文件夹")
                        folder_ids = []
                        seen_folder_ids: set[str] = set()
                        for raw_folder_id in group.folder_ids:
                            if not isinstance(raw_folder_id, str):
                                raise HTTPException(status_code=400, detail="文件夹 ID 格式无效")
                            folder_id = raw_folder_id.strip()
                            if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", folder_id):
                                raise HTTPException(status_code=400, detail="文件夹 ID 格式无效")
                            if folder_id not in seen_folder_ids:
                                seen_folder_ids.add(folder_id)
                                folder_ids.append(folder_id)
                    enabled = bool(group.enabled and folder_ids)
                    if not enabled:
                        if previous is not None and previous.mount_folder_ids:
                            clear_group_ids.append(group_id)
                        folder_ids = []
                    elif group.folder_ids is not None and not folder_ids:
                        clear_group_ids.append(group_id)
                    groups.append(
                        {
                            "id": group_id,
                            "name": name,
                            "knowledge_base_id": knowledge_base_id,
                            "root_folder_id": root_folder_id,
                            "folder_ids": folder_ids,
                            "enabled": enabled,
                            "source": previous.source if previous else "manual",
                            "interval_seconds": _clamp_group_interval(
                                group.interval_seconds if group.interval_seconds is not None else (
                                    previous.interval_seconds if previous else 3600
                                )
                            ),
                        }
                    )
                clear_group_ids.extend(
                    group_id
                    for group_id, previous in existing.items()
                    if group_id not in group_ids
                )
                updates[IMA_PURE_GROUPS_KEY] = json.dumps(groups, ensure_ascii=False)
                audit_parts.append(f"groups_count={len(group_ids)};group_ids={','.join(group_ids)}")
                if updates:
                    db.set_settings_atomic(updates)
                    for group_id in clear_group_ids:
                        ima_documents.store.save_group_manifest(group_id, [])
                    audit_parts.extend(sorted(key for key in updates if key != IMA_PURE_GROUPS_KEY))
                    _audit(admin, "set_ima_collector", "", ";".join(audit_parts))
        elif updates:
            with ima_documents.config_lock:
                db.set_settings_atomic(updates)
                audit_parts.extend(sorted(key for key in updates if key != IMA_PURE_GROUPS_KEY))
                _audit(admin, "set_ima_collector", "", ";".join(audit_parts))
        return _ima_collector_status()

    @router.post("/admin/ima-collector/sync", dependencies=[Depends(require_admin)])
    def trigger_ima_collector(
        body: ImaCollectorSyncIn = Body(default_factory=ImaCollectorSyncIn),
        admin: dict = Depends(require_admin),
    ):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        group_id = (body.group_id if body else "").strip()
        if group_id:
            group = next((item for item in _configured_groups() if item.id == group_id), None)
            if group is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
            if not group.mount_folder_ids:
                raise HTTPException(status_code=409, detail="请先挂载该知识库")
        result = ima_documents.trigger(group_id=group_id)
        if result["status"] == "not_configured":
            raise HTTPException(status_code=400, detail="请先配置 IMA UID 和 Refresh Token")
        storage_messages = {
            "storage_unavailable": "知识库存储暂不可用",
            "storage_stale": "知识库存储状态已过期",
            "storage_readonly": "知识库存储当前只读",
            "capacity_blocked": "知识库存储空间已达限制",
        }
        blocked_detail = storage_messages.get(result["status"])
        if blocked_detail:
            raise HTTPException(status_code=503, detail=blocked_detail)
        if result["status"] == "too_soon":
            raise HTTPException(status_code=429, detail="距离上次同步时间太短，请稍后再试")
        _audit(admin, "trigger_ima_collector", "", result["status"])
        return result

    def _ima_storage_public():
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        return ima_documents.storage_status.public()

    def _require_remote_archive():
        if ima_documents is None or not ima_documents.storage_status.remote:
            raise HTTPException(status_code=409, detail="当前部署未启用远程归档")

    @router.post("/admin/ima-storage/refresh", dependencies=[Depends(require_admin)])
    def refresh_ima_storage(admin: dict = Depends(require_admin)):
        from .ima_storage import write_request_file

        _require_remote_archive()
        path = os.environ.get("IMA_STORAGE_REFRESH_REQUEST", "/data/.vpush-ima-refresh-request")
        write_request_file(path)
        _audit(admin, "ima_storage_refresh", "", "requested")
        return _ima_storage_public()

    @router.post("/admin/ima-storage/backup", dependencies=[Depends(require_admin)])
    def backup_ima_storage(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        # 旧实现写 .vpush-backup-request 请求文件，但存储机从未有消费者（死信）；
        # 改走命令通道：dispatch 的 backup 模式直接运行 restic-backup.sh
        _require_remote_archive()
        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        result = ctl.trigger("backup", admin["username"])
        _audit(admin, "ima_storage_backup", "", "requested")
        return {"status": "started", **result}

    @router.get("/admin/cicc/status", dependencies=[Depends(require_admin)])
    def cicc_status(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        return ctl.status()

    @router.post("/admin/cicc/trigger", dependencies=[Depends(require_admin)])
    def cicc_trigger(body: CiccTriggerIn, admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        try:
            result = ctl.trigger(body.mode, admin["username"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _audit(admin, "cicc_trigger", "", body.mode)
        return result

    @router.get("/admin/cicc/schedule", dependencies=[Depends(require_admin)])
    def cicc_get_schedule(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        return ctl.read_schedule()

    @router.put("/admin/cicc/schedule", dependencies=[Depends(require_admin)])
    def cicc_set_schedule(body: CiccScheduleIn, admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env, validate_time_of_day

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        if body.time is not None and not validate_time_of_day(body.time):
            raise HTTPException(status_code=400, detail="时间格式应为 HH:mm（00:00-23:59）")
        result = ctl.set_schedule(body.enabled)
        if body.time is not None:
            result.update(ctl.set_schedule_time(body.time, admin["username"]))
        _audit(admin, "cicc_schedule", "",
               f"{'enabled' if body.enabled else 'disabled'} time={body.time or '-'}")
        return result

    CICC_CATEGORIES_KEY = "cicc_category_settings"

    @router.get("/admin/ima-collector/cicc-categories", dependencies=[Depends(require_admin)])
    def cicc_categories_get(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        cicc_settings = (ctl.status().get("cicc_settings") or {})
        settings = cicc_settings.get("categories")
        if settings is None:  # 存储机还没透传（离线/未刷新）→ 退回 DB 里上次保存的定向
            raw = db.get_setting(CICC_CATEGORIES_KEY)
            try:
                settings = json.loads(raw) if raw else []
            except ValueError:
                settings = []
        raw_kw = db.get_setting("cicc_keywords_key")
        try:
            keywords = json.loads(raw_kw) if raw_kw else []
        except ValueError:
            keywords = []
        return {"categories": settings, "keywords": keywords}

    @router.put("/admin/ima-collector/cicc-categories", dependencies=[Depends(require_admin)])
    def cicc_categories_put(body: CiccCategoriesIn, admin: dict = Depends(require_admin)):
        from .cicc_collector import CICC_CATEGORIES, from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        cats = list(dict.fromkeys(c.strip() for c in body.categories if c.strip()))
        unknown = sorted(set(cats) - set(CICC_CATEGORIES))
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知品类：{'、'.join(unknown)}")
        keywords = list(dict.fromkeys(k.strip() for k in body.keywords if k.strip()))
        ctl.set_cicc_settings(cats, admin["username"], keywords)
        db.set_setting(CICC_CATEGORIES_KEY, json.dumps(cats, ensure_ascii=False))
        db.set_setting("cicc_keywords_key", json.dumps(keywords, ensure_ascii=False))
        note = "全部品类" if not cats else "、".join(cats)
        if keywords:
            note += f"｜关键词：{'、'.join(keywords)}"
        _audit(admin, "cicc_categories", "", note)
        return {"categories": cats, "keywords": keywords}

    @router.get("/admin/ima-storage/health", dependencies=[Depends(require_admin)])
    def ima_storage_health(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        return ctl.status()

    @router.get("/admin/ima-storage/consistency", dependencies=[Depends(require_admin)])
    def ima_storage_consistency_get(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        return ctl.status().get("consistency") or {}

    @router.post("/admin/ima-storage/consistency/run", dependencies=[Depends(require_admin)])
    def ima_storage_consistency_run(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        result = ctl.trigger("consistency", admin["username"])
        _audit(admin, "ima_consistency_run", "", "")
        return result

    @router.post("/admin/ima-storage/dedup", dependencies=[Depends(require_admin)])
    def ima_storage_dedup(admin: dict = Depends(require_admin)):
        from .cicc_collector import from_env

        ctl = from_env()
        if ctl is None:
            raise HTTPException(status_code=503, detail="当前部署未挂载存储归档")
        result = ctl.trigger("dedup", admin["username"])
        _audit(admin, "ima_dedup", "", "")
        return result

    @router.get("/admin/ima-storage/alerts", dependencies=[Depends(require_admin)])
    def ima_storage_alerts_get(admin: dict = Depends(require_admin)):
        from .cicc_alerts import load_alert_settings

        return {"settings": load_alert_settings(db)}

    @router.put("/admin/ima-storage/alerts", dependencies=[Depends(require_admin)])
    def ima_storage_alerts_put(body: dict, admin: dict = Depends(require_admin)):
        from .cicc_alerts import save_alert_settings

        try:
            saved = save_alert_settings(db, body or {})
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"参数非法：{exc}")
        _audit(admin, "ima_storage_alerts", "", json.dumps(saved, ensure_ascii=False))
        return {"settings": saved}

    def _with_local_library_acl(payload: dict) -> dict:
        for item in payload.get("libraries") or []:
            group_id = str(item.get("group_id") or "")
            item["acl_usernames"] = db.ima_kb_acl_usernames(group_id) if group_id else []
        return payload

    @router.get("/admin/ima-local-libraries", dependencies=[Depends(require_admin)])
    def get_ima_local_libraries():
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        return _with_local_library_acl(ima_documents.local_scan_status())

    @router.post("/admin/ima-local-libraries/scan", dependencies=[Depends(require_admin)])
    def scan_ima_local_libraries(admin: dict = Depends(require_admin)):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        result = ima_documents.scan_local_libraries()
        if result.get("status") == "already_running":
            raise HTTPException(status_code=409, detail="IMA 同步或扫描正在进行，请稍后再试")
        _audit(admin, "scan_ima_local_libraries", "", str(result.get("status") or ""))
        return _with_local_library_acl(result)

    @router.put("/admin/ima-local-libraries/{slug}/enabled", dependencies=[Depends(require_admin)])
    def set_ima_local_library_enabled(
        slug: str, body: LocalLibraryEnabledIn, admin: dict = Depends(require_admin)
    ):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        try:
            ima_documents.set_local_library_enabled(slug, body.enabled)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            # 属主/权限不对（须 99:100 可写）时必须报错，不能静默
            raise HTTPException(status_code=502, detail=f"标记文件写入失败：{_safe_error(exc)}") from exc
        _audit(
            admin,
            "set_ima_local_library_enabled",
            slug,
            "enabled" if body.enabled else "disabled",
        )
        return _with_local_library_acl(ima_documents.local_scan_status())

    @router.put("/admin/ima-local-libraries/{slug}", dependencies=[Depends(require_admin)])
    def update_ima_local_library(
        slug: str, body: LocalLibraryMetaIn, admin: dict = Depends(require_admin)
    ):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        if body.name is None and body.tags is None:
            raise HTTPException(status_code=400, detail="name 与 tags 至少填一项")
        try:
            result = ima_documents.update_local_library_meta(slug, name=body.name, tags=body.tags)
        except LocalLibraryInvalidMeta as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            # 属主/权限不对（须 99:100 可写）时必须报错，不能静默
            raise HTTPException(status_code=502, detail=f"标记文件写入失败：{_safe_error(exc)}") from exc
        _audit(admin, "update_ima_local_library", slug, "")
        return _with_local_library_acl(result)

    @router.post("/admin/ima-local-libraries", dependencies=[Depends(require_admin)])
    def create_ima_local_library(body: LocalLibraryCreateIn, admin: dict = Depends(require_admin)):
        if ima_documents is None:
            raise HTTPException(status_code=503, detail="IMA 文档服务未启用")
        try:
            result = ima_documents.create_local_library(body.slug, body.name, body.tags)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"本地库已存在：{body.slug}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            # 存储归档不可写（须 99:100 可写）时必须报错，不能静默
            raise HTTPException(status_code=502, detail=f"存储归档写入失败：{_safe_error(exc)}") from exc
        _audit(admin, "create_ima_local_library", body.slug, str(result.get("status") or ""))
        return _with_local_library_acl(result)

    @router.get("/admin/ima-credentials", dependencies=[Depends(require_admin)])
    def get_ima_credentials():
        cookie = db.get_setting(IMA_COOKIE_KEY) or os.environ.get("IMA_COOKIE", "")
        client_id = db.get_setting(IMA_CLIENT_ID_KEY) or os.environ.get("IMA_OPENAPI_CLIENTID", "")
        api_key = db.get_setting(IMA_API_KEY_KEY) or os.environ.get("IMA_OPENAPI_APIKEY", "")
        return {
            "cookie": {
                "set": bool(cookie),
                "updated_at": db.get_setting(IMA_COOKIE_TIME_KEY) or "",
                "preview": "已配置" if cookie else "",
                "from_env": bool(cookie) and not db.get_setting(IMA_COOKIE_KEY),
            },
            "openapi_clientid": {"set": bool(client_id), "preview": (client_id[:12] + "…") if len(client_id) > 12 else client_id},
            "openapi_apikey": {"set": bool(api_key)},
            "mode": "openapi" if (client_id and api_key) else ("cookie" if cookie else "none"),
        }

    @router.post("/admin/ima-credentials", dependencies=[Depends(require_admin)])
    def set_ima_credentials(body: ImaCredentialsIn, admin: dict = Depends(require_admin)):
        cookie = (body.cookie or "").strip()
        client_id = (body.openapi_clientid or "").strip()
        api_key = (body.openapi_apikey or "").strip()
        if not cookie and not (client_id and api_key):
            raise HTTPException(status_code=400, detail="需至少提供 ima Cookie 或 OpenAPI 凭证（clientid + apikey）")
        if bool(client_id) != bool(api_key):
            raise HTTPException(status_code=400, detail="OpenAPI 凭证需同时提供 clientid 与 apikey")
        if cookie:
            db.set_setting(IMA_COOKIE_KEY, cookie)
            db.set_setting(IMA_COOKIE_TIME_KEY, str(int(time.time())))
        if client_id:
            db.set_setting(IMA_CLIENT_ID_KEY, client_id)
            db.set_setting(IMA_API_KEY_KEY, api_key)
        _audit(
            admin,
            "set_ima_credentials",
            "",
            f"cookie={'y' if cookie else 'n'} openapi={'y' if client_id else 'n'}",
        )
        return {"ok": True}

    def _validate_pool_fields(kind: str, protocol: str, extract_url: str) -> None:
        if kind not in ("static", "extract"):
            raise HTTPException(status_code=400, detail="kind 须为 static 或 extract")
        if protocol not in ("http", "socks5"):
            raise HTTPException(status_code=400, detail="protocol 须为 http 或 socks5")
        url = (extract_url or "").strip()
        if kind == "extract" and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="提取 URL 仅支持 http/https")

    def _validate_routes_input(routes: dict) -> None:
        if not isinstance(routes, dict):
            raise HTTPException(status_code=400, detail="路由格式无效")
        for platform, route in routes.items():
            if platform not in ALLOWED_PLATFORMS:
                raise HTTPException(status_code=400, detail=f"未知平台: {platform}")
            if not isinstance(route, dict):
                raise HTTPException(status_code=400, detail="路由格式无效")
            mode = route.get("mode")
            if mode not in ("direct", "pool", "proxy"):
                raise HTTPException(status_code=400, detail="mode 须为 direct / pool / proxy")
            if mode == "pool":
                try:
                    pool_id = int(route.get("pool_id"))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="代理池不存在") from None
                if db.get_proxy_pool(pool_id) is None:
                    raise HTTPException(status_code=400, detail="代理池不存在")
            if mode == "proxy":
                try:
                    proxy_id = int(route.get("proxy_id"))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="指定代理不存在") from None
                if db.get_proxy(proxy_id) is None:
                    raise HTTPException(status_code=400, detail="指定代理不存在")

    @router.get("/admin/proxy-pools", dependencies=[Depends(require_admin)])
    def list_proxy_pools():
        return {"items": [public_pool(row) for row in db.list_proxy_pools()]}

    @router.post("/admin/proxy-pools", dependencies=[Depends(require_admin)])
    def create_proxy_pool(body: ProxyPoolIn, admin: dict = Depends(require_admin)):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        _validate_pool_fields(body.kind, body.protocol, body.extract_url)
        try:
            pool_id = db.create_proxy_pool(
                name,
                kind=body.kind,
                extract_url=body.extract_url,
                protocol=body.protocol,
                expire_seconds=body.expire_seconds,
                refresh_interval_seconds=body.refresh_interval_seconds,
                enabled=body.enabled,
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="代理池名称已存在") from None
        _audit(admin, "create_proxy_pool", str(pool_id), name)
        return public_pool(db.get_proxy_pool(pool_id), hide_extract_query=False)

    @router.get("/admin/proxy-pools/{pool_id}", dependencies=[Depends(require_admin)])
    def get_proxy_pool(pool_id: int):
        row = db.get_proxy_pool(pool_id)
        if row is None:
            raise HTTPException(status_code=404, detail="代理池不存在")
        return public_pool(row, hide_extract_query=False)

    @router.put("/admin/proxy-pools/{pool_id}", dependencies=[Depends(require_admin)])
    def update_proxy_pool_api(pool_id: int, body: ProxyPoolUpdate, admin: dict = Depends(require_admin)):
        row = db.get_proxy_pool(pool_id)
        if row is None:
            raise HTTPException(status_code=404, detail="代理池不存在")
        kind = body.kind if body.kind is not None else row["kind"]
        protocol = body.protocol if body.protocol is not None else row["protocol"]
        extract_url = body.extract_url if body.extract_url is not None else row["extract_url"]
        _validate_pool_fields(kind, protocol, extract_url)
        payload = body.model_dump(exclude_unset=True)
        if "name" in payload and not (payload["name"] or "").strip():
            raise HTTPException(status_code=400, detail="名称不能为空")
        try:
            db.update_proxy_pool(pool_id, **payload)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="代理池名称已存在") from None
        _audit(admin, "update_proxy_pool", str(pool_id), "")
        return public_pool(db.get_proxy_pool(pool_id), hide_extract_query=False)

    @router.delete("/admin/proxy-pools/{pool_id}", dependencies=[Depends(require_admin)])
    def delete_proxy_pool_api(pool_id: int, admin: dict = Depends(require_admin)):
        if db.get_proxy_pool(pool_id) is None:
            raise HTTPException(status_code=404, detail="代理池不存在")
        db.delete_proxy_pool(pool_id)
        _audit(admin, "delete_proxy_pool", str(pool_id), "")
        return {"ok": True}

    @router.post("/admin/proxy-pools/{pool_id}/import", dependencies=[Depends(require_admin)])
    def import_proxy_pool(pool_id: int, body: ProxyImportIn, admin: dict = Depends(require_admin)):
        try:
            result = import_proxies(db, pool_id, body.text, body.protocol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "import_proxies", str(pool_id), f"imported={result['imported']}")
        return result

    @router.post("/admin/proxy-pools/{pool_id}/extract", dependencies=[Depends(require_admin)])
    def extract_proxy_pool(pool_id: int, admin: dict = Depends(require_admin)):
        try:
            result = extract_pool(db, pool_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"提取失败: {exc}") from None
        _audit(admin, "extract_proxy_pool", str(pool_id), f"imported={result['imported']}")
        return result

    @router.get("/admin/proxies", dependencies=[Depends(require_admin)])
    def list_proxies_api(pool_id: int | None = None):
        return {"items": [public_proxy(row) for row in db.list_proxies(pool_id)]}

    @router.post("/admin/proxies", dependencies=[Depends(require_admin)])
    def add_proxy_api(body: ProxyIn, admin: dict = Depends(require_admin)):
        try:
            result = import_proxies(db, body.pool_id, body.text, body.protocol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "add_proxy", str(body.pool_id), f"imported={result['imported']}")
        return result

    @router.delete("/admin/proxies/{proxy_id}", dependencies=[Depends(require_admin)])
    def delete_proxy_api(proxy_id: int, admin: dict = Depends(require_admin)):
        if db.get_proxy(proxy_id) is None:
            raise HTTPException(status_code=404, detail="代理不存在")
        db.delete_proxy(proxy_id)
        _audit(admin, "delete_proxy", str(proxy_id), "")
        return {"ok": True}

    @router.post("/admin/proxies/{proxy_id}/test", dependencies=[Depends(require_admin)])
    def test_proxy_api(proxy_id: int):
        row = db.get_proxy(proxy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="代理不存在")
        return probe_proxy(row)

    @router.get("/admin/proxy-routes", dependencies=[Depends(require_admin)])
    def get_proxy_routes():
        return ProxyRouter(db).routes()

    @router.put("/admin/proxy-routes", dependencies=[Depends(require_admin)])
    def put_proxy_routes(body: dict, admin: dict = Depends(require_admin)):
        _validate_routes_input(body)
        routes = ProxyRouter(db).set_routes(body)
        _audit(admin, "set_proxy_routes", "", "")
        return routes

    def _revoke_one(code: str, admin: dict) -> dict:
        row = db.get_register_code(code)
        if row is None:
            raise HTTPException(status_code=404, detail="注册码不存在")
        if row["used_by"]:
            raise HTTPException(status_code=400, detail="该注册码已被使用，不能删除")
        if row["revoked_at"]:
            raise HTTPException(status_code=400, detail="该注册码已作废")
        if not db.revoke_register_code(code):
            row = db.get_register_code(code)
            if row is None:
                raise HTTPException(status_code=404, detail="注册码不存在")
            if row["used_by"]:
                raise HTTPException(status_code=400, detail="该注册码已被使用，不能删除")
            if row["revoked_at"]:
                raise HTTPException(status_code=400, detail="该注册码已作废")
            raise HTTPException(status_code=400, detail="该注册码已被使用，不能删除")
        _audit(admin, "revoke_register_code", code)
        return {"ok": True}

    @router.delete("/admin/register-codes/{code}", dependencies=[Depends(require_admin)])
    def revoke_register_code(code: str, admin: dict = Depends(require_admin)):
        return _revoke_one(code, admin)

    @router.post("/admin/register-codes/{code}/revoke", dependencies=[Depends(require_admin)])
    def revoke_register_code_post(code: str, admin: dict = Depends(require_admin)):
        return _revoke_one(code, admin)

    @router.post(
        "/admin/register-code-batches/{batch_id}/revoke-unused",
        dependencies=[Depends(require_admin)],
    )
    def revoke_unused_register_codes(batch_id: str, admin: dict = Depends(require_admin)):
        n = db.revoke_unused_in_batch(batch_id)
        _audit(admin, "revoke_register_code_batch", batch_id, f"count={n}")
        return {"ok": True, "count": n}

    @router.post("/admin/register-codes/batch", dependencies=[Depends(require_admin)])
    def register_codes_batch_action(body: RegisterCodeBatchAction, admin: dict = Depends(require_admin)):
        codes = [c.strip().upper() for c in body.codes if c and str(c).strip()]
        if not codes:
            raise HTTPException(status_code=400, detail="请先选择注册码")
        if body.action == "revoke":
            count = sum(1 for c in codes if db.revoke_register_code(c))
            skipped = len(codes) - count
            if count == 0:
                raise HTTPException(status_code=400, detail="没有可作废的注册码")
            _audit(admin, "batch_revoke_register_codes", str(count), f"skipped={skipped}")
            return {"ok": True, "count": count, "skipped": skipped}
        if body.action == "delete":
            count = db.purge_register_codes(codes)
            skipped = len(codes) - count
            if count == 0:
                raise HTTPException(status_code=400, detail="没有可删除的注册码")
            _audit(admin, "batch_delete_register_codes", str(count), f"skipped={skipped}")
            return {"ok": True, "count": count, "skipped": skipped}
        raise HTTPException(status_code=400, detail=f"不支持的操作: {body.action}")

    @router.patch("/admin/register-codes/{code}", dependencies=[Depends(require_admin)])
    def patch_register_code(
        code: str, body: RegisterCodeNoteIn, admin: dict = Depends(require_admin)
    ):
        row = db.get_register_code(code)
        if row is None:
            raise HTTPException(status_code=404, detail="注册码不存在")
        note = (body.note or "").strip()
        if len(note) > REGISTER_NOTE_MAX:
            raise HTTPException(status_code=400, detail=f"备注最长{REGISTER_NOTE_MAX}字")
        db.update_register_code_note(code, note)
        return db.get_register_code(code)

    @router.get("/admin/logs", dependencies=[Depends(require_admin)])
    def list_audit_logs(limit: int = 100):
        return db.list_admin_logs(limit=bounded_limit(limit))

    def _backup_http(exc):
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    @router.get("/admin/backup", dependencies=[Depends(require_admin)])
    def backup_status():
        from .backup import public_status

        return public_status(db)

    @router.put("/admin/backup/webdav", dependencies=[Depends(require_admin)])
    def backup_save_webdav(body: BackupWebDAVIn, admin: dict = Depends(require_admin)):
        from .backup import BackupError, public_status, save_config

        try:
            save_config(db, body.model_dump())
        except BackupError as exc:
            _backup_http(exc)
        _audit(admin, "backup_webdav_save", body.url or "")
        return public_status(db)

    @router.post("/admin/backup/webdav/test", dependencies=[Depends(require_admin)])
    def backup_test_webdav(body: BackupWebDAVIn | None = None):
        from .backup import BackupError, test_connection

        try:
            test_connection(db, None if body is None else body.model_dump())
        except BackupError as exc:
            _backup_http(exc)
        return {"ok": True}

    @router.get("/admin/backup/download", dependencies=[Depends(require_admin)])
    def backup_download():
        from .backup import BackupError, snapshot, with_lock

        try:
            path = with_lock(lambda: snapshot(db))
        except BackupError as exc:
            _backup_http(exc)
        return FileResponse(
            path,
            filename=path.name,
            media_type="application/octet-stream",
        )

    @router.post("/admin/backup/restore/webdav", dependencies=[Depends(require_admin)])
    def backup_restore_webdav(admin: dict = Depends(require_admin)):
        from .backup import BackupError, restore_from_webdav, with_lock

        try:
            with_lock(lambda: restore_from_webdav(db))
        except BackupError as exc:
            _backup_http(exc)
        _audit(admin, "backup_restore", "webdav")
        return {"ok": True}

    @router.post("/admin/backup/restore/upload", dependencies=[Depends(require_admin)])
    def backup_restore_upload(
        admin: dict = Depends(require_admin),
        file: UploadFile = File(...),
    ):
        from .backup import (
            MSG_BAD_UPLOAD,
            UPLOAD_MAX,
            BackupError,
            restore_from_bytes,
            with_lock,
        )

        name = file.filename or ""
        if not name.lower().endswith(".db"):
            raise HTTPException(status_code=400, detail=MSG_BAD_UPLOAD)
        data = file.file.read(UPLOAD_MAX + 1)
        if not data or len(data) > UPLOAD_MAX:
            raise HTTPException(status_code=400, detail=MSG_BAD_UPLOAD)
        try:
            with_lock(lambda: restore_from_bytes(db, data))
        except BackupError as exc:
            _backup_http(exc)
        _audit(admin, "backup_restore", "upload", name)
        return {"ok": True}

    @router.get("/admin/dashboard", dependencies=[Depends(require_admin)])
    def dashboard():
        """业务数据看板：用户/订阅/帖子/推送/数据源健康聚合。"""
        return db.dashboard_stats()

    @router.get("/admin/error-logs", dependencies=[Depends(require_admin)])
    def list_error_logs(
        limit: int = 200,
        level: str | None = None,
        q: str | None = None,
    ):
        """WARNING+ 持久化错误日志（跨重启可查），可按级别与关键词过滤。"""
        if level and level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise HTTPException(status_code=400, detail="level 需为 DEBUG/INFO/WARNING/ERROR/CRITICAL")
        return {
            "logs": db.list_error_logs(
                min(max(limit, 10), 2000),
                level=level,
                q=(q or "").strip() or None,
            )
        }

    @router.get("/admin/system-logs", dependencies=[Depends(require_admin)])
    def list_system_logs(
        limit: int = 200,
        level: str | None = None,
        q: str | None = None,
    ):
        """返回内存环形缓冲里的最近日志行（新→旧），可按级别与关键词过滤（用于网页/Agent 调试）。"""
        from .logging_setup import recent_logs

        if level and level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise HTTPException(status_code=400, detail="level 需为 DEBUG/INFO/WARNING/ERROR/CRITICAL")
        return {
            "lines": recent_logs(
                min(max(limit, 10), 2000),
                level=level,
                q=(q or "").strip() or None,
            )
        }

    # ---- 管理（管理员）----
    @router.get("/admin/kols", dependencies=[Depends(require_admin)])
    def admin_list_kols(
        limit: int = 50,
        offset: int = 0,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        status: int | None = None,
    ):
        """大V管理列表：分页 + 关键词/平台/分类/状态筛选（与公开目录 /api/kols 分离）。"""
        if status is not None and status not in (0, 1):
            raise HTTPException(status_code=400, detail="status 需为 0 或 1")
        q = (q or "").strip() or None
        return {
            "total": db.count_kols(platform=platform, category_id=category_id, q=q, status=status),
            "items": db.list_kols(
                platform=platform,
                category_id=category_id,
                q=q,
                status=status,
                limit=bounded_limit(limit, default=50),
                offset=max(offset, 0),
                with_subscriber_count=True,
            ),
            "ids": db.list_kol_ids(platform=platform, category_id=category_id, q=q, status=status),
        }

    @router.post("/admin/kols/batch", dependencies=[Depends(require_admin)])
    def kol_batch_action(body: KolBatchAction, admin: dict = Depends(require_admin)):
        """批量操作：enable/disable/priority/secondary/normal/category/delete。"""
        if not body.ids:
            raise HTTPException(status_code=400, detail="请先选择大V")
        action = body.action
        if action in ("enable", "disable"):
            db.set_kols_enabled(body.ids, action == "enable")
        elif action in ("priority", "secondary"):
            db.set_kols_flag(body.ids, action, bool(body.value))
        elif action == "normal":
            db.set_kols_flag(body.ids, "priority", False)
            db.set_kols_flag(body.ids, "secondary", False)
        elif action == "category":
            db.set_kols_category(body.ids, body.value)
        elif action == "delete":
            for kol_id in body.ids:
                db.delete_kol(kol_id)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的操作: {action}")
        _audit(admin, f"batch_{action}", str(len(body.ids)), f"ids={body.ids[:20]}")
        return {"ok": True, "count": len(body.ids)}

    @router.get("/kols", dependencies=[Depends(require_admin)])
    def list_kols(platform: str | None = None, category_id: int | None = None):
        return db.list_kols(platform, category_id)

    @router.post("/kols", dependencies=[Depends(require_admin)])
    def add_kol(body: KolIn, admin: dict = Depends(require_admin)):
        if body.platform not in ALLOWED_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {body.platform}")
        external_id = body.external_id.strip()
        name = body.name.strip()
        if body.platform == "xueqiu":
            # 支持直接粘贴雪球主页链接，自动提取 UID
            match = re.search(r"xueqiu\.com/(?:u/)?(\d+)", external_id)
            if match:
                external_id = match.group(1)
        elif body.platform == "combination":
            # 支持直接粘贴组合主页链接，自动提取组合编码 ZHxxxxxx
            symbol = extract_cube_symbol(external_id)
            if symbol:
                external_id = symbol
        elif body.platform == "weibo":
            # 支持直接粘贴微博主页链接，自动提取 UID
            external_id = _normalize_weibo_id(external_id)
        elif body.platform == "zsxq":
            ext, err = kol_requests.normalize_kol_request_input("zsxq", external_id)
            if err:
                raise HTTPException(status_code=400, detail=err)
            external_id = ext
        elif body.platform == "twitter":
            # 与申请/批量导入同一归一化：主页链接存 screen name，推文/系统页拒绝
            if not external_id.startswith(("http://", "https://")) or re.search(
                r"(?:x|twitter)\.com", external_id
            ):
                ext, err = kol_requests.normalize_kol_request_input("twitter", external_id)
                if err:
                    raise HTTPException(status_code=400, detail=err)
                external_id = ext
        if not external_id:
            raise HTTPException(status_code=400, detail="昵称与外部ID不能为空")
        if not name:
            if body.platform == "combination":
                # 没填昵称时自动查组合名称（失败退回占位名）
                cookie = db.get_setting(XUEQIU_COOKIE_KEY) or os.environ.get("XUEQIU_COOKIE", "")
                profile = resolve_combination_profile(external_id, cookie, db=db)
                name = profile.get("name") or f"combination_{external_id}"
            elif body.platform == "weibo":
                # 没填昵称时自动查微博昵称（公开接口，失败退回占位名）
                profile = resolve_weibo_profile(
                    external_id,
                    db.get_setting(WEIBO_COOKIE_KEY) or os.environ.get("WEIBO_COOKIE", ""),
                    db=db,
                )
                name = profile.get("name") or f"weibo_{external_id}"
            elif body.platform == "twitter":
                # 没填昵称时自动查 X 显示名（需 TWITTER_COOKIE，失败退回占位名）
                profile = resolve_x_profile(external_id, db=db)
                name = profile.get("name") or f"twitter_{external_id}"
            elif body.platform == "zsxq":
                profile = resolve_zsxq_profile(external_id, db=db)
                name = profile.get("name") or f"zsxq_{external_id}"
        if body.category_id is not None and db.get_category(body.category_id) is None:
            raise HTTPException(status_code=400, detail="分类不存在")
        kid = db.add_kol(
            body.platform,
            name,
            external_id,
            category_id=body.category_id,
            priority=body.priority,
            secondary=body.secondary,
            original_only=body.original_only,
        )
        _audit(admin, "add_kol", str(kid), f"{body.platform} {name} {external_id}")
        kol = db.get_kol(kid)
        if not kol["avatar_url"]:
            if body.platform == "combination":
                profile = resolve_combination_profile(
                    external_id, db.get_setting(XUEQIU_COOKIE_KEY) or "", db=db
                )
            elif body.platform == "weibo":
                profile = resolve_weibo_profile(
                    external_id,
                    db.get_setting(WEIBO_COOKIE_KEY) or os.environ.get("WEIBO_COOKIE", ""),
                    db=db,
                )
            elif body.platform == "twitter":
                profile = resolve_x_profile(external_id, db=db)
            elif body.platform == "zsxq":
                profile = resolve_zsxq_profile(external_id, db=db)
            else:
                profile = {}
            if profile.get("avatar_url"):
                db.update_kol_avatar(kid, cache_avatar(db, kid, profile["avatar_url"]))
                kol = db.get_kol(kid)
        return kol

    @router.post("/kols/batch", dependencies=[Depends(require_admin)])
    def batch_add_kols(body: KolBatchIn, admin: dict = Depends(require_admin)):
        """批量导入：每行一个「昵称 链接」或「链接」。

        按链接自动识别平台（雪球主页/雪球组合页/微博主页/X主页/知识星球）。
        纯 UID 等无法识别的行失败，不再使用默认平台。
        """
        if body.category_id is not None and db.get_category(body.category_id) is None:
            raise HTTPException(status_code=400, detail="分类不存在")
        results = []
        for raw in body.lines.splitlines():
            line = raw.strip()
            if not line:
                continue
            platform, external_id, nickname, err = _parse_batch_kol_line(line)
            if err:
                results.append({"ok": False, "line": line[:80], "error": err})
                continue
            name = nickname or f"{platform}_{external_id}"
            avatar_url = ""
            if not nickname and platform == "xueqiu" and external_id.isdigit():
                # 没填昵称时自动查雪球昵称与头像（失败则退回 xueqiu_uid）
                cookie = db.get_setting(XUEQIU_COOKIE_KEY) or os.environ.get("XUEQIU_COOKIE", "")
                profile = resolve_profile(external_id, cookie, db=db)
                if profile.get("screen_name"):
                    name = profile["screen_name"]
                avatar_url = profile.get("avatar_url") or ""
            elif platform == "xueqiu" and external_id.isdigit():
                # 已填昵称也补头像（与微博批量行为一致）
                cookie = db.get_setting(XUEQIU_COOKIE_KEY) or os.environ.get("XUEQIU_COOKIE", "")
                profile = resolve_profile(external_id, cookie, db=db)
                avatar_url = profile.get("avatar_url") or ""
            elif platform == "combination":
                # 自动查组合名称（没填昵称时）与主理人头像
                cookie = db.get_setting(XUEQIU_COOKIE_KEY) or os.environ.get("XUEQIU_COOKIE", "")
                profile = resolve_combination_profile(external_id, cookie, db=db)
                if not nickname and profile.get("name"):
                    name = profile["name"]
                avatar_url = profile.get("avatar_url") or ""
            elif platform == "twitter":
                # 自动查 X 显示名（没填昵称时）与头像（需 TWITTER_COOKIE）
                profile = resolve_x_profile(external_id, db=db)
                if not nickname and profile.get("name"):
                    name = profile["name"]
                avatar_url = profile.get("avatar_url") or ""
            elif platform == "weibo":
                # 微博批量导入始终拉取头像（填了昵称也查）；昵称为空时顺带补昵称
                profile = resolve_weibo_profile(
                    external_id,
                    db.get_setting(WEIBO_COOKIE_KEY) or os.environ.get("WEIBO_COOKIE", ""),
                    db=db,
                )
                if not nickname and profile.get("name"):
                    name = profile["name"]
                avatar_url = profile.get("avatar_url") or ""
            elif platform == "zsxq":
                profile = resolve_zsxq_profile(external_id, db=db)
                if not nickname and profile.get("name"):
                    name = profile["name"]
                avatar_url = profile.get("avatar_url") or ""
            try:
                kid = db.add_kol(
                    platform,
                    name,
                    external_id,
                    category_id=body.category_id,
                    priority=body.priority,
                    secondary=body.secondary,
                    original_only=body.original_only,
                )
                if avatar_url:
                    db.update_kol_avatar(kid, cache_avatar(db, kid, avatar_url))
                results.append({"ok": True, "id": kid, "name": name, "external_id": external_id})
            except ValueError as exc:
                results.append({"ok": False, "line": line[:80], "error": str(exc)})
        ok_count = sum(1 for r in results if r["ok"])
        _audit(admin, "batch_add_kols", "", f"ok={ok_count}/{len(results)}")
        return {
            "total": len(results),
            "ok": ok_count,
            "ids": [r["id"] for r in results if r["ok"]],
            "failed": [r for r in results if not r["ok"]],
        }

    @router.put("/kols/{kol_id}", dependencies=[Depends(require_admin)])
    def update_kol(kol_id: int, body: KolUpdate, admin: dict = Depends(require_admin)):
        if db.get_kol(kol_id) is None:
            raise HTTPException(status_code=404, detail="KOL 不存在")
        if "category_id" in body.model_fields_set and body.category_id is not None and db.get_category(body.category_id) is None:
            raise HTTPException(status_code=400, detail="分类不存在")
        name = body.name.strip() if body.name is not None else None
        external_id = body.external_id.strip() if body.external_id is not None else None
        if name == "" or external_id == "":
            raise HTTPException(status_code=400, detail="昵称与外部ID不能为空")
        kol = db.get_kol(kol_id)
        if external_id is not None and kol["platform"] == "weibo":
            external_id = _normalize_weibo_id(external_id)
        if external_id is not None:
            dup = db.get_kol_by_external(kol["platform"], external_id)
            if dup is not None and dup["id"] != kol_id:
                raise HTTPException(status_code=400, detail="该平台已存在相同的外部ID")
        db.update_kol(
            kol_id,
            name=name,
            external_id=external_id,
            enabled=body.enabled,
            category_id=body.category_id if "category_id" in body.model_fields_set else _UNSET,
            priority=body.priority if "priority" in body.model_fields_set else _UNSET,
            secondary=body.secondary if "secondary" in body.model_fields_set else _UNSET,
            recommend_weight=(
                body.recommend_weight if "recommend_weight" in body.model_fields_set else _UNSET
            ),
        )
        if "is_private" in body.model_fields_set and body.is_private is not None:
            db.update_kol(kol_id, is_private=body.is_private)
        if "silent" in body.model_fields_set and body.silent is not None:
            db.update_kol(kol_id, silent=body.silent)
        if "original_only" in body.model_fields_set and body.original_only is not None:
            db.update_kol(kol_id, original_only=body.original_only)
        if "visible_users" in body.model_fields_set and body.visible_users is not None:
            user_ids = []
            for username in body.visible_users:
                target = db.get_user_by_username_ci(username.strip())
                if target is None:
                    raise HTTPException(status_code=400, detail=f"用户不存在: {username}")
                user_ids.append(target["id"])
            db.set_kol_acl(kol_id, user_ids)
        _audit(admin, "update_kol", str(kol_id), f"name={name} enabled={body.enabled}")
        return db.get_kol(kol_id)

    @router.delete("/kols/{kol_id}", dependencies=[Depends(require_admin)])
    def delete_kol(kol_id: int, admin: dict = Depends(require_admin)):
        if db.get_kol(kol_id) is None:
            raise HTTPException(status_code=404, detail="KOL 不存在")
        db.delete_kol(kol_id)
        _audit(admin, "delete_kol", str(kol_id))
        return {"ok": True}

    @router.get("/categories")
    def list_categories(user: dict = Depends(get_current_user)):
        """分类列表：登录用户可读（动态页分类筛选），管理与写入仍需管理员。"""
        return db.list_categories()

    @router.post("/categories", dependencies=[Depends(require_admin)])
    def add_category(body: CategoryIn, admin: dict = Depends(require_admin)):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="分类名不能为空")
        try:
            cid = db.add_category(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "add_category", name)
        return db.get_category(cid)

    @router.put("/categories/{category_id}", dependencies=[Depends(require_admin)])
    def rename_category(category_id: int, body: CategoryIn, admin: dict = Depends(require_admin)):
        if db.get_category(category_id) is None:
            raise HTTPException(status_code=404, detail="分类不存在")
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="分类名不能为空")
        try:
            db.rename_category(category_id, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _audit(admin, "rename_category", str(category_id), name)
        return db.get_category(category_id)

    @router.delete("/categories/{category_id}", dependencies=[Depends(require_admin)])
    def delete_category(category_id: int, admin: dict = Depends(require_admin)):
        if db.get_category(category_id) is None:
            raise HTTPException(status_code=404, detail="分类不存在")
        db.delete_category(category_id)
        _audit(admin, "delete_category", str(category_id))
        return {"ok": True}

    @router.get("/tags")
    def list_tags(request: Request, user: dict = Depends(get_current_user)):
        """贴文话题词表：登录用户可读（动态页标签筛选），管理与写入仍需管理员。

        stats 供管理端展示已打标/待打标贴文数量。
        stock_names 为常用股票名表（纯文字提及打标用，管理端可手动增删）；
        excluded_stock_names 为管理员删掉、维护不加回的名字。
        universe 为全市场 3 字及以上正式简称规模（不进手改名单）。
        dynamic_tags 为贴文里实际出现过的标签（含股票名，去重按频次）。
        stock_aliases 为黑话别名表（LLM 每日自动识别 + 管理端可手动修正）。
        maintain 为最近一次维护摘要 + 是否已配置 LLM（管理端按钮用）。
        """
        from .scheduler import _system_llm_config
        from .stock_universe import universe_meta

        llm_cfg = _system_llm_config(db, getattr(request.app.state, "llm_config", None))
        return {
            "tags": db.get_tag_vocabulary(),
            "stock_names": db.get_stock_names(),
            "stock_aliases": db.get_stock_aliases(),
            "excluded_stock_names": db.get_stock_name_exclusions(),
            "universe": universe_meta(),
            "dynamic_tags": db.aggregate_post_tags(),
            "stats": db.tag_stats(),
            "maintain": {
                "last": db.get_tag_maintain_last(),
                "llm_ready": bool(llm_cfg and getattr(llm_cfg, "api_key", "")),
                "llm_model": (getattr(llm_cfg, "model", "") or "") if llm_cfg else "",
            },
        }

    @router.put("/tags", dependencies=[Depends(require_admin)])
    def update_tag_vocabulary(body: TagVocabularyIn, admin: dict = Depends(require_admin)):
        from .tagging import STOCK_TABLE_MAX, TAG_VOCABULARY_MAX, is_equity_name

        if body.tags is None and body.stock_names is None and body.stock_aliases is None:
            raise HTTPException(status_code=400, detail="没有可保存的字段")

        deduped = None
        if body.tags is not None:
            tags = []
            for rule in body.tags:
                tag = (rule.tag or "").strip()
                if not tag:
                    continue
                tags.append(
                    {
                        "tag": tag,
                        "keywords": [
                            str(k).strip() for k in (rule.keywords or []) if str(k).strip()
                        ],
                    }
                )
            seen, deduped = set(), []
            for rule in tags:
                if rule["tag"] not in seen:
                    seen.add(rule["tag"])
                    deduped.append(rule)
            if not deduped:
                raise HTTPException(status_code=400, detail="词表不能为空")
            if len(deduped) > TAG_VOCABULARY_MAX:
                raise HTTPException(
                    status_code=400, detail=f"词表最多 {TAG_VOCABULARY_MAX} 个标签"
                )

        # 先校验后写入：任一 400 都不产生部分持久化
        previous_names = db.get_stock_names()
        stock_names = previous_names
        if body.stock_names is not None:
            seen_stocks, deduped_stocks, rejected = set(), [], []
            for n in body.stock_names:
                name = (n or "").strip()
                if not name or name in seen_stocks:
                    continue
                if not is_equity_name(name):
                    rejected.append(name)
                    continue
                seen_stocks.add(name)
                deduped_stocks.append(name)
            if rejected:
                raise HTTPException(
                    status_code=400, detail=f"不是个股名：{'、'.join(rejected)}"
                )
            if len(deduped_stocks) > STOCK_TABLE_MAX:
                raise HTTPException(
                    status_code=400, detail=f"常用股票名最多 {STOCK_TABLE_MAX} 个"
                )
            stock_names = deduped_stocks

        from .stock_universe import bundled_plain_names

        pending_excluded = set(db.get_stock_name_exclusions())
        if body.stock_names is not None:
            pending_excluded |= set(previous_names) - set(stock_names)
            pending_excluded -= set(stock_names)
        known_officials = (set(stock_names) | set(bundled_plain_names())) - pending_excluded
        alias_targets: dict[str, str] | None = None
        dropped_aliases: list[dict] = []
        if body.stock_aliases is not None:
            alias_targets = {}
            for a in body.stock_aliases:
                alias = (a.alias or "").strip()
                stock = (a.stock or "").strip()
                if not alias or not stock:
                    continue
                if stock not in known_officials:
                    raise HTTPException(
                        status_code=400,
                        detail=f"别名 {alias} 的正式名 {stock} 不在股票名表中",
                    )
                if alias in known_officials:
                    raise HTTPException(
                        status_code=400,
                        detail=f"别名 {alias} 与股票名重复",
                    )
                previous = alias_targets.get(alias)
                if previous and previous != stock:
                    raise HTTPException(
                        status_code=400,
                        detail=f"别名 {alias} 映射冲突：{previous} / {stock}",
                    )
                alias_targets[alias] = stock
        elif body.stock_names is not None:
            kept, dropped_aliases = [], []
            for item in db.get_stock_aliases():
                if item.get("stock") in known_officials:
                    kept.append(item)
                else:
                    dropped_aliases.append(item)
            alias_targets = {item["alias"]: item["stock"] for item in kept}

        if deduped is not None:
            db.set_tag_vocabulary(deduped)
        if body.stock_names is not None:
            db.sync_stock_name_exclusions(previous_names, stock_names)
            db.set_stock_names(stock_names)
        if alias_targets is not None:
            db.set_stock_aliases(
                [
                    {"alias": alias, "stock": stock}
                    for alias, stock in alias_targets.items()
                ]
            )
        bits = []
        if deduped is not None:
            bits.append(f"{len(deduped)} tags")
        if body.stock_names is not None:
            bits.append(f"{len(stock_names)} stocks")
        if alias_targets is not None:
            bits.append(f"{len(alias_targets)} aliases")
        _audit(admin, "update_tag_vocabulary", detail=", ".join(bits))
        return {
            "tags": db.get_tag_vocabulary(),
            "stock_names": db.get_stock_names(),
            "stock_aliases": db.get_stock_aliases(),
            "excluded_stock_names": db.get_stock_name_exclusions(),
            "dropped_aliases": dropped_aliases,
            "dynamic_tags": db.aggregate_post_tags(),
        }

    @router.post("/tags/maintain", dependencies=[Depends(require_admin)])
    def maintain_post_tags(
        body: TagMaintainIn, request: Request, admin: dict = Depends(require_admin)
    ):
        """立即跑一轮 LLM 标签维护（别名/$标记$/清误标），可选接着回填。

        与每日调度任务同一套逻辑。已有维护在跑时返回 409。
        backfill=none 只改词表；pending/all 随后按当前规则回填贴文。
        """
        from .scheduler import _system_llm_config
        from .tagging import backfill_post_tags, try_run_tag_maintenance

        llm_cfg = _system_llm_config(db, getattr(request.app.state, "llm_config", None))
        result = try_run_tag_maintenance(db, llm_cfg)
        if result is None:
            raise HTTPException(status_code=409, detail="标签维护正在进行")
        purged = 0
        if ima_documents is not None:
            purged = purge_ima_document_tags(ima_documents.store, ima_kb_valid_tags(db))
        backfill = None
        if body.backfill != "none":
            backfill = backfill_post_tags(db, body.backfill)
        last = dict(result)
        last["at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last["purged"] = purged
        if backfill is not None:
            last["backfill"] = {"mode": body.backfill, **backfill}
        db.set_tag_maintain_last(last)
        db.set_setting("stock_alias_last_date", time.strftime("%Y-%m-%d"))
        _audit(
            admin,
            "maintain_post_tags",
            detail=(
                f"backfill={body.backfill} aliases=+{len(result.get('added_aliases') or [])} "
                f"names=+{len(result.get('added_stock_names') or [])} "
                f"cleaned={result.get('cleaned') or 0} purged_kb={purged}"
            ),
        )
        return {**result, "backfill": backfill, "at": last["at"], "purged": purged}

    @router.post("/tags/backfill", dependencies=[Depends(require_admin)])
    def backfill_post_tags_api(body: TagBackfillIn, admin: dict = Depends(require_admin)):
        """按当前规则回填/重算贴文标签（关键词规则，零成本）。

        mode=pending（默认）只处理尚未打标的帖（''/NULL）；mode=all 全量重算，
        覆盖全部历史标签（含零命中帖标记为 []）。两种模式都用 id 游标扫一遍，
        不会无限循环。保存词表不触发此操作，全量重算必须由管理员显式发起。
        """
        from .tagging import backfill_post_tags

        result = backfill_post_tags(db, body.mode)
        _audit(
            admin,
            "backfill_post_tags",
            detail=f"mode={body.mode} processed={result['processed']} tagged={result['tagged']}",
        )
        return result

    @router.get("/posts", dependencies=[Depends(require_admin)])
    def list_posts(limit: int = 100, platform: str | None = None, kol_id: int | None = None, q: str | None = None, offset: int = 0):
        return db.list_posts(limit=bounded_limit(limit), platform=platform, kol_id=kol_id, q=q, offset=offset)

    @router.get("/push-logs", dependencies=[Depends(require_admin)])
    def list_push_logs(
        limit: int = 100,
        user_id: int | None = None,
        channel: str | None = None,
        status: str | None = None,
    ):
        return db.list_push_logs(
            limit=bounded_limit(limit),
            user_id=user_id,
            channel=channel,
            status=status,
        )

    @router.get("/users", dependencies=[Depends(require_admin)])
    def list_users():
        invites = _invite_by_user_id()
        personal = db.active_feishu_personal_user_ids()
        webpush_ids = db.webpush_user_ids()
        counts = db.subscription_counts()
        n_days, m_days = db.get_inactive_policy()
        inactive_ids = {r["id"] for r in db.list_inactive_user_rows(n_days)}
        ima_kb_acl = db.ima_kb_acl_map()
        ima_kb_subs = db.ima_kb_sub_map()
        return [
            admin_user_summary(
                u,
                invites.get(u["id"]),
                feishu_personal_active=u["id"] in personal,
                webpush_bound=u["id"] in webpush_ids,
                subscription_count=counts.get(u["id"], 0),
                inactive=u["id"] in inactive_ids,
                days_until_purge=(
                    days_until_purge(u.get("created_at"), n_days, m_days)
                    if u["id"] in inactive_ids
                    else None
                ),
                ima_kb_groups=ima_kb_acl.get(u["id"], []),
                ima_kb_subscribed=ima_kb_subs.get(u["id"], []),
            )
            for u in db.list_users()
        ]

    def _inactive_policy_out(
        preview_after: int | None = None,
        preview_purge: int | None = None,
    ) -> dict:
        n, m = db.get_inactive_policy()
        pn = n if preview_after is None else preview_after
        pm = m if preview_purge is None else preview_purge
        marked, doomed = db.inactive_policy_counts(pn, pm)
        return {
            "inactive_after_days": n,
            "inactive_purge_after_days": m,
            "customized": db.inactive_policy_customized(),
            "marked_count": marked,
            "purge_count": doomed,
        }

    @router.get("/admin/inactive-users-policy", dependencies=[Depends(require_admin)])
    def get_inactive_users_policy(
        preview_after: int | None = Query(None, alias="inactive_after_days"),
        preview_purge: int | None = Query(None, alias="inactive_purge_after_days"),
    ):
        for value in (preview_after, preview_purge):
            if value is not None and (value < 0 or value > 3650):
                raise HTTPException(status_code=400, detail="天数须在 0–3650")
        return _inactive_policy_out(preview_after, preview_purge)

    @router.put("/admin/inactive-users-policy")
    def put_inactive_users_policy(body: InactiveUsersPolicyIn, admin: dict = Depends(require_admin)):
        n, m = body.inactive_after_days, body.inactive_purge_after_days
        if n < 0 or n > 3650 or m < 0 or m > 3650:
            raise HTTPException(status_code=400, detail="天数须在 0–3650")
        n, m = db.set_inactive_policy(n, m)
        _audit(admin, "update_inactive_users_policy", "", f"n={n} m={m}")
        return _inactive_policy_out()

    @router.post("/admin/users/batch", dependencies=[Depends(require_admin)])
    def users_batch_action(body: UserBatchAction, admin: dict = Depends(require_admin)):
        if not body.ids:
            raise HTTPException(status_code=400, detail="请先选择用户")
        action = body.action
        if action in ("enable_notify", "disable_notify"):
            n = db.set_users_notify(body.ids, action == "enable_notify")
            skipped = max(0, len(body.ids) - n)
            _audit(admin, f"batch_{action}", str(n), f"ids={body.ids[:20]}")
            return {"ok": True, "count": n, "skipped": skipped}
        if action == "delete":
            count = 0
            skipped = 0
            for uid in body.ids:
                target = db.get_user(uid)
                if delete_user_block_reason(target, admin):
                    skipped += 1
                    continue
                db.delete_user(uid)
                count += 1
            _audit(admin, "batch_delete_users", str(count), f"ids={body.ids[:20]} skipped={skipped}")
            return {"ok": True, "count": count, "skipped": skipped}
        raise HTTPException(status_code=400, detail=f"不支持的操作: {action}")

    @router.get("/stats", dependencies=[Depends(require_admin)])
    def stats():
        kols = db.list_kols(with_subscriber_count=True)
        # 「正常」状态有新鲜度窗口：source_ok 太久没更新视为近期无成功，
        # 避免平台曾成功过一次就永远显示正常（连续失败被掩盖）。
        # 窗口取 2× 全局轮询间隔，至少 5 分钟；无启用大V的平台不判定。
        try:
            poll_interval = int(db.get_setting("config_interval_seconds") or 0)
        except (TypeError, ValueError):
            poll_interval = 0
        ok_window = max(poll_interval * 2, 300)
        now = int(time.time())
        enabled_by_platform: dict[str, int] = {}
        for k in kols:
            if k["enabled"]:
                enabled_by_platform[k["platform"]] = enabled_by_platform.get(k["platform"], 0) + 1
        sources = []
        for platform in sorted(ALLOWED_PLATFORMS):
            ok_at = db.get_setting(f"source_ok_{platform}")
            err = db.get_setting(f"source_err_{platform}") or ""
            fails = db.get_setting(f"source_fails_{platform}") or "0"
            ev = db.source_event_stats(platform, 24)
            total = ev["ok"] + ev["fail"]
            fresh = False
            if ok_at:
                try:
                    fresh = now - int(ok_at) <= ok_window
                except (TypeError, ValueError):
                    fresh = True  # 时间戳格式异常时按有效处理，不阻断展示
            if enabled_by_platform.get(platform, 0) == 0:
                fresh = bool(ok_at)  # 无启用大V：不判过期，保留原语义
            src = {
                "platform": platform,
                "ok": fresh,
                "last_ok_at": ok_at,
                "last_error": err,
                "consecutive_fails": int(fails),
                "ok_24h": ev["ok"],
                "fail_24h": ev["fail"],
                "warn_24h": ev["warn"],
                "success_rate_24h": round(ev["ok"] * 100 / total) if total else None,
                "next_retry_at": db.get_setting(f"source_next_retry_at_{platform}") or "",
                "last_alert_at": db.get_setting(f"source_alert_{platform}") or "",
            }
            if platform == "twitter":
                # X 通道状态：直抓成功为 direct；失败未恢复为 fallback（沿用字段名）
                direct_ok = db.get_setting("x_direct_last_ok_at")
                fallback_at = db.get_setting("x_direct_last_fallback_at")
                if fallback_at and (not direct_ok or fallback_at > direct_ok):
                    src["direct_mode"] = "fallback"
                elif direct_ok:
                    src["direct_mode"] = "direct"
                else:
                    src["direct_mode"] = "unknown"
                src["direct_last_ok_at"] = direct_ok
                src["direct_fallback_reason"] = (
                    db.get_setting("x_direct_fallback_reason") or ""
                )
            sources.append(src)
        xueqiu_cookie = db.get_setting("xueqiu_cookie") or ""
        xueqiu_updated = db.get_setting("xueqiu_cookie_updated_at") or ""
        weibo_cookie = db.get_setting("weibo_cookie") or ""
        weibo_updated = db.get_setting("weibo_cookie_updated_at") or ""
        twitter_status = _cookie_status(TWITTER_COOKIE_KEY, TWITTER_COOKIE_TIME_KEY)
        if not twitter_status["set"]:
            env_tw = os.environ.get("TWITTER_COOKIE", "")
            if env_tw:
                twitter_status = {
                    "set": True,
                    "updated_at": "",
                    "preview": "已配置",
                    "from_env": True,
                }
        zsxq_status = _cookie_status(ZSXQ_COOKIE_KEY, ZSXQ_COOKIE_TIME_KEY)
        if not zsxq_status["set"]:
            env_zq = os.environ.get("ZSXQ_COOKIE") or os.environ.get("ZSXQ_ACCESS_TOKEN", "")
            if env_zq:
                zsxq_status = {
                    "set": True,
                    "updated_at": "",
                    "preview": "已配置",
                    "from_env": True,
                }
        ima_cookie_status = _cookie_status(IMA_COOKIE_KEY, IMA_COOKIE_TIME_KEY)
        if not ima_cookie_status["set"]:
            env_ima = os.environ.get("IMA_COOKIE", "")
            if env_ima:
                ima_cookie_status = {
                    "set": True,
                    "updated_at": "",
                    "preview": "已配置",
                    "from_env": True,
                }
        last_post_at = db.last_post_time_by_kol()
        kol_health = [
            {
                "id": k["id"],
                "name": k["name"],
                "platform": k["platform"],
                "enabled": bool(k["enabled"]),
                "last_post_at": last_post_at.get(k["id"]) or "",
                "subscriber_count": int(k.get("subscriber_count") or 0),
            }
            for k in kols
        ]
        kol_health.sort(key=lambda h: h["last_post_at"])
        return {
            "polling_interval_seconds": int(db.get_setting("stats_polling_interval") or 0),
            "keepalive_interval_seconds": int(db.get_setting("stats_keepalive_interval") or 0),
            "posts_retention_days": int(db.get_setting("stats_posts_retention_days") or 0),
            "last_poll_at": db.get_setting("stats_last_poll_at"),
            "last_poll_duration_ms": db.get_setting("stats_last_poll_duration_ms"),
            "last_poll_error": db.get_setting("stats_last_poll_error") or "",
            "kols": len(kols),
            "enabled_kols": sum(1 for k in kols if k["enabled"]),
            "active_kols": len(db.kol_ids_with_subscribers()),  # 有订阅者、正在被抓取的大V数
            "priority_kols": sum(1 for k in kols if k.get("priority")),
            "secondary_kols": sum(1 for k in kols if k.get("secondary")),
            "users": db.count_users(),
            "posts": db.count_posts(),
            "sources": sources,
            "xueqiu_cookie": {
                "set": bool(xueqiu_cookie),
                "updated_at": xueqiu_updated,
                "preview": "已配置" if xueqiu_cookie else "",
            },
            "weibo_cookie": {
                "set": bool(weibo_cookie),
                "updated_at": weibo_updated,
                "preview": "已配置" if weibo_cookie else "",
            },
            "twitter_cookie": twitter_status,
            "zsxq_cookie": zsxq_status,
            "ima_credentials": {
                "mode": ("openapi" if (db.get_setting(IMA_CLIENT_ID_KEY) or os.environ.get("IMA_OPENAPI_CLIENTID", "")) and (db.get_setting(IMA_API_KEY_KEY) or os.environ.get("IMA_OPENAPI_APIKEY", "")) else ("cookie" if db.get_setting(IMA_COOKIE_KEY) or os.environ.get("IMA_COOKIE", "") else "none")),
                "cookie": ima_cookie_status,
                "openapi_clientid": {
                    "set": bool(db.get_setting(IMA_CLIENT_ID_KEY) or os.environ.get("IMA_OPENAPI_CLIENTID", "")),
                    "preview": _cred_preview(db.get_setting(IMA_CLIENT_ID_KEY) or os.environ.get("IMA_OPENAPI_CLIENTID", "")),
                },
            },
            "ima_collector": _ima_collector_status(),
            "imgbed": _imgbed_status(),
            "turnstile": _turnstile_admin_status(),
            "polling_config": _effective_polling(),
            "plaza_sources": plaza_source_rows(db),
            "zsxq_cache": zsxq_cache_stats(db),
            "recent_source_events": db.recent_source_events(30),
            "kol_health": kol_health,
            "retry_pending": int(db.get_setting("stats_retry_pending") or 0),
            "pending_kol_requests": db.count_pending_kol_requests(),
            "alerts": {
                "push_alert_last_at": db.get_setting("push_alert_last_at") or "",
                "x_direct_alert_at": db.get_setting("x_direct_alert_at") or "",
                "cookie_keepalive_alert_at": db.get_setting("cookie_keepalive_alert_at") or "",
                "xueqiu_probe_alert_at": db.get_setting("xueqiu_probe_alert_at") or "",
            },
        }

    @router.put("/users/{user_id}", dependencies=[Depends(require_admin)])
    def update_user(user_id: int, body: UserUpdate, admin: dict = Depends(require_admin)):
        target = db.get_user(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        updates = {}
        revoke_tokens = False
        if "is_admin" in body.model_fields_set:
            if user_id == admin["id"] and not body.is_admin:
                raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
            updates["is_admin"] = body.is_admin
        if "password" in body.model_fields_set:
            password = body.password or ""
            if len(password) < MIN_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail=f"密码至少{MIN_PASSWORD_LEN}位")
            if len(password) > MAX_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail=f"密码最长{MAX_PASSWORD_LEN}位")
            updates["password_hash"] = auth.hash_password(password)
            revoke_tokens = True
        if "username" in body.model_fields_set:
            try:
                username = auth.validate_username(body.username or "")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            existing = db.get_user_by_username_ci(username)
            if existing is not None and existing["id"] != user_id:
                raise HTTPException(status_code=400, detail="用户名已存在")
            updates["username"] = username
        db.update_user_atomic(user_id, updates, revoke_tokens=revoke_tokens)
        _audit(
            admin,
            "update_user",
            str(user_id),
            f"is_admin={body.is_admin} password={'*' if body.password else ''} username={body.username}",
        )
        user_row = db.get_user(user_id)
        bot = db.get_feishu_personal_bot(user_id)
        return admin_user_summary(
            user_row,
            _invite_by_user_id().get(user_id),
            feishu_personal_active=bool(bot and bot["status"] == "active" and bot.get("chat_id")),
            webpush_bound=db.count_webpush_subscriptions(user_id) > 0,
            subscription_count=db.count_subscriptions(user_id),
            ima_kb_groups=db.ima_kb_group_ids_for_user(user_id),
            ima_kb_subscribed=db.ima_kb_subscribed_group_ids_for_user(user_id),
        )

    @router.delete("/users/{user_id}", dependencies=[Depends(require_admin)])
    def delete_user(user_id: int, admin: dict = Depends(require_admin)):
        target = db.get_user(user_id)
        blocked = delete_user_block_reason(target, admin)
        if blocked:
            status = 404 if blocked == "用户不存在" else 400
            raise HTTPException(status_code=status, detail=blocked)
        db.delete_user(user_id)
        _audit(admin, "delete_user", str(user_id), target["username"])
        return {"ok": True}

    @router.post("/admin/test-push", dependencies=[Depends(require_admin)])
    def test_push(body: TestPushIn):
        user = db.get_user(body.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if notifiers_config is None:
            raise HTTPException(status_code=400, detail="未配置推送渠道")
        from .channels import CHANNELS, build_channel_notifier, channel_bound

        results = []
        for channel in CHANNELS:
            if not channel_bound(user, channel, notifiers_config, db):
                continue
            notifier = build_channel_notifier(channel, user, notifiers_config, db=db)
            try:
                notifier.send_text(f"【测试推送】{body.message}")
                results.append({"channel": channel, "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({"channel": channel, "ok": False, "error": str(exc)})
            finally:
                notifier.client.close()
        if not results:
            raise HTTPException(status_code=400, detail="该用户未绑定任何推送渠道")
        return {"results": results}

    @router.post("/admin/weibo-qr/start", dependencies=[Depends(require_admin)])
    def weibo_qr_start():
        """生成微博扫码登录二维码，返回 qrid 与二维码图片地址。"""
        now = time.time()
        # 清理超过 5 分钟的旧会话
        for qrid, session in list(weibo_qr_sessions.items()):
            if now - session["created_at"] > 300:
                session["client"].close()
                weibo_qr_sessions.pop(qrid, None)
        try:
            client, qrid, qrurl = create_qr(db=db)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="获取微博二维码失败，请稍后重试")
        weibo_qr_sessions[qrid] = {
            "client": client,
            "created_at": now,
        }
        return {"qrid": qrid, "qrurl": qrurl}

    @router.get("/admin/weibo-qr/status", dependencies=[Depends(require_admin)])
    def weibo_qr_status(qrid: str):
        """轮询扫码状态；确认后自动完成登录并保存 Cookie。"""
        session = weibo_qr_sessions.get(qrid)
        if session is None:
            raise HTTPException(status_code=404, detail="二维码已过期，请重新生成")
        client = session["client"]
        try:
            result = poll_qr(client, qrid)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="微博登录状态获取失败，请重试") from None
        status = result.get("status")
        if status == "pending":
            return {"status": "pending"}
        if status == "scanned":
            return {"status": "scanned"}
        if status == "expired":
            # 二维码已作废：会话客户端无人再轮询，就地回收
            client.close()
            weibo_qr_sessions.pop(qrid, None)
            raise HTTPException(status_code=400, detail="二维码已失效，请重新生成")
        if status == "ok" and result.get("cookie"):
            cookie = result["cookie"]
            db.set_setting(WEIBO_COOKIE_KEY, cookie)
            client.close()
            weibo_qr_sessions.pop(qrid, None)
            return {"status": "ok"}
        raise HTTPException(
            status_code=400,
            detail=f"微博登录异常: {result.get('detail') or status}",
        )

    @router.get("/img-proxy")
    def img_proxy(url: str, request: Request):
        """受信图床代理：精确域名、HTTPS、无重定向、流式限制 10 MB，按 IP 限速。

        视频（.mp4/.webm）单独走流式通道：透传 Range（206 分段，播放器拖动
        进度必需）、上限 60MB、边下边发不整段缓冲——图床对视频不回 206，
        播放统一从源站代理。
        """
        from urllib.parse import urlparse

        url = (url or "").strip()
        try:
            parsed = urlparse(url)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() not in IMAGE_PROXY_HOSTS
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(status_code=400, detail="不支持的图片地址")
        from .url_safety import img_proxy_resolved_ok

        if not img_proxy_resolved_ok(parsed.hostname):
            raise HTTPException(status_code=400, detail="不支持的图片地址")

        if url.lower().split("?", 1)[0].endswith((".mp4", ".webm")):
            return _img_proxy_video(url, request)

        _check_img_proxy_limit(_client_ip(request))
        client = httpx.Client(
            timeout=15,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Referer": "https://weibo.com/",
            },
        )
        try:
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(status_code=502, detail="图片源请求失败")
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    raise HTTPException(status_code=400, detail="非图片内容")
                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > 10 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail="图片过大")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 10 * 1024 * 1024:
                        raise HTTPException(status_code=400, detail="图片过大")
            return Response(
                content=bytes(body),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
        finally:
            client.close()

    return router
