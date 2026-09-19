"""SQLite 持久化：KOL、帖子（去重）、推送日志。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .logging_setup import redact_secrets
from .zh_simp import to_simplified

_UNSET = object()
BIND_TRY_LIMIT = 8
BIND_TRY_WINDOW = 600
BIND_ISSUE_LIMIT = 3
BIND_ISSUE_WINDOW = 600

# ---- 用户级推送凭据的 at-rest 加密 ----
# 存储格式：enc1:<Fernet 密文>。无前缀即存量明文（读取时原样返回），
# 配置了凭据密钥后由 _migrate 一次性加密收编，幂等。
SECRET_PREFIX = "enc1:"
# users 表中以密文落库的凭证列；feed_token 刻意除外（本身随 RSS URL 公开）
SECRET_COLUMNS = ("telegram_bot_token", "wecom_webhook", "bark_key", "llm_api_key")
# 需要维护明文哈希列做唯一性查找的凭证（Fernet 非确定性，密文不能当查询条件）
SECRET_HASH_COLUMNS = {
    "wecom_webhook": "wecom_webhook_hash",
    "bark_key": "bark_key_hash",
    "telegram_bot_token": "telegram_bot_token_hash",
}

# ---- 有序 schema 迁移 ----
# 新增表结构/索引/数据回填时，往 SCHEMA_MIGRATIONS 追加 (版本号, 名称, 语句或语句元组)。
# 版本号用日期风格（如 2026090601）且严格递增；runner 按 PRAGMA user_version 只执行一次，
# 每个迁移独立提交，失败回滚该迁移并中止启动。历史遗留的「缺列即补」幂等逻辑保留在
# _migrate() 中，两者并存：一次性/破坏性变更进这里，幂等兜底留在 _migrate()。
SCHEMA_MIGRATIONS: list[tuple[int, str, str | tuple[str, ...]]] = [
    (
        2026090601,
        "ima_document_index downloaded_at 索引",
        (
            "CREATE INDEX IF NOT EXISTS idx_ima_doc_downloaded "
            "ON ima_document_index(downloaded_at)"
        ),
    ),
]

_SLOW_QUERY_SECONDS = 0.2


def _log_slow_query(sql: str, started: float) -> None:
    """超过阈值的查询打 warning（只记 SQL 不记参数，参数可能含 Cookie/token）。"""
    elapsed = time.monotonic() - started
    if elapsed < _SLOW_QUERY_SECONDS:
        return
    trimmed = " ".join(str(sql).split())[:200]
    logging.getLogger(__name__).warning("slow query %.0fms: %s", elapsed * 1000, trimmed)


def _secret_hash(plain: str) -> str:
    """明文凭据的唯一性指纹（sha256）；空值返回空串不参与查找。"""
    plain = (plain or "").strip()
    return hashlib.sha256(plain.encode()).hexdigest() if plain else ""


def _bind_code_digest(code: str) -> str:
    """绑定码只存大写规范化后的哈希，库里不留明文。"""
    return _secret_hash((code or "").strip().upper())


def decrypt_stored_secret(value: str | None, credential_key: str) -> str:
    """解出 enc1: 前缀的列值；无前缀视为存量明文原样返回。

    解不开（密钥缺失/换环境）返回空串并只告警一次——表现为该渠道需要
    重新绑定，而不是把密文当配置发出去。
    """
    value = (value or "").strip()
    if not value.startswith(SECRET_PREFIX):
        return value
    from .feishu_personal import decrypt_secret

    try:
        return decrypt_secret(credential_key, value[len(SECRET_PREFIX):])
    except Exception:
        global _decrypt_warned
        if not _decrypt_warned:
            _decrypt_warned = True
            logging.getLogger(__name__).warning(
                "推送凭据解密失败（FEISHU_CREDENTIAL_KEY 未配置或已更换？），"
                "受影响渠道需重新绑定"
            )
        return ""


_decrypt_warned = False


def user_plain_secret(user: dict, field: str, db=None) -> str:
    """从 user 行取凭证明文；enc1: 前缀值借 db.credential_key 解密。"""
    value = (user.get(field) or "").strip()
    if value.startswith(SECRET_PREFIX):
        return decrypt_stored_secret(value, getattr(db, "credential_key", "") if db else "")
    return value


def _merge_sub_types(a: str, b: str) -> str:
    """两个订阅类型合并（并集语义）：post + reply = both。"""
    types = {a or "post", b or "post"}
    if "both" in types or {"post", "reply"} <= types:
        return "both"
    return next(iter(types))


def _to_int(value) -> int:
    """COUNT/SUM 等聚合结果转 int，None/非数字兜底为 0。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# 布尔字段的显式真值集合：字符串 "false"/"0"/"" 不应被 Python 的 truthy 误判为真
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _to_bool(value) -> int:
    """把任意输入归一化为 0/1：字符串按显式真值集合判断，其余按布尔语义。"""
    if isinstance(value, str):
        return 1 if value.strip().lower() in _TRUE_VALUES else 0
    return 1 if value else 0


def _user_has_channel_sql(alias: str = "") -> str:
    """用户已绑定任一推送渠道（含飞书个人机器人 active + chat_id）。"""
    p = f"{alias}." if alias else ""
    uid = f"{alias}.id" if alias else "id"
    return (
        f"({p}telegram_chat_id != '' OR {p}feishu_open_id != '' OR {p}feishu_chat_id != '' "
        f"OR {p}wecom_webhook != '' OR {p}bark_key != '' "
        f"OR EXISTS (SELECT 1 FROM feishu_personal_bots b "
        f"WHERE b.user_id = {uid} AND b.status = 'active' AND b.chat_id != '') "
        f"OR EXISTS (SELECT 1 FROM webpush_subscriptions w WHERE w.user_id = {uid}))"
    )


INACTIVE_AFTER_KEY = "inactive_after_days"
INACTIVE_PURGE_KEY = "inactive_purge_after_days"
INACTIVE_LAST_PURGE_KEY = "inactive_users_last_purge_at"
INACTIVE_CUSTOMIZED_KEY = "inactive_policy_customized"
INACTIVE_AFTER_DEFAULT = 90
INACTIVE_PURGE_DEFAULT = 30


def _parse_inactive_days(value, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < 0 or n > 3650:
        return default
    return n


def days_until_purge(created_at: str | None, n: int, m: int) -> int | None:
    """距离 created_at + N + M 的剩余整天数；非活跃且 M>0 时由调用方使用。"""
    if n <= 0 or m <= 0 or not created_at:
        return None
    created = datetime.strptime(
        str(created_at)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=UTC)
    sec = (created + timedelta(days=n + m) - datetime.now(UTC)).total_seconds()
    return max(0, int(sec // 86400))


def _normalize_post_images(rows: list[dict], db=None) -> list[dict]:
    """posts 行的 images 是 JSON 文本，API 场景统一解析为数组。"""
    from . import imgbed

    for row in rows:
        raw = row.get("images")
        if isinstance(raw, list):
            images = raw
        elif isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                images = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError):
                images = []
        else:
            images = []
        row["images"] = imgbed.rewrite_urls(db, images) if db is not None else images
    return rows


def _normalize_post_tags(rows: list[dict]) -> list[dict]:
    """posts 行的 tags 是 JSON 数组文本（LLM 打标结果），统一解析为列表。"""
    for row in rows:
        raw = row.get("tags")
        if isinstance(raw, list):
            continue
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                row["tags"] = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError):
                row["tags"] = []
        else:
            row["tags"] = []
    return rows


def _sanitize_post_detail(rows: list[dict]) -> list[dict]:
    """列表接口丢掉星球 detail.raw（完整 API 载荷），只留 files/comments。"""
    for row in rows:
        raw = row.get("detail")
        if not raw:
            continue
        try:
            detail = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and "raw" in detail:
            detail = {k: v for k, v in detail.items() if k != "raw"}
            row["detail"] = detail
        elif isinstance(detail, dict):
            row["detail"] = detail
    return rows


def _detail_json(detail) -> str:
    if not detail:
        return ""
    if isinstance(detail, dict) and "raw" in detail:
        detail = {k: v for k, v in detail.items() if k != "raw"}
    return json.dumps(detail, ensure_ascii=False)


# 贴文规则打标的默认词表（标签 + 关键词，管理员可在后台改，存 settings 表 tag_vocabulary）。
# 关键词做子串匹配：任一命中即给该标签；英文关键词打标时统一小写比较，故此处可混用大小写。
DEFAULT_TAG_RULES = [
    {"tag": "宏观", "keywords": ["央行", "降息", "加息", "GDP", "通胀", "CPI", "PPI", "利率", "美联储", "货币政策", "汇率", "国债", "衰退", "滞胀"]},
    {"tag": "大盘", "keywords": ["A股", "沪指", "上证", "深成指", "创业板指", "大盘", "指数", "两市", "涨停", "跌停", "成交量"]},
    {"tag": "板块", "keywords": ["板块", "概念股", "半导体", "新能源", "光伏", "锂电", "白酒", "券商", "地产", "汽车", "光模块", "军工"]},
    {"tag": "个股", "keywords": ["个股", "股价", "买入", "卖出", "目标价", "重仓", "持仓", "业绩", "市盈率", "PE"]},
    {"tag": "科技", "keywords": ["AI", "人工智能", "芯片", "大模型", "OpenAI", "英伟达", "NVIDIA", "算力", "机器人", "半导体设备", "GPU", "DeepSeek"]},
    {"tag": "政策", "keywords": ["政策", "监管", "证监会", "国务院", "发改委", "央行行长", "降准", "专项债", "限购", "补贴", "关税", "两会"]},
    {"tag": "财报", "keywords": ["财报", "季报", "年报", "营收", "净利润", "EPS", "毛利率", "分红", "回购", "指引"]},
    {"tag": "公告", "keywords": ["公告", "停牌", "复牌", "减持", "增持", "重组", "要约收购", "重大合同", "诉讼", "立案"]},
    {"tag": "资讯", "keywords": ["消息", "传闻", "报道", "据悉", "知情人士", "来源", "表示", "称", "透露"]},
    {"tag": "美股", "keywords": ["美股", "纳斯达克", "纳指", "标普", "道指", "标普500", "美债", "非农"]},
    {"tag": "港股", "keywords": ["港股", "恒指", "恒生", "南向", "北向资金", "港交所"]},
    {"tag": "黄金", "keywords": ["黄金", "金价", "伦敦金", "黄金ETF", "黄金期货"]},
    {"tag": "大宗", "keywords": ["原油", "布伦特", "铜价", "铁矿", "期货", "有色"]},
    {"tag": "医药", "keywords": ["医药", "创新药", "CXO", "集采", "医保", "生物医药"]},
    {"tag": "加密", "keywords": ["比特币", "以太坊", "BTC", "ETH", "加密货币", "稳定币"]},
    {"tag": "理念", "keywords": ["价值投资", "护城河", "能力圈", "安全边际", "巴菲特", "芒格", "长期持有"]},
    {"tag": "调仓", "keywords": ["调仓", "换仓", "组合调仓", "再平衡"]},
]
TAG_VOCABULARY_KEY = "tag_vocabulary"

# 常用股票名表：纯文字提及（无 $标记$）时按名称子串匹配打股票标签。
# 管理员可在后台增删；$股票名(代码)$ 标记会自动识别、无需在此登记。
DEFAULT_STOCK_NAMES = [
    "贵州茅台", "宁德时代", "比亚迪", "中芯国际", "英伟达", "台积电", "三星",
    "SK海力士", "长鑫", "中船特气", "神火", "云铝", "中际旭创", "药明康德",
    "恒瑞医药", "招商银行", "中国平安", "茅台", "腾讯", "阿里", "小米",
    "华为", "赛力斯", "理想", "蔚来", "小鹏", "隆基", "通威", "阳光电源",
    "京东方", "立讯精密", "海康威视", "紫光国微", "兆易创新", "寒武纪",
    "五粮液",
]
STOCK_NAMES_KEY = "stock_names"
# 管理员从常用股票名里删掉的名字；每日维护 / $标记$ 解析不再写回。
STOCK_NAMES_EXCLUDED_KEY = "stock_names_excluded"

# 常见黑话种子：启动时合并进别名表，不经过 LLM。正式名必须已在股票名表中。
DEFAULT_STOCK_ALIASES = [
    {"alias": "宁王", "stock": "宁德时代"},
    {"alias": "药茅", "stock": "恒瑞医药"},
]

# 黑话别名表：种子词表 + $戏称(代码)$ 解析（settings 键 stock_aliases）。
# [{"alias": "宁王", "stock": "宁德时代"}, ...]；打标时命中别名输出正式名。
STOCK_ALIASES_KEY = "stock_aliases"
# 最近一次标签维护结果（管理端展示用）
TAG_MAINTAIN_LAST_KEY = "tag_maintain_last"

IMA_DOCUMENT_INDEX_COLUMNS = (
    "group_id",
    "media_id",
    "day",
    "valid_day",
    "sort_date",
    "name",
    "group_name",
    "name_folded",
    "metadata_folded",
    "abstract",
    "abstract_folded",
    "abstract_zh",
    "abstract_src_hash",
    "cover_url",
    "tags_json",
    "size",
    "chars",
    "has_pdf",
    "has_txt",
    "pdf_path",
    "txt_path",
    "downloaded_at",
)

# 允许空索引以降级到 manifest/state；写入时只接受这些状态。
IMA_DOCUMENT_INDEX_STATUSES = {"ready", "rebuilding", "fallback", "failed"}

IMA_DOCUMENT_INDEX_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ima_document_index (
    group_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    day TEXT NOT NULL DEFAULT 'unknown',
    valid_day INTEGER NOT NULL DEFAULT 0,
    sort_date TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    name_folded TEXT NOT NULL DEFAULT '',
    metadata_folded TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    abstract_folded TEXT NOT NULL DEFAULT '',
    abstract_zh TEXT NOT NULL DEFAULT '',
    abstract_src_hash TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    size INTEGER NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
    has_pdf INTEGER NOT NULL DEFAULT 0,
    has_txt INTEGER NOT NULL DEFAULT 0,
    pdf_path TEXT NOT NULL DEFAULT '',
    txt_path TEXT NOT NULL DEFAULT '',
    downloaded_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, media_id)
);
"""
IMA_DOCUMENT_TAGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ima_document_tags (
    group_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (group_id, media_id, tag)
);
"""
REPORT_EXTRACTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS report_extractions (
    group_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    txt_hash TEXT NOT NULL DEFAULT '',
    report_kind TEXT NOT NULL DEFAULT '',
    rating TEXT NOT NULL DEFAULT '',
    target_price TEXT NOT NULL DEFAULT '',
    thesis TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    extracted_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, media_id)
);
"""
REPORT_EXTRACTION_TICKERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS report_extraction_tickers (
    group_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    stance TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, media_id, code)
);
"""
IMA_TICKER_DIGEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ima_ticker_digests (
    kind TEXT NOT NULL DEFAULT 'ticker',
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    source_count INTEGER NOT NULL DEFAULT 0,
    digest TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, code)
);
"""
IMA_DOCUMENT_INDEX_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ima_document_index_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'fallback',
    fingerprint TEXT NOT NULL DEFAULT '',
    rebuilt_at TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    document_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
"""

# PRAGMA table_info: name, declared type, notnull, dflt_value, pk ordinal.
_IMA_DOC_COLUMN_SPEC = (
    ("group_id", "TEXT", 1, None, 1),
    ("media_id", "TEXT", 1, None, 2),
    ("day", "TEXT", 1, "'unknown'", 0),
    ("valid_day", "INTEGER", 1, "0", 0),
    ("sort_date", "TEXT", 1, "''", 0),
    ("name", "TEXT", 1, "''", 0),
    ("group_name", "TEXT", 1, "''", 0),
    ("name_folded", "TEXT", 1, "''", 0),
    ("metadata_folded", "TEXT", 1, "''", 0),
    ("abstract", "TEXT", 1, "''", 0),
    ("abstract_folded", "TEXT", 1, "''", 0),
    ("abstract_zh", "TEXT", 1, "''", 0),
    ("abstract_src_hash", "TEXT", 1, "''", 0),
    ("cover_url", "TEXT", 1, "''", 0),
    ("tags_json", "TEXT", 1, "'[]'", 0),
    ("size", "INTEGER", 1, "0", 0),
    ("chars", "INTEGER", 1, "0", 0),
    ("has_pdf", "INTEGER", 1, "0", 0),
    ("has_txt", "INTEGER", 1, "0", 0),
    ("pdf_path", "TEXT", 1, "''", 0),
    ("txt_path", "TEXT", 1, "''", 0),
    ("downloaded_at", "TEXT", 1, "''", 0),
)
_IMA_TAG_COLUMN_SPEC = (
    ("group_id", "TEXT", 1, None, 1),
    ("media_id", "TEXT", 1, None, 2),
    ("tag", "TEXT", 1, None, 3),
)
_IMA_META_COLUMN_SPEC = (
    ("id", "INTEGER", 0, None, 1),
    ("version", "INTEGER", 1, "1", 0),
    ("status", "TEXT", 1, "'fallback'", 0),
    ("fingerprint", "TEXT", 1, "''", 0),
    ("rebuilt_at", "TEXT", 1, "''", 0),
    ("duration_ms", "INTEGER", 1, "0", 0),
    ("document_count", "INTEGER", 1, "0", 0),
    ("error", "TEXT", 1, "''", 0),
)
_IMA_DOC_INT_COLUMNS = frozenset({"size", "chars", "has_pdf", "has_txt"})
_IMA_INDEX_SPECS = (
    (
        "idx_ima_doc_latest",
        "ima_document_index(sort_date DESC, name DESC, group_id ASC, media_id ASC)",
        (("sort_date", 1), ("name", 1), ("group_id", 0), ("media_id", 0)),
    ),
    (
        "idx_ima_doc_group_latest",
        "ima_document_index(group_id, sort_date DESC, name DESC, media_id ASC)",
        (("group_id", 0), ("sort_date", 1), ("name", 1), ("media_id", 0)),
    ),
    (
        "idx_ima_doc_tag_group",
        "ima_document_tags(tag, group_id)",
        (("tag", 0), ("group_id", 0)),
    ),
    (
        "idx_ima_doc_group_tag",
        "ima_document_tags(group_id, tag)",
        (("group_id", 0), ("tag", 0)),
    ),
)


def _ima_pragma_matches(rows, expected) -> bool:
    if len(rows) != len(expected):
        return False
    for row, (name, column_type, notnull, default, pk) in zip(rows, expected):
        if (
            row["name"] != name
            or str(row["type"] or "").upper() != column_type
            or int(row["notnull"]) != notnull
            or row["dflt_value"] != default
            or int(row["pk"]) != pk
        ):
            return False
    return True


def _ima_meta_has_id_check(sql: str) -> bool:
    compact = "".join((sql or "").upper().split())
    token = "CHECK(ID="
    start = 0
    while True:
        pos = compact.find(token, start)
        if pos < 0:
            return False
        rest = compact[pos + len(token):]
        if rest.startswith("1)"):
            return True
        start = pos + 1


def _ima_create_table_sql(if_not_exists_sql: str) -> str:
    return if_not_exists_sql.replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ", 1)


def _ima_doc_select_expr(column: str, old_columns: set[str]) -> str:
    if column == "group_id":
        return (
            "COALESCE(NULLIF(CAST(group_id AS TEXT), ''), 'legacy')"
            if "group_id" in old_columns
            else "'legacy'"
        )
    if column == "media_id":
        return (
            "COALESCE(NULLIF(CAST(media_id AS TEXT), ''), "
            "'legacy:' || CAST(rowid AS TEXT))"
            if "media_id" in old_columns
            else "'legacy:' || CAST(rowid AS TEXT)"
        )
    if column == "day":
        return (
            "COALESCE(NULLIF(TRIM(day), ''), 'unknown')"
            if "day" in old_columns
            else "'unknown'"
        )
    if column == "valid_day":
        if "valid_day" in old_columns:
            return (
                "CASE WHEN CAST(valid_day AS INTEGER) != 0 THEN 1 ELSE 0 END"
            )
        if "day" in old_columns:
            return (
                "CASE WHEN TRIM(day) GLOB '[0-9][0-9][0-9][0-9]' "
                "THEN 1 ELSE 0 END"
            )
        return "0"
    if column == "tags_json":
        return (
            "COALESCE(CAST(tags_json AS TEXT), '[]')"
            if "tags_json" in old_columns
            else "'[]'"
        )
    if column in old_columns:
        if column in _IMA_DOC_INT_COLUMNS:
            return f"COALESCE(CAST({column} AS INTEGER), 0)"
        return f"COALESCE(CAST({column} AS TEXT), '')"
    return "0" if column in _IMA_DOC_INT_COLUMNS else "''"


def _ima_tag_select_expr(column: str, old_columns: set[str]) -> str:
    if column == "group_id":
        return (
            "COALESCE(NULLIF(CAST(group_id AS TEXT), ''), 'legacy')"
            if "group_id" in old_columns
            else "'legacy'"
        )
    if column == "media_id":
        return (
            "COALESCE(NULLIF(CAST(media_id AS TEXT), ''), "
            "'legacy:' || CAST(rowid AS TEXT))"
            if "media_id" in old_columns
            else "'legacy:' || CAST(rowid AS TEXT)"
        )
    return "COALESCE(CAST(tag AS TEXT), '')" if "tag" in old_columns else "''"


def _ima_meta_select_expr(column: str, old_columns: set[str]) -> str:
    if column == "id":
        return "1"
    if column == "version":
        return (
            "CASE WHEN COALESCE(CAST(version AS INTEGER), 0) = 0 "
            "THEN 1 ELSE CAST(version AS INTEGER) END"
            if "version" in old_columns
            else "1"
        )
    if column in {"duration_ms", "document_count"}:
        return (
            f"COALESCE(CAST({column} AS INTEGER), 0)"
            if column in old_columns
            else "0"
        )
    if column == "status":
        return (
            "COALESCE(NULLIF(CAST(status AS TEXT), ''), 'fallback')"
            if "status" in old_columns
            else "'fallback'"
        )
    if column in old_columns:
        return f"COALESCE(CAST({column} AS TEXT), '')"
    return "''"


def _like_pattern(value: str) -> str:
    folded = str(value or "").strip().casefold()
    escaped = folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _ima_query_usable(value: str) -> bool:
    """单字母 ASCII 几乎匹配全库，LIKE 摘要会把整站锁死；中文单字仍可搜。"""
    text = str(value or "").strip()
    if not text:
        return False
    if any(ord(char) > 127 for char in text):
        return True
    return len(text) >= 2


def _ima_authorized_groups(readable_group_ids, group: str = "") -> list[str]:
    allowed = [str(item) for item in readable_group_ids if str(item)]
    requested = str(group or "").strip()
    if requested:
        return [item for item in allowed if item == requested]
    return allowed


def _ima_public_document(row: dict) -> dict:
    try:
        tags = json.loads(str(row.get("tags_json") or "[]"))
    except (TypeError, ValueError):
        tags = []
    if not isinstance(tags, list):
        tags = []
    return {
        "group_id": row["group_id"],
        "media_id": row["media_id"],
        "day": row["day"],
        "sort_date": str(row.get("sort_date") or ""),
        "name": row["name"],
        "group_name": row.get("group_name") or "",
        "abstract": row.get("abstract") or "",
        "abstract_zh": row.get("abstract_zh") or "",
        "abstract_src_hash": row.get("abstract_src_hash") or "",
        "cover_url": row.get("cover_url") or "",
        "tags": [str(tag) for tag in tags if str(tag)],
        "size": _to_int(row.get("size")),
        "chars": _to_int(row.get("chars")),
        "has_pdf": bool(row.get("has_pdf")),
        "has_txt": bool(row.get("has_txt")),
        "pdf_path": str(row.get("pdf_path") or ""),
        "txt_path": str(row.get("txt_path") or ""),
        "downloaded_at": str(row.get("downloaded_at") or ""),
    }


