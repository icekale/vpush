"""应用入口：FastAPI + 调度器生命周期。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import auth, imgbed
from .api import create_api_router
from .config import load_config
from .db import DB
from .feishu_documents import FeishuDocumentSyncService
from .fetchers import build_fetchers
from .ima_documents import ImaDocumentService
from .ima_search import ImaSearchIndex
from .ima_storage import ImaStorageStatus
from .logging_setup import register_error_sink, setup_logging
from .news import NewsService
from .notifiers import build_notifiers
from .scheduler import Scheduler, set_alerts_enabled
from .static_assets import (
    IMMUTABLE_CACHE_CONTROL,
    REVALIDATE_CACHE_CONTROL,
    resolve_fingerprinted_path,
    should_revalidate,
)

# 纯 UI 调试模式开关：置 1 时跳过调度器与机器人长连接，避免测试实例
# 抢生产 Telegram 机器人（getUpdates 409）、用测试配置误发降级告警
# （曾因本地测试实例未配 TWITTER_COOKIE 给生产群发「未配置 TWITTER_COOKIE」）。
WORKERS_ENV = "DAV_UI_ONLY"
IMA_PAGE_WARMUP_ENV = "IMA_PAGE_WARMUP"

logger = logging.getLogger(__name__)


def background_workers_enabled() -> bool:
    """调度器/机器人等后台任务是否启用（DAV_UI_ONLY=1 时关闭）。"""
    return os.environ.get(WORKERS_ENV, "0") != "1"


def ima_page_warmup_enabled() -> bool:
    """研报列表页启动预热是否启用（默认启用，IMA_PAGE_WARMUP=0 时关闭）。"""
    return os.environ.get(IMA_PAGE_WARMUP_ENV, "1") != "0"


def start_ima_page_warmup(db: DB) -> threading.Thread | None:
    if not ima_page_warmup_enabled():
        return None
    thread = threading.Thread(
        target=db.warm_ima_document_page,
        daemon=True,
        name="ima-page-warmup",
    )
    thread.start()
    return thread


def docs_enabled() -> bool:
    """是否对外暴露 /docs、/redoc、/openapi.json（默认关闭，WEB_ENABLE_DOCS=1 时开启）。

    生产默认关闭以减少接口暴露面；本地开发调试时设 WEB_ENABLE_DOCS=1 即可。
    """
    return os.environ.get("WEB_ENABLE_DOCS", "0") == "1"


SPA_PREFIXES = frozenset({
    "timeline", "home", "combinations", "mysubs", "settings", "news",
    "search", "kol", "more", "admin", "zsxq", "ima-documents", "knowledge", "ticker",
})


def is_spa_path(path: str) -> bool:
    """History API 前端路径：第一段在白名单内则回退 index.html。"""
    first = path.replace("\\", "/").strip("/").split("/", 1)[0]
    return first in SPA_PREFIXES


class _SpaStaticFiles(StaticFiles):
    """SPA 静态资源：带内容哈希的 JS/CSS 长期不可变缓存；HTML/manifest 每次校验。"""

    async def get_response(self, path, scope):
        directory = Path(self.directory)
        logical = resolve_fingerprinted_path(directory, path)
        cache_control = IMMUTABLE_CACHE_CONTROL if logical else (
            REVALIDATE_CACHE_CONTROL if should_revalidate(path) else None
        )
        serve_path = logical or path
        try:
            response = await super().get_response(serve_path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and is_spa_path(path):
                response = await super().get_response("index.html", scope)
                response.headers["Cache-Control"] = REVALIDATE_CACHE_CONTROL
                return response
            raise
        if cache_control:
            response.headers["Cache-Control"] = cache_control
        elif "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = REVALIDATE_CACHE_CONTROL
        return response

setup_logging()
access_logger = logging.getLogger("app.access")


def create_app(config=None, db_path: str | Path | None = None) -> FastAPI:
    config = config or load_config()
    if db_path is not None:
        config.db_path = str(db_path)
    db = DB(config.db_path, credential_key=config.notifiers.feishu.credential_key)
    news_service = NewsService(db)
    index_root = Path(config.db_path).parent / "ima"
    archive_env = os.environ.get("IMA_ARCHIVE_ROOT", "").strip()
    archive_root = Path(archive_env) if archive_env else index_root
    status_env = os.environ.get("IMA_STORAGE_STATUS_PATH", "").strip()
    storage_status = ImaStorageStatus(status_env or None, remote=bool(archive_env))
    # 全文检索索引：只当 IMA_SEARCH_GROUP_IDS 非空时启用；为空则整个 FTS 路径关闭，检索回退到
    # db.py 的 LIKE 路径（中文编译产物的召回在那里，不依赖索引）。
    search_group_ids = tuple(
        dict.fromkeys(
            item.strip()
            for item in os.environ.get("IMA_SEARCH_GROUP_IDS", "").split(",")
            if item.strip()
        )
    )
    ima_search_index = ImaSearchIndex(
        Path(config.db_path).parent / "ima-search.db",
        archive_root,
        search_group_ids,
    )
    ima_documents = ImaDocumentService(
        db,
        index_root,
        archive_root=archive_root,
        storage_status=storage_status,
        llm_config=config.llm,
        search_index=ima_search_index,
    )
    feishu_documents = FeishuDocumentSyncService(
        db,
        config.feishu_documents,
        archive_root,
        ima_documents=ima_documents,
    )
    # WARNING+ 日志持久化到 error_logs 表（跨重启可查，管理后台错误记录面板）
    register_error_sink(
        lambda record: db.record_error_log(record.levelname, record.name, record.getMessage())
    )
    existing_xueqiu_cookie = db.get_setting("xueqiu_cookie")
    if existing_xueqiu_cookie:
        try:
            from .fetchers.xueqiu import write_xueqiu_seed_cookie

            write_xueqiu_seed_cookie(existing_xueqiu_cookie)
        except OSError:
            logger.warning("雪球 sidecar seed cookie 启动同步失败")
    db.set_setting("stats_polling_interval", str(config.polling.interval_seconds))
    db.set_setting("stats_posts_retention_days", str(config.polling.posts_retention_days))
    db.set_setting(
        "stats_keepalive_interval",
        str(config.polling.cookie_keepalive_interval_seconds),
    )
    db.set_setting("stats_priority_interval_seconds", str(config.polling.priority_interval_seconds))
    db.set_setting("stats_digest_interval_seconds", str(config.polling.digest_interval_seconds))
    db.set_setting(
        "stats_source_probe_interval_seconds",
        str(config.polling.source_probe_interval_seconds),
    )
    db.set_setting("stats_daily_report_hour", str(config.polling.daily_report_hour))
    # 采集频率档位默认值（无新帖自适应降频参数）：首次启动写入，后台可覆盖
    from .scheduler import (
        COMBINATION_BASE_SECONDS,
        COMBINATION_IDLE_CAP_SECONDS,
        NORMAL_IDLE_CAP_SECONDS,
        PRIORITY_IDLE_CAP_SECONDS,
        X_FALLBACK_CAP_SECONDS,
    )

    for key, value in (
        ("config_combination_base_seconds", COMBINATION_BASE_SECONDS),
        ("config_combination_idle_cap_seconds", COMBINATION_IDLE_CAP_SECONDS),
        ("config_normal_idle_cap_seconds", NORMAL_IDLE_CAP_SECONDS),
        ("config_priority_idle_cap_seconds", PRIORITY_IDLE_CAP_SECONDS),
        ("config_x_fallback_cap_seconds", X_FALLBACK_CAP_SECONDS),
    ):
        db.set_setting(key, str(value))
    # 次要大V档位：从 polling 配置取值（ENV 可覆盖），且不覆盖管理员已调值
    for key, value in (
        ("config_secondary_base_seconds", config.polling.secondary_interval_seconds),
        ("config_secondary_idle_cap_seconds", config.polling.secondary_idle_cap_seconds),
        ("config_secondary_digest_interval_seconds", config.polling.secondary_digest_interval_seconds),
        ("config_secondary_min_digest_count", config.polling.secondary_min_digest_count),
    ):
        if db.get_setting(key) is None:
            db.set_setting(key, str(value))
    db.merge_default_tag_vocabulary()
    db.merge_default_stock_aliases()
    secret = auth.get_or_create_secret(db, config.web.token_secret)
    if not (config.notifiers.feishu.credential_key or "").strip():
        logging.getLogger(__name__).warning(
            "FEISHU_CREDENTIAL_KEY 未配置，用户推送凭据将明文落库"
        )
    if not (config.web.token_secret or "").strip():
        logging.getLogger(__name__).warning(
            "WEB_TOKEN_SECRET 未配置，会话签名密钥写在数据库里，备份即可伪造登录"
        )
    if not (
        (config.web.turnstile_secret or "").strip()
        and (config.web.turnstile_site_key or "").strip()
    ):
        logging.getLogger(__name__).warning(
            "Turnstile 未配齐，登录注册将跳过人机验证"
        )

    if config.web.admin_password:
        admin = db.get_user_by_username("admin")
        if admin is None:
            db.add_user(
                "admin",
                auth.hash_password(config.web.admin_password),
                is_admin=True,
            )
    imgbed.configure(config)
    imgbed.apply_runtime(
        db.get_setting("imgbed_base_url") or config.imgbed.base_url,
        db.get_setting("imgbed_token") or config.imgbed.token,
        db.get_setting("imgbed_channel") or config.imgbed.channel,
        db.get_setting("imgbed_channel_name") or config.imgbed.channel_name,
        db.get_setting("imgbed_folder") or config.imgbed.folder,
    )
    fetchers = build_fetchers(config, db)
    notifiers = build_notifiers(config)
    def _ima_archive_file(relative):
        store = getattr(ima_documents, "store", None)
        if store is None or not store.archive_readable():
            return None
        return store.authorized_archive_file(relative)

    scheduler = Scheduler(
        db,
        fetchers,
        notifiers,
        config.polling,
        config.notifiers,
        config.sources.xueqiu,
        config.sources.weibo,
        config.llm,
        news_service=news_service,
        ima_archive_file=_ima_archive_file,
    )
    ima_documents.on_files_ready = scheduler._run_report_extraction_task

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        bot = None
        bot_task = None
        # 告警总开关统一用 config.alerts_enabled（config.yaml 与 ALERTS_ENABLED 环境变量均可配置）；
        # 在 lifespan 内注入而非模块级，避免导入即钉死全局 flag、影响测试对环境变量的操控
        set_alerts_enabled(config.alerts_enabled)
        if background_workers_enabled():
            ima_documents.start()
            feishu_documents.start()
            # ponytail: 后台线程预热 timeline 缓存，不挡启动；首击不再冷读
            threading.Thread(target=feishu_documents.warm_timeline_cache, daemon=True, name="feishu-timeline-warmup").start()
            # ponytail: 同上，预热研报列表页（冷页缓存下首屏列表查询实测 6.5-12s）
            start_ima_page_warmup(db)
            task = asyncio.create_task(scheduler.run())
            if config.alerts_enabled and config.notifiers.telegram.bot_token:
                from .telegram_bot import TelegramBot

                bot = TelegramBot(
                    db,
                    config.notifiers.telegram.bot_token,
                    secret,
                    proxy=config.notifiers.telegram.proxy,
                )
                bot_task = asyncio.create_task(bot.run())
            if (
                config.alerts_enabled
                and config.notifiers.feishu.app_id
                and config.notifiers.feishu.app_secret
            ):
                from .feishu_bot import FeishuBot

                FeishuBot(db, config.notifiers.feishu.app_id, config.notifiers.feishu.app_secret).start()
            # 飞书个人机器人：清理进程重启遗留的未结束注册会话
            if config.notifiers.feishu.credential_key:
                from .feishu_personal import FeishuPersonalManager

                FeishuPersonalManager(db, config.notifiers.feishu).expire_stale()
            if "PYTEST_CURRENT_TEST" not in os.environ:
                from .api import start_wscn_live_refresh

                threading.Thread(target=start_wscn_live_refresh, daemon=True, name="wscn-refresh").start()
        else:
            logger.warning("DAV_UI_ONLY=1 已跳过调度器与机器人长连接，仅提供网页 UI")
        yield
        # 先通知调度器停止并等待当前 to_thread 任务返回，最后再关闭 SQLite。
        if task is not None:
            scheduler.stop()
            await task
        if bot_task is not None:
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
            if bot is not None:
                bot.client.close()
        news_service.close()
        feishu_documents.stop()
        ima_documents.stop()
        db.close()

    docs = docs_enabled()
    app = FastAPI(
        title="V Push",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.db = db
    app.state.llm_config = config.llm
    app.state.ima_documents = ima_documents
    app.state.feishu_documents = feishu_documents
    app.state.ima_search_index = ima_search_index
    app.state.news_service = news_service

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """基础安全响应头：防 MIME 嗅探 / 点击劫持 / Referer 泄露。"""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "connect-src 'self' https://challenges.cloudflare.com; "
            "frame-src 'self' blob: https://challenges.cloudflare.com; "
            "worker-src 'self'; "
            "manifest-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'",
        )
        path = request.url.path
        if path.startswith(("/news/", "/api/news/")) or path in ("/news", "/api/news"):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        """API 请求日志：默认 DEBUG；超过 1 秒的慢请求 WARNING 提醒（方便排查）。"""
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        user = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            payload = auth.verify_token(auth_header[7:], secret)
            if payload:
                user = payload.get("name") or ""
        line = (
            f"{request.method} {request.url.path} -> {response.status_code} "
            f"({duration_ms:.0f}ms) user={user}"
        )
        if duration_ms >= 1000:
            access_logger.warning("SLOW %s", line)
        else:
            access_logger.debug("%s", line)
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/healthz/ima-storage")
    def ima_storage_health(response: Response):
        payload = ima_documents.storage_status.public()
        if not ima_documents.store.archive_readable():
            response.status_code = 503
        return {"status": payload.get("status"), "available": payload.get("available")}

    app.include_router(
        create_api_router(
            db,
            secret,
            allow_register=config.web.allow_register,
            wechat_config=config.wechat,
            notifiers_config=config.notifiers,
            trust_proxy=config.web.trust_proxy,
            turnstile_site_key=config.web.turnstile_site_key,
            turnstile_secret=config.web.turnstile_secret,
            turnstile_hostnames=config.web.turnstile_hostnames,
            ima_documents=ima_documents,
            feishu_documents=feishu_documents,
            news_service=news_service,
        )
    )
    # 本地头像缓存（数据目录/avatars），避免第三方图床过期/外链失效
    avatars_dir = Path(config.db_path).parent / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=avatars_dir), name="avatars")
    # 雪球新采集图片去水印后的本地缓存（数据目录/xq_images）
    xq_images_dir = Path(config.db_path).parent / "xq_images"
    xq_images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/xq-images", StaticFiles(directory=xq_images_dir), name="xq-images")
    zsxq_images_dir = Path(config.db_path).parent / "zsxq_images"
    zsxq_images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/zsxq-images", StaticFiles(directory=zsxq_images_dir), name="zsxq-images")
    # 知识星球附件不设静态挂载：附件可能是私有大V的付费内容，
    # 一律走鉴权路由 /api/media/zsxq-file/{id}（命中本地缓存时直接下发）
    app.mount(
        "/",
        _SpaStaticFiles(directory=Path(__file__).parent / "static", html=True),
        name="static",
    )
    return app


app = create_app()