def _ima_empty_page(day: str, offset: int) -> dict:
    return {
        "items": [],
        "days": [],
        "tags": [],
        "tag_counts": {},
        "document_count": 0,
        "day": day,
        "has_more": False,
        "offset": offset,
        "group_counts": {},
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS kols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT '',
    avatar_source TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_private INTEGER NOT NULL DEFAULT 0,
    original_only INTEGER NOT NULL DEFAULT 0,
    category_id INTEGER,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS kol_acl (
    kol_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (kol_id, user_id)
);
CREATE TABLE IF NOT EXISTS kol_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    category_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    handled_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    kol_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    title_src TEXT NOT NULL DEFAULT '',
    content_src TEXT NOT NULL DEFAULT '',
    post_type TEXT NOT NULL DEFAULT '',
    images TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, external_id)
);
CREATE TABLE IF NOT EXISTS push_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    user_id INTEGER
);
CREATE TABLE IF NOT EXISTS error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    logger TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS daily_report_deliveries (
    user_id INTEGER NOT NULL,
    report_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, report_date, channel)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    wechat_openid TEXT NOT NULL DEFAULT '',
    telegram_chat_id TEXT NOT NULL DEFAULT '',
    telegram_bot_token TEXT NOT NULL DEFAULT '',
    telegram_bot_token_hash TEXT NOT NULL DEFAULT '',
    feishu_open_id TEXT NOT NULL DEFAULT '',
    feishu_chat_id TEXT NOT NULL DEFAULT '',
    wecom_webhook TEXT NOT NULL DEFAULT '',
    wecom_webhook_hash TEXT NOT NULL DEFAULT '',
    bark_key TEXT NOT NULL DEFAULT '',
    bark_key_hash TEXT NOT NULL DEFAULT '',
    notify_enabled INTEGER NOT NULL DEFAULT 1,
    daily_report INTEGER NOT NULL DEFAULT 0,
    translate_twitter INTEGER NOT NULL DEFAULT 1,
    push_channels TEXT NOT NULL DEFAULT '',
    dnd_start TEXT NOT NULL DEFAULT '',
    dnd_end TEXT NOT NULL DEFAULT '',
    dnd_allow_favorite INTEGER NOT NULL DEFAULT 0,
    token_version INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    news_last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kol_id INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT 'post',
    favorite INTEGER NOT NULL DEFAULT 0,
    hide_images INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, kol_id)
);
CREATE TABLE IF NOT EXISTS news_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    built_in INTEGER NOT NULL DEFAULT 0,
    default_selected INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS news_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES news_sources(id),
    name TEXT NOT NULL COLLATE NOCASE,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error_code TEXT NOT NULL DEFAULT '',
    last_error_detail TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_id, name)
);
CREATE TABLE IF NOT EXISTS user_news_sources (
    user_id INTEGER NOT NULL REFERENCES users(id),
    source_id INTEGER NOT NULL REFERENCES news_sources(id),
    selected_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, source_id)
);
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES news_sources(id),
    feed_id INTEGER NOT NULL REFERENCES news_feeds(id),
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    content_html TEXT NOT NULL DEFAULT '',
    images TEXT NOT NULL DEFAULT '[]',
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_news_articles_time ON news_articles(published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_source_time ON news_articles(source_id, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_news_feeds_due ON news_feeds(enabled, archived_at, last_attempt_at);

CREATE TABLE IF NOT EXISTS bind_codes (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS register_codes (
    code TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    used_by INTEGER,
    used_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    batch_id TEXT NOT NULL DEFAULT '',
    expires_at TEXT,
    revoked_at TEXT,
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS source_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0
);

-- 雪球组合快照：quote（实时净值/涨跌）、holdings（当前持仓）、nav（净值序列）
-- 抓取端定时写入（TTL 内不重复请求），API 端只读，页面展示不依赖雪球在线
CREATE TABLE IF NOT EXISTS cube_snapshots (
    kol_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kol_id, kind)
);

-- 飞书个人机器人（扫码自动创建的应用，纯推送、共享回退）
CREATE TABLE IF NOT EXISTS feishu_personal_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    app_id TEXT UNIQUE NOT NULL,
    app_secret_ciphertext TEXT NOT NULL,
    open_id TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    tenant_brand TEXT NOT NULL DEFAULT 'feishu',
    status TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fpb_status ON feishu_personal_bots(status);
-- 飞书个人机器人扫码注册会话（同一用户同时只有一个未结束会话）
CREATE TABLE IF NOT EXISTS feishu_registration_sessions (
    session_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    device_code_ciphertext TEXT NOT NULL,
    registration_base_url TEXT NOT NULL,
    verification_uri TEXT NOT NULL,
    candidate_app_id TEXT NOT NULL DEFAULT '',
    candidate_app_secret_ciphertext TEXT NOT NULL DEFAULT '',
    candidate_tenant_brand TEXT NOT NULL DEFAULT 'feishu',
    expected_open_id TEXT NOT NULL DEFAULT '',
    bind_code_hash TEXT NOT NULL DEFAULT '',
    bind_code_expires_at INTEGER,
    session_expires_at INTEGER NOT NULL,
    poll_interval INTEGER NOT NULL,
    status TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_frs_user ON feishu_registration_sessions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_frs_status ON feishu_registration_sessions(status);

-- 飞书云文档：共享 OAuth 凭据、一次性授权会话和可软删除的文档来源。
CREATE TABLE IF NOT EXISTS feishu_document_oauth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token_ciphertext TEXT NOT NULL,
    refresh_token_ciphertext TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    refresh_expires_at INTEGER NOT NULL DEFAULT 0,
    scopes TEXT NOT NULL DEFAULT '',
    open_id TEXT NOT NULL DEFAULT '',
    tenant_key TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS feishu_document_oauth_sessions (
    state_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    code_verifier_ciphertext TEXT NOT NULL DEFAULT '',
    consumed_at INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fdos_expires ON feishu_document_oauth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS feishu_document_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key_hash TEXT UNIQUE NOT NULL,
    host TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_token TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    group_id TEXT UNIQUE NOT NULL,
    media_id TEXT UNIQUE NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    revision_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    timeline_path TEXT NOT NULL DEFAULT '',
    txt_path TEXT NOT NULL DEFAULT '',
    asset_root TEXT NOT NULL DEFAULT '',
    entry_count INTEGER NOT NULL DEFAULT 0,
    published_record_json TEXT NOT NULL DEFAULT '',
    published_state_json TEXT NOT NULL DEFAULT '',
    display_mode TEXT NOT NULL DEFAULT 'timeline',
    enabled INTEGER NOT NULL DEFAULT 1,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    last_checked_at TEXT NOT NULL DEFAULT '',
    last_success_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fds_active ON feishu_document_sources(enabled, deleted_at);

CREATE TABLE IF NOT EXISTS webpush_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_webpush_user ON webpush_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS android_devices (
    installation_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    provider TEXT NOT NULL,
    device_model TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_android_devices_user ON android_devices(user_id);

CREATE TABLE IF NOT EXISTS proxy_pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'static',
    extract_url TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT 'http',
    expire_seconds INTEGER NOT NULL DEFAULT 0,
    refresh_interval_seconds INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_extract_at INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'unknown',
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_ok_at INTEGER,
    last_fail_at INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    expires_at INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (pool_id, protocol, host, port, username)
);
CREATE INDEX IF NOT EXISTS idx_proxies_pool ON proxies(pool_id);
CREATE INDEX IF NOT EXISTS idx_proxies_expires ON proxies(expires_at);

-- 性能索引：帖子/日志/订阅按数据量增长后的高频查询
CREATE INDEX IF NOT EXISTS idx_posts_kol_id ON posts(kol_id);
CREATE INDEX IF NOT EXISTS idx_posts_fetched_at ON posts(fetched_at);
CREATE INDEX IF NOT EXISTS idx_posts_kol_id_id ON posts(kol_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_posts_platform_id ON posts(platform, id DESC);
CREATE INDEX IF NOT EXISTS idx_posts_kol_published ON posts(kol_id, published_at);
CREATE INDEX IF NOT EXISTS idx_push_logs_created_at ON push_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_push_logs_post_id ON push_logs(post_id);
CREATE INDEX IF NOT EXISTS idx_kol_acl_user ON kol_acl(user_id);
CREATE TABLE IF NOT EXISTS ima_kb_acl (
    group_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS ima_kb_subscriptions (
    user_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_ima_kb_acl_user ON ima_kb_acl(user_id);
CREATE INDEX IF NOT EXISTS idx_ima_kb_sub_group ON ima_kb_subscriptions(group_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_kol_id ON subscriptions(kol_id);
CREATE INDEX IF NOT EXISTS idx_source_events_platform ON source_events(platform, created_at);
"""

ALLOWED_PLATFORMS = {"xueqiu", "combination", "weibo", "twitter", "ima", "zsxq", "truth"}

_BUILTIN_NEWS = (
    ("bloomberg", "Bloomberg", (("最新财经", "https://quanwenrss.com/bloomberg"),)),
    ("caixin", "财新", (("最新文章", "https://quanwenrss.com/caixin"),)),
    ("ft", "FT 中文网", (("综合新闻", "https://quanwenrss.com/ft"),)),
    ("morganstanley", "摩根士丹利", (
        ("中国", "https://quanwenrss.com/morganstanley/china"),
        ("全球", "https://quanwenrss.com/morganstanley/global"),
    )),
)


class DB:
    def __init__(self, path: str | Path, credential_key: str = ""):
        self.path = str(path)
        # 凭据加密密钥（Fernet 兼容 Base64）：来自 FEISHU_CREDENTIAL_KEY /
        # config.notifiers.feishu.credential_key，与飞书个人机器人共用一把
        self.credential_key = credential_key or ""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # RLock：_migrate 等内部路径会在持锁状态下调用 _rows/_execute，
        # 非重入锁会自死锁
        self._lock = threading.RLock()
        self._reader_condition = threading.Condition()
        self._active_readers = 0
        self._replace_pending = False
        self._open_unlocked()
        self._migrate()
        self._conn.commit()

    def _open_unlocked(self) -> None:
        """建立连接。调用方须已持有 _lock，或处于 __init__ 的单线程窗口。"""
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=0)
        if self.path != ":memory:":
            Path(self.path).chmod(0o600)
        self._conn.row_factory = sqlite3.Row
        # 并发写（多 worker/健康检查脚本）时等待而非直接报错
        self._conn.execute("PRAGMA busy_timeout = 5000")
        # 默认回滚日志：Docker 里 /data 可能是 virtiofs 挂载，WAL 的共享内存映射
        # 不可靠（wal/shm 被删除后写入丢失）。库在本地盘时（如生产 VPS）可用
        # DB_JOURNAL_MODE=wal 切换，读不再被写阻塞——多用户下知识库列表
        # 「读等写锁 400-800ms」的问题即源于此。
        mode = os.environ.get("DB_JOURNAL_MODE", "delete").strip().lower() or "delete"
        if mode not in ("delete", "wal"):
            mode = "delete"
        self._conn.execute(f"PRAGMA journal_mode={mode.upper()}")
        self._conn.executescript(SCHEMA)

    def online_backup(self, target: str | Path) -> None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dst = sqlite3.connect(str(target))
            try:
                with dst:
                    self._conn.backup(dst)
            finally:
                dst.close()

    def reopen(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._open_unlocked()
            # migrate 直接写连接，必须与在线线程共用一把锁：
            # 否则并行写入会把迁移的隐式事务提前 commit 成半迁移
            self._migrate()
            self._conn.commit()

    def replace_database(self, candidate: str | Path) -> None:
        """等待只读连接退出，原子替换库文件后重新打开。调用方负责失败回滚。"""
        path = Path(self.path)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with self._lock:
            with self._reader_condition:
                self._replace_pending = True
                while self._active_readers:
                    self._reader_condition.wait()
            try:
                shutil.copy2(candidate, temp)
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                temp.replace(path)
                Path(str(path) + "-wal").unlink(missing_ok=True)
                Path(str(path) + "-shm").unlink(missing_ok=True)
                self._open_unlocked()
                self._migrate()
                self._conn.commit()
            finally:
                try:
                    temp.unlink(missing_ok=True)
                finally:
                    with self._reader_condition:
                        self._replace_pending = False
                        self._reader_condition.notify_all()

    def _migrate(self):
        post_cols = {row["name"] for row in self._rows("PRAGMA table_info(posts)")}
        if "post_type" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN post_type TEXT NOT NULL DEFAULT ''"
            )
        if "detail" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN detail TEXT NOT NULL DEFAULT ''"
            )
        if "images" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN images TEXT NOT NULL DEFAULT ''"
            )
        if "tags" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN tags TEXT NOT NULL DEFAULT ''"
            )
        if "title_src" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN title_src TEXT NOT NULL DEFAULT ''"
            )
        if "content_src" not in post_cols:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN content_src TEXT NOT NULL DEFAULT ''"
            )
        sub_cols = {row["name"] for row in self._rows("PRAGMA table_info(subscriptions)")}
        if "type" not in sub_cols:
            self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN type TEXT NOT NULL DEFAULT 'post'"
            )
        if "favorite" not in sub_cols:
            self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
            )
        if "secondary" not in sub_cols:
            self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN secondary INTEGER NOT NULL DEFAULT 0"
            )
        if "hide_images" not in sub_cols:
            self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN hide_images INTEGER NOT NULL DEFAULT 0"
            )
        cols = {row["name"] for row in self._rows("PRAGMA table_info(kols)")}
        if "category_id" not in cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN category_id INTEGER")
        if "secondary" not in cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN secondary INTEGER NOT NULL DEFAULT 0")
        if "silent" not in cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN silent INTEGER NOT NULL DEFAULT 0")
        if "priority" not in cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        # 知识星球默认次要：只跑一次，避免覆盖管理员后来改回的「普通」
        if (self.get_setting("zsxq_default_secondary_v1") or "") != "1":
            self._conn.execute(
                "UPDATE kols SET secondary = 1 WHERE platform = 'zsxq' AND COALESCE(priority, 0) = 0"
            )
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES ('zsxq_default_secondary_v1', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        push_cols = {row["name"] for row in self._rows("PRAGMA table_info(push_logs)")}
        if "user_id" not in push_cols:
            self._conn.execute("ALTER TABLE push_logs ADD COLUMN user_id INTEGER")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_push_logs_user ON push_logs(user_id)")
        user_cols = {row["name"] for row in self._rows("PRAGMA table_info(users)")}
        if "wechat_openid" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN wechat_openid TEXT NOT NULL DEFAULT ''")
        if "feishu_chat_id" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN feishu_chat_id TEXT NOT NULL DEFAULT ''")
        if "daily_report" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN daily_report INTEGER NOT NULL DEFAULT 0")
        if "translate_twitter" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN translate_twitter INTEGER NOT NULL DEFAULT 1"
            )
        if "push_channels" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN push_channels TEXT NOT NULL DEFAULT ''")
        if "dnd_start" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN dnd_start TEXT NOT NULL DEFAULT ''")
        if "dnd_end" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN dnd_end TEXT NOT NULL DEFAULT ''")
        if "dnd_allow_favorite" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN dnd_allow_favorite INTEGER NOT NULL DEFAULT 0"
            )
        if "wecom_webhook" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN wecom_webhook TEXT NOT NULL DEFAULT ''")
        if "telegram_bot_token" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN telegram_bot_token TEXT NOT NULL DEFAULT ''")
        if "feed_token" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN feed_token TEXT NOT NULL DEFAULT ''")
        if "bark_key" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN bark_key TEXT NOT NULL DEFAULT ''")
        if "llm_api_base" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN llm_api_base TEXT NOT NULL DEFAULT ''")
        if "llm_api_key" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN llm_api_key TEXT NOT NULL DEFAULT ''")
        if "llm_model" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN llm_model TEXT NOT NULL DEFAULT ''")
        if "llm_api_format" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN llm_api_format TEXT NOT NULL DEFAULT 'chat'"
            )
        if "llm_last_status" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN llm_last_status TEXT NOT NULL DEFAULT ''"
            )
        if "token_version" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
        if "last_login_at" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        if "news_last_seen_at" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN news_last_seen_at TEXT")
        self._migrate_news()
        if "wecom_webhook_hash" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN wecom_webhook_hash TEXT NOT NULL DEFAULT ''")
        if "bark_key_hash" not in user_cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN bark_key_hash TEXT NOT NULL DEFAULT ''")
        if "telegram_bot_token_hash" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN telegram_bot_token_hash TEXT NOT NULL DEFAULT ''"
            )
        if "keywords_match_reports" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN keywords_match_reports INTEGER NOT NULL DEFAULT 0"
            )
        if "keywords_match_reports_since" not in user_cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN keywords_match_reports_since TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_feed_token "
            "ON users(feed_token) WHERE feed_token != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_bark_key "
            "ON users(bark_key) WHERE bark_key != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_telegram_bot_token_hash "
            "ON users(telegram_bot_token_hash) WHERE telegram_bot_token_hash != ''"
        )
        # 凭据加密迁移：配了凭据密钥才把明文收编成 enc1: 密文，并补齐
        # 唯一性哈希列；全部幂等（已是密文且哈希正确的行直接跳过）
        if self.credential_key:
            from .feishu_personal import decrypt_secret, encrypt_secret

            for col in SECRET_COLUMNS:
                hash_col = SECRET_HASH_COLUMNS.get(col)
                rows = self._rows(f"SELECT id, {col} AS v FROM users WHERE {col} != ''")
                for row in rows:
                    stored = row["v"]
                    if stored.startswith(SECRET_PREFIX):
                        try:
                            plain = decrypt_secret(self.credential_key, stored[len(SECRET_PREFIX):])
                        except Exception:  # noqa: S112
                            continue  # 密钥对不上：保持现状，配置正确后下轮再收编
                        target_cipher = stored  # 已是密文，无需重写
                    else:
                        plain = stored
                        target_cipher = SECRET_PREFIX + encrypt_secret(self.credential_key, plain)
                    digest = _secret_hash(plain)
                    need_write = target_cipher != stored
                    if hash_col:
                        current_hash = self._rows(
                            f"SELECT {hash_col} AS h FROM users WHERE id = ?", (row["id"],)
                        )[0]["h"]
                        need_write = need_write or current_hash != digest
                    if not need_write:
                        continue
                    if hash_col:
                        self._conn.execute(
                            f"UPDATE users SET {col} = ?, {hash_col} = ? WHERE id = ?",
                            (target_cipher, digest, row["id"]),
                        )
                    else:
                        self._conn.execute(
                            f"UPDATE users SET {col} = ? WHERE id = ?",
                            (target_cipher, row["id"]),
                        )
        # 无密钥时也能给明文凭据补哈希，保证查重列可用
        for col, hash_col in SECRET_HASH_COLUMNS.items():
            rows = self._rows(
                f"SELECT id, {col} AS v, {hash_col} AS h FROM users WHERE {col} != ''"
            )
            for row in rows:
                stored = row["v"]
                if stored.startswith(SECRET_PREFIX):
                    if not self.credential_key:
                        continue
                    from .feishu_personal import decrypt_secret

                    try:
                        plain = decrypt_secret(
                            self.credential_key, stored[len(SECRET_PREFIX):]
                        )
                    except Exception:  # noqa: S112
                        continue
                else:
                    plain = stored
                digest = _secret_hash(plain)
                if row["h"] != digest:
                    self._conn.execute(
                        f"UPDATE users SET {hash_col} = ? WHERE id = ?",
                        (digest, row["id"]),
                    )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS webpush_subscriptions ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  user_id INTEGER NOT NULL,"
            "  endpoint TEXT NOT NULL UNIQUE,"
            "  p256dh TEXT NOT NULL,"
            "  auth TEXT NOT NULL,"
            "  user_agent TEXT NOT NULL DEFAULT '',"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webpush_user ON webpush_subscriptions(user_id)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS android_devices ("
            "  installation_id TEXT PRIMARY KEY,"
            "  user_id INTEGER NOT NULL,"
            "  token TEXT NOT NULL,"
            "  provider TEXT NOT NULL,"
            "  device_model TEXT NOT NULL DEFAULT '',"
            "  app_version TEXT NOT NULL DEFAULT '',"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_android_devices_user ON android_devices(user_id)"
        )
        feishu_oauth_session_cols = {
            row["name"] for row in self._rows("PRAGMA table_info(feishu_document_oauth_sessions)")
        }
        if "code_verifier_ciphertext" not in feishu_oauth_session_cols:
            self._conn.execute(
                "ALTER TABLE feishu_document_oauth_sessions ADD COLUMN "
                "code_verifier_ciphertext TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS ima_kb_acl ("
            "  group_id TEXT NOT NULL,"
            "  user_id INTEGER NOT NULL,"
            "  PRIMARY KEY (group_id, user_id)"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS ima_kb_subscriptions ("
            "  user_id INTEGER NOT NULL,"
            "  group_id TEXT NOT NULL,"
            "  created_at INTEGER NOT NULL,"
            "  PRIMARY KEY (user_id, group_id)"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ima_kb_acl_user ON ima_kb_acl(user_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ima_kb_sub_group ON ima_kb_subscriptions(group_id)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS user_quota ("
            "  user_id INTEGER NOT NULL,"
            "  bucket TEXT NOT NULL,"
            "  period_start INTEGER NOT NULL,"
            "  count INTEGER NOT NULL,"
            "  PRIMARY KEY (user_id, bucket)"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bind_quota ("
            "  key TEXT PRIMARY KEY,"
            "  period_start INTEGER NOT NULL,"
            "  count INTEGER NOT NULL"
            ")"
        )
        self._ensure_ima_document_tables()
        self._migrate_ima_document_index()
        feishu_source_cols = {
            row["name"] for row in self._rows("PRAGMA table_info(feishu_document_sources)")
        }
        if "published_record_json" not in feishu_source_cols:
            self._conn.execute(
                "ALTER TABLE feishu_document_sources ADD COLUMN "
                "published_record_json TEXT NOT NULL DEFAULT ''"
            )
        if "published_state_json" not in feishu_source_cols:
            self._conn.execute(
                "ALTER TABLE feishu_document_sources ADD COLUMN "
                "published_state_json TEXT NOT NULL DEFAULT ''"
            )
        if "display_mode" not in feishu_source_cols:
            self._conn.execute(
                "ALTER TABLE feishu_document_sources ADD COLUMN "
                "display_mode TEXT NOT NULL DEFAULT 'timeline'"
            )
        if "display_name" not in feishu_source_cols:
            self._conn.execute(
                "ALTER TABLE feishu_document_sources ADD COLUMN "
                "display_name TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS user_keywords ("
            "  user_id INTEGER NOT NULL,"
            "  keyword TEXT NOT NULL,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  UNIQUE (user_id, keyword)"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_keyword_notified ("
            "  user_id INTEGER NOT NULL,"
            "  group_id TEXT NOT NULL,"
            "  media_id TEXT NOT NULL,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  PRIMARY KEY (user_id, group_id, media_id)"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_kw_notified_user "
            "ON knowledge_keyword_notified(user_id)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS hosted_images ("
            "  source_url TEXT PRIMARY KEY,"
            "  hosted_url TEXT NOT NULL DEFAULT '',"
            "  content_hash TEXT NOT NULL DEFAULT '',"
            "  status TEXT NOT NULL DEFAULT 'pending',"
            "  attempts INTEGER NOT NULL DEFAULT 0,"
            "  last_error TEXT NOT NULL DEFAULT '',"
            "  last_attempt_at TEXT NOT NULL DEFAULT '',"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hosted_images_status "
            "ON hosted_images(status, last_attempt_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hosted_images_hash "
            "ON hosted_images(content_hash) WHERE content_hash != ''"
        )
        kol_cols = {row["name"] for row in self._rows("PRAGMA table_info(kols)")}
        if "is_private" not in kol_cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0")
        if "recommend_weight" not in kol_cols:
            # 订阅广场推荐权重：越大越靠前，优先于订阅人数排序（0 = 默认）
            self._conn.execute(
                "ALTER TABLE kols ADD COLUMN recommend_weight INTEGER NOT NULL DEFAULT 0"
            )
        if "avatar_url" not in kol_cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''")
        if "avatar_source" not in kol_cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN avatar_source TEXT NOT NULL DEFAULT ''")
        if "original_only" not in kol_cols:
            self._conn.execute("ALTER TABLE kols ADD COLUMN original_only INTEGER NOT NULL DEFAULT 0")
        if "baseline_ready" not in kol_cols:
            # 1=已建首次抓取基线（存量默认 1，升级后新帖照常推送）；0=新大V待首轮建基线
            self._conn.execute(
                "ALTER TABLE kols ADD COLUMN baseline_ready INTEGER NOT NULL DEFAULT 1"
            )
        if "last_post_at" not in kol_cols:
            self._conn.execute(
                "ALTER TABLE kols ADD COLUMN last_post_at TEXT NOT NULL DEFAULT ''"
            )
            self._conn.execute(
                "UPDATE kols SET last_post_at = COALESCE(("
                "SELECT MAX(fetched_at) FROM posts WHERE posts.kol_id = kols.id), '')"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_kol_id_id ON posts(kol_id, id DESC)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_push_logs_user ON push_logs(user_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_kol_acl_user ON kol_acl(user_id)")
        req_cols = {row["name"] for row in self._rows("PRAGMA table_info(kol_requests)")}
        if "category_id" not in req_cols:
            self._conn.execute("ALTER TABLE kol_requests ADD COLUMN category_id INTEGER")
        ev_cols = {row["name"] for row in self._rows("PRAGMA table_info(source_events)")}
        if "ok_count" not in ev_cols:
            self._conn.execute(
                "ALTER TABLE source_events ADD COLUMN ok_count INTEGER NOT NULL DEFAULT 0"
            )
        if "fail_count" not in ev_cols:
            self._conn.execute(
                "ALTER TABLE source_events ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0"
            )
        rc_cols = {row["name"] for row in self._rows("PRAGMA table_info(register_codes)")}
        if "batch_id" not in rc_cols:
            self._conn.execute(
                "ALTER TABLE register_codes ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''"
            )
        if "expires_at" not in rc_cols:
            self._conn.execute("ALTER TABLE register_codes ADD COLUMN expires_at TEXT")
        if "revoked_at" not in rc_cols:
            self._conn.execute("ALTER TABLE register_codes ADD COLUMN revoked_at TEXT")
        if "created_by" not in rc_cols:
            self._conn.execute("ALTER TABLE register_codes ADD COLUMN created_by INTEGER")
        for row in self._rows("SELECT code FROM register_codes WHERE batch_id = ''"):
            self._conn.execute(
                "UPDATE register_codes SET batch_id = ? WHERE code = ?",
                (secrets.token_hex(8), row["code"]),
            )
        # 并发创建的历史重复项先合并/收口，再由数据库唯一索引兜底。
        duplicates = self._rows(
            "SELECT platform, external_id, MIN(id) AS keep_id FROM kols "
            "GROUP BY platform, external_id HAVING COUNT(*) > 1"
        )
        for group in duplicates:
            keep_id = group["keep_id"]
            duplicate_ids = [
                r["id"] for r in self._rows(
                    "SELECT id FROM kols WHERE platform = ? AND external_id = ? AND id != ?",
                    (group["platform"], group["external_id"], keep_id),
                )
            ]
            for duplicate_id in duplicate_ids:
                for subscription in self._rows(
                    "SELECT user_id, type, favorite, secondary, hide_images "
                    "FROM subscriptions WHERE kol_id = ?",
                    (duplicate_id,),
                ):
                    existing_rows = self._rows(
                        "SELECT type, favorite, secondary, hide_images FROM subscriptions "
                        "WHERE user_id = ? AND kol_id = ?",
                        (subscription["user_id"], keep_id),
                    )
                    existing = existing_rows[0] if existing_rows else None
                    if existing:
                        subscribe_type = (
                            existing["type"]
                            if existing["type"] == subscription["type"]
                            else "both"
                        )
                        self._conn.execute(
                            "UPDATE subscriptions SET type = ?, favorite = ?, secondary = ?, "
                            "hide_images = ? "
                            "WHERE user_id = ? AND kol_id = ?",
                            (
                                subscribe_type,
                                max(existing["favorite"], subscription["favorite"]),
                                max(existing["secondary"], subscription["secondary"]),
                                max(existing["hide_images"], subscription["hide_images"]),
                                subscription["user_id"],
                                keep_id,
                            ),
                        )
                    else:
                        self._conn.execute(
                            "INSERT INTO subscriptions "
                            "(user_id, kol_id, type, favorite, secondary, hide_images) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                subscription["user_id"],
                                keep_id,
                                subscription["type"],
                                subscription["favorite"],
                                subscription["secondary"],
                                subscription["hide_images"],
                            ),
                        )
                self._conn.execute("DELETE FROM subscriptions WHERE kol_id = ?", (duplicate_id,))
                self._conn.execute(
                    "INSERT OR IGNORE INTO kol_acl (kol_id, user_id) "
                    "SELECT ?, user_id FROM kol_acl WHERE kol_id = ?",
                    (keep_id, duplicate_id),
                )
                self._conn.execute("DELETE FROM kol_acl WHERE kol_id = ?", (duplicate_id,))
                self._conn.execute("UPDATE posts SET kol_id = ? WHERE kol_id = ?", (keep_id, duplicate_id))
                self._conn.execute(
                    "INSERT OR IGNORE INTO cube_snapshots (kol_id, kind, payload, fetched_at) "
                    "SELECT ?, kind, payload, fetched_at FROM cube_snapshots WHERE kol_id = ?",
                    (keep_id, duplicate_id),
                )
                self._conn.execute("DELETE FROM cube_snapshots WHERE kol_id = ?", (duplicate_id,))
                self._conn.execute("DELETE FROM kols WHERE id = ?", (duplicate_id,))
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_kols_platform_external "
            "ON kols(platform, external_id)"
        )
        pending_duplicates = self._rows(
            "SELECT platform, external_id, MIN(id) AS keep_id FROM kol_requests "
            "WHERE status = 'pending' GROUP BY platform, external_id HAVING COUNT(*) > 1"
        )
        for group in pending_duplicates:
            self._conn.execute(
                "UPDATE kol_requests SET status = 'rejected', handled_at = datetime('now') "
                "WHERE platform = ? AND external_id = ? AND status = 'pending' AND id != ?",
                (group["platform"], group["external_id"], group["keep_id"]),
            )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_kol_requests_pending "
            "ON kol_requests(platform, external_id) WHERE status = 'pending'"
        )

        # 渠道绑定唯一化：先清理重复（保留最早注册的用户），再建唯一索引，
        # 避免两个账号绑定同一个 chat_id/open_id 导致重复推送或 /bind 合并错账号。
        for column in (
            "telegram_chat_id",
            "feishu_open_id",
            "feishu_chat_id",
            "wechat_openid",
            "wecom_webhook",
            "telegram_bot_token",
        ):
            seen = set()
            for row in self._rows(
                f"SELECT id, {column} AS v FROM users WHERE {column} != '' ORDER BY id"
            ):
                if row["v"] in seen:
                    self._conn.execute(
                        f"UPDATE users SET {column} = '' WHERE id = ?", (row["id"],)
                    )
                else:
                    seen.add(row["v"])
            self._conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_users_{column} "
                f"ON users({column}) WHERE {column} != ''"
            )

        self._apply_schema_migrations()

    def _migrate_news(self) -> None:
        for slug, name, feeds in _BUILTIN_NEWS:
            self._conn.execute(
                "INSERT OR IGNORE INTO news_sources "
                "(slug, name, built_in, default_selected) VALUES (?, ?, 1, 1)",
                (slug, name),
            )
            source = self._conn.execute(
                "SELECT id FROM news_sources WHERE slug = ?", (slug,)
            ).fetchone()
            for feed_name, url in feeds:
                self._conn.execute(
                    "INSERT OR IGNORE INTO news_feeds "
                    "(source_id, name, url, normalized_url) VALUES (?, ?, ?, ?)",
                    (source["id"], feed_name, url, url),
                )
        self._conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES "
            "('news_enabled', '1'), ('news_visible', '1'), ('news_refresh_interval_seconds', '600')"
        )
        if self.get_setting("news_default_sources_v1") == "1":
            return
        default_ids = [
            row["id"] for row in self._rows(
                "SELECT id FROM news_sources WHERE built_in = 1 "
                "AND default_selected = 1 AND archived_at IS NULL ORDER BY id"
            )
        ]
        for user in self._rows("SELECT id FROM users"):
            for source_id in default_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO user_news_sources (user_id, source_id) "
                    "VALUES (?, ?)",
                    (user["id"], source_id),
                )
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES ('news_default_sources_v1', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )

    def _ensure_ima_document_tables(self) -> None:
        # CREATE IF NOT EXISTS only. Do not insert meta here: a malformed table
        # without a unique id would gain an extra empty row before validation.
        # Use execute(), not executescript(): the latter issues COMMIT first.
        for sql in (
            IMA_DOCUMENT_INDEX_TABLE_SQL,
            IMA_DOCUMENT_TAGS_TABLE_SQL,
            IMA_DOCUMENT_INDEX_META_TABLE_SQL,
            REPORT_EXTRACTIONS_TABLE_SQL,
            REPORT_EXTRACTION_TICKERS_TABLE_SQL,
            IMA_TICKER_DIGEST_TABLE_SQL,
        ):
            self._conn.execute(sql)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_tickers_code "
            "ON report_extraction_tickers(code)"
        )

    def _ima_index_key_columns(self, name: str) -> list[tuple[str, int]]:
        return [
            (row["name"], int(row["desc"]))
            for row in self._rows(f"PRAGMA index_xinfo({name})")
            if row["key"]
        ]

    def _rebuild_ima_document_table(self, old_columns: set[str]) -> None:
        self._conn.execute(
            "ALTER TABLE ima_document_index RENAME TO ima_document_index_legacy"
        )
        self._conn.execute(_ima_create_table_sql(IMA_DOCUMENT_INDEX_TABLE_SQL))
        columns = ", ".join(IMA_DOCUMENT_INDEX_COLUMNS)
        expressions = ", ".join(
            _ima_doc_select_expr(column, old_columns)
            for column in IMA_DOCUMENT_INDEX_COLUMNS
        )
        self._conn.execute(
            f"INSERT OR IGNORE INTO ima_document_index ({columns}) "
            f"SELECT {expressions} FROM ima_document_index_legacy "
            "ORDER BY rowid"
        )
        self._conn.execute("DROP TABLE ima_document_index_legacy")

    def _rebuild_ima_document_tags(self, old_columns: set[str]) -> None:
        self._conn.execute(
            "ALTER TABLE ima_document_tags RENAME TO ima_document_tags_legacy"
        )
        self._conn.execute(_ima_create_table_sql(IMA_DOCUMENT_TAGS_TABLE_SQL))
        columns = ", ".join(item[0] for item in _IMA_TAG_COLUMN_SPEC)
        expressions = ", ".join(
            _ima_tag_select_expr(column, old_columns)
            for column, *_ in _IMA_TAG_COLUMN_SPEC
        )
        self._conn.execute(
            f"INSERT OR IGNORE INTO ima_document_tags ({columns}) "
            f"SELECT {expressions} FROM ima_document_tags_legacy "
            "ORDER BY rowid"
        )
        self._conn.execute("DROP TABLE ima_document_tags_legacy")

    def _rebuild_ima_document_index_meta(self, old_columns: set[str]) -> None:
        self._conn.execute(
            "ALTER TABLE ima_document_index_meta "
            "RENAME TO ima_document_index_meta_legacy"
        )
        self._conn.execute(_ima_create_table_sql(IMA_DOCUMENT_INDEX_META_TABLE_SQL))
        columns = ", ".join(item[0] for item in _IMA_META_COLUMN_SPEC)
        expressions = ", ".join(
            _ima_meta_select_expr(column, old_columns)
            for column, *_ in _IMA_META_COLUMN_SPEC
        )
        order_expression = (
            "CASE WHEN CAST(id AS TEXT) = '1' THEN 0 ELSE 1 END"
            if "id" in old_columns
            else "rowid"
        )
        self._conn.execute(
            f"INSERT OR IGNORE INTO ima_document_index_meta ({columns}) "
            f"SELECT {expressions} FROM ima_document_index_meta_legacy "
            f"ORDER BY {order_expression}, rowid LIMIT 1"
        )
        self._conn.execute("DROP TABLE ima_document_index_meta_legacy")

    def _sync_ima_document_indexes(self) -> None:
        expected = {name: list(columns) for name, _, columns in _IMA_INDEX_SPECS}
        current = {
            name: self._ima_index_key_columns(name) for name in expected
        }
        if current == expected:
            return
        for name in expected:
            self._conn.execute(f"DROP INDEX IF EXISTS {name}")
        for name, target, _columns in _IMA_INDEX_SPECS:
            self._conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")

    def _migrate_ima_document_index(self) -> None:
        """Upgrade malformed read-model tables before creating dependent indexes."""
        # sqlite3 only auto-BEGINs DML. Rebuild uses DDL, so start a transaction
        # explicitly; otherwise a failed CREATE would leave the renamed legacy table.
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN")
        try:
            doc_info = self._rows("PRAGMA table_info(ima_document_index)")
            tags_info = self._rows("PRAGMA table_info(ima_document_tags)")
            meta_info = self._rows("PRAGMA table_info(ima_document_index_meta)")
            meta_sql_rows = self._rows(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'ima_document_index_meta'"
            )
            meta_sql = meta_sql_rows[0]["sql"] if meta_sql_rows else ""

            if not _ima_pragma_matches(doc_info, _IMA_DOC_COLUMN_SPEC):
                self._rebuild_ima_document_table({row["name"] for row in doc_info})
            if not _ima_pragma_matches(tags_info, _IMA_TAG_COLUMN_SPEC):
                self._rebuild_ima_document_tags({row["name"] for row in tags_info})
            if (
                not _ima_pragma_matches(meta_info, _IMA_META_COLUMN_SPEC)
                or not _ima_meta_has_id_check(meta_sql)
                or len(self._rows("SELECT rowid FROM ima_document_index_meta")) > 1
            ):
                self._rebuild_ima_document_index_meta(
                    {row["name"] for row in meta_info}
                )
            self._conn.execute(
                "INSERT OR IGNORE INTO ima_document_index_meta (id) VALUES (1)"
            )
            self._sync_ima_document_indexes()
        except Exception:
            self._conn.rollback()
            raise

    def _apply_schema_migrations(self) -> None:
        """按 PRAGMA user_version 一次性执行有序迁移；失败回滚整个迁移并抛出。"""
        if not SCHEMA_MIGRATIONS:
            return
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for version, name, statements in SCHEMA_MIGRATIONS:
            if not isinstance(version, int) or version <= 0:
                raise ValueError(f"迁移版本必须为正整数: {version} ({name})")
            if version <= current:
                continue
            if not isinstance(statements, tuple):
                statements = (statements,)
            try:
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(f"PRAGMA user_version = {version}")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise RuntimeError(f"schema 迁移失败: #{version} {name}") from None
            logging.getLogger(__name__).info("schema migration applied: #%s %s", version, name)

    def close(self):
        with self._lock:
            self._conn.close()

    def _encrypt_secret(self, value: str) -> str:
        """有密钥时存 enc1:<Fernet 密文>；无密钥保持原样（功能不退化）。"""
        value = (value or "").strip()
        if not value or not self.credential_key or value.startswith(SECRET_PREFIX):
            return value
        from .feishu_personal import encrypt_secret

        return SECRET_PREFIX + encrypt_secret(self.credential_key, value)

    def _rows(self, sql, params=()):
        started = time.monotonic()
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]
        _log_slow_query(sql, started)
        return rows

    def _read_only_rows(self, sql, params=()):
        if self.path == ":memory:":
            return self._rows(sql, params)
        started = time.monotonic()
        with self._reader_condition:
            while self._replace_pending:
                self._reader_condition.wait()
            self._active_readers += 1
        try:
            conn = sqlite3.connect(
                Path(self.path).resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000")
                rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()
        finally:
            with self._reader_condition:
                self._active_readers -= 1
                if not self._active_readers:
                    self._reader_condition.notify_all()
        _log_slow_query(sql, started)
        return rows

    def _execute(self, sql, params=()):
        started = time.monotonic()
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            rowid = cur.lastrowid
        _log_slow_query(sql, started)
        return rowid

    # ---- KOL ----
    def add_kol(
        self,
        platform: str,
        name: str,
        external_id: str,
        category_id: int | None = None,
        priority: bool = False,
        secondary: bool = False,
        original_only: bool = False,
        silent: bool = False,
    ) -> int:
        if platform not in ALLOWED_PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}")
        if self._rows(
            "SELECT id FROM kols WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        ):
            raise ValueError("该大V已存在")
        if platform == "zsxq":
            silent = True
            if not priority:
                secondary = True
        if priority and secondary:
            secondary = False  # 互斥：priority 优先（与 update_kol 行为一致）
        try:
            return self._execute(
                "INSERT INTO kols (platform, name, external_id, category_id, priority, secondary, original_only, baseline_ready, silent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    platform,
                    name,
                    external_id,
                    category_id,
                    1 if priority else 0,
                    1 if secondary else 0,
                    1 if original_only else 0,
                    1 if silent else 0,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError("该大V已存在") from None

    def get_kol(self, kol_id: int) -> dict | None:
        rows = self._rows(
            "SELECT k.*, c.name AS category_name FROM kols k "
            "LEFT JOIN categories c ON c.id = k.category_id WHERE k.id = ?",
            (kol_id,),
        )
        return rows[0] if rows else None

    def update_kol_avatar(self, kol_id: int, avatar_url: str) -> None:
        self._execute(
            "UPDATE kols SET avatar_url = ? WHERE id = ?",
            (avatar_url or "", kol_id),
        )

    def update_kol_avatar_source(self, kol_id: int, source: str) -> None:
        self._execute(
            "UPDATE kols SET avatar_source = ? WHERE id = ?",
            (source or "", kol_id),
        )

    def _kol_filters(
        self,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        status: int | None = None,
    ) -> tuple[list[str], list]:
        conds, params = [], []
        if platform:
            conds.append("k.platform = ?")
            params.append(platform)
        if category_id is not None:
            conds.append("k.category_id = ?")
            params.append(category_id)
        if q:
            like = f"%{q}%"
            conds.append("(k.name LIKE ? OR k.external_id LIKE ?)")
            params.extend([like, like])
        if status is not None:
            conds.append("k.enabled = ?")
            params.append(1 if status else 0)
        return conds, params

    def list_kols(
        self,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        status: int | None = None,
        limit: int | None = None,
        offset: int = 0,
        with_subscriber_count: bool = False,
    ) -> list[dict]:
        """大V列表：可选平台/分类/关键词/启用状态筛选 + 分页（管理列表用）。"""
        extra = (
            ", COALESCE(sc.n, 0) AS subscriber_count"
            if with_subscriber_count
            else ""
        )
        join_counts = (
            " LEFT JOIN (SELECT kol_id, COUNT(*) AS n FROM subscriptions GROUP BY kol_id) sc "
            "ON sc.kol_id = k.id"
            if with_subscriber_count
            else ""
        )
        sql = (
            f"SELECT k.*, c.name AS category_name{extra} FROM kols k "
            f"LEFT JOIN categories c ON c.id = k.category_id{join_counts}"
        )
        conds, params = self._kol_filters(platform, category_id, q, status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        # 推荐权重置顶（订阅广场「置顶」位），其余按 id 稳定排序
        sql += " ORDER BY k.recommend_weight DESC, k.id"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return self._rows(sql, params)

    def list_kol_ids(
        self,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        status: int | None = None,
    ) -> list[int]:
        """与 list_kols 同条件的全部 id（管理端跨页勾选用）。"""
        conds, params = self._kol_filters(platform, category_id, q, status)
        sql = "SELECT k.id FROM kols k"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY k.id"
        return [r["id"] for r in self._rows(sql, params)]

    def count_enabled_kols_by_platform(self) -> dict[str, int]:
        """各平台当前启用的大V数（广场「自动」显隐用）。"""
        rows = self._rows(
            "SELECT platform, COUNT(*) AS n FROM kols WHERE enabled = 1 GROUP BY platform"
        )
        return {row["platform"]: _to_int(row["n"]) for row in rows}

    def count_kols(
        self,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        status: int | None = None,
    ) -> int:
        """与 list_kols 同条件的大V总数（分页控件用）。"""
        conds, params = self._kol_filters(platform, category_id, q, status)
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        row = self._rows(f"SELECT COUNT(*) AS n FROM kols k {where}", params)
        return _to_int(row[0]["n"]) if row else 0

    def set_kols_enabled(self, ids: list[int], enabled: bool) -> None:
        placeholders = ",".join("?" * len(ids))
        self._execute(
            f"UPDATE kols SET enabled = ? WHERE id IN ({placeholders})",
            (1 if enabled else 0, *ids),
        )

    def set_kols_flag(self, ids: list[int], flag: str, value: bool) -> None:
        """批量设置 priority / secondary；设为 True 时清掉另一档（与 update_kol 互斥一致）。"""
        col = "priority" if flag == "priority" else "secondary"
        other = "secondary" if col == "priority" else "priority"
        placeholders = ",".join("?" * len(ids))
        if value:
            self._execute(
                f"UPDATE kols SET {col} = 1, {other} = 0 WHERE id IN ({placeholders})",
                ids,
            )
        else:
            self._execute(
                f"UPDATE kols SET {col} = 0 WHERE id IN ({placeholders})",
                ids,
            )

    def set_kols_category(self, ids: list[int], category_id: int | None) -> None:
        placeholders = ",".join("?" * len(ids))
        self._execute(
            f"UPDATE kols SET category_id = ? WHERE id IN ({placeholders})",
            (category_id, *ids),
        )

    def get_kol_by_external(self, platform: str, external_id: str) -> dict | None:
        """按平台 + 外部ID 查大V（更新 external_id 时的唯一性校验用）。"""
        rows = self._rows(
            "SELECT * FROM kols WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        )
        return rows[0] if rows else None

    def recommended_kols(self, user_id: int, limit: int = 4) -> list[dict]:
        """新用户引导推荐：启用且公开的大V，推荐权重优先，其后按订阅人数倒序。"""
        return self._rows(
            "SELECT k.*, c.name AS category_name, "
            "(SELECT COUNT(*) FROM subscriptions s WHERE s.kol_id = k.id) AS subscriber_count, "
            "EXISTS(SELECT 1 FROM subscriptions mine "
            "       WHERE mine.kol_id = k.id AND mine.user_id = ?) AS subscribed "
            "FROM kols k LEFT JOIN categories c ON c.id = k.category_id "
            "WHERE k.enabled = 1 AND k.is_private = 0 "
            "ORDER BY k.recommend_weight DESC, subscriber_count DESC, k.id DESC LIMIT ?",
            (user_id, limit),
        )

    def last_post_time_by_kol(self) -> dict[int, str]:
        """每个大V最近一次抓到帖子的时间（kols.last_post_at），用于活跃度排序。"""
        rows = self._rows(
            "SELECT id, last_post_at FROM kols WHERE last_post_at IS NOT NULL AND last_post_at != ''"
        )
        return {r["id"]: r["last_post_at"] for r in rows}

    def update_kol(
        self,
        kol_id: int,
        name=None,
        external_id=None,
        enabled=None,
        original_only=_UNSET,
        category_id=_UNSET,
        priority=_UNSET,
        secondary=_UNSET,
        is_private=_UNSET,
        silent=_UNSET,
        recommend_weight=_UNSET,
    ):
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if external_id is not None:
            sets.append("external_id = ?")
            params.append(external_id)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if original_only is not _UNSET:
            sets.append("original_only = ?")
            params.append(1 if original_only else 0)
        if category_id is not _UNSET:
            sets.append("category_id = ?")
            params.append(category_id)
        if priority is not _UNSET:
            sets.append("priority = ?")
            params.append(1 if priority else 0)
            if priority:
                sets.append("secondary = 0")  # 互斥：优先大V不能同时是次要
        if secondary is not _UNSET:
            sets.append("secondary = ?")
            params.append(1 if secondary else 0)
            if secondary:
                sets.append("priority = 0")  # 互斥：次要大V不能同时是优先
        if is_private is not _UNSET:
            sets.append("is_private = ?")
            params.append(1 if is_private else 0)
        if silent is not _UNSET:
            sets.append("silent = ?")
            params.append(1 if silent else 0)
        if recommend_weight is not _UNSET:
            sets.append("recommend_weight = ?")
            params.append(max(int(recommend_weight or 0), 0))
        if not sets:
            return
        params.append(kol_id)
        self._execute(f"UPDATE kols SET {', '.join(sets)} WHERE id = ?", params)

    def delete_kol(self, kol_id: int):
        # 级联清理必须作为一个事务，任一步失败都保留完整原状态。
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM kol_acl WHERE kol_id = ?", (kol_id,))
                self._conn.execute("DELETE FROM subscriptions WHERE kol_id = ?", (kol_id,))
                self._conn.execute(
                    "DELETE FROM push_logs WHERE post_id IN (SELECT id FROM posts WHERE kol_id = ?)",
                    (kol_id,),
                )
                self._conn.execute("DELETE FROM posts WHERE kol_id = ?", (kol_id,))
                self._conn.execute("DELETE FROM cube_snapshots WHERE kol_id = ?", (kol_id,))
                self._conn.execute("DELETE FROM kols WHERE id = ?", (kol_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- KOL 可见性（白名单） ----
    def set_kol_acl(self, kol_id: int, user_ids: list[int]) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM kol_acl WHERE kol_id = ?", (kol_id,))
                for uid in set(user_ids):
                    self._conn.execute(
                        "INSERT OR IGNORE INTO kol_acl (kol_id, user_id) VALUES (?, ?)",
                        (kol_id, uid),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def acl_usernames(self, kol_id: int) -> list[str]:
        return [
            r["username"]
            for r in self._rows(
                "SELECT u.username FROM kol_acl a JOIN users u ON u.id = a.user_id "
                "WHERE a.kol_id = ? ORDER BY u.username",
                (kol_id,),
            )
        ]

    def acl_user_ids(self, kol_id: int) -> list[int]:
        return [r["user_id"] for r in self._rows("SELECT user_id FROM kol_acl WHERE kol_id = ?", (kol_id,))]

    def set_ima_kb_acl(self, group_id: str, user_ids: list[int]) -> None:
        group_id = str(group_id or "").strip()
        allowed = {int(uid) for uid in user_ids}
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM ima_kb_acl WHERE group_id = ?", (group_id,))
                for uid in allowed:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO ima_kb_acl (group_id, user_id) VALUES (?, ?)",
                        (group_id, uid),
                    )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO ima_kb_subscriptions (user_id, group_id, created_at) "
                        "VALUES (?, ?, ?)",
                        (uid, group_id, int(time.time())),
                    )
                rows = self._conn.execute(
                    "SELECT user_id FROM ima_kb_subscriptions WHERE group_id = ?",
                    (group_id,),
                ).fetchall()
                for row in rows:
                    if int(row["user_id"]) not in allowed:
                        self._conn.execute(
                            "DELETE FROM ima_kb_subscriptions WHERE group_id = ? AND user_id = ?",
                            (group_id, row["user_id"]),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def ima_kb_acl_usernames(self, group_id: str) -> list[str]:
        return [
            r["username"]
            for r in self._read_only_rows(
                "SELECT u.username FROM ima_kb_acl a JOIN users u ON u.id = a.user_id "
                "WHERE a.group_id = ? ORDER BY u.username",
                (group_id,),
            )
        ]

    def ima_kb_acl_user_ids(self, group_id: str) -> list[int]:
        return [
            r["user_id"]
            for r in self._read_only_rows(
                "SELECT user_id FROM ima_kb_acl WHERE group_id = ?",
                (group_id,),
            )
        ]

    def ima_kb_group_ids_for_user(self, user_id: int) -> list[str]:
        return [
            str(row["group_id"])
            for row in self._read_only_rows(
                "SELECT group_id FROM ima_kb_acl WHERE user_id = ? ORDER BY group_id",
                (int(user_id),),
            )
        ]

    def ima_kb_subscribed_group_ids_for_user(self, user_id: int) -> list[str]:
        return [
            str(row["group_id"])
            for row in self._read_only_rows(
                "SELECT group_id FROM ima_kb_subscriptions WHERE user_id = ? ORDER BY group_id",
                (int(user_id),),
            )
        ]

    def ima_kb_acl_map(self) -> dict[int, list[str]]:
        mapped: dict[int, list[str]] = {}
        for row in self._read_only_rows("SELECT user_id, group_id FROM ima_kb_acl"):
            mapped.setdefault(int(row["user_id"]), []).append(str(row["group_id"]))
        return mapped

    def ima_kb_sub_map(self) -> dict[int, list[str]]:
        mapped: dict[int, list[str]] = {}
        for row in self._read_only_rows("SELECT user_id, group_id FROM ima_kb_subscriptions"):
            mapped.setdefault(int(row["user_id"]), []).append(str(row["group_id"]))
        return mapped

    def set_ima_kb_acl_for_user(self, user_id: int, group_ids: list[str]) -> None:
        uid = int(user_id)
        allowed = {str(group_id).strip() for group_id in group_ids if str(group_id).strip()}
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                existing = {
                    str(row["group_id"])
                    for row in self._conn.execute(
                        "SELECT group_id FROM ima_kb_acl WHERE user_id = ?",
                        (uid,),
                    ).fetchall()
                }
                for group_id in allowed - existing:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO ima_kb_acl (group_id, user_id) VALUES (?, ?)",
                        (group_id, uid),
                    )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO ima_kb_subscriptions (user_id, group_id, created_at) "
                        "VALUES (?, ?, ?)",
                        (uid, group_id, int(time.time())),
                    )
                for group_id in existing - allowed:
                    self._conn.execute(
                        "DELETE FROM ima_kb_acl WHERE group_id = ? AND user_id = ?",
                        (group_id, uid),
                    )
                    self._conn.execute(
                        "DELETE FROM ima_kb_subscriptions WHERE group_id = ? AND user_id = ?",
                        (group_id, uid),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def consume_user_quota(
        self, user_id: int, bucket: str, period_start: int, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Increment a quota bucket. Returns (allowed, retry_after_seconds)."""
        uid = int(user_id)
        period_start = int(period_start)
        limit = max(int(limit), 1)
        window_seconds = max(int(window_seconds), 1)
        retry = max(period_start + window_seconds - int(time.time()), 1)
        with self._lock:
            row = self._conn.execute(
                "SELECT period_start, count FROM user_quota WHERE user_id = ? AND bucket = ?",
                (uid, bucket),
            ).fetchone()
            if row is None or int(row["period_start"]) != period_start:
                self._conn.execute(
                    "INSERT INTO user_quota (user_id, bucket, period_start, count) "
                    "VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(user_id, bucket) DO UPDATE SET period_start = excluded.period_start, "
                    "count = excluded.count",
                    (uid, bucket, period_start),
                )
                self._conn.commit()
                return True, 0
            count = int(row["count"])
            if count >= limit:
                return False, retry
            self._conn.execute(
                "UPDATE user_quota SET count = count + 1 WHERE user_id = ? AND bucket = ?",
                (uid, bucket),
            )
            self._conn.commit()
            return True, 0

    def ima_kb_can_subscribe(self, user_id: int, group_id: str) -> bool:
        from .ima_kb import is_open_group
        if is_open_group(group_id):
            return True
        return bool(
            self._read_only_rows(
                "SELECT 1 FROM ima_kb_acl WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
        )

    def ima_kb_subscribe(self, user_id: int, group_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO ima_kb_subscriptions (user_id, group_id, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, group_id, int(time.time())),
            )
            self._conn.commit()

    def ima_kb_unsubscribe(self, user_id: int, group_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM ima_kb_subscriptions WHERE user_id = ? AND group_id = ?",
                (user_id, group_id),
            )
            self._conn.commit()

    def ima_kb_is_subscribed(self, user_id: int, group_id: str) -> bool:
        return bool(
            self._read_only_rows(
                "SELECT 1 FROM ima_kb_subscriptions WHERE user_id = ? AND group_id = ?",
                (user_id, group_id),
            )
        )

    def ima_kb_can_read(self, user_id: int, group_id: str) -> bool:
        return self.ima_kb_can_subscribe(user_id, group_id) and self.ima_kb_is_subscribed(
            user_id, group_id
        )

    def visible_kol_ids(self, user_id: int) -> set[int]:
        """用户可见的大V：公开大V + 白名单里的私有大V。"""
        rows = self._rows(
            "SELECT id FROM kols WHERE is_private = 0 "
            "UNION SELECT kol_id FROM kol_acl WHERE user_id = ?",
            (user_id,),
        )
        return {r["id"] for r in rows}

    # ---- 求添加申请 ----
    def add_kol_request(
        self, platform: str, external_id: str, user_id: int, name: str = "", category_id: int | None = None
    ) -> int:
        if platform not in ALLOWED_PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}")
        if category_id is None:
            raise ValueError("请选择分类")
        if self.get_category(category_id) is None:
            raise ValueError("分类不存在")
        if self._rows(
            "SELECT id FROM kol_requests WHERE platform = ? AND external_id = ? AND status = 'pending'",
            (platform, external_id),
        ):
            raise ValueError("该大V的申请已在处理中")
        if self._rows(
            "SELECT id FROM kols WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        ):
            raise ValueError("该大V已在目录中，直接订阅即可")
        try:
            return self._execute(
                "INSERT INTO kol_requests (platform, name, external_id, user_id, category_id) VALUES (?, ?, ?, ?, ?)",
                (platform, name.strip(), external_id, user_id, category_id),
            )
        except sqlite3.IntegrityError:
            raise ValueError("该大V的申请已在处理中") from None

    def list_kol_requests(
        self, status: str | None = None, user_id: int | None = None
    ) -> list[dict]:
        sql = (
            "SELECT r.*, u.username AS requester, c.name AS category_name "
            "FROM kol_requests r "
            "LEFT JOIN users u ON u.id = r.user_id "
            "LEFT JOIN categories c ON c.id = r.category_id"
        )
        conds: list[str] = []
        params: list = []
        if status:
            conds.append("r.status = ?")
            params.append(status)
        if user_id is not None:
            conds.append("r.user_id = ?")
            params.append(user_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY r.id DESC"
        return self._rows(sql, params)

    def count_pending_kol_requests(self) -> int:
        return _to_int((self._rows(
            "SELECT COUNT(*) AS v FROM kol_requests WHERE status = 'pending'"
        ) or [{}])[0].get("v"))

    def get_kol_request(self, request_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM kol_requests WHERE id = ?", (request_id,))
        return rows[0] if rows else None

    def set_kol_request_status(self, request_id: int, status: str) -> None:
        self._execute(
            "UPDATE kol_requests SET status = ?, handled_at = datetime('now') WHERE id = ?",
            (status, request_id),
        )

    # ---- Category ----
    def list_categories(self) -> list[dict]:
        return self._rows(
            "SELECT c.*, (SELECT COUNT(*) FROM kols k WHERE k.category_id = c.id) AS kol_count "
            "FROM categories c ORDER BY c.id"
        )

    def get_category(self, category_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM categories WHERE id = ?", (category_id,))
        return rows[0] if rows else None

    def add_category(self, name: str) -> int:
        try:
            return self._execute("INSERT INTO categories (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            raise ValueError(f"分类已存在: {name}") from None

    def rename_category(self, category_id: int, name: str) -> None:
        try:
            self._execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
        except sqlite3.IntegrityError:
            raise ValueError(f"分类已存在: {name}") from None

    def delete_category(self, category_id: int) -> None:
        # 两步落库必须原子：半提交会让 KOL 挂到已删除的分组上
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "UPDATE kols SET category_id = NULL WHERE category_id = ?", (category_id,)
                )
                self._conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- User ----
    def get_user(self, user_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None

    def get_authenticated_user(self, user_id: int) -> dict | None:
        rows = self._read_only_rows("SELECT * FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None

    def get_user_by_username(self, username: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE username = ?", (username,))
        return rows[0] if rows else None

    def get_user_by_username_ci(self, username: str) -> dict | None:
        """按用户名查找（不区分大小写），用于注册/改名的唯一性校验。"""
        rows = self._rows(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        return rows[0] if rows else None

    def get_user_by_telegram(self, chat_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE telegram_chat_id = ?", (chat_id,))
        return rows[0] if rows else None

    def get_user_by_telegram_bot(self, bot_token: str) -> dict | None:
        digest = _secret_hash(bot_token)
        if not digest:
            return None
        rows = self._rows(
            "SELECT * FROM users WHERE telegram_bot_token_hash = ?", (digest,)
        )
        return rows[0] if rows else None

    def get_user_by_feishu(self, open_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE feishu_open_id = ?", (open_id,))
        return rows[0] if rows else None

    def get_user_by_feishu_chat(self, chat_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE feishu_chat_id = ?", (chat_id,))
        return rows[0] if rows else None

    def get_user_by_wecom_webhook(self, webhook: str) -> dict | None:
        # 凭据列存的是密文，唯一性查找走明文哈希影子列
        digest = _secret_hash(webhook)
        if not digest:
            return None
        rows = self._rows(
            "SELECT * FROM users WHERE wecom_webhook_hash = ?", (digest,)
        )
        return rows[0] if rows else None

    def get_user_by_bark_key(self, bark_key: str) -> dict | None:
        digest = _secret_hash(bark_key)
        if not digest:
            return None
        rows = self._rows("SELECT * FROM users WHERE bark_key_hash = ?", (digest,))
        return rows[0] if rows else None

    def list_webpush_subscriptions(self, user_id: int) -> list[dict]:
        return self._rows(
            "SELECT endpoint, p256dh, auth, user_agent, created_at "
            "FROM webpush_subscriptions WHERE user_id = ? ORDER BY id",
            (user_id,),
        )

    def count_webpush_subscriptions(self, user_id: int) -> int:
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM webpush_subscriptions WHERE user_id = ?",
            (user_id,),
        )
        return rows[0]["n"] if rows else 0

    def webpush_user_ids(self) -> set[int]:
        return {r["user_id"] for r in self._rows("SELECT DISTINCT user_id FROM webpush_subscriptions")}

    def upsert_webpush_subscription(
        self, user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: str = ""
    ) -> None:
        self._execute(
            "INSERT INTO webpush_subscriptions (user_id, endpoint, p256dh, auth, user_agent) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "user_id = excluded.user_id, p256dh = excluded.p256dh, "
            "auth = excluded.auth, user_agent = excluded.user_agent",
            (user_id, endpoint, p256dh, auth, user_agent or ""),
        )

    def delete_webpush_subscription(self, endpoint: str) -> None:
        self._execute("DELETE FROM webpush_subscriptions WHERE endpoint = ?", (endpoint,))

    def delete_webpush_subscriptions(self, user_id: int) -> None:
        self._execute("DELETE FROM webpush_subscriptions WHERE user_id = ?", (user_id,))

    def list_android_devices(self, user_id: int) -> list[dict]:
        return self._rows(
            "SELECT installation_id, user_id, token, provider, device_model, app_version, "
            "created_at, updated_at FROM android_devices WHERE user_id = ? "
            "ORDER BY updated_at DESC, installation_id",
            (user_id,),
        )

    def count_android_devices(self, user_id: int) -> int:
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM android_devices WHERE user_id = ?", (user_id,)
        )
        return int(rows[0]["n"]) if rows else 0

    def upsert_android_device(
        self,
        installation_id: str,
        user_id: int,
        token: str,
        provider: str,
        device_model: str = "",
        app_version: str = "",
    ) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT user_id FROM android_devices WHERE installation_id = ?",
                    (installation_id,),
                ).fetchone()
                if row is not None and int(row["user_id"]) != int(user_id):
                    raise ValueError("该设备已绑定其他账号")
                self._conn.execute(
                    "INSERT INTO android_devices "
                    "(installation_id, user_id, token, provider, device_model, app_version) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(installation_id) DO UPDATE SET "
                    "token = excluded.token, provider = excluded.provider, "
                    "device_model = excluded.device_model, "
                    "app_version = excluded.app_version, updated_at = datetime('now')",
                    (
                        installation_id,
                        user_id,
                        token,
                        provider,
                        device_model or "",
                        app_version or "",
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def delete_android_device(self, installation_id: str, user_id: int) -> None:
        self._execute(
            "DELETE FROM android_devices WHERE installation_id = ? AND user_id = ?",
            (installation_id, user_id),
        )

    def get_user_by_openid(self, openid: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE wechat_openid = ?", (openid,))
        return rows[0] if rows else None

    def count_users(self) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM users")
        return rows[0]["n"]

    def _insert_default_news_sources(self, user_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO user_news_sources (user_id, source_id) "
            "SELECT ?, id FROM news_sources "
            "WHERE built_in = 1 AND default_selected = 1 AND archived_at IS NULL",
            (user_id,),
        )

    def add_user(
        self,
        username: str,
        password_hash: str,
        is_admin: bool = False,
        telegram_chat_id: str = "",
        feishu_open_id: str = "",
        feishu_chat_id: str = "",
        notify_enabled: bool = True,
        wechat_openid: str = "",
    ) -> int:
        if self.get_user_by_username_ci(username):
            raise ValueError(f"用户名已存在: {username}")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                insert = self._conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin, telegram_chat_id, "
                    "feishu_open_id, feishu_chat_id, notify_enabled, wechat_openid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, password_hash, 1 if is_admin else 0, telegram_chat_id, feishu_open_id,
                     feishu_chat_id, 1 if notify_enabled else 0, wechat_openid),
                )
                user_id = insert.lastrowid
                self._insert_default_news_sources(user_id)
                self._conn.commit()
                return user_id
            except sqlite3.IntegrityError:
                self._conn.rollback()
                raise ValueError(f"用户名已存在: {username}") from None
            except Exception:
                self._conn.rollback()
                raise

    def touch_last_login(self, user_id: int) -> None:
        self._execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
            (user_id,),
        )

    def note_llm_status(self, user_id: int, status: str) -> None:
        self._execute(
            "UPDATE users SET llm_last_status = ? WHERE id = ?",
            (status, user_id),
        )

    # update_user 允许写入的字段白名单：拦截任意 key 拼接进 SQL（防注入脚枪）
    _UPDATE_USER_COLUMNS = frozenset({
        "username", "password_hash", "is_admin", "wechat_openid",
        "telegram_chat_id", "telegram_bot_token", "feishu_open_id",
        "feishu_chat_id", "wecom_webhook", "notify_enabled", "daily_report",
        "translate_twitter",
        "push_channels", "dnd_start", "dnd_end", "dnd_allow_favorite",
        "feed_token", "bark_key", "llm_api_base", "llm_api_key", "llm_model",
        "llm_api_format",
        "token_version", "last_login_at",
        "keywords_match_reports", "keywords_match_reports_since",
    })

    def _build_user_sets(self, updates: dict) -> tuple[list, list]:
        """把字段更新字典归一化为 SET 子句；凭证列在此统一加密并维护哈希。"""
        sets, params = [], []
        for key, value in updates.items():
            if key not in self._UPDATE_USER_COLUMNS:
                raise ValueError(f"非法用户字段: {key}")
            if key in (
                "is_admin", "notify_enabled", "daily_report", "translate_twitter",
                "dnd_allow_favorite", "keywords_match_reports",
            ):
                value = _to_bool(value)
            if key in SECRET_COLUMNS:
                plain = (value or "").strip()
                hash_col = SECRET_HASH_COLUMNS.get(key)
                if hash_col:
                    sets.append(f"{hash_col} = ?")
                    params.append(_secret_hash(plain))
                value = self._encrypt_secret(plain)
            sets.append(f"{key} = ?")
            params.append(value)
        return sets, params

    def update_user(self, user_id: int, **kwargs) -> None:
        sets, params = self._build_user_sets(kwargs)
        if not sets:
            return
        params.append(user_id)
        self._execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)

    def set_users_notify(self, ids: list[int], enabled: bool) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE users SET notify_enabled = ? WHERE id IN ({placeholders})",
                (1 if enabled else 0, *ids),
            )
            self._conn.commit()
            return cur.rowcount

    def update_user_atomic(
        self,
        user_id: int,
        updates: dict,
        *,
        keywords=_UNSET,
        news_source_ids=_UNSET,
        revoke_tokens: bool = False,
    ) -> None:
        """一次提交用户字段与关键词；密码变更可同时撤销既有 token。"""
        sets, params = self._build_user_sets(updates)
        if revoke_tokens:
            sets.append("token_version = token_version + 1")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                if sets:
                    self._conn.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                        (*params, user_id),
                    )
                if keywords is not _UNSET:
                    keywords = list(dict.fromkeys(keywords))
                    self._conn.execute("DELETE FROM user_keywords WHERE user_id = ?", (user_id,))
                    for keyword in keywords:
                        self._conn.execute(
                            "INSERT INTO user_keywords (user_id, keyword) VALUES (?, ?)",
                            (user_id, keyword),
                        )
                if news_source_ids is not _UNSET:
                    ids = list(dict.fromkeys(int(source_id) for source_id in news_source_ids))
                    if ids:
                        placeholders = ",".join("?" * len(ids))
                        valid = self._conn.execute(
                            f"SELECT id FROM news_sources WHERE archived_at IS NULL AND id IN ({placeholders})",
                            ids,
                        ).fetchall()
                        if {row["id"] for row in valid} != set(ids):
                            raise ValueError("来源不存在或已归档")
                    self._conn.execute(
                        "DELETE FROM user_news_sources WHERE user_id = ?", (user_id,)
                    )
                    for source_id in ids:
                        self._conn.execute(
                            "INSERT INTO user_news_sources (user_id, source_id) VALUES (?, ?)",
                            (user_id, source_id),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def update_user_password(self, user_id: int, password_hash: str) -> None:
        self.update_user_atomic(
            user_id, {"password_hash": password_hash}, revoke_tokens=True
        )

    def list_users(self) -> list[dict]:
        return self._rows("SELECT * FROM users ORDER BY id DESC")

    def get_inactive_policy(self) -> tuple[int, int]:
        raw_n = self.get_setting(INACTIVE_AFTER_KEY)
        raw_m = self.get_setting(INACTIVE_PURGE_KEY)
        n = _parse_inactive_days(raw_n, INACTIVE_AFTER_DEFAULT)
        m = _parse_inactive_days(raw_m, INACTIVE_PURGE_DEFAULT)
        if raw_n is None:
            self.set_setting(INACTIVE_AFTER_KEY, str(n))
        if raw_m is None:
            self.set_setting(INACTIVE_PURGE_KEY, str(m))
        return n, m

    def inactive_policy_customized(self) -> bool:
        return (self.get_setting(INACTIVE_CUSTOMIZED_KEY) or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    def inactive_policy_counts(self, after_days: int, purge_after_days: int) -> tuple[int, int]:
        return (
            len(self.list_inactive_user_rows(after_days)),
            len(self.list_inactive_purge_ids(after_days, purge_after_days)),
        )

    def set_inactive_policy(self, after_days: int, purge_after_days: int) -> tuple[int, int]:
        n = int(after_days)
        m = int(purge_after_days)
        self.set_setting(INACTIVE_AFTER_KEY, str(n))
        self.set_setting(INACTIVE_PURGE_KEY, str(m))
        self.set_setting(INACTIVE_CUSTOMIZED_KEY, "1")
        return n, m

    def list_inactive_user_rows(self, after_days: int) -> list[dict]:
        if after_days <= 0:
            return []
        return self._rows(
            "SELECT * FROM users WHERE is_admin = 0 AND last_login_at IS NULL "
            f"AND created_at <= datetime('now', ?) AND NOT {_user_has_channel_sql()} "
            "AND NOT EXISTS (SELECT 1 FROM push_logs p WHERE p.user_id = users.id) "
            "AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = users.id)",
            (f"-{int(after_days)} days",),
        )

    def list_inactive_purge_ids(self, after_days: int, purge_after_days: int) -> list[int]:
        if after_days <= 0 or purge_after_days <= 0:
            return []
        total = int(after_days) + int(purge_after_days)
        return [
            r["id"]
            for r in self._rows(
                "SELECT id FROM users WHERE is_admin = 0 AND last_login_at IS NULL "
                f"AND created_at <= datetime('now', ?) AND NOT {_user_has_channel_sql()} "
                "AND NOT EXISTS (SELECT 1 FROM push_logs p WHERE p.user_id = users.id) "
                "AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = users.id)",
                (f"-{total} days",),
            )
        ]

    def purge_inactive_users(self) -> int:
        n_days, m_days = self.get_inactive_policy()
        ids = self.list_inactive_purge_ids(n_days, m_days)
        for uid in ids:
            row = self.get_user(uid)
            username = (row or {}).get("username") or ""
            self.log_admin_action(None, "purge_inactive_user", str(uid), username)
            self.delete_user(uid)
        return len(ids)

    def purge_inactive_users_if_due(self, now_ts: int | None = None) -> int:
        now_ts = int(now_ts or time.time())
        raw = self.get_setting(INACTIVE_LAST_PURGE_KEY) or "0"
        try:
            last = int(float(raw))
        except (TypeError, ValueError):
            last = 0
        if last and now_ts - last < 24 * 3600:
            return 0
        n = self.purge_inactive_users()
        self.set_setting(INACTIVE_LAST_PURGE_KEY, str(now_ts))
        return n

    def subscription_counts(self) -> dict[int, int]:
        return {
            r["user_id"]: r["n"]
            for r in self._rows("SELECT user_id, COUNT(*) AS n FROM subscriptions GROUP BY user_id")
        }

    # ---- 注册码 ----
    def log_admin_action(self, user_id: int | None, action: str, target: str = "", detail: str = "") -> None:
        self._execute(
            "INSERT INTO admin_logs (user_id, action, target, detail) VALUES (?, ?, ?, ?)",
            (user_id, action, target, detail),
        )

    def list_admin_logs(self, limit: int = 100) -> list[dict]:
        return self._rows(
            "SELECT l.*, u.username FROM admin_logs l LEFT JOIN users u ON u.id = l.user_id "
            "ORDER BY l.id DESC LIMIT ?",
            (limit,),
        )

    def add_register_code(
        self,
        code: str,
        note: str = "",
        batch_id: str | None = None,
        expires_at: str | None = None,
        created_by: int | None = None,
    ) -> None:
        try:
            self._execute(
                "INSERT INTO register_codes (code, note, batch_id, expires_at, created_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    code.strip().upper(),
                    note.strip(),
                    (batch_id or secrets.token_hex(8)).strip(),
                    expires_at or None,
                    created_by,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"注册码已存在: {code}") from None

    def list_register_codes(self) -> list[dict]:
        return self._rows(
            "SELECT rc.*, u.username AS used_by_name, c.username AS created_by_name "
            "FROM register_codes rc "
            "LEFT JOIN users u ON u.id = rc.used_by "
            "LEFT JOIN users c ON c.id = rc.created_by "
            "ORDER BY rc.created_at DESC"
        )

    def get_register_code(self, code: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM register_codes WHERE code = ?", (code.strip().upper(),)
        )
        return rows[0] if rows else None

    def revoke_register_code(self, code: str) -> bool:
        """软作废未使用的码；已使用返回 False。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE register_codes SET revoked_at = datetime('now') "
                "WHERE code = ? AND used_by IS NULL AND revoked_at IS NULL",
                (code.strip().upper(),),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_register_code(self, code: str) -> bool:
        """兼容旧名：软作废。"""
        return self.revoke_register_code(code)

    def revoke_unused_in_batch(self, batch_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE register_codes SET revoked_at = datetime('now') "
                "WHERE batch_id = ? AND used_by IS NULL AND revoked_at IS NULL",
                (batch_id,),
            )
            self._conn.commit()
            return cur.rowcount

    def purge_register_codes(self, codes: list[str]) -> int:
        codes = [str(c).strip().upper() for c in codes if str(c).strip()]
        if not codes:
            return 0
        placeholders = ",".join("?" * len(codes))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM register_codes WHERE code IN ({placeholders}) AND ("
                "used_by IS NOT NULL OR revoked_at IS NOT NULL OR "
                "(expires_at IS NOT NULL AND expires_at <= datetime('now')))",
                codes,
            )
            self._conn.commit()
            return cur.rowcount

    def update_register_code_note(self, code: str, note: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE register_codes SET note = ? WHERE code = ?",
                (note.strip(), code.strip().upper()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def register_with_code(self, code: str, username: str, password_hash: str) -> int:
        """凭注册码注册：原子消费可用码 + 创建用户，任一失败整体回滚。"""
        code = code.strip().upper()
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                cur = self._conn.execute(
                    "UPDATE register_codes SET used_at = datetime('now') "
                    "WHERE code = ? AND used_by IS NULL AND revoked_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > datetime('now'))",
                    (code,),
                )
                if cur.rowcount == 0:
                    row = self._conn.execute(
                        "SELECT used_by, revoked_at, expires_at FROM register_codes WHERE code = ?",
                        (code,),
                    ).fetchone()
                    if row is None or row["used_by"] is not None:
                        raise ValueError("邀请码无效或已被使用")
                    if row["revoked_at"]:
                        raise ValueError("邀请码已作废，请向管理员索取新的")
                    raise ValueError("邀请码已过期，请向管理员索取新的")
                if self._conn.execute(
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone():
                    raise ValueError(f"用户名已存在: {username}")
                try:
                    insert = self._conn.execute(
                        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)",
                        (username, password_hash),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError(f"用户名已存在: {username}") from None
                uid = insert.lastrowid
                self._insert_default_news_sources(uid)
                self._conn.execute(
                    "UPDATE register_codes SET used_by = ? WHERE code = ?",
                    (uid, code),
                )
                self._conn.commit()
                return uid
            except Exception:
                self._conn.rollback()
                raise

    def register_wechat_with_code(self, code: str, username: str, wechat_openid: str) -> int:
        """微信首次登录：原子消费邀请码并创建带 openid 的用户。"""
        code = code.strip().upper()
        wechat_openid = (wechat_openid or "").strip()
        if not wechat_openid:
            raise ValueError("微信登录态无效")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                existing = self._conn.execute(
                    "SELECT id FROM users WHERE wechat_openid = ?", (wechat_openid,)
                ).fetchone()
                if existing is not None:
                    raise ValueError("微信账号已存在")
                cur = self._conn.execute(
                    "UPDATE register_codes SET used_at = datetime('now') "
                    "WHERE code = ? AND used_by IS NULL AND revoked_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > datetime('now'))",
                    (code,),
                )
                if cur.rowcount == 0:
                    row = self._conn.execute(
                        "SELECT used_by, revoked_at, expires_at FROM register_codes WHERE code = ?",
                        (code,),
                    ).fetchone()
                    if row is None or row["used_by"] is not None:
                        raise ValueError("邀请码无效或已被使用")
                    if row["revoked_at"]:
                        raise ValueError("邀请码已作废，请向管理员索取新的")
                    raise ValueError("邀请码已过期，请向管理员索取新的")
                if self._conn.execute(
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone():
                    raise ValueError(f"用户名已存在: {username}")
                try:
                    insert = self._conn.execute(
                        "INSERT INTO users (username, password_hash, is_admin, wechat_openid) "
                        "VALUES (?, '', 0, ?)",
                        (username, wechat_openid),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("微信账号已存在") from None
                uid = insert.lastrowid
                self._insert_default_news_sources(uid)
                self._conn.execute(
                    "UPDATE register_codes SET used_by = ? WHERE code = ?",
                    (uid, code),
                )
                self._conn.commit()
                return uid
            except Exception:
                self._conn.rollback()
                raise

    # ---- Financial news ----
    def list_news_sources(self, include_archived: bool = False) -> list[dict]:
        sql = "SELECT * FROM news_sources"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY id"
        return self._rows(sql)

    def get_news_source(self, source_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM news_sources WHERE id = ?", (source_id,))
        return rows[0] if rows else None

    def add_news_source(self, name: str) -> int:
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise ValueError("媒体名称长度必须为 1-60 个字符")
        try:
            return self._execute(
                "INSERT INTO news_sources (slug, name, built_in, default_selected) "
                "VALUES (?, ?, 0, 0)",
                (f"custom-{uuid.uuid4().hex}", name),
            )
        except sqlite3.IntegrityError:
            raise ValueError("媒体名称已存在") from None

    def update_news_source(
        self,
        source_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> dict | None:
        current = self.get_news_source(source_id)
        if current is None:
            return None
        sets: list[str] = []
        params: list[object] = []
        if name is not None:
            name = name.strip()
            if not name or len(name) > 60:
                raise ValueError("媒体名称长度必须为 1-60 个字符")
            sets.append("name = ?")
            params.append(name)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not sets:
            return current
        sets.append("updated_at = datetime('now')")
        params.append(source_id)
        try:
            self._execute(
                f"UPDATE news_sources SET {', '.join(sets)} WHERE id = ?", params
            )
        except sqlite3.IntegrityError:
            raise ValueError("媒体名称已存在") from None
        return self.get_news_source(source_id)

    def set_news_source_archived(self, source_id: int, archived: bool) -> None:
        self._execute(
            "UPDATE news_sources SET archived_at = ?, updated_at = datetime('now') WHERE id = ?",
            (datetime.now(UTC).isoformat() if archived else None, source_id),
        )

    def delete_news_source(self, source_id: int) -> dict | None:
        """硬删除媒体并级联清除其文章、用户订阅与全部 Feed。"""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                rows = self._conn.execute(
                    "SELECT * FROM news_sources WHERE id = ?", (source_id,)
                ).fetchall()
                if not rows:
                    self._conn.rollback()
                    return None
                self._conn.execute(
                    "DELETE FROM news_articles WHERE source_id = ?", (source_id,)
                )
                self._conn.execute(
                    "DELETE FROM user_news_sources WHERE source_id = ?", (source_id,)
                )
                self._conn.execute(
                    "DELETE FROM news_feeds WHERE source_id = ?", (source_id,)
                )
                self._conn.execute(
                    "DELETE FROM news_sources WHERE id = ?", (source_id,)
                )
                self._conn.commit()
                return rows[0]
            except Exception:
                self._conn.rollback()
                raise

    def list_news_feeds(
        self, source_id: int | None = None, include_archived: bool = False
    ) -> list[dict]:
        conds: list[str] = []
        params: list[object] = []
        if source_id is not None:
            conds.append("source_id = ?")
            params.append(source_id)
        if not include_archived:
            conds.append("archived_at IS NULL")
        sql = "SELECT * FROM news_feeds"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id"
        return self._rows(sql, params)

    def get_news_feed(self, feed_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM news_feeds WHERE id = ?", (feed_id,))
        return rows[0] if rows else None

    def get_news_feed_by_normalized_url(self, normalized_url: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM news_feeds WHERE normalized_url = ?", (normalized_url,)
        )
        return rows[0] if rows else None

    def add_news_feed(
        self, source_id: int, name: str, url: str, normalized_url: str
    ) -> int:
        name, url, normalized_url = name.strip(), url.strip(), normalized_url.strip()
        if not name or len(name) > 80:
            raise ValueError("Feed 名称长度必须为 1-80 个字符")
        if not url or len(url) > 2048 or not normalized_url:
            raise ValueError("Feed URL 无效")
        try:
            return self._execute(
                "INSERT INTO news_feeds (source_id, name, url, normalized_url) "
                "VALUES (?, ?, ?, ?)",
                (source_id, name, url, normalized_url),
            )
        except sqlite3.IntegrityError as exc:
            if "normalized_url" in str(exc):
                raise ValueError("Feed URL 已存在") from None
            raise ValueError("该媒体下的 Feed 名称已存在") from None

    def update_news_feed(
        self,
        feed_id: int,
        *,
        name: str | None = None,
        url: str | None = None,
        normalized_url: str | None = None,
        enabled: bool | None = None,
    ) -> dict | None:
        current = self.get_news_feed(feed_id)
        if current is None:
            return None
        next_name = current["name"] if name is None else name.strip()
        next_url = current["url"] if url is None else url.strip()
        next_normalized = (
            current["normalized_url"] if normalized_url is None else normalized_url.strip()
        )
        if not next_name or len(next_name) > 80:
            raise ValueError("Feed 名称长度必须为 1-80 个字符")
        if not next_url or len(next_url) > 2048 or not next_normalized:
            raise ValueError("Feed URL 无效")
        url_changed = (
            next_url != current["url"] or next_normalized != current["normalized_url"]
        )
        sets = ["name = ?", "url = ?", "normalized_url = ?"]
        params: list[object] = [next_name, next_url, next_normalized]
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if url_changed:
            sets.extend([
                "etag = ''", "last_modified = ''", "last_attempt_at = NULL",
                "last_success_at = NULL", "last_error_code = ''",
                "last_error_detail = ''", "consecutive_failures = 0",
            ])
        sets.append("updated_at = datetime('now')")
        params.append(feed_id)
        try:
            self._execute(
                f"UPDATE news_feeds SET {', '.join(sets)} WHERE id = ?", params
            )
        except sqlite3.IntegrityError as exc:
            if "normalized_url" in str(exc):
                raise ValueError("Feed URL 已存在") from None
            raise ValueError("该媒体下的 Feed 名称已存在") from None
        return self.get_news_feed(feed_id)

    def set_news_feed_archived(self, feed_id: int, archived: bool) -> None:
        self._execute(
            "UPDATE news_feeds SET archived_at = ?, updated_at = datetime('now') WHERE id = ?",
            (datetime.now(UTC).isoformat() if archived else None, feed_id),
        )

    def list_user_news_source_ids(
        self, user_id: int, include_archived: bool = False
    ) -> list[int]:
        sql = (
            "SELECT u.source_id FROM user_news_sources u "
            "JOIN news_sources s ON s.id = u.source_id WHERE u.user_id = ?"
        )
        params: list[object] = [user_id]
        if not include_archived:
            sql += " AND s.archived_at IS NULL"
        sql += " ORDER BY u.source_id"
        return [row["source_id"] for row in self._rows(sql, params)]

    def set_user_news_sources(self, user_id: int, source_ids: list[int]) -> None:
        ids = list(dict.fromkeys(int(source_id) for source_id in source_ids))
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = self._rows(
                f"SELECT id FROM news_sources WHERE archived_at IS NULL AND id IN ({placeholders})",
                ids,
            )
            if {row["id"] for row in rows} != set(ids):
                raise ValueError("来源不存在或已归档")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "DELETE FROM user_news_sources WHERE user_id = ?", (user_id,)
                )
                for source_id in ids:
                    self._conn.execute(
                        "INSERT INTO user_news_sources (user_id, source_id) VALUES (?, ?)",
                        (user_id, source_id),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def list_due_news_feeds(self, before_iso: str) -> list[dict]:
        return self._rows(
            "SELECT f.*, s.name AS source_name, s.enabled AS source_enabled "
            "FROM news_feeds f JOIN news_sources s ON s.id = f.source_id "
            "WHERE f.enabled = 1 AND f.archived_at IS NULL "
            "AND s.enabled = 1 AND s.archived_at IS NULL "
            "AND (f.last_attempt_at IS NULL OR f.last_attempt_at < ?) ORDER BY f.id",
            (before_iso,),
        )

    def mark_news_feed_attempt(self, feed_id: int, attempted_at: str) -> None:
        self._execute(
            "UPDATE news_feeds SET last_attempt_at = ?, updated_at = datetime('now') WHERE id = ?",
            (attempted_at, feed_id),
        )

    def mark_news_feed_success(
        self,
        feed_id: int,
        *,
        etag: str,
        last_modified: str,
        succeeded_at: str,
    ) -> None:
        self._execute(
            "UPDATE news_feeds SET etag = ?, last_modified = ?, last_success_at = ?, "
            "last_error_code = '', last_error_detail = '', consecutive_failures = 0, "
            "updated_at = datetime('now') WHERE id = ?",
            (etag or "", last_modified or "", succeeded_at, feed_id),
        )

    def mark_news_feed_failure(
        self, feed_id: int, code: str, detail: str, attempted_at: str
    ) -> None:
        self._execute(
            "UPDATE news_feeds SET last_error_code = ?, last_error_detail = ?, "
            "consecutive_failures = consecutive_failures + 1, last_attempt_at = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (code, detail[:300], attempted_at, feed_id),
        )

    def upsert_news_article(self, article: dict) -> int:
        images = article.get("images", [])
        if not isinstance(images, list):
            images = []
        values = (
            article["source_id"], article["feed_id"], article["external_id"],
            article["title"], article["url"], article.get("author", ""),
            article.get("summary", ""), article.get("content_html", ""),
            json.dumps(images, ensure_ascii=False), article["published_at"],
            article["fetched_at"], article.get("content_hash", ""),
        )
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO news_articles "
                    "(source_id, feed_id, external_id, title, url, author, summary, "
                    "content_html, images, published_at, fetched_at, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id, external_id) DO UPDATE SET "
                    "feed_id = excluded.feed_id, title = excluded.title, url = excluded.url, "
                    "author = excluded.author, summary = excluded.summary, "
                    "content_html = excluded.content_html, images = excluded.images, "
                    "published_at = excluded.published_at, fetched_at = excluded.fetched_at, "
                    "content_hash = excluded.content_hash",
                    values,
                )
                row = self._conn.execute(
                    "SELECT id FROM news_articles WHERE source_id = ? AND external_id = ?",
                    (article["source_id"], article["external_id"]),
                ).fetchone()
                self._conn.commit()
                return row["id"]
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _normalize_news_article(row: dict) -> dict:
        raw_images = row.get("images")
        try:
            images = json.loads(raw_images) if isinstance(raw_images, str) else raw_images
        except (TypeError, ValueError):
            images = []
        row["images"] = images if isinstance(images, list) else []
        row["has_image"] = bool(row["images"])
        return row

    def delete_news_articles_older_than(self, days: int) -> int:
        if days < 0:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM news_articles WHERE published_at < ?", (cutoff,)
            )
            self._conn.commit()
            return cur.rowcount

    def _news_article_filter(
        self, user_id: int, source_id: int | None, q: str
    ) -> tuple[str, list[object]]:
        conds = [
            "u.user_id = ?",
            "s.id = a.source_id",
            "s.archived_at IS NULL",
        ]
        params: list[object] = [user_id]
        if source_id is not None:
            conds.append("a.source_id = ?")
            params.append(source_id)
        if q:
            conds.append("(a.title LIKE ? OR a.summary LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        return " AND ".join(conds), params

    def list_news_articles(
        self,
        user_id: int,
        *,
        source_id: int | None,
        q: str,
        limit: int,
        offset: int,
    ) -> list[dict]:
        where, params = self._news_article_filter(user_id, source_id, (q or "").strip())
        params.extend([max(1, min(int(limit), 100)), max(0, int(offset))])
        rows = self._rows(
            "SELECT a.*, s.name AS source_name, s.slug AS source_slug, s.enabled AS source_enabled "
            "FROM news_articles a JOIN user_news_sources u ON u.source_id = a.source_id "
            "JOIN news_sources s ON s.id = a.source_id "
            f"WHERE {where} ORDER BY a.published_at DESC, a.id DESC LIMIT ? OFFSET ?",
            params,
        )
        return [self._normalize_news_article(row) for row in rows]

    def count_news_articles_for_source(self, source_id: int) -> int:
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM news_articles WHERE source_id = ?", (source_id,)
        )
        return _to_int(rows[0]["n"]) if rows else 0

    def count_news_articles(self, user_id: int, *, source_id: int | None, q: str) -> int:
        where, params = self._news_article_filter(user_id, source_id, (q or "").strip())
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM news_articles a "
            "JOIN user_news_sources u ON u.source_id = a.source_id "
            "JOIN news_sources s ON s.id = a.source_id "
            f"WHERE {where}",
            params,
        )
        return _to_int(rows[0]["n"]) if rows else 0

    def get_news_article(
        self, article_id: int, user_id: int | None = None
    ) -> dict | None:
        sql = (
            "SELECT a.*, s.name AS source_name, s.slug AS source_slug, "
            "s.enabled AS source_enabled FROM news_articles a "
            "JOIN news_sources s ON s.id = a.source_id "
            "WHERE a.id = ? AND s.archived_at IS NULL"
        )
        params: list[object] = [article_id]
        if user_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM user_news_sources u WHERE u.user_id = ? AND u.source_id = a.source_id)"
            params.append(user_id)
        rows = self._rows(sql, params)
        return self._normalize_news_article(rows[0]) if rows else None

    def advance_news_seen(self, user_id: int, view_started_at: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET news_last_seen_at = ? WHERE id = ? "
                "AND (news_last_seen_at IS NULL OR news_last_seen_at < ?)",
                (view_started_at, user_id, view_started_at),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def news_source_statuses(self, user_id: int) -> list[dict]:
        statuses = []
        for source_id in self.list_user_news_source_ids(user_id):
            source = self.get_news_source(source_id)
            feeds = self.list_news_feeds(source_id)
            enabled_feeds = [feed for feed in feeds if feed["enabled"]]
            successes = [feed["last_success_at"] for feed in enabled_feeds if feed["last_success_at"]]
            if not source["enabled"] or not enabled_feeds:
                code = "paused"
            elif any(feed["consecutive_failures"] > 0 and not feed["last_success_at"] for feed in enabled_feeds):
                code = "unavailable"
            elif any(feed["consecutive_failures"] > 0 for feed in enabled_feeds):
                code = "delayed"
            else:
                code = "ok"
            statuses.append({
                "id": source_id,
                "code": code,
                "last_success_at": max(successes) if successes else None,
            })
        return statuses

    # ---- Subscription ----
    def add_subscription(self, user_id: int, kol_id: int, type: str = "post") -> bool:
        try:
            self._execute(
                "INSERT INTO subscriptions (user_id, kol_id, type) VALUES (?, ?, ?)",
                (user_id, kol_id, type),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_subscription_type(self, user_id: int, kol_id: int, type: str) -> bool:
        """切换订阅类型：post / reply / both。"""
        if type not in ("post", "reply", "both"):
            raise ValueError(f"无效的订阅类型: {type}")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE subscriptions SET type = ? WHERE user_id = ? AND kol_id = ?",
                (type, user_id, kol_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def remove_subscription(self, user_id: int, kol_id: int) -> None:
        self._execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND kol_id = ?",
            (user_id, kol_id),
        )

    def list_subscriptions(self, user_id: int) -> list[dict]:
        rows = self._rows(
            "SELECT k.*, s.type AS subscribe_type, s.favorite AS favorite, "
            "s.secondary AS sub_secondary, s.hide_images AS hide_images, "
            "c.name AS category_name, "
            "s.created_at AS subscribed_at "
            "FROM subscriptions s JOIN kols k ON k.id = s.kol_id "
            "LEFT JOIN categories c ON c.id = k.category_id "
            "WHERE s.user_id = ? ORDER BY s.id",
            (user_id,),
        )
        # kols 表也有 secondary（全局次要）列，k.* 会与之同名冲突且 dict(row) 取到全局值；
        # 用别名带回个人次要（覆盖全局列）
        for r in rows:
            r["secondary"] = r.pop("sub_secondary")
        return rows

    def count_subscriptions(self, user_id: int) -> int:
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        return rows[0]["n"]

    def dashboard_stats(self) -> dict:
        """业务数据看板聚合：用户/订阅/帖子/推送/数据源健康（近 N 天窗口用 UTC）。"""
        def scalar(sql: str, *params) -> int:
            return _to_int((self._rows(sql, params) or [{}])[0].get("v"))

        users = {
            "total": scalar("SELECT COUNT(*) AS v FROM users"),
            "admins": scalar("SELECT COUNT(*) AS v FROM users WHERE is_admin = 1"),
            "bound": scalar(
                "SELECT COUNT(*) AS v FROM users WHERE " + _user_has_channel_sql("users")
            ),
            "new_7d": scalar(
                "SELECT COUNT(*) AS v FROM users WHERE created_at >= datetime('now', '-7 days')"
            ),
        }
        subs_total = scalar("SELECT COUNT(*) AS v FROM subscriptions")
        subscriptions = {
            "total": subs_total,
            "favorite": scalar("SELECT COUNT(*) AS v FROM subscriptions WHERE favorite = 1"),
            "avg_per_user": round(subs_total / users["total"], 1) if users["total"] else 0,
        }
        posts = {
            "total": scalar("SELECT COUNT(*) AS v FROM posts"),
            "today": scalar(
                "SELECT COUNT(*) AS v FROM posts WHERE fetched_at >= datetime('now', '-24 hours')"
            ),
            "last_7d": scalar(
                "SELECT COUNT(*) AS v FROM posts WHERE fetched_at >= datetime('now', '-7 days')"
            ),
            "by_platform": {
                r["platform"]: _to_int(r["c"])
                for r in self._rows(
                    "SELECT platform, COUNT(*) AS c FROM posts GROUP BY platform ORDER BY c DESC"
                )
            },
        }
        push_ok = scalar(
            "SELECT COUNT(*) AS v FROM push_logs WHERE status = 'success' "
            "AND created_at >= datetime('now', '-7 days')"
        )
        push_total = scalar(
            "SELECT COUNT(*) AS v FROM push_logs WHERE created_at >= datetime('now', '-7 days')"
        )
        pushes = {
            "total_7d": push_total,
            "ok_7d": push_ok,
            "fail_7d": push_total - push_ok,
            # 无推送时置 None，前端显示 "—"，避免误导性的绿色 100%
            "success_rate": round(push_ok / push_total * 100, 1) if push_total else None,
            "today": scalar(
                "SELECT COUNT(*) AS v FROM push_logs WHERE created_at >= datetime('now', '-24 hours')"
            ),
            "by_channel": {
                r["channel"]: {"total": _to_int(r["c"]), "ok": _to_int(r["ok"])}
                for r in self._rows(
                    "SELECT channel, COUNT(*) AS c, "
                    "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok "
                    "FROM push_logs WHERE created_at >= datetime('now', '-7 days') "
                    "GROUP BY channel ORDER BY c DESC"
                )
            },
            "trend_14d": [
                {"date": r["d"], "pushed": _to_int(r["c"]), "ok": _to_int(r["ok"])}
                for r in self._rows(
                    # 按本地时间分桶显示，与看板事件流一致；窗口边界与其他统计统一用 UTC
                    "SELECT strftime('%Y-%m-%d', created_at, 'localtime') AS d, COUNT(*) AS c, "
                    "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok "
                    "FROM push_logs WHERE created_at >= datetime('now', '-13 days') "
                    "GROUP BY d ORDER BY d"
                )
            ],
        }
        sources_fail_24h = {
            r["platform"]: _to_int(r["c"])
            for r in self._rows(
                "SELECT platform, COUNT(*) AS c FROM source_events "
                "WHERE status != 'ok' AND created_at >= datetime('now', '-24 hours') "
                "GROUP BY platform"
            )
        }
        return {
            "users": users,
            "subscriptions": subscriptions,
            "posts": posts,
            "pushes": pushes,
            "sources_fail_24h": sources_fail_24h,
        }

    def subscribed_kol_ids(self, user_id: int) -> set[int]:
        rows = self._rows("SELECT kol_id FROM subscriptions WHERE user_id = ?", (user_id,))
        return {row["kol_id"] for row in rows}

    def kol_ids_with_subscribers(self) -> set[int]:
        """当前有任何订阅关系的大V id 集合（含关闭通知的订阅者）。

        抓取调度用它跳过无人订阅的大V——没有订阅者就没有推送/阅读对象，
        不值得每轮白耗抓取配额。
        """
        rows = self._rows("SELECT DISTINCT kol_id FROM subscriptions")
        return {row["kol_id"] for row in rows}

    def readable_subscribed_kol_ids(self, user_id: int, is_admin: bool = False) -> set[int]:
        """用户可读的已订阅大V集合：订阅集合 ∩ 可见集合（公开 + ACL 私有大V）。

        内容读取（动态/每日精选）统一走该集合，权限判断必须在后端完成；
        管理员保留对已订阅私有大V的管理访问语义，不做可见性过滤。
        """
        subscribed = self.subscribed_kol_ids(user_id)
        if is_admin:
            return subscribed
        return subscribed & self.visible_kol_ids(user_id)

    def subscribed_platforms(self, user_id: int, is_admin: bool = False) -> set[str]:
        ids = self.readable_subscribed_kol_ids(user_id, is_admin)
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        rows = self._rows(
            f"SELECT DISTINCT platform FROM kols WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return {row["platform"] for row in rows}

    def subscribed_kol_types(self, user_id: int) -> dict[int, str]:
        rows = self._rows(
            "SELECT kol_id, type FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        return {row["kol_id"]: row["type"] for row in rows}

    def subscribers_of_kol(self, kol_id: int) -> list[dict]:
        """该大V的订阅者（启用通知且绑定了渠道的用户）。"""
        return self._rows(
            "SELECT u.*, s.type AS subscribe_type, s.favorite AS favorite, "
            "s.secondary AS secondary, s.hide_images AS hide_images FROM subscriptions s "
            "JOIN users u ON u.id = s.user_id "
            "JOIN kols k ON k.id = s.kol_id "
            "WHERE s.kol_id = ? AND u.notify_enabled = 1 "
            f"AND {_user_has_channel_sql('u')} "
            "AND (k.is_private = 0 OR EXISTS "
            "(SELECT 1 FROM kol_acl a WHERE a.kol_id = k.id AND a.user_id = u.id))",
            (kol_id,),
        )

    def get_subscription(self, user_id: int, kol_id: int) -> dict | None:
        """单个订阅记录（type/favorite/secondary/hide_images），未订阅返回 None。"""
        rows = self._rows(
            "SELECT type, favorite, secondary, hide_images FROM subscriptions "
            "WHERE user_id = ? AND kol_id = ?",
            (user_id, kol_id),
        )
        return rows[0] if rows else None

    def set_subscription_favorite(self, user_id: int, kol_id: int, favorite: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE subscriptions SET favorite = ? WHERE user_id = ? AND kol_id = ?",
                (1 if favorite else 0, user_id, kol_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def subscribed_favorite_ids(self, user_id: int) -> set[int]:
        rows = self._rows(
            "SELECT kol_id FROM subscriptions WHERE user_id = ? AND favorite = 1",
            (user_id,),
        )
        return {row["kol_id"] for row in rows}

    def set_subscription_secondary(self, user_id: int, kol_id: int, secondary: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE subscriptions SET secondary = ? WHERE user_id = ? AND kol_id = ?",
                (1 if secondary else 0, user_id, kol_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_subscription_hide_images(self, user_id: int, kol_id: int, hide_images: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE subscriptions SET hide_images = ? WHERE user_id = ? AND kol_id = ?",
                (1 if hide_images else 0, user_id, kol_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def subscribed_secondary_ids(self, user_id: int) -> set[int]:
        rows = self._rows(
            "SELECT kol_id FROM subscriptions WHERE user_id = ? AND secondary = 1",
            (user_id,),
        )
        return {row["kol_id"] for row in rows}

    def get_users_keywords(self, user_ids: list[int]) -> dict[int, list[str]]:
        """一次取出多名用户的关键词。"""
        if not user_ids:
            return {}
        out: dict[int, list[str]] = {int(uid): [] for uid in user_ids}
        placeholders = ", ".join("?" * len(user_ids))
        for row in self._rows(
            f"SELECT user_id, keyword FROM user_keywords WHERE user_id IN ({placeholders}) "
            "ORDER BY rowid",
            tuple(user_ids),
        ):
            out.setdefault(int(row["user_id"]), []).append(row["keyword"])
        return out

    def get_user_keywords(self, user_id: int) -> list[str]:
        """用户的关键词提醒规则（命中即穿透免打扰并加急推送）。"""
        return self.get_users_keywords([user_id]).get(int(user_id), [])

    def list_knowledge_keyword_users(self) -> list[dict]:
        return self._rows(
            "SELECT * FROM users WHERE notify_enabled = 1 AND keywords_match_reports = 1 "
            "ORDER BY id"
        )

    def list_recent_ima_documents(self, since: str, limit: int = 400) -> list[dict]:
        since = str(since or "").strip()
        if not since:
            return []
        cap = max(1, min(int(limit), 800))
        rows = self._read_only_rows(
            "SELECT * FROM ima_document_index WHERE downloaded_at >= ? "
            "ORDER BY downloaded_at DESC, sort_date DESC LIMIT ?",
            (since, cap),
        )
        return [_ima_public_document(row) for row in rows]

    def filter_unnotified_knowledge_docs(self, user_id: int, docs: list[dict]) -> list[dict]:
        if not docs:
            return []
        keys = [
            (str(doc.get("group_id") or ""), str(doc.get("media_id") or ""))
            for doc in docs
        ]
        keys = [(g, m) for g, m in keys if g and m]
        if not keys:
            return []
        media_ids = list({media_id for _, media_id in keys})
        placeholders = ", ".join("?" * len(media_ids))
        seen = {
            (str(row["group_id"]), str(row["media_id"]))
            for row in self._rows(
                "SELECT group_id, media_id FROM knowledge_keyword_notified "
                f"WHERE user_id = ? AND media_id IN ({placeholders})",
                (int(user_id), *media_ids),
            )
        }
        out = []
        for doc in docs:
            key = (str(doc.get("group_id") or ""), str(doc.get("media_id") or ""))
            if key[0] and key[1] and key not in seen:
                out.append(doc)
        return out

    def mark_knowledge_keyword_notified(self, user_id: int, docs: list[dict]) -> None:
        rows = []
        seen: set[tuple[str, str]] = set()
        for doc in docs:
            group_id = str(doc.get("group_id") or "")
            media_id = str(doc.get("media_id") or "")
            if not group_id or not media_id or (group_id, media_id) in seen:
                continue
            seen.add((group_id, media_id))
            rows.append((int(user_id), group_id, media_id))
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO knowledge_keyword_notified "
                "(user_id, group_id, media_id) VALUES (?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def set_user_keywords(self, user_id: int, keywords: list[str]) -> None:
        # 先去重保序：user_keywords 有 UNIQUE(user_id, keyword)，重复值会让
        # 中途 INSERT 失败留下悬空事务
        keywords = list(dict.fromkeys(keywords))
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM user_keywords WHERE user_id = ?", (user_id,))
                for keyword in keywords:
                    self._conn.execute(
                        "INSERT INTO user_keywords (user_id, keyword) VALUES (?, ?)",
                        (user_id, keyword),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 绑定码 ----
    def create_bind_code(self, code: str, user_id: int, expires_at: int) -> None:
        digest = _bind_code_digest(code)
        if not digest:
            raise ValueError("绑定码无效")
        self._execute(
            "INSERT INTO bind_codes (code, user_id, expires_at) VALUES (?, ?, ?)",
            (digest, user_id, expires_at),
        )

    def get_bind_code(self, code: str) -> dict | None:
        digest = _bind_code_digest(code)
        if not digest:
            return None
        rows = self._rows("SELECT * FROM bind_codes WHERE code = ?", (digest,))
        return rows[0] if rows else None

    def delete_bind_code(self, code: str) -> None:
        digest = _bind_code_digest(code)
        if not digest:
            return
        self._execute("DELETE FROM bind_codes WHERE code = ?", (digest,))

    def delete_expired_bind_codes(self) -> None:
        self._execute("DELETE FROM bind_codes WHERE expires_at < ?", (int(time.time()),))

    def consume_bind_quota(
        self, key: str, period_start: int, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        period_start = int(period_start)
        limit = max(int(limit), 1)
        window_seconds = max(int(window_seconds), 1)
        retry = max(period_start + window_seconds - int(time.time()), 1)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT period_start, count FROM bind_quota WHERE key = ?", (key,)
                ).fetchone()
                if row is None or int(row["period_start"]) != period_start:
                    self._conn.execute(
                        "INSERT INTO bind_quota (key, period_start, count) VALUES (?, ?, 1) "
                        "ON CONFLICT(key) DO UPDATE SET period_start = excluded.period_start, "
                        "count = excluded.count",
                        (key, period_start),
                    )
                    self._conn.commit()
                    return True, 0
                count = int(row["count"])
                if count >= limit:
                    self._conn.commit()
                    return False, retry
                self._conn.execute(
                    "UPDATE bind_quota SET count = count + 1 WHERE key = ?", (key,)
                )
                self._conn.commit()
                return True, 0
            except Exception:
                self._conn.rollback()
                raise

    def get_quota_count(self, key: str, period_start: int) -> int:
        rows = self._rows(
            "SELECT period_start, count FROM bind_quota WHERE key = ?", (key,)
        )
        if not rows or int(rows[0]["period_start"]) != int(period_start):
            return 0
        return int(rows[0]["count"])

    def clear_quota(self, key: str) -> None:
        self._execute("DELETE FROM bind_quota WHERE key = ?", (key,))

    def consume_bind_code(self, code: str, identity_type: str, identity: str) -> dict | None:
        field = (
            "telegram_chat_id"
            if identity_type == "telegram_chat_id"
            else "feishu_open_id" if identity_type == "feishu_open_id" else ""
        )
        if not field:
            return None
        now = int(time.time())
        digest = _bind_code_digest(code)
        if not digest:
            return None
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                claimed = self._conn.execute(
                    "DELETE FROM bind_codes WHERE code = ? AND expires_at >= ? RETURNING user_id",
                    (digest, now),
                ).fetchone()
                if claimed is None:
                    self._conn.commit()
                    return None
                target_id = int(claimed["user_id"])
                target = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (target_id,)
                ).fetchone()
                if target is None:
                    self._conn.commit()
                    return None
                existing = self._conn.execute(
                    f"SELECT * FROM users WHERE {field} = ?", (identity,)
                ).fetchone()
                if existing is not None and int(existing["id"]) != target_id:
                    self._transfer_subscriptions_body(int(existing["id"]), target_id)
                    self._delete_user_body(int(existing["id"]))
                self._conn.execute(
                    f"UPDATE users SET {field} = ? WHERE id = ?", (identity, target_id)
                )
                self._conn.commit()
                user = self._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (target_id,)
                ).fetchone()
                return dict(user) if user else None
            except Exception:
                self._conn.rollback()
                raise

    # ---- 账号合并 ----
    def _transfer_subscriptions_body(self, from_user_id: int, to_user_id: int) -> None:
        rows = self._conn.execute(
            "SELECT kol_id, type, favorite, secondary, hide_images "
            "FROM subscriptions WHERE user_id = ?",
            (from_user_id,),
        ).fetchall()
        for row in rows:
            existing = self._conn.execute(
                "SELECT type, favorite, secondary, hide_images FROM subscriptions "
                "WHERE user_id = ? AND kol_id = ?",
                (to_user_id, row["kol_id"]),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO subscriptions "
                    "(user_id, kol_id, type, favorite, secondary, hide_images) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        to_user_id,
                        row["kol_id"],
                        row["type"] or "post",
                        row["favorite"],
                        row["secondary"],
                        row["hide_images"],
                    ),
                )
            else:
                merged = _merge_sub_types(row["type"], existing["type"])
                favorite = 1 if (row["favorite"] or existing["favorite"]) else 0
                secondary = max(row["secondary"], existing["secondary"])
                hide_images = max(row["hide_images"], existing["hide_images"])
                self._conn.execute(
                    "UPDATE subscriptions SET type = ?, favorite = ?, secondary = ?, "
                    "hide_images = ? WHERE user_id = ? AND kol_id = ?",
                    (
                        merged,
                        favorite,
                        secondary,
                        hide_images,
                        to_user_id,
                        row["kol_id"],
                    ),
                )
        self._conn.execute(
            "INSERT OR IGNORE INTO user_news_sources (user_id, source_id, selected_at) "
            "SELECT ?, source_id, selected_at FROM user_news_sources WHERE user_id = ?",
            (to_user_id, from_user_id),
        )
        source_anchor = self._conn.execute(
            "SELECT news_last_seen_at FROM users WHERE id = ?", (from_user_id,)
        ).fetchone()["news_last_seen_at"]
        target_anchor = self._conn.execute(
            "SELECT news_last_seen_at FROM users WHERE id = ?", (to_user_id,)
        ).fetchone()["news_last_seen_at"]
        if source_anchor and (not target_anchor or source_anchor > target_anchor):
            self._conn.execute(
                "UPDATE users SET news_last_seen_at = ? WHERE id = ?",
                (source_anchor, to_user_id),
            )
        self._conn.execute(
            "DELETE FROM user_news_sources WHERE user_id = ?", (from_user_id,)
        )
        self._conn.execute(
            "DELETE FROM subscriptions WHERE user_id = ?", (from_user_id,)
        )

    def transfer_subscriptions(self, from_user_id: int, to_user_id: int) -> None:
        """把源账号的订阅合并到目标账号；同一大V保留更全的订阅类型。

        用于机器人账号绑定网页账号后的合并，避免「回复/帖子+回复」被降级成「帖子」。
        """
        if from_user_id == to_user_id:
            return
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._transfer_subscriptions_body(from_user_id, to_user_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _delete_user_body(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM bind_codes WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM user_news_sources WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM push_logs WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM kol_acl WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM ima_kb_acl WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM ima_kb_subscriptions WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM webpush_subscriptions WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM android_devices WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM user_keywords WHERE user_id = ?", (user_id,))
        self._conn.execute(
            "DELETE FROM knowledge_keyword_notified WHERE user_id = ?", (user_id,)
        )
        self._conn.execute("DELETE FROM feishu_personal_bots WHERE user_id = ?", (user_id,))
        self._conn.execute(
            "DELETE FROM feishu_registration_sessions WHERE user_id = ?", (user_id,)
        )
        self._conn.execute(
            "DELETE FROM daily_report_deliveries WHERE user_id = ?", (user_id,)
        )
        self._conn.execute("DELETE FROM kol_requests WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def delete_user(self, user_id: int) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._delete_user_body(user_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 雪球组合快照 ----
    def set_cube_snapshot(self, kol_id: int, kind: str, payload) -> None:
        """写入/覆盖组合快照（quote/holdings/nav），刷新 fetched_at。"""
        self._execute(
            "INSERT INTO cube_snapshots (kol_id, kind, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(kol_id, kind) DO UPDATE SET "
            "payload = excluded.payload, fetched_at = datetime('now')",
            (kol_id, kind, json.dumps(payload, ensure_ascii=False)),
        )

    def get_cube_snapshot(self, kol_id: int, kind: str) -> dict | None:
        """读组合快照：{"payload": 已解析 JSON, "fetched_at": "YYYY-MM-DD HH:MM:SS"}。"""
        rows = self._rows(
            "SELECT payload, fetched_at FROM cube_snapshots WHERE kol_id = ? AND kind = ?",
            (kol_id, kind),
        )
        if not rows:
            return None
        try:
            payload = json.loads(rows[0]["payload"])
        except (TypeError, ValueError):
            return None
        return {"payload": payload, "fetched_at": rows[0]["fetched_at"]}

    def cube_snapshot_fresh(self, kol_id: int, kind: str, ttl_seconds: int) -> bool:
        """快照是否在 TTL 内（fetcher 据此决定要不要重新请求雪球）。"""
        rows = self._rows(
            "SELECT 1 FROM cube_snapshots WHERE kol_id = ? AND kind = ? "
            "AND CAST(strftime('%s', fetched_at) AS INTEGER) >= strftime('%s', 'now') - ?",
            (kol_id, kind, ttl_seconds),
        )
        return bool(rows)

    # ---- Post ----
    def post_exists(self, platform: str, external_id: str) -> bool:
        rows = self._rows(
            "SELECT id FROM posts WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        )
        return bool(rows)

    def get_post_id(self, platform: str, external_id: str) -> int | None:
        rows = self._rows(
            "SELECT id FROM posts WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        )
        return rows[0]["id"] if rows else None

    def existing_post_keys(self, pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
        """一次查出已存在的 (platform, external_id)。"""
        found: set[tuple[str, str]] = set()
        if not pairs:
            return found
        chunk = 200
        for i in range(0, len(pairs), chunk):
            part = pairs[i : i + chunk]
            # row-value IN 让规划器稳定走 UNIQUE(platform, external_id)，
            # 200 对 OR 写法会退化成全表扫（慢日志高频项）
            values = ", ".join("(?, ?)" for _ in part)
            params = [x for pair in part for x in pair]
            for row in self._rows(
                f"SELECT platform, external_id FROM posts "
                f"WHERE (platform, external_id) IN (VALUES {values})",
                params,
            ):
                found.add((row["platform"], row["external_id"]))
        return found

    def get_post_detail(self, platform: str, external_id: str) -> dict:
        """读单帖 detail JSON；不存在或解析失败返回空 dict。"""
        rows = self._rows(
            "SELECT detail FROM posts WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        )
        if not rows:
            return {}
        raw = rows[0].get("detail") or ""
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except (TypeError, ValueError):
            return {}

    def find_zsxq_file_posts(self, file_id: str) -> list[dict]:
        """按 file_id 找星球帖（精确 JSON 边界，避免 123 命中 1234）。"""
        fid = str(file_id or "").strip()
        if not fid:
            return []
        rows = self._rows(
            "SELECT id, kol_id, detail FROM posts WHERE platform = 'zsxq' AND "
            "(detail LIKE ? OR detail LIKE ? OR detail LIKE ?)",
            (f'%"file_id": "{fid}"%', f'%"file_id":"{fid}"%', f'%"file_id": {fid}%'),
        )
        hits = []
        for row in rows:
            raw = row.get("detail") or ""
            try:
                detail = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            files = (detail or {}).get("files") if isinstance(detail, dict) else []
            if any(str(f.get("file_id")) == fid for f in (files or []) if isinstance(f, dict)):
                hits.append(row)
        return hits

    def update_post_details(self, updates: list[tuple[int, dict]]) -> None:
        if not updates:
            return
        with self._lock:
            for post_id, detail in updates:
                self._conn.execute(
                    "UPDATE posts SET detail = ? WHERE id = ?",
                    (json.dumps(detail, ensure_ascii=False), post_id),
                )
            self._conn.commit()

    def list_cube_snapshots(self, kol_ids: list[int], kind: str) -> dict[int, dict]:
        if not kol_ids:
            return {}
        placeholders = ", ".join("?" * len(kol_ids))
        rows = self._rows(
            f"SELECT kol_id, payload, fetched_at FROM cube_snapshots "
            f"WHERE kind = ? AND kol_id IN ({placeholders})",
            (kind, *kol_ids),
        )
        out = {}
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = None
            out[row["kol_id"]] = {"payload": payload, "fetched_at": row.get("fetched_at") or ""}
        return out

    def mark_kol_baseline(self, kol_id: int) -> None:
        """标记该大V已建立首次抓取基线（首次成功 fetch 后调用，含空列表）。"""
        self._execute("UPDATE kols SET baseline_ready = 1 WHERE id = ?", (kol_id,))

    def max_published_at(self, kol_id: int) -> str:
        rows = self._rows(
            "SELECT MAX(published_at) AS m FROM posts WHERE kol_id = ? AND published_at != ''",
            (kol_id,),
        )
        return str(rows[0]["m"] or "") if rows else ""

    def get_post(self, post_id: int) -> dict | None:
        rows = self._rows(
            "SELECT p.*, k.name AS kol_name, k.platform AS kol_platform "
            "FROM posts p JOIN kols k ON k.id = p.kol_id WHERE p.id = ?",
            (post_id,),
        )
        return rows[0] if rows else None

    def insert_post(
        self,
        platform,
        kol_id,
        external_id,
        title,
        content,
        url,
        published_at,
        post_type: str = "",
        detail: dict | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
        title_src: str = "",
        content_src: str = "",
    ) -> int | None:
        detail_json = _detail_json(detail)
        images_json = json.dumps(images, ensure_ascii=False) if images else ""
        # None=未打标（pending，待回填）；[]=已处理但零命中（也持久化为 '[]'，避免重复回填）
        tags_json = json.dumps(tags, ensure_ascii=False) if tags is not None else ""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO posts (platform, kol_id, external_id, title, content, title_src, content_src, post_type, images, url, published_at, detail, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        platform,
                        kol_id,
                        external_id,
                        to_simplified(title),
                        to_simplified(content),
                        title_src,
                        content_src,
                        post_type,
                        images_json,
                        url,
                        published_at,
                        detail_json,
                        tags_json,
                    ),
                )
                # 无论是否命中唯一约束都提交：忽略插入同样会打开隐式事务，
                # 提前 return 不提交会把悬空事务留给下一个 BEGIN（事务嵌套报错）
                if cur.rowcount:
                    self._conn.execute(
                        "UPDATE kols SET last_post_at = datetime('now') WHERE id = ?",
                        (kol_id,),
                    )
                self._conn.commit()
                if cur.rowcount == 0:
                    return None  # 唯一约束命中，帖子已存在
                return cur.lastrowid
        except sqlite3.IntegrityError:
            # 并发下重复插入，视为已存在；回滚关闭隐式事务，避免悬空事务污染后续 BEGIN
            self._conn.rollback()
            return None

    def insert_posts_batch(self, posts) -> list[int | None]:
        """一个事务批量插入帖子，返回与入参对齐的 id 列表（已存在为 None）。"""
        if not posts:
            return []
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                ids: list[int | None] = []
                for p in posts:
                    # 写回对象，后面 notify/digest 用的是同一份 Post，不能只在 SQL 里转
                    p.title = to_simplified(p.title)
                    p.content = to_simplified(p.content)
                    detail_json = _detail_json(p.detail)
                    images_json = json.dumps(p.images, ensure_ascii=False) if p.images else ""
                    tags_json = (
                        json.dumps(p.tags, ensure_ascii=False)
                        if p.tags is not None
                        else ""
                    )
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO posts (platform, kol_id, external_id, title, content, title_src, content_src, post_type, images, url, published_at, detail, tags) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            p.platform,
                            p.kol_id,
                            p.external_id,
                            to_simplified(p.title),
                            to_simplified(p.content),
                            p.title_src or "",
                            p.content_src or "",
                            p.post_type,
                            images_json,
                            p.url,
                            p.published_at,
                            detail_json,
                            tags_json,
                        ),
                    )
                    if cur.rowcount:
                        ids.append(cur.lastrowid)
                    else:
                        ids.append(None)
                        # 星球附件/图片签名 URL 会过期，已存在帖回写最新 files+images
                        if p.platform == "zsxq":
                            self._conn.execute(
                                "UPDATE posts SET images = ?, detail = ? WHERE platform = ? AND external_id = ?",
                                (images_json, detail_json, p.platform, p.external_id),
                            )
                touched = {p.kol_id for p, pid in zip(posts, ids) if pid is not None}
                for kid in touched:
                    self._conn.execute(
                        "UPDATE kols SET last_post_at = datetime('now') WHERE id = ?",
                        (kid,),
                    )
                self._conn.commit()
                return ids
            except Exception:
                self._conn.rollback()
                raise

    def list_posts(
        self,
        limit: int = 100,
        platform: str | None = None,
        kol_id: int | None = None,
        q: str | None = None,
        offset: int = 0,
        untagged_only: bool = False,
        below_id: int | None = None,
        order_published: bool = False,
    ) -> list[dict]:
        sql = (
            "SELECT p.*, k.name AS kol_name, k.category_id AS category_id, "
            "k.avatar_url AS avatar_url, k.external_id AS kol_external_id, "
            "c.name AS category_name FROM posts p "
            "JOIN kols k ON k.id = p.kol_id "
            "LEFT JOIN categories c ON c.id = k.category_id"
        )
        conds, params = [], []
        if platform:
            conds.append("p.platform = ?")
            params.append(platform)
        if kol_id:
            conds.append("p.kol_id = ?")
            params.append(kol_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append("(p.title LIKE ? ESCAPE '\\' OR p.content LIKE ? ESCAPE '\\')")
            like = f"%{escaped}%"
            params.extend([like, like])
        if untagged_only:
            # 直接过滤未打标帖（tags 为空串），避免先取全量再在 Python 里过滤导致
            # 「最新 N 条都已打标」时回填数量恒为 0
            conds.append("(p.tags IS NULL OR p.tags = '')")
        if below_id is not None:
            # id 游标：只取该 id 之下的帖（配合 ORDER BY id DESC 实现一次扫描的分页回填）
            conds.append("p.id < ?")
            params.append(below_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        # below_id 游标（打标回填）依赖 id 序，默认保持不变；用户可见列表用发布时间序，
        # 避免首见入库的旧帖顶着最前
        order = "p.published_at DESC, p.id DESC" if order_published else "p.id DESC"
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return _sanitize_post_detail(
            _normalize_post_tags(_normalize_post_images(self._rows(sql, params), db=self))
        )

    def count_posts(self) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM posts")
        return rows[0]["n"]

    def max_external_id_num(self, platform: str) -> int:
        """平台内最大数字 external_id（Truth 存档按 id 增量拉取）。无帖返回 0。"""
        rows = self._rows(
            "SELECT MAX(CAST(external_id AS INTEGER)) AS m FROM posts WHERE platform = ?",
            (platform,),
        )
        return int(rows[0]["m"] or 0)

    def delete_push_logs_older_than(self, days: int) -> int:
        """删除超过 N 天的推送日志，返回删除条数（帖子保留期之外的独立清理）。"""
        if days <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM push_logs WHERE created_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            self._conn.commit()
            return cur.rowcount

    def delete_posts_older_than(self, days: int, batch_size: int = 500) -> int:
        """删除超过 N 天的帖子及其推送记录，返回删除条数。

        分批（默认每批 500）避免过期帖子量大时 IN (...) 触达 SQLite 变量上限。
        """
        if days <= 0:
            return 0
        removed = 0
        while True:
            rows = self._rows(
                "SELECT id FROM posts WHERE fetched_at < datetime('now', ?) "
                "ORDER BY id LIMIT ?",
                (f"-{days} days", batch_size),
            )
            ids = [row["id"] for row in rows]
            if not ids:
                break
            placeholders = ", ".join("?" * len(ids))
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    self._conn.execute(
                        f"DELETE FROM push_logs WHERE post_id IN ({placeholders})", ids
                    )
                    self._conn.execute(
                        f"DELETE FROM posts WHERE id IN ({placeholders})", ids
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
            removed += len(ids)
        return removed

    def list_feed_posts(
        self,
        kol_ids: list[int],
        limit: int = 100,
        user_id: int | None = None,
        offset: int = 0,
        platform: str | None = None,
        category_id: int | None = None,
        q: str | None = None,
        favorite: bool = False,
        tag: str | None = None,
        include_secondary: bool = False,
        since_id: int | None = None,
        exclude_platforms: list[str] | None = None,
    ) -> list[dict]:
        if not kol_ids:
            return []
        hidden = [p for p in (exclude_platforms or []) if p]
        if platform and hidden and platform in hidden:
            return []
        placeholders = ", ".join("?" * len(kol_ids))
        conds = [f"p.kol_id IN ({placeholders})"]
        params: list = [user_id, *kol_ids]
        if not include_secondary and not platform:
            # 默认隐藏次要大V的动态（全局 kols.secondary 或个人订阅 secondary）：
            # 避免连珠炮式发言刷屏时间线；特别关注（favorite）穿透始终显示
            # 点平台角标时不隐藏次要，否则高频星球角标会空
            conds.append(
                "(s.favorite = 1 OR (COALESCE(k.secondary, 0) = 0 AND COALESCE(s.secondary, 0) = 0))"
            )
        if platform:
            conds.append("p.platform = ?")
            params.append(platform)
        elif hidden:
            hide_ph = ", ".join("?" * len(hidden))
            conds.append(f"p.platform NOT IN ({hide_ph})")
            params.extend(hidden)
        if category_id:
            conds.append("k.category_id = ?")
            params.append(category_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append("(p.title LIKE ? ESCAPE '\\' OR p.content LIKE ? ESCAPE '\\')")
            like = f"%{escaped}%"
            params.extend([like, like])
        if tag:
            # tags 列存 JSON 数组文本，按 JSON 编码后的元素边界匹配（%"标签"%），
            # 避免「宏观」误中「宏观经济」；标签含引号/反斜杠时 json.dumps 保证转义一致
            escaped_tag = json.dumps(tag, ensure_ascii=False)[1:-1]
            escaped = escaped_tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append("p.tags LIKE ? ESCAPE '\\'")
            params.append(f'%"{escaped}"%')
        if favorite:
            conds.append("s.favorite = 1")
        if since_id:
            conds.append("p.id > ?")
            params.append(since_id)
            # 新帖检测只认近 48h 内发布的帖：回灌入库的旧帖 id 虽大，不该进「新动态」
            # 胶囊（与推送侧 STALE_HOURS 语义一致）。published_at 是北京时间文本，
            # datetime('now') 是 UTC，先 +8h 对齐再截到分钟
            conds.append(
                "p.published_at >= substr(datetime('now', '+8 hours', '-48 hours'), 1, 16)"
            )
        rows = _sanitize_post_detail(_normalize_post_tags(_normalize_post_images(self._rows(
            "SELECT p.*, k.name AS kol_name, k.category_id AS category_id, "
            "k.avatar_url AS avatar_url, k.external_id AS kol_external_id, "
            "c.name AS category_name, "
            "COALESCE(s.favorite, 0) AS favorite, "
            "COALESCE(s.hide_images, 0) AS _hide_images FROM posts p "
            "JOIN kols k ON k.id = p.kol_id "
            "LEFT JOIN categories c ON c.id = k.category_id "
            "LEFT JOIN subscriptions s ON s.kol_id = p.kol_id AND s.user_id = ? "
            # 按发布时间排序而非入库 id：大V置顶旧帖/解除隐藏/删帖后浮上来的
            # 首见旧帖会拿到最大 id，按 id 排会顶着动态页最前（推送侧有水位线，
            # 动态页只有这一道防线）
            f"WHERE {' AND '.join(conds)} ORDER BY p.published_at DESC, p.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ), db=self)))
        for row in rows:
            if row.pop("_hide_images"):
                row["images"] = []
        return rows

    def list_daily_posts(
        self, kol_ids: list[int], since: str, limit: int = 15, user_id: int | None = None
    ) -> list[dict]:
        """用户订阅大V在 since（北京时间 "YYYY-MM-DD HH:MM"）之后发布的帖，用于每日精选。

        按发布时间而非入库时间筛选：回灌入库的旧帖（fetched_at 是当天）不混进当日
        精选。published_at 与 since 同为北京钟面文本，直接字典序比较——不要经
        strftime（SQLite 会把北京时间当 UTC 解析，窗口漂 8 小时）。
        """
        if not kol_ids:
            return []
        placeholders = ", ".join("?" * len(kol_ids))
        return _sanitize_post_detail(_normalize_post_tags(_normalize_post_images(self._rows(
            "SELECT p.*, k.name AS kol_name, k.avatar_url AS avatar_url, "
            "c.name AS category_name, COALESCE(s.favorite, 0) AS favorite FROM posts p "
            "JOIN kols k ON k.id = p.kol_id "
            "LEFT JOIN categories c ON c.id = k.category_id "
            "LEFT JOIN subscriptions s ON s.kol_id = p.kol_id AND s.user_id = ? "
            f"WHERE p.kol_id IN ({placeholders}) AND p.published_at >= ? "
            "AND COALESCE(k.silent, 0) = 0 "
            "ORDER BY p.published_at DESC, p.id DESC LIMIT ?",
            (user_id, *kol_ids, since, limit),
        ), db=self)))

    def daily_report_users(self) -> list[dict]:
        """开启每日精选、启用通知且绑定过渠道的用户。"""
        return self._rows(
            "SELECT * FROM users WHERE notify_enabled = 1 AND daily_report = 1 "
            f"AND {_user_has_channel_sql()}"
        )

    def active_feishu_personal_user_ids(self) -> set[int]:
        return {
            row["user_id"]
            for row in self._rows(
                "SELECT user_id FROM feishu_personal_bots "
                "WHERE status = 'active' AND chat_id != ''"
            )
        }

    # ---- Push log ----
    def add_push_log(self, post_id: int, channel: str, status: str, error: str = "", user_id: int | None = None) -> int:
        return self._execute(
            "INSERT INTO push_logs (post_id, channel, status, error, user_id) VALUES (?, ?, ?, ?, ?)",
            (post_id, channel, status, redact_secrets(error), user_id),
        )

    # ---- 持久化错误日志（WARNING+，跨重启可查） ----
    ERROR_LOG_KEEP = 5000

    def record_error_log(self, level: str, logger: str, message: str) -> None:
        rowid = self._execute(
            "INSERT INTO error_logs (level, logger, message) VALUES (?, ?, ?)",
            (level.upper(), logger, redact_secrets(message)),
        )
        if rowid and rowid % 50 == 0:
            self._execute(
                "DELETE FROM error_logs WHERE id NOT IN "
                "(SELECT id FROM error_logs ORDER BY id DESC LIMIT ?)",
                (self.ERROR_LOG_KEEP,),
            )

    def list_error_logs(
        self, limit: int = 200, level: str | None = None, q: str | None = None
    ) -> list[dict]:
        conds, params = [], []
        if level:
            min_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}[
                level.upper()
            ]
            conds.append(
                "CASE level WHEN 'DEBUG' THEN 10 WHEN 'INFO' THEN 20 WHEN 'WARNING' THEN 30 "
                "WHEN 'ERROR' THEN 40 WHEN 'CRITICAL' THEN 50 ELSE 0 END >= ?"
            )
            params.append(min_rank)
        if q:
            conds.append("(logger LIKE ? OR message LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return self._rows(
            f"SELECT id, level, logger, message, created_at FROM error_logs "
            f"{where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )

    def list_push_logs(
        self,
        limit: int = 100,
        user_id: int | None = None,
        channel: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        conds, params = [], []
        if user_id is not None:
            conds.append("l.user_id = ?")
            params.append(user_id)
        if channel:
            conds.append("l.channel = ?")
            params.append(channel)
        if status:
            conds.append("l.status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return self._rows(
            "SELECT l.*, p.title, k.name AS kol_name, u.username AS user_name FROM push_logs l "
            "JOIN posts p ON p.id = l.post_id "
            "JOIN kols k ON k.id = p.kol_id "
            "LEFT JOIN users u ON u.id = l.user_id "
            f"{where} ORDER BY l.id DESC LIMIT ?",
            (*params, limit),
        )

    def list_failed_push_logs(self, since_hours: int = 24, limit: int = 2000) -> list[dict]:
        """最近 N 小时内失败的推送记录（用于重启后恢复重推）。"""
        return self._rows(
            "SELECT post_id, channel, user_id FROM push_logs "
            "WHERE status = 'failed' AND created_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT ?",
            (f"-{since_hours} hours", limit),
        )

    def get_failed_push_error(self, post_id: int, channel: str, user_id: int | None) -> str:
        """最近一条失败推送的原始错误（重试成功前取用，写入日志便于追溯）。"""
        if user_id is not None:
            cond = "user_id = ?"
            params = (post_id, channel, user_id)
        else:
            cond = "user_id IS NULL"
            params = (post_id, channel)
        rows = self._rows(
            f"SELECT error FROM push_logs WHERE post_id = ? AND channel = ? AND {cond} "
            "AND status = 'failed' ORDER BY id DESC LIMIT 1",
            params,
        )
        return rows[0]["error"] if rows else ""

    def mark_failed_push_success(self, post_id: int, channel: str, user_id: int | None) -> None:
        """把最近一条失败推送标记为成功（重试成功后）。"""
        if user_id is not None:
            self._execute(
                "UPDATE push_logs SET status = 'success', error = '' WHERE id = ("
                "SELECT id FROM push_logs WHERE post_id = ? AND channel = ? "
                "AND user_id = ? AND status = 'failed' ORDER BY id DESC LIMIT 1)",
                (post_id, channel, user_id),
            )
        else:
            self._execute(
                "UPDATE push_logs SET status = 'success', error = '' WHERE id = ("
                "SELECT id FROM push_logs WHERE post_id = ? AND channel = ? "
                "AND user_id IS NULL AND status = 'failed' ORDER BY id DESC LIMIT 1)",
                (post_id, channel),
            )

    # ---- Settings ----
    def enqueue_hosted_image(self, source_url: str) -> bool:
        url = (source_url or "").strip()
        if not url:
            return False
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO hosted_images (source_url, status) VALUES (?, 'pending')",
                (url,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def hosted_image_url(self, source_url: str) -> str:
        url = (source_url or "").strip()
        if not url:
            return ""
        rows = self._rows(
            "SELECT hosted_url FROM hosted_images WHERE source_url = ? AND status = 'ready' "
            "AND hosted_url != ''",
            (url,),
        )
        return (rows[0]["hosted_url"] if rows else "") or ""

    def hosted_image_url_by_hash(self, content_hash: str) -> str:
        digest = (content_hash or "").strip()
        if not digest:
            return ""
        rows = self._rows(
            "SELECT hosted_url FROM hosted_images WHERE content_hash = ? AND status = 'ready' "
            "AND hosted_url != '' LIMIT 1",
            (digest,),
        )
        return (rows[0]["hosted_url"] if rows else "") or ""

    def recent_mirrorable_image_rows(self, limit: int = 40) -> list[dict]:
        """最近的镜像源图（X / Truth Social），供图床缓慢回填。

        UNION ALL 双分支各自走 (platform, id DESC) 索引逆序扫，LIKE 只在
        命中平台内过滤，不再全表扫（批处理查询走只读连接不占写锁）。
        """
        return self._read_only_rows(
            "SELECT images FROM ("
            "SELECT id, images FROM posts WHERE platform = 'twitter' "
            "AND images LIKE '%twimg.com%' "
            "UNION ALL "
            "SELECT id, images FROM posts WHERE platform = 'truth' "
            "AND images LIKE '%truthsocial.com%'"
            ") ORDER BY id DESC LIMIT ?",
            (max(int(limit), 1),),
        )

    def list_untranslated_truth_posts(self, limit: int = 3, days: int = 1) -> list[dict]:
        """最近未翻译的 Truth 帖（content_src 为空），供后台低频回填。"""
        return self._rows(
            "SELECT id, title, content FROM posts "
            "WHERE platform = 'truth' AND content_src = '' AND length(content) >= 20 "
            "AND published_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT ?",
            (f"-{int(days)} day", max(int(limit), 1)),
        )

    def set_post_translation(
        self, post_id: int, title: str, content: str, title_src: str, content_src: str
    ) -> None:
        self._execute(
            "UPDATE posts SET title = ?, content = ?, title_src = ?, content_src = ? WHERE id = ?",
            (to_simplified(title), to_simplified(content), title_src or "", content_src or "", int(post_id)),
        )

    def list_pending_hosted_images(self, limit: int = 8) -> list[dict]:
        from .imgbed import retry_cutoff

        cutoff = retry_cutoff()
        return self._rows(
            "SELECT source_url, hosted_url, content_hash, status, attempts, last_error, "
            "last_attempt_at FROM hosted_images "
            "WHERE status = 'pending' OR (status = 'failed' AND last_attempt_at <= ?) "
            "ORDER BY created_at LIMIT ?",
            (cutoff, max(int(limit), 1)),
        )

    def mark_hosted_image(
        self,
        source_url: str,
        *,
        status: str,
        hosted_url: str = "",
        content_hash: str = "",
        last_error: str = "",
    ) -> None:
        from .imgbed import utc_now

        self._execute(
            "UPDATE hosted_images SET status = ?, hosted_url = CASE WHEN ? != '' THEN ? ELSE hosted_url END, "
            "content_hash = CASE WHEN ? != '' THEN ? ELSE content_hash END, "
            "attempts = attempts + 1, last_error = ?, last_attempt_at = ? WHERE source_url = ?",
            (
                status,
                hosted_url,
                hosted_url,
                content_hash,
                content_hash,
                last_error,
                utc_now(),
                source_url,
            ),
        )

    def list_hosted_images_older_than(self, days: int, limit: int = 100) -> list[dict]:
        if days <= 0:
            return []
        return self._rows(
            "SELECT source_url, hosted_url FROM hosted_images "
            "WHERE created_at < datetime('now', ?) ORDER BY created_at LIMIT ?",
            (f"-{int(days)} days", max(int(limit), 1)),
        )

    def hosted_url_has_newer(self, hosted_url: str, days: int) -> bool:
        url = (hosted_url or "").strip()
        if not url or days <= 0:
            return False
        return bool(self._rows(
            "SELECT 1 FROM hosted_images WHERE hosted_url = ? "
            "AND created_at >= datetime('now', ?) LIMIT 1",
            (url, f"-{int(days)} days"),
        ))

    def delete_hosted_images(self, source_urls: list[str]) -> int:
        urls = [str(u).strip() for u in source_urls if str(u).strip()]
        if not urls:
            return 0
        placeholders = ",".join("?" * len(urls))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM hosted_images WHERE source_url IN ({placeholders})",
                urls,
            )
            self._conn.commit()
            return cur.rowcount

    def get_setting(self, key: str) -> str | None:
        reader = self._read_only_rows if str(key).startswith("ima_") else self._rows
        rows = reader("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def set_settings_atomic(self, values: dict[str, str]) -> None:
        if not values:
            return
        sql = (
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for key, value in values.items():
                    self._conn.execute(sql, (key, value))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 抓取代理池 ----
    def create_proxy_pool(
        self,
        name: str,
        kind: str = "static",
        extract_url: str = "",
        protocol: str = "http",
        expire_seconds: int = 0,
        refresh_interval_seconds: int = 0,
        enabled: bool = True,
    ) -> int:
        return self._execute(
            "INSERT INTO proxy_pools (name, kind, extract_url, protocol, "
            "expire_seconds, refresh_interval_seconds, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name.strip(),
                kind,
                extract_url.strip(),
                protocol,
                int(expire_seconds or 0),
                int(refresh_interval_seconds or 0),
                1 if enabled else 0,
            ),
        )

    def get_proxy_pool(self, pool_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM proxy_pools WHERE id = ?", (pool_id,))
        return rows[0] if rows else None

    def list_proxy_pools(self) -> list[dict]:
        return self._rows(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM proxies x WHERE x.pool_id = p.id) AS proxy_count "
            "FROM proxy_pools p ORDER BY p.id"
        )

    def update_proxy_pool(self, pool_id: int, **kwargs) -> None:
        allowed = {
            "name",
            "kind",
            "extract_url",
            "protocol",
            "expire_seconds",
            "refresh_interval_seconds",
            "enabled",
            "last_extract_at",
            "last_error",
        }
        sets = []
        params: list = []
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            if key == "enabled":
                value = 1 if value else 0
            elif (
                key in {"name", "extract_url"}
                and isinstance(value, str)
            ):
                value = value.strip()
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(pool_id)
        self._execute(
            f"UPDATE proxy_pools SET {', '.join(sets)} WHERE id = ?",
            params,
        )

    def delete_proxy_pool(self, pool_id: int) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM proxies WHERE pool_id = ?", (pool_id,))
                self._conn.execute("DELETE FROM proxy_pools WHERE id = ?", (pool_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def upsert_proxy(
        self,
        pool_id: int,
        protocol: str,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        source: str = "manual",
        expires_at: int | None = None,
    ) -> int:
        self._execute(
            "INSERT INTO proxies (pool_id, protocol, host, port, username, password, "
            "source, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pool_id, protocol, host, port, username) DO UPDATE SET "
            "password = excluded.password, "
            "expires_at = excluded.expires_at, "
            "source = excluded.source, "
            "status = 'unknown', "
            "fail_count = 0",
            (
                pool_id,
                protocol,
                host.strip(),
                int(port),
                username or "",
                password or "",
                source,
                expires_at,
            ),
        )
        rows = self._rows(
            "SELECT id FROM proxies WHERE pool_id = ? AND protocol = ? AND host = ? "
            "AND port = ? AND username = ?",
            (pool_id, protocol, host.strip(), int(port), username or ""),
        )
        return int(rows[0]["id"])

    def get_proxy(self, proxy_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM proxies WHERE id = ?", (proxy_id,))
        return rows[0] if rows else None

    def list_proxies(self, pool_id: int | None = None) -> list[dict]:
        if pool_id is None:
            return self._rows("SELECT * FROM proxies ORDER BY id DESC")
        return self._rows(
            "SELECT * FROM proxies WHERE pool_id = ? ORDER BY id DESC", (pool_id,)
        )

    def delete_proxy(self, proxy_id: int) -> None:
        self._execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))

    def delete_expired_extracted_proxies(self, now: int | None = None) -> int:
        ts = int(now if now is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM proxies WHERE source = 'extract' AND expires_at IS NOT NULL "
                "AND expires_at > 0 AND expires_at < ?",
                (ts,),
            )
            self._conn.commit()
            return cur.rowcount

    def list_usable_proxies(self, pool_id: int, now: int | None = None) -> list[dict]:
        ts = int(now if now is not None else time.time())
        return self._rows(
            "SELECT * FROM proxies WHERE pool_id = ? AND status IN ('unknown', 'ok') "
            "AND (expires_at IS NULL OR expires_at = 0 OR expires_at > ?) "
            "ORDER BY id",
            (pool_id, ts),
        )

    def mark_proxy_ok(self, proxy_id: int, now: int | None = None) -> None:
        ts = int(now if now is not None else time.time())
        self._execute(
            "UPDATE proxies SET status = 'ok', fail_count = 0, last_ok_at = ?, "
            "last_error = '' WHERE id = ?",
            (ts, proxy_id),
        )

    def mark_proxy_fail(
        self, proxy_id: int, error: str = "", dead_after: int = 3, now: int | None = None
    ) -> None:
        ts = int(now if now is not None else time.time())
        row = self.get_proxy(proxy_id)
        if row is None:
            return
        fails = int(row["fail_count"] or 0) + 1
        status = "dead" if fails >= dead_after else row["status"]
        if status != "dead" and row["status"] == "ok":
            status = "ok"
        self._execute(
            "UPDATE proxies SET fail_count = ?, status = ?, last_fail_at = ?, "
            "last_error = ? WHERE id = ?",
            (fails, status, ts, (error or "")[:300], proxy_id),
        )

    def update_post_tags(self, post_id: int, tags: list[str]) -> None:
        """回写单条贴文的标签（回填/纠错用），空列表持久化为 '[]'（已处理零命中）。"""
        tags_json = json.dumps(tags, ensure_ascii=False)
        self._execute("UPDATE posts SET tags = ? WHERE id = ?", (tags_json, post_id))

    @staticmethod
    def _ima_document_row(row, group_id: str | None = None) -> tuple[dict, list[str]]:
        source = dict(row)
        actual_group_id = str(source.get("group_id") or group_id or "")
        if group_id is not None and actual_group_id != str(group_id):
            raise ValueError("row group_id does not match group_id")
        media_id = str(source.get("media_id") or "")
        if not actual_group_id or not media_id:
            raise ValueError("IMA document requires group_id and media_id")

        raw_tags = source.get("tags", _UNSET)
        if raw_tags is _UNSET or raw_tags is None:
            try:
                raw_tags = json.loads(str(source.get("tags_json") or "[]"))
            except (TypeError, ValueError):
                raw_tags = []
        if not isinstance(raw_tags, (list, tuple, set)):
            raw_tags = []
        tags = list(dict.fromkeys(str(tag) for tag in raw_tags if str(tag)))
        day = str(source.get("day") or "unknown").strip() or "unknown"
        valid_day = int(day.isascii() and len(day) == 4 and day.isdigit())
        # 跨年排序键 YYYY-MM-DD：上层建行 helper（_index_row → ima_sort_date）总是显式携带；
        # 裸行（历史数据/直写）缺省按 valid_day 用当前年份补全，unknown 为空（排序沉底）。
        raw_sort_date = source.get("sort_date", _UNSET)
        if raw_sort_date is _UNSET:
            raw_sort_date = (
                f"{time.strftime('%Y', time.gmtime())}-{day[:2]}-{day[2:]}"
                if valid_day
                else ""
            )
        sort_date = str(raw_sort_date or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sort_date):
            sort_date = ""
        values = {
            "group_id": actual_group_id,
            "media_id": media_id,
            "day": day,
            "valid_day": valid_day,
            "sort_date": sort_date,
            "name": str(source.get("name") or ""),
            "group_name": str(source.get("group_name") or ""),
            "name_folded": str(
                source.get("name_folded") or source.get("name") or ""
            ).casefold(),
            "metadata_folded": str(
                source.get("metadata_folded") or source.get("group_name") or ""
            ).casefold(),
            "abstract": str(source.get("abstract") or ""),
            "abstract_folded": str(
                source.get("abstract_folded") or source.get("abstract") or ""
            ).casefold(),
            "abstract_zh": str(source.get("abstract_zh") or ""),
            "abstract_src_hash": str(source.get("abstract_src_hash") or ""),
            "cover_url": str(source.get("cover_url") or ""),
            "tags_json": json.dumps(tags, ensure_ascii=False),
            "size": _to_int(source.get("size")),
            "chars": _to_int(source.get("chars")),
            "has_pdf": _to_bool(source.get("has_pdf", bool(source.get("pdf_path")))),
            "has_txt": _to_bool(source.get("has_txt", bool(source.get("txt_path")))),
            "pdf_path": str(source.get("pdf_path") or ""),
            "txt_path": str(source.get("txt_path") or ""),
            "downloaded_at": str(source.get("downloaded_at") or ""),
        }
        return values, tags

    @classmethod
    def _ima_document_insert_sql(cls, upsert: bool = False) -> str:
        columns = ", ".join(IMA_DOCUMENT_INDEX_COLUMNS)
        placeholders = ", ".join("?" for _ in IMA_DOCUMENT_INDEX_COLUMNS)
        sql = f"INSERT INTO ima_document_index ({columns}) VALUES ({placeholders})"
        if upsert:
            updates = ", ".join(
                f"{column} = excluded.{column}"
                for column in IMA_DOCUMENT_INDEX_COLUMNS[2:]
            )
            sql += (
                " ON CONFLICT(group_id, media_id) DO UPDATE SET "
                + updates
            )
        return sql

    def _insert_ima_document_rows_unlocked(
        self,
        prepared: list[tuple[dict, list[str]]],
        *,
        upsert: bool = False,
        tags_first: bool = False,
    ) -> None:
        if not prepared:
            return
        document_params = [
            tuple(row[column] for column in IMA_DOCUMENT_INDEX_COLUMNS)
            for row, _ in prepared
        ]
        tag_params = [
            (row["group_id"], row["media_id"], tag)
            for row, tags in prepared
            for tag in tags
        ]
        tag_sql = (
            "INSERT INTO ima_document_tags (group_id, media_id, tag) VALUES (?, ?, ?)"
        )
        if tags_first and tag_params:
            self._conn.executemany(tag_sql, tag_params)
        self._conn.executemany(
            self._ima_document_insert_sql(upsert=upsert), document_params
        )
        if not tags_first and tag_params:
            self._conn.executemany(tag_sql, tag_params)

    @staticmethod
    def _ima_meta_fallback() -> dict:
        return {
            "id": 1,
            "version": 1,
            "status": "fallback",
            "fingerprint": "",
            "rebuilt_at": "",
            "duration_ms": 0,
            "document_count": 0,
            "error": "",
        }

    def replace_ima_document_group(self, group_id: str, rows) -> None:
        group_id = str(group_id)
        prepared = [self._ima_document_row(row, group_id) for row in rows]
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "DELETE FROM ima_document_tags WHERE group_id = ?", (group_id,)
                )
                self._conn.execute(
                    "DELETE FROM ima_document_index WHERE group_id = ?", (group_id,)
                )
                self._insert_ima_document_rows_unlocked(prepared)
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM ima_document_index WHERE group_id = ?",
                    (group_id,),
                ).fetchone()[0]
                if int(count) != len(prepared):
                    raise sqlite3.IntegrityError("IMA document group count mismatch")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def replace_ima_document_index(
        self, rows, fingerprint: str, duration_ms: int
    ) -> None:
        prepared = [self._ima_document_row(row) for row in rows]
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM ima_document_tags")
                self._conn.execute("DELETE FROM ima_document_index")
                self._insert_ima_document_rows_unlocked(prepared)
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM ima_document_index"
                ).fetchone()[0]
                if int(count) != len(prepared):
                    raise sqlite3.IntegrityError("IMA document count mismatch")
                self._conn.execute(
                    "UPDATE ima_document_index_meta SET version = 1, status = 'ready', "
                    "fingerprint = ?, rebuilt_at = datetime('now'), duration_ms = ?, "
                    "document_count = ?, error = '' WHERE id = 1",
                    (str(fingerprint or ""), _to_int(duration_ms), len(prepared)),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def set_ima_index_fingerprint(self, fingerprint: str) -> None:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM ima_document_index"
            ).fetchone()[0]
            self._conn.execute(
                "UPDATE ima_document_index_meta SET fingerprint = ?, document_count = ? "
                "WHERE id = 1",
                (str(fingerprint or ""), int(total)),
            )
            self._conn.commit()

    def update_ima_document_batch(self, rows, fingerprint: str) -> int:
        prepared_by_key = {}
        for row in rows:
            prepared, tags = self._ima_document_row(row)
            prepared_by_key[(prepared["group_id"], prepared["media_id"])] = (
                prepared,
                tags,
            )
        prepared = list(prepared_by_key.values())
        if not prepared:
            return 0
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "DELETE FROM ima_document_tags WHERE group_id = ? AND media_id = ?",
                    [(row["group_id"], row["media_id"]) for row, _ in prepared],
                )
                self._insert_ima_document_rows_unlocked(
                    prepared, upsert=True, tags_first=True
                )
                self._conn.execute(
                    "UPDATE ima_document_index_meta SET fingerprint = ? WHERE id = 1",
                    (str(fingerprint or ""),),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(prepared)

    def ima_document_index_meta(self) -> dict:
        rows = self._read_only_rows("SELECT * FROM ima_document_index_meta WHERE id = 1")
        if not rows:
            return self._ima_meta_fallback()
        meta = self._ima_meta_fallback()
        meta.update(rows[0])
        for field in ("id", "version", "duration_ms", "document_count"):
            meta[field] = _to_int(meta.get(field))
        for field in ("status", "fingerprint", "rebuilt_at", "error"):
            meta[field] = str(meta.get(field) or "")
        return meta

    def mark_ima_document_index(self, status: str, error: str = "") -> None:
        if status not in IMA_DOCUMENT_INDEX_STATUSES:
            raise ValueError(f"invalid IMA document index status: {status}")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT OR IGNORE INTO ima_document_index_meta (id) VALUES (1)"
                )
                self._conn.execute(
                    "UPDATE ima_document_index_meta SET status = ?, error = ? WHERE id = 1",
                    (status, str(error or "")),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _ima_page_filters(
        self,
        groups: list[str],
        query: str,
        day: str,
        tag: str,
        rating: str = "",
        ticker: str = "",
    ) -> tuple[str, list, str, str | None, int]:
        clauses = [f"d.group_id IN ({', '.join('?' for _ in groups)})"]
        params: list = list(groups)
        if day:
            clauses.append("d.day = ?")
            params.append(day)
        if rating:
            # 研报结构化抽取（report_extractions）筛选：评级精确匹配
            clauses.append(
                "EXISTS (SELECT 1 FROM report_extractions re "
                "WHERE re.group_id = d.group_id AND re.media_id = d.media_id "
                "AND re.rating = ? AND re.status = 'ok')"
            )
            params.append(rating)
        if ticker:
            # 标的筛选：纯数字按代码精确匹配，否则按名称模糊
            if ticker.isdigit():
                clauses.append(
                    "EXISTS (SELECT 1 FROM report_extraction_tickers rt "
                    "WHERE rt.group_id = d.group_id AND rt.media_id = d.media_id "
                    "AND rt.code = ?)"
                )
                params.append(ticker)
            else:
                clauses.append(
                    "EXISTS (SELECT 1 FROM report_extraction_tickers rt "
                    "WHERE rt.group_id = d.group_id AND rt.media_id = d.media_id "
                    "AND rt.name LIKE ? ESCAPE '\\')"
                )
                params.append(_like_pattern(ticker))
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM ima_document_tags t "
                "WHERE t.group_id = d.group_id AND t.media_id = d.media_id "
                "AND t.tag = ?)"
            )
            params.append(tag)
        like = "LIKE ? ESCAPE '\\'"
        tag_like = (
            "EXISTS (SELECT 1 FROM ima_document_tags t "
            "WHERE t.group_id = d.group_id AND t.media_id = d.media_id "
            "AND t.tag {like})"
        )
        # 中文编译产物（机器摘要 abstract_zh + LLM 抽取 thesis）：正文以英文为主，
        # 中文查询只能靠它们召回，因此与 metadata/标签同级参与 LIKE 与排序。
        thesis_like = (
            "EXISTS (SELECT 1 FROM report_extractions re "
            "WHERE re.group_id = d.group_id AND re.media_id = d.media_id "
            f"AND re.status = 'ok' AND re.thesis {like})"
        )
        rank_sql = "0"
        pattern = None
        rank_placeholders = 0
        if query:
            pattern = _like_pattern(query)
            if len(query.strip()) < 2:
                clauses.append(f"(d.name_folded {like} OR d.metadata_folded {like})")
                params.extend([pattern, pattern])
                rank_sql = f"CASE WHEN d.name_folded {like} THEN 3 ELSE 2 END"
                rank_placeholders = 1
            else:
                clauses.append(
                    f"(d.name_folded {like} OR d.metadata_folded {like} "
                    f"OR d.abstract_folded {like} OR d.abstract_zh {like} "
                    f"OR {tag_like.format(like=like)} OR {thesis_like})"
                )
                params.extend([pattern] * 6)
                rank_sql = (
                    f"CASE WHEN d.name_folded {like} THEN 3 "
                    f"WHEN d.metadata_folded {like} OR d.abstract_zh {like} "
                    f"OR {tag_like.format(like=like)} OR {thesis_like} THEN 2 "
                    "ELSE 1 END"
                )
                rank_placeholders = 5
        return " AND ".join(clauses), params, rank_sql, pattern, rank_placeholders

    def ima_document_page(
        self,
        readable_group_ids: list[str],
        *,
        group: str = "",
        query: str = "",
        day: str = "",
        tag: str = "",
        rating: str = "",
        ticker: str = "",
        limit: int = 50,
        offset: int = 0,
        facets: bool = True,
    ) -> dict:
        requested_day = str(day or "").strip()
        requested_tag = str(tag or "").strip()
        requested_query = str(query or "").strip()
        requested_rating = str(rating or "").strip()
        requested_ticker = str(ticker or "").strip()
        if requested_query and not _ima_query_usable(requested_query):
            requested_query = ""
        page_limit = max(int(limit), 1)
        page_offset = max(int(offset), 0)
        groups = _ima_authorized_groups(readable_group_ids, group)
        if not groups:
            return _ima_empty_page(requested_day, page_offset)

        where_sql, where_params, rank_sql, pattern, rank_n = self._ima_page_filters(
            groups, requested_query, requested_day, requested_tag,
            requested_rating, requested_ticker,
        )
        if pattern:
            item_params = [pattern] * rank_n + list(where_params)
            rows = self._read_only_rows(
                f"SELECT d.*, {rank_sql} AS match_rank FROM ima_document_index d "
                f"WHERE {where_sql} "
                # 跨年排序：sort_date（YYYY-MM-DD）DESC，空串（unknown）沉底
                "ORDER BY match_rank DESC, (d.sort_date = '') ASC, d.sort_date DESC, "
                "d.name DESC, d.group_id ASC, d.media_id ASC LIMIT ? OFFSET ?",
                (*item_params, page_limit + 1, page_offset),
            )
        elif not any(
            (requested_day, requested_tag, requested_rating, requested_ticker)
        ):
            if len(groups) == 1:
                rows = self._read_only_rows(
                    "SELECT d.* FROM ima_document_index d INDEXED BY idx_ima_doc_group_latest "
                    f"WHERE {where_sql} "
                    "ORDER BY d.sort_date DESC, d.name DESC, d.media_id ASC "
                    "LIMIT ? OFFSET ?",
                    (*where_params, page_limit + 1, page_offset),
                )
            else:
                rows = self._read_only_rows(
                    "SELECT d.* FROM ima_document_index d INDEXED BY idx_ima_doc_latest "
                    f"WHERE {where_sql} "
                    "ORDER BY d.sort_date DESC, d.name DESC, d.group_id ASC, d.media_id ASC "
                    "LIMIT ? OFFSET ?",
                    (*where_params, page_limit + 1, page_offset),
                )
        else:
            branches: list[str] = []
            slice_params: list[object] = []
            for group_id in groups:
                group_where, group_params, *_ = self._ima_page_filters(
                    [group_id], "", requested_day, requested_tag,
                    requested_rating, requested_ticker,
                )
                branches.append(
                    "SELECT * FROM (SELECT d.* FROM ima_document_index d "
                    f"WHERE {group_where} "
                    "ORDER BY d.sort_date DESC, d.name DESC, d.media_id ASC LIMIT ?)"
                )
                slice_params.extend([*group_params, page_offset + page_limit + 1])
            rows = self._read_only_rows(
                f"SELECT * FROM ({' UNION ALL '.join(branches)}) d "
                "ORDER BY d.sort_date DESC, d.name DESC, d.group_id ASC, d.media_id ASC "
                "LIMIT ? OFFSET ?",
                (*slice_params, page_limit + 1, page_offset),
            )
        has_more = len(rows) > page_limit
        items = [_ima_public_document(row) for row in rows[:page_limit]]

        searching = bool(requested_query)
        if searching:
            return {
                "items": items,
                "days": [],
                "tags": [],
                "tag_counts": {},
                "document_count": page_offset + len(items) + int(has_more),
                "day": requested_day,
                "has_more": has_more,
                "offset": page_offset,
                "group_counts": {},
            }

        if not facets:
            # 首屏只要列表：分面（总数/分组/日期/标签）由客户端随后单独取一次，
            # 冷页缓存下聚合查询比列表切片慢得多，不拖首屏。
            return {
                "items": items,
                "days": [],
                "tags": [],
                "tag_counts": {},
                "document_count": 0,
                "day": requested_day,
                "has_more": has_more,
                "offset": page_offset,
                "group_counts": {},
            }

        count_row = self._read_only_rows(
            f"SELECT COUNT(*) AS n FROM ima_document_index d WHERE {where_sql}",
            where_params,
        )
        group_rows = self._read_only_rows(
            f"SELECT d.group_id AS group_id, COUNT(*) AS n "
            f"FROM ima_document_index d WHERE {where_sql} GROUP BY d.group_id",
            where_params,
        )
        group_counts = {row["group_id"]: int(row["n"]) for row in group_rows}
        # 日期 DISTINCT 全库会扫两万行；标签 JOIN 文档表生产实测 ~800ms。
        # 全库只跳过日期面。标签改扫 ima_document_tags（按 group_id，约 17ms）。
        need_day_facets = len(groups) == 1 or bool(requested_day or requested_tag)
        if need_day_facets:
            days = [
                row["day"]
                for row in self._read_only_rows(
                    f"SELECT DISTINCT d.day FROM ima_document_index d "
                    f"WHERE {where_sql} ORDER BY d.valid_day DESC, d.day DESC",
                    where_params,
                )
            ]
        else:
            days = []
        if requested_day:
            tag_rows = self._read_only_rows(
                "SELECT t.tag AS tag, COUNT(*) AS n FROM ima_document_tags t "
                "JOIN ima_document_index d "
                "ON d.group_id = t.group_id AND d.media_id = t.media_id "
                f"WHERE {where_sql} GROUP BY t.tag ORDER BY n DESC, t.tag",
                where_params,
            )
        else:
            tag_rows = self._read_only_rows(
                "SELECT t.tag AS tag, COUNT(*) AS n FROM ima_document_tags t "
                f"WHERE t.group_id IN ({', '.join('?' for _ in groups)}) "
                "GROUP BY t.tag ORDER BY n DESC, t.tag",
                groups,
            )
        tag_counts = {row["tag"]: int(row["n"]) for row in tag_rows}
        return {
            "items": items,
            "days": days,
            "tags": list(tag_counts),
            "tag_counts": tag_counts,
            "document_count": int(count_row[0]["n"] if count_row else 0),
            "day": requested_day,
            "has_more": has_more,
            "offset": page_offset,
            "group_counts": group_counts,
        }

    def warm_ima_document_page(self, limit: int = 50) -> int:
        """启动后预热研报列表页：按首屏同样形态跑一次只读查询，把索引页/数据页带进页缓存。"""
        try:
            groups = [
                row["group_id"]
                for row in self._read_only_rows(
                    "SELECT DISTINCT d.group_id FROM ima_document_index d"
                )
            ]
            if not groups:
                return 0
            self.ima_document_page(groups, limit=limit)
            return len(groups)
        except Exception as exc:
            logging.getLogger(__name__).warning("[ima-page-warmup] %s", exc)
            return 0

    def ima_document_match_count(
        self,
        readable_group_ids: list[str],
        *,
        group: str = "",
        query: str = "",
        day: str = "",
        tag: str = "",
        rating: str = "",
        ticker: str = "",
    ) -> int:
        requested_query = str(query or "").strip()
        if requested_query and not _ima_query_usable(requested_query):
            requested_query = ""
        groups = _ima_authorized_groups(readable_group_ids, group)
        if not groups:
            return 0
        where_sql, where_params, _rank_sql, _pattern, _rank_n = self._ima_page_filters(
            groups,
            requested_query,
            str(day or "").strip(),
            str(tag or "").strip(),
            str(rating or "").strip(),
            str(ticker or "").strip(),
        )
        rows = self._read_only_rows(
            f"SELECT COUNT(*) AS n FROM ima_document_index d WHERE {where_sql}",
            where_params,
        )
        return int(rows[0]["n"] if rows else 0)

    def ima_document_index_rows(self, group_ids) -> list[dict]:
        groups = list(dict.fromkeys(str(item).strip() for item in group_ids if str(item).strip()))
        if not groups:
            return []
        # ponytail: 全库 FTS 体量索引在生产跑不动（NFS 归档 + 1C1G），这里不再带 thesis。
        return self._rows(
            "SELECT * FROM ima_document_index "
            f"WHERE group_id IN ({', '.join('?' for _ in groups)}) "
            "ORDER BY group_id, media_id",
            groups,
        )

    # ---- 研报结构化抽取（report_extractions，LLM 批处理） ----

    def attach_report_extractions(self, items: list[dict]) -> list[dict]:
        """给文档列表项就地挂 extraction 字段（无抽取结果则不挂）。"""
        if not items:
            return items
        found = self.report_extractions_for_keys(
            [(item.get("group_id"), item.get("media_id")) for item in items]
        )
        if not found:
            return items
        from .llm import report_rating_zh

        for item in items:
            extra = found.get((str(item.get("group_id") or ""), str(item.get("media_id") or "")))
            if extra:
                extra = dict(extra)
                extra["rating"] = report_rating_zh(extra.get("rating") or "")
                item["extraction"] = extra
        return items

    def pending_report_extractions(
        self,
        limit: int = 40,
        group_ids: list[str] | None = None,
        min_sort_date: str = "",
        per_group: int | None = None,
    ) -> list[dict]:
        """待抽取研报：有 txt 或 pdf、尚无抽取行，且可限制最早研报日期。

        per_group 开启按库轮转公平配额（ROW_NUMBER 分组编号后 rn<=N），
        避免单个大库的存量垄断队列头导致其他库饿死；空则退化为全局新→旧。
        """
        params: list = []
        where = ["d.has_txt = 1 AND d.txt_path != '' OR d.has_pdf = 1 AND d.pdf_path != ''"]
        groups = [str(g).strip() for g in (group_ids or []) if str(g).strip()]
        if groups:
            where.append(f"d.group_id IN ({', '.join('?' for _ in groups)})")
            params.extend(groups)
        if min_sort_date:
            where.append("d.sort_date >= ?")
            params.append(min_sort_date)
        if per_group:
            params.append(max(int(per_group), 1))
        params.append(max(int(limit), 1))
        rn_filter = "WHERE rn <= ? " if per_group else ""
        return self._read_only_rows(
            "SELECT group_id, media_id, txt_path, pdf_path, name, sort_date FROM ("
            "SELECT d.group_id AS group_id, d.media_id AS media_id, d.txt_path AS txt_path, "
            "d.pdf_path AS pdf_path, d.name AS name, d.sort_date AS sort_date, "
            "ROW_NUMBER() OVER (PARTITION BY d.group_id "
            "ORDER BY (d.sort_date = '') ASC, d.sort_date DESC, d.media_id) AS rn "
            "FROM ima_document_index d LEFT JOIN report_extractions re "
            "ON re.group_id = d.group_id AND re.media_id = d.media_id "
            f"WHERE ({where[0]}) AND {' AND '.join(where[1:]) or '1=1'} "
            "AND re.media_id IS NULL"
            f") {rn_filter}ORDER BY sort_date DESC, group_id LIMIT ?",
            params,
        )

    def report_extraction_group_ids(self, min_sort_date: str = "") -> list[str]:
        """列出可抽取的研报库；飞书个人文档不参与研报结构化。"""
        params: list[str] = []
        where = [
            "group_id NOT LIKE 'feishu-%'",
            "(has_txt = 1 AND txt_path != '' OR has_pdf = 1 AND pdf_path != '')",
        ]
        if min_sort_date:
            where.append("sort_date >= ?")
            params.append(min_sort_date)
        rows = self._rows(
            "SELECT DISTINCT group_id FROM ima_document_index "
            f"WHERE {' AND '.join(where)} ORDER BY group_id",
            params,
        )
        return [str(row["group_id"]) for row in rows]

    def reset_report_extractions(
        self,
        statuses: tuple[str, ...],
        group_ids: list[str] | None = None,
        min_sort_date: str = "",
    ) -> int:
        """删除指定范围的失败态，使修复后的抽取管线可以重新处理。"""
        recoverable = tuple(
            status for status in statuses if status in {"failed", "notext", "empty", "nofile"}
        )
        if not recoverable:
            return 0
        placeholders = ", ".join("?" for _ in recoverable)
        groups = [str(group).strip() for group in (group_ids or []) if str(group).strip()]

        def scope(alias: str) -> tuple[str, list[str]]:
            conditions = [f"{alias}.status IN ({placeholders})"]
            params = list(recoverable)
            if groups:
                conditions.append(
                    f"{alias}.group_id IN ({', '.join('?' for _ in groups)})"
                )
                params.extend(groups)
            if min_sort_date:
                conditions.append(
                    "EXISTS (SELECT 1 FROM ima_document_index d "
                    f"WHERE d.group_id = {alias}.group_id AND d.media_id = {alias}.media_id "
                    "AND d.sort_date >= ?)"
                )
                params.append(min_sort_date)
            return " AND ".join(conditions), params

        ticker_scope, ticker_params = scope("re")
        extraction_scope, extraction_params = scope("report_extractions")
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "DELETE FROM report_extraction_tickers WHERE EXISTS ("
                    "SELECT 1 FROM report_extractions re "
                    "WHERE re.group_id = report_extraction_tickers.group_id "
                    "AND re.media_id = report_extraction_tickers.media_id "
                    f"AND {ticker_scope})",
                    ticker_params,
                )
                cursor = self._conn.execute(
                    f"DELETE FROM report_extractions WHERE {extraction_scope}",
                    extraction_params,
                )
                self._conn.commit()
                return max(int(cursor.rowcount), 0)
            except Exception:
                self._conn.rollback()
                raise

    def save_report_extraction(
        self,
        group_id: str,
        media_id: str,
        *,
        txt_hash: str = "",
        report_kind: str = "",
        rating: str = "",
        target_price: str = "",
        thesis: str = "",
        tickers: list[dict] | None = None,
        model: str = "",
        status: str = "ok",
    ) -> None:
        """写入/覆盖单篇抽取结果；无论成败都落行，防止同篇被反复重试。"""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT INTO report_extractions (group_id, media_id, txt_hash, report_kind, rating, target_price, thesis, model, status, extracted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                    "ON CONFLICT(group_id, media_id) DO UPDATE SET "
                    "txt_hash = excluded.txt_hash, report_kind = excluded.report_kind, "
                    "rating = excluded.rating, target_price = excluded.target_price, "
                    "thesis = excluded.thesis, model = excluded.model, "
                    "status = excluded.status, extracted_at = excluded.extracted_at",
                    (
                        group_id,
                        media_id,
                        txt_hash,
                        report_kind,
                        rating,
                        target_price,
                        thesis,
                        model,
                        status,
                    ),
                )
                self._conn.execute(
                    "DELETE FROM report_extraction_tickers WHERE group_id = ? AND media_id = ?",
                    (group_id, media_id),
                )
                for t in tickers or []:
                    code = str(t.get("code") or "").strip()
                    if not code:
                        continue
                    self._conn.execute(
                        "INSERT OR IGNORE INTO report_extraction_tickers "
                        "(group_id, media_id, code, name, stance) VALUES (?, ?, ?, ?, ?)",
                        (group_id, media_id, code, str(t.get("name") or ""), str(t.get("stance") or "")),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 标的聚合编译（ima_ticker_digests，LLM 增量综述） ----

    def ima_ticker_code(self, ticker: str) -> str:
        """把标的筛选词解析成规范代码（聚合键）：纯数字/代码直通，名称查词表。

        与列表筛选同语义：纯数字按代码，非数字是名称（中文库名习惯）。
        """
        raw = str(ticker or "").strip()
        if not raw or raw.isdigit():
            return raw
        rows = self._rows(
            "SELECT code FROM report_extraction_tickers WHERE name = ? "
            "GROUP BY code ORDER BY COUNT(*) DESC LIMIT 1",
            (raw,),
        )
        return str(rows[0]["code"]) if rows else raw.upper()

    def ima_ticker_reports(self, code, group_ids=None, limit: int = 200) -> list[dict]:
        """单个标的的历史研报时间线（按可读库过滤）：标的页与综述编译共用。

        ponytail: 按 sort_date 取最近 limit 篇；热门标的历史更长，需要全量时再分页。
        """
        code = self.ima_ticker_code(code)  # 名称或代码都接受（列表筛选同语义）
        if not code:
            return []
        where = ["t.code = ?"]
        params: list = [code]
        groups = [str(g).strip() for g in (group_ids or []) if str(g).strip()]
        if groups:
            where.append(f"t.group_id IN ({', '.join('?' for _ in groups)})")
            params.extend(groups)
        params.append(max(int(limit), 1))
        return self._read_only_rows(
            "SELECT t.group_id AS group_id, t.media_id AS media_id, t.name AS ticker_name, "
            "t.stance AS stance, d.name AS name, d.sort_date AS sort_date, d.day AS day, "
            "d.has_txt AS has_txt, re.rating AS rating, re.target_price AS target_price, "
            "re.thesis AS thesis, re.report_kind AS report_kind, re.status AS status "
            "FROM report_extraction_tickers t "
            "JOIN ima_document_index d ON d.group_id = t.group_id AND d.media_id = t.media_id "
            "LEFT JOIN report_extractions re ON re.group_id = t.group_id AND re.media_id = t.media_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY (d.sort_date = '') ASC, d.sort_date DESC, d.media_id DESC LIMIT ?",
            params,
        )

    def ima_ticker_groups(self, code) -> list[str]:
        """该标的被哪些库收录（综述按全部库编译，据此判定谁能看这份摘要）。"""
        normalized = self.ima_ticker_code(code)
        if not normalized:
            return []
        rows = self._read_only_rows(
            "SELECT DISTINCT group_id FROM report_extraction_tickers WHERE code = ?",
            (normalized,),
        )
        return [str(row["group_id"]) for row in rows]

    def ima_ticker_digest(self, code, kind: str = "ticker") -> dict:
        """读缓存的聚合综述（无则空字典）。"""
        rows = self._rows(
            "SELECT * FROM ima_ticker_digests WHERE kind = ? AND code = ?",
            (kind, str(code or "").strip()),
        )
        return rows[0] if rows else {}

    def ima_digest_candidates(
        self,
        kind: str = "ticker",
        group_ids=None,
        min_reports: int = 2,
        limit: int = 50,
    ) -> list[dict]:
        """聚合单元候选（按研报数排序）+ 当前源签名，调用方比签名决定是否重编。"""
        where: list[str] = []
        params: list = [kind]
        groups = [str(g).strip() for g in (group_ids or []) if str(g).strip()]
        if groups:
            where.append(f"t.group_id IN ({', '.join('?' for _ in groups)})")
            params.extend(groups)
        params.extend((max(int(min_reports), 1), max(int(limit), 1)))
        rows = self._rows(
            "SELECT t.code AS code, MAX(t.name) AS name, "
            "COUNT(DISTINCT t.group_id || '/' || t.media_id) AS report_count, "
            "COALESCE(MAX(d.sort_date), '') AS latest_date, "
            "COALESCE(g.signature, '') AS signature, COALESCE(g.digest, '') AS digest "
            "FROM report_extraction_tickers t "
            "JOIN ima_document_index d ON d.group_id = t.group_id AND d.media_id = t.media_id "
            "LEFT JOIN ima_ticker_digests g ON g.kind = ? AND g.code = t.code "
            f"WHERE {' AND '.join(where) or '1=1'} "
            "GROUP BY t.code HAVING report_count >= ? "
            "ORDER BY report_count DESC, latest_date DESC LIMIT ?",
            params,
        )
        for row in rows:
            row["source_signature"] = f"{row['report_count']}:{row['latest_date']}"
            # 缓存行不存在（没编过）或源签名变了才重编；失败行也落了签名，不空转惹 LLM
            row["stale"] = row["signature"] != row["source_signature"]
        return rows

    def save_ima_digest(
        self,
        code,
        *,
        kind: str = "ticker",
        name: str = "",
        signature: str = "",
        source_count: int = 0,
        digest: str = "",
        model: str = "",
        status: str = "ok",
    ) -> None:
        """写入聚合综述（签名相同则内容不会变，重编失败也要落行以免每轮重试）。"""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT INTO ima_ticker_digests (kind, code, name, signature, source_count, digest, model, status, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                    "ON CONFLICT(kind, code) DO UPDATE SET "
                    "name = excluded.name, signature = excluded.signature, "
                    "source_count = excluded.source_count, digest = excluded.digest, "
                    "model = excluded.model, status = excluded.status, "
                    "updated_at = excluded.updated_at",
                    (
                        kind,
                        str(code or "").strip(),
                        name,
                        signature,
                        int(source_count),
                        digest,
                        model,
                        status,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def report_extractions_for_keys(
        self, keys: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict]:
        """批量取抽取结果（含标的），键为 (group_id, media_id)。"""
        out: dict[tuple[str, str], dict] = {}
        clean = [(str(g), str(m)) for g, m in keys if str(g) and str(m)]
        chunk = 200
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            conds = " OR ".join("(group_id = ? AND media_id = ?)" for _ in part)
            params = [x for pair in part for x in pair]
            rows = self._rows(
                "SELECT group_id, media_id, rating, target_price, thesis, status, model "
                f"FROM report_extractions WHERE {conds}",
                params,
            )
            if not rows:
                continue
            trows = self._rows(
                "SELECT group_id, media_id, code, name, stance "
                f"FROM report_extraction_tickers WHERE {conds}",
                params,
            )
            tickmap: dict[tuple[str, str], list[dict]] = {}
            for tr in trows:
                tickmap.setdefault((tr["group_id"], tr["media_id"]), []).append(
                    {"code": tr["code"], "name": tr["name"], "stance": tr["stance"]}
                )
            for r in rows:
                k = (r["group_id"], r["media_id"])
                out[k] = {
                    "rating": r["rating"],
                    "target_price": r["target_price"],
                    "thesis": r["thesis"],
                    "status": r["status"],
                    "model": r["model"],
                    "tickers": tickmap.get(k, []),
                }
        return out

    def ima_documents_by_keys(
        self,
        keys,
        readable_group_ids: list[str],
        *,
        group: str = "",
        query: str = "",
        day: str = "",
        tag: str = "",
    ) -> list[dict]:
        groups = _ima_authorized_groups(readable_group_ids, group)
        allowed = set(groups)
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for key in keys:
            try:
                group_id, media_id = (str(value).strip() for value in key)
            except (TypeError, ValueError):
                continue
            pair = (group_id, media_id)
            if group_id in allowed and media_id and pair not in seen:
                seen.add(pair)
                pairs.append(pair)
            if len(pairs) >= 200:
                break
        if not groups or not pairs:
            return []

        requested_query = str(query or "").strip()
        if requested_query and not _ima_query_usable(requested_query):
            requested_query = ""
        requested_day = str(day or "").strip()
        requested_tag = str(tag or "").strip()
        base_where, base_params, _rank_sql, _pattern, _rank_n = self._ima_page_filters(
            groups, "", requested_day, requested_tag
        )
        metadata_where, metadata_params, _rank_sql, _pattern, _rank_n = (
            self._ima_page_filters(
                groups, requested_query, requested_day, requested_tag
            )
        )
        pair_sql = " OR ".join(
            "(d.group_id = ? AND d.media_id = ?)" for _ in pairs
        )
        rows = self._read_only_rows(
            f"SELECT d.*, CASE WHEN {metadata_where} THEN 1 ELSE 0 END "
            "AS metadata_match FROM ima_document_index d "
            f"WHERE {base_where} AND ({pair_sql})",
            (
                *metadata_params,
                *base_params,
                *(value for pair in pairs for value in pair),
            ),
        )
        items = []
        for row in rows:
            item = _ima_public_document(row)
            item["_metadata_match"] = bool(row["metadata_match"])
            items.append(item)
        return items

    def ima_document_catalog_stats(self, group_ids: list[str]) -> dict[str, dict]:
        groups = _ima_authorized_groups(group_ids)
        if not groups:
            return {}
        placeholders = ", ".join("?" for _ in groups)
        rows = self._read_only_rows(
            "WITH counts AS ("
            "SELECT group_id, COUNT(*) AS document_count FROM ima_document_index "
            f"WHERE group_id IN ({placeholders}) GROUP BY group_id) "
            "SELECT counts.group_id, counts.document_count, "
            "d.day, d.sort_date, d.name, d.media_id FROM counts "
            "JOIN ima_document_index AS d ON d.rowid = ("
            "SELECT latest.rowid FROM ima_document_index AS latest "
            "WHERE latest.group_id = counts.group_id "
            "ORDER BY latest.sort_date DESC, latest.name DESC LIMIT 1)",
            groups,
        )
        return {
            row["group_id"]: {
                "document_count": int(row["document_count"]),
                "latest_day": row["day"],
                "latest_sort_date": row["sort_date"],
                "latest_title": row["name"],
                "latest_media_id": row["media_id"],
            }
            for row in rows
        }

    def ima_document_from_index(
        self,
        media_id: str,
        readable_group_ids: list[str],
        group: str = "",
    ) -> dict | None:
        media_id = str(media_id or "").strip()
        groups = _ima_authorized_groups(readable_group_ids, group)
        if not media_id or not groups:
            return None
        rows = self._read_only_rows(
            "SELECT * FROM ima_document_index WHERE media_id = ? "
            f"AND group_id IN ({', '.join('?' for _ in groups)}) "
            "ORDER BY valid_day DESC, day DESC, name DESC",
            (media_id, *groups),
        )
        if len(rows) != 1:
            return None
        return _ima_public_document(rows[0])

    def ima_document_index_count(self) -> int:
        rows = self._read_only_rows("SELECT COUNT(*) AS n FROM ima_document_index")
        return int(rows[0]["n"] if rows else 0)

    def get_tag_vocabulary(self) -> list[dict]:
        """读贴文打标词表（settings 持久化），返回「标签 + 关键词」对象数组。

        兼容旧格式：settings 里若是纯字符串数组（旧 LLM 打标版本），自动迁移——
        tag 在默认规则里则补默认关键词，否则给空关键词（管理页可见可改）。
        """
        default_tags = {r["tag"]: r.get("keywords") or [] for r in DEFAULT_TAG_RULES}
        raw = self.get_setting(TAG_VOCABULARY_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    if isinstance(parsed[0], str):
                        # 旧格式：字符串数组 → 迁移为对象数组
                        return [
                            {"tag": t, "keywords": list(default_tags.get(t, []))}
                            for t in parsed
                        ]
                    rules = []
                    for r in parsed:
                        if not isinstance(r, dict) or not str(r.get("tag") or "").strip():
                            continue
                        rules.append(
                            {
                                "tag": str(r["tag"]).strip(),
                                "keywords": [
                                    str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()
                                ],
                            }
                        )
                    if rules:
                        return rules
            except (TypeError, ValueError):
                pass
        return [dict(r) for r in DEFAULT_TAG_RULES]

    def set_tag_vocabulary(self, tags: list[dict]) -> None:
        """保存贴文打标词表（对象数组：tag + keywords）。"""
        self.set_setting(TAG_VOCABULARY_KEY, json.dumps(tags, ensure_ascii=False))

    def merge_default_tag_vocabulary(self) -> int:
        """把默认词表里还没有的标签追加进去，不改已有标签的关键词。返回新增条数。"""
        from .tagging import TAG_VOCABULARY_MAX

        current = self.get_tag_vocabulary()
        have = {r["tag"] for r in current}
        extra = [dict(r) for r in DEFAULT_TAG_RULES if r["tag"] not in have]
        room = max(0, TAG_VOCABULARY_MAX - len(current))
        extra = extra[:room]
        if not extra:
            return 0
        self.set_tag_vocabulary(current + extra)
        return len(extra)

    def tag_stats(self) -> dict:
        """打标统计（管理端回填进度展示用）。

        processed = 已成功执行规则（含零命中）；tagged = 实际有标签；
        pending = 尚未执行规则（'' 或 NULL，需回填）。
        """
        rows = self._rows(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN tags != '' THEN 1 ELSE 0 END) AS processed, "
            "SUM(CASE WHEN tags != '' AND tags != '[]' THEN 1 ELSE 0 END) AS tagged "
            "FROM posts"
        )
        row = rows[0] if rows else {"n": 0, "processed": 0, "tagged": 0}
        total = _to_int(row["n"])
        processed = _to_int(row["processed"])
        return {
            "total": total,
            "processed": processed,
            "tagged": _to_int(row["tagged"]),
            "pending": total - processed,
        }

    def get_stock_names(self) -> list[str]:
        """读常用股票名表（settings 持久化），从未保存过时用内置默认名单。"""
        raw = self.get_setting(STOCK_NAMES_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(n) for n in parsed]
            except (TypeError, ValueError):
                pass
        return list(DEFAULT_STOCK_NAMES)

    def set_stock_names(self, names: list[str]) -> None:
        """保存常用股票名表。空列表表示管理员清空，不再回落到默认名单。"""
        self.set_setting(STOCK_NAMES_KEY, json.dumps(names, ensure_ascii=False))

    def get_stock_name_exclusions(self) -> list[str]:
        """管理员删掉、维护不得加回的股票名。"""
        raw = self.get_setting(STOCK_NAMES_EXCLUDED_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    seen, out = set(), []
                    for n in parsed:
                        name = str(n).strip()
                        if name and name not in seen:
                            seen.add(name)
                            out.append(name)
                    return out
            except (TypeError, ValueError):
                pass
        return []

    def set_stock_name_exclusions(self, names: list[str]) -> None:
        seen, out = set(), []
        for n in names or []:
            name = str(n).strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        self.set_setting(STOCK_NAMES_EXCLUDED_KEY, json.dumps(out, ensure_ascii=False))

    def sync_stock_name_exclusions(self, previous, updated) -> list[str]:
        """按管理员本次编辑更新排除表。返回新删掉的名字。"""
        prev = {str(n).strip() for n in previous or [] if str(n).strip()}
        new = {str(n).strip() for n in updated or [] if str(n).strip()}
        excluded = set(self.get_stock_name_exclusions())
        removed = sorted(prev - new)
        excluded.update(removed)
        excluded -= new
        self.set_stock_name_exclusions(sorted(excluded))
        return removed

    def get_stock_aliases(self) -> list[dict]:
        """读黑话别名表（settings 持久化），缺省空表。

        兼容旧数据：元素若是纯字符串（早期格式），视为无对应正式名，跳过。
        """
        raw = self.get_setting(STOCK_ALIASES_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    aliases = []
                    for a in parsed:
                        if isinstance(a, dict):
                            alias = str(a.get("alias") or "").strip()
                            stock = str(a.get("stock") or "").strip()
                            if alias and stock:
                                aliases.append({"alias": alias, "stock": stock})
                        elif isinstance(a, str) and a.strip():
                            # 旧格式纯字符串：只有别名无正式名，无法打标，忽略
                            continue
                    return aliases
            except (TypeError, ValueError):
                pass
        return []

    def set_stock_aliases(self, aliases: list[dict]) -> None:
        """保存黑话别名表。"""
        self.set_setting(STOCK_ALIASES_KEY, json.dumps(aliases, ensure_ascii=False))

    def merge_default_stock_aliases(self) -> dict:
        """去掉正式名切半等碎片别名，并合并种子黑话。返回 purged/seeded。"""
        from .tagging import reconcile_alias_table

        kept, purged, seeded = reconcile_alias_table(
            self.get_stock_aliases(), self.get_stock_names(), DEFAULT_STOCK_ALIASES
        )
        if purged or seeded:
            self.set_stock_aliases(kept)
        return {"purged": purged, "seeded": seeded}

    def get_tag_maintain_last(self) -> dict | None:
        """读最近一次标签维护摘要；无记录或损坏返回 None。"""
        raw = self.get_setting(TAG_MAINTAIN_LAST_KEY)
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_tag_maintain_last(self, data: dict) -> None:
        """保存最近一次标签维护摘要。"""
        self.set_setting(TAG_MAINTAIN_LAST_KEY, json.dumps(data, ensure_ascii=False))

    def aggregate_post_tags(self, limit: int = 50, ttl_seconds: int = 600) -> list[str]:
        """聚合贴文里出现过的全部标签（去重，按出现次数降序）。

        供前端动态标签筛选下拉使用（词表标签之外的实际标签，如股票名）。
        全表扫描 + Python 聚合，结果带 TTL 缓存（标签变化低频，10 分钟内
        新标签可接受）；防缓存击穿用 monotonic 时间戳。
        """
        now = time.monotonic()
        cached = getattr(self, "_tag_aggregate_cache", None)
        if cached is not None and now - getattr(self, "_tag_aggregate_at", 0.0) < ttl_seconds:
            return cached[:limit]
        counts: dict[str, int] = {}
        for row in self._read_only_rows("SELECT tags FROM posts WHERE tags != ''"):
            raw = row["tags"]
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(parsed, list):
                continue
            for tag in parsed:
                tag = str(tag).strip()
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1
        ranked = [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        self._tag_aggregate_cache = ranked
        self._tag_aggregate_at = now
        return ranked[:limit]

    # ---- 每日精选投递状态（按渠道幂等） ----
    def daily_report_delivered(self, user_id: int, report_date: str, channel: str) -> bool:
        """该用户当日该渠道是否已成功投递。"""
        rows = self._rows(
            "SELECT 1 FROM daily_report_deliveries "
            "WHERE user_id = ? AND report_date = ? AND channel = ? AND status = 'success'",
            (user_id, report_date, channel),
        )
        return bool(rows)

    def mark_daily_report_delivered(self, user_id: int, report_date: str, channel: str) -> None:
        """标记渠道当日投递成功；重复标记覆盖为成功（幂等）。"""
        self._execute(
            "INSERT INTO daily_report_deliveries (user_id, report_date, channel, status) "
            "VALUES (?, ?, ?, 'success') "
            "ON CONFLICT(user_id, report_date, channel) "
            "DO UPDATE SET status = 'success', updated_at = datetime('now')",
            (user_id, report_date, channel),
        )

    def mark_daily_report_failed(self, user_id: int, report_date: str, channel: str) -> None:
        """标记渠道当日投递失败（用于重试时区分，成功标记覆盖失败标记）。"""
        self._execute(
            "INSERT INTO daily_report_deliveries (user_id, report_date, channel, status) "
            "VALUES (?, ?, ?, 'failed') "
            "ON CONFLICT(user_id, report_date, channel) "
            "DO UPDATE SET status = 'failed', updated_at = datetime('now')",
            (user_id, report_date, channel),
        )

    def delete_daily_report_deliveries_older_than(self, days: int) -> int:
        """清理超过 N 天的每日精选投递状态，避免表无限增长。"""
        if days <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM daily_report_deliveries WHERE report_date < date('now', ?)",
                (f"-{days} days",),
            )
            self._conn.commit()
            return cur.rowcount

    # ---- 数据源稳定性事件 ----
    def add_source_event(
        self, platform: str, status: str, detail: str = "", ok_count: int = 0, fail_count: int = 0
    ) -> None:
        """记录一次数据源事件：ok（本轮有抓取成功）/ fail（本轮有失败）/ warn（降级）。

        ok_count/fail_count 是本轮该平台成功/失败的大V抓取次数，用于按
        「尝试次数」统计真实成功率，避免单个大V失败被同平台多数成功掩盖。
        """
        self._execute(
            "INSERT INTO source_events (platform, status, detail, ok_count, fail_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (platform, status, detail[:300], int(ok_count), int(fail_count)),
        )

    def source_event_stats(self, platform: str, hours: int = 24) -> dict[str, int]:
        """最近 N 小时内的抓取成功/失败次数与降级事件数。

        ok/fail 按「尝试次数」求和；旧版事件行（迁移前 ok_count=0）按 1 次
        计，避免升级后 24h 内成功率瞬时归零。
        """
        rows = self._rows(
            "SELECT status, "
            "SUM(CASE WHEN ok_count > 0 THEN ok_count ELSE 1 END) AS ok, "
            "SUM(CASE WHEN fail_count > 0 THEN fail_count ELSE 1 END) AS fail, "
            "COUNT(*) AS n "
            "FROM source_events "
            "WHERE platform = ? AND created_at >= datetime('now', ?) "
            "GROUP BY status",
            (platform, f"-{hours} hours"),
        )
        out = {"ok": 0, "fail": 0, "warn": 0}
        for row in rows:
            status = row["status"]
            if status == "warn":
                out["warn"] = row["n"]
            elif status == "ok":
                out["ok"] = int(row["ok"] or 0)
            elif status == "fail":
                out["fail"] = int(row["fail"] or 0)
        return out

    def recent_source_events(self, limit: int = 30) -> list[dict]:
        return self._rows(
            "SELECT * FROM source_events ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 200),),
        )

    def delete_source_events_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM source_events WHERE created_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            self._conn.commit()
            return cur.rowcount

    def delete_admin_logs_older_than(self, days: int) -> int:
        """删除超过 N 天的管理员操作日志，返回删除条数。"""
        if days <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM admin_logs WHERE created_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            self._conn.commit()
            return cur.rowcount

    # ---- 飞书个人机器人 ----
    _PERSONAL_BOT_COLUMNS = frozenset({
        "app_id", "app_secret_ciphertext", "open_id", "chat_id",
        "tenant_brand", "status", "last_error", "verified_at", "last_success_at",
    })

    def get_feishu_personal_bot(self, user_id: int) -> dict | None:
        rows = self._rows(
            "SELECT * FROM feishu_personal_bots WHERE user_id = ?", (user_id,)
        )
        return rows[0] if rows else None

    def get_feishu_personal_bot_by_app(self, app_id: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM feishu_personal_bots WHERE app_id = ?", (app_id,)
        )
        return rows[0] if rows else None

    def save_feishu_personal_bot(self, user_id: int, app_id: str,
                                 app_secret_ciphertext: str, tenant_brand: str,
                                 status: str, *, open_id: str = "", chat_id: str = "",
                                 last_error: str = "") -> None:
        """按 user_id upsert 个人机器人记录（唯一约束：每用户一条）。"""
        self._execute(
            "INSERT INTO feishu_personal_bots "
            "(user_id, app_id, app_secret_ciphertext, tenant_brand, status, open_id, chat_id, last_error, verified_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'active' THEN datetime('now') ELSE NULL END, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "app_id=excluded.app_id, app_secret_ciphertext=excluded.app_secret_ciphertext, "
            "tenant_brand=excluded.tenant_brand, status=excluded.status, open_id=excluded.open_id, "
            "chat_id=excluded.chat_id, last_error=excluded.last_error, "
            "verified_at=CASE WHEN excluded.status = 'active' THEN datetime('now') ELSE verified_at END, "
            "updated_at=datetime('now')",
            (user_id, app_id, app_secret_ciphertext, tenant_brand, status, open_id, chat_id, last_error, status),
        )

    def update_feishu_personal_bot(self, user_id: int, **kwargs) -> None:
        sets, params = [], []
        for key, value in kwargs.items():
            if key not in self._PERSONAL_BOT_COLUMNS:
                raise ValueError(f"非法个人机器人字段: {key}")
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        params.append(user_id)
        self._execute(
            f"UPDATE feishu_personal_bots SET {', '.join(sets)} WHERE user_id = ?", params
        )

    def delete_feishu_personal_bot(self, user_id: int) -> None:
        self._execute("DELETE FROM feishu_personal_bots WHERE user_id = ?", (user_id,))

    # ---- 飞书个人机器人注册会话 ----
    _REG_SESSION_COLUMNS = frozenset({
        "device_code_ciphertext", "registration_base_url", "verification_uri",
        "candidate_app_id", "candidate_app_secret_ciphertext", "candidate_tenant_brand",
        "expected_open_id", "bind_code_hash", "bind_code_expires_at",
        "session_expires_at", "poll_interval", "status", "last_error",
    })

    def create_feishu_registration_session(self, **kwargs) -> None:
        keys = [k for k in kwargs if k in self._REG_SESSION_COLUMNS or k == "session_id" or k == "user_id"]
        missing = {"session_id", "user_id", "device_code_ciphertext", "registration_base_url",
                   "verification_uri", "session_expires_at", "poll_interval", "status"} - set(keys)
        if missing:
            raise ValueError(f"注册会话缺少必填字段: {', '.join(sorted(missing))}")
        cols = ", ".join(keys)
        marks = ", ".join("?" for _ in keys)
        self._execute(
            f"INSERT INTO feishu_registration_sessions ({cols}) VALUES ({marks})",
            tuple(kwargs[k] for k in keys),
        )

    def get_feishu_registration_session(self, session_id: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM feishu_registration_sessions WHERE session_id = ?", (session_id,)
        )
        return rows[0] if rows else None

    def get_active_feishu_registration_session(self, user_id: int) -> dict | None:
        rows = self._rows(
            "SELECT * FROM feishu_registration_sessions WHERE user_id = ? "
            "AND status NOT IN ('expired', 'cancelled') ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        return rows[0] if rows else None

    def update_feishu_registration_session(self, session_id: str, **kwargs) -> None:
        sets, params = [], []
        for key, value in kwargs.items():
            if key not in self._REG_SESSION_COLUMNS:
                raise ValueError(f"非法注册会话字段: {key}")
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        params.append(session_id)
        self._execute(
            f"UPDATE feishu_registration_sessions SET {', '.join(sets)} WHERE session_id = ?",
            params,
        )

    def cancel_feishu_registration_sessions_by_user(self, user_id: int) -> None:
        """取消某用户所有未结束的注册会话（开始新会话前调用）。"""
        self._execute(
            "UPDATE feishu_registration_sessions SET status = 'cancelled', "
            "updated_at = datetime('now') WHERE user_id = ? AND status NOT IN ('expired', 'cancelled')",
            (user_id,),
        )

    def expire_stale_feishu_registration_sessions(self, now: int | None = None) -> int:
        """把已过期的非终态会话置为 expired（启动清理），返回处理条数。"""
        now = int(now if now is not None else time.time())
        stale = self._rows(
            "SELECT session_id FROM feishu_registration_sessions "
            "WHERE status NOT IN ('expired', 'cancelled', 'active') AND session_expires_at < ?",
            (now,),
        )
        for row in stale:
            self._execute(
                "UPDATE feishu_registration_sessions SET status = 'expired', "
                "updated_at = datetime('now') WHERE session_id = ?",
                (row["session_id"],),
            )
        return len(stale)

    # ---- 飞书文档 OAuth 与来源 ----
    _FEISHU_DOCUMENT_SOURCE_COLUMNS = frozenset({
        "document_id", "title", "revision_id", "content_hash", "timeline_path",
        "txt_path", "asset_root", "entry_count", "published_record_json",
        "published_state_json", "display_mode", "display_name", "enabled", "sync_status",
        "last_checked_at", "last_success_at", "last_error", "deleted_at",
    })

    def create_feishu_oauth_session(
        self,
        state_hash: str,
        user_id: int,
        expires_at: int,
        code_verifier: str = "",
    ) -> None:
        verifier_ciphertext = self._encrypt_secret(code_verifier) if code_verifier else ""
        with self._lock:
            self._conn.execute(
                "DELETE FROM feishu_document_oauth_sessions WHERE expires_at < ? OR consumed_at IS NOT NULL",
                (int(time.time()),),
            )
            self._conn.execute(
                "INSERT INTO feishu_document_oauth_sessions "
                "(state_hash, user_id, expires_at, code_verifier_ciphertext) VALUES (?, ?, ?, ?)",
                (state_hash, int(user_id), int(expires_at), verifier_ciphertext),
            )
            self._conn.commit()

    def consume_feishu_oauth_session(self, state_hash: str, now: int) -> dict | None:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                row = self._conn.execute(
                    "SELECT * FROM feishu_document_oauth_sessions "
                    "WHERE state_hash = ? AND consumed_at IS NULL AND expires_at >= ?",
                    (state_hash, int(now)),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return None
                self._conn.execute(
                    "UPDATE feishu_document_oauth_sessions SET consumed_at = ? WHERE state_hash = ?",
                    (int(now), state_hash),
                )
                self._conn.commit()
                value = dict(row)
                ciphertext = str(value.pop("code_verifier_ciphertext", "") or "")
                value["code_verifier"] = (
                    decrypt_stored_secret(ciphertext, self.credential_key)
                    if ciphertext else ""
                )
                return value
            except Exception:
                self._conn.rollback()
                raise

    def save_feishu_oauth_credential(self, token: dict) -> None:
        access = str(token.get("access_token") or "").strip()
        refresh = str(token.get("refresh_token") or "").strip()
        if not access or not refresh or not self.credential_key:
            raise ValueError("飞书 OAuth 凭据不完整或未配置加密密钥")
        now = int(time.time())
        expires_at = now + int(token.get("expires_in") or 0)
        refresh_expires_at = now + int(token.get("refresh_token_expires_in") or token.get("refresh_expires_in") or 0)
        access_cipher = self._encrypt_secret(access)
        refresh_cipher = self._encrypt_secret(refresh)
        with self._lock:
            self._conn.execute(
                "INSERT INTO feishu_document_oauth "
                "(id, access_token_ciphertext, refresh_token_ciphertext, expires_at, refresh_expires_at, scopes, open_id, tenant_key, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET access_token_ciphertext=excluded.access_token_ciphertext, "
                "refresh_token_ciphertext=excluded.refresh_token_ciphertext, expires_at=excluded.expires_at, "
                "refresh_expires_at=excluded.refresh_expires_at, scopes=excluded.scopes, open_id=excluded.open_id, "
                "tenant_key=excluded.tenant_key, updated_at=datetime('now')",
                (
                    access_cipher,
                    refresh_cipher,
                    expires_at,
                    refresh_expires_at,
                    str(token.get("scope") or ""),
                    str(token.get("open_id") or ""),
                    str(token.get("tenant_key") or ""),
                ),
            )
            self._conn.commit()

    def get_feishu_oauth_credential(self, *, decrypt: bool = False) -> dict | None:
        rows = self._read_only_rows("SELECT * FROM feishu_document_oauth WHERE id = 1")
        if not rows:
            return None
        row = rows[0]
        if decrypt:
            row["access_token"] = decrypt_stored_secret(row.pop("access_token_ciphertext"), self.credential_key)
            row["refresh_token"] = decrypt_stored_secret(row.pop("refresh_token_ciphertext"), self.credential_key)
        else:
            row.pop("access_token_ciphertext", None)
            row.pop("refresh_token_ciphertext", None)
        return row

    # ---- 飞书文档应用配置（设置页可改，优先于环境变量） ----
    _FEISHU_DOCS_PLAIN_KEYS = ("app_id", "redirect_uri", "scopes", "interval_seconds")

    def set_feishu_docs_settings(self, values: dict) -> None:
        secret = str(values.get("app_secret") or "").strip()
        if secret:
            if not self.credential_key:
                raise ValueError("未配置 FEISHU_CREDENTIAL_KEY，无法保存应用密钥")
            self.set_setting("feishu_docs_app_secret", self._encrypt_secret(secret))
        for key in self._FEISHU_DOCS_PLAIN_KEYS:
            if values.get(key) is not None:
                self.set_setting(f"feishu_docs_{key}", str(values[key]))

    def get_feishu_docs_settings(self) -> dict:
        # 只读连接：同步线程每轮都会调用，不得抢占 DB._lock（与飞书来源读路径同一约定）
        out: dict[str, str] = {}
        for key in self._FEISHU_DOCS_PLAIN_KEYS:
            rows = self._read_only_rows(
                "SELECT value FROM settings WHERE key = ?", (f"feishu_docs_{key}",)
            )
            out[key] = str(rows[0]["value"]) if rows else ""
        cipher_rows = self._read_only_rows(
            "SELECT value FROM settings WHERE key = ?", ("feishu_docs_app_secret",)
        )
        cipher = str(cipher_rows[0]["value"]) if cipher_rows else ""
        if cipher.startswith(SECRET_PREFIX):
            out["app_secret"] = decrypt_stored_secret(cipher, self.credential_key)
        else:
            out["app_secret"] = cipher
        return out

    def upsert_feishu_document_source(self, parsed: dict[str, str]) -> dict:
        key_hash = hashlib.sha256(parsed["source_key"].encode()).hexdigest()
        with self._lock:
            self._conn.execute(
                "INSERT INTO feishu_document_sources "
                "(source_key_hash, host, source_type, source_token, canonical_url, group_id, media_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_key_hash) DO UPDATE SET "
                "host=excluded.host, source_type=excluded.source_type, source_token=excluded.source_token, "
                "canonical_url=excluded.canonical_url, enabled=1, deleted_at=NULL, sync_status='pending', "
                "last_error='', updated_at=datetime('now')",
                (
                    key_hash, parsed["host"], parsed["source_type"], parsed["source_token"],
                    parsed["canonical_url"], parsed["group_id"], parsed["media_id"],
                ),
            )
            self._conn.commit()
        row = self.get_feishu_document_source_by_hash(key_hash)
        if row is None:
            raise sqlite3.IntegrityError("飞书文档来源写入失败")
        return row

    def get_feishu_document_source_by_hash(self, source_key_hash: str) -> dict | None:
        rows = self._read_only_rows("SELECT * FROM feishu_document_sources WHERE source_key_hash = ?", (source_key_hash,))
        return rows[0] if rows else None

    def get_feishu_document_source(self, source_id: int) -> dict | None:
        rows = self._read_only_rows("SELECT * FROM feishu_document_sources WHERE id = ?", (int(source_id),))
        return rows[0] if rows else None

    def get_feishu_document_source_by_group(self, group_id: str) -> dict | None:
        rows = self._read_only_rows("SELECT * FROM feishu_document_sources WHERE group_id = ?", (group_id,))
        return rows[0] if rows else None

    def list_feishu_document_sources(
        self, *, active_only: bool = False, include_deleted: bool = False
    ) -> list[dict]:
        if active_only:
            where = "WHERE enabled = 1 AND deleted_at IS NULL"
        elif include_deleted:
            where = ""
        else:
            where = "WHERE deleted_at IS NULL"
        return self._read_only_rows(f"SELECT * FROM feishu_document_sources {where} ORDER BY id")

    def update_feishu_document_source(self, source_id: int, **kwargs) -> None:
        sets, params = [], []
        for key, value in kwargs.items():
            if key not in self._FEISHU_DOCUMENT_SOURCE_COLUMNS:
                raise ValueError(f"非法飞书文档字段: {key}")
            sets.append(f"{key} = ?")
            params.append(_to_bool(value) if key == "enabled" else value)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        params.append(int(source_id))
        self._execute(f"UPDATE feishu_document_sources SET {', '.join(sets)} WHERE id = ?", params)

    def publish_feishu_document_source(self, source_id: int, **kwargs) -> None:
        values = dict(kwargs)
        values.update({"sync_status": "succeeded", "last_error": "", "last_checked_at": kwargs.get("last_success_at") or ""})
        self.update_feishu_document_source(source_id, **values)

    def soft_delete_feishu_document_source(self, source_id: int) -> dict | None:
        source = self.get_feishu_document_source(source_id)
        if source is None:
            return None
        now = datetime.now(UTC).isoformat()
        self.update_feishu_document_source(source_id, enabled=0, sync_status="disabled", deleted_at=now)
        return source
