#!/usr/bin/env python3
"""中金点睛研报批量采集 → 存储机本地库（默认）或 ARM staging（显式打开）。

用法（存储 VPS 上，默认生产/存储机行为）：
  python3 cicc_report_collector.py --days 30            # 最近30天
  python3 cicc_report_collector.py --all                # 全量（分页拉完为止）
  python3 cicc_report_collector.py --categories 宏观经济,市场策略 --days 7
  python3 cicc_report_collector.py --self-test          # 离线自检（含经典/中间层路径）

登录态：/root/cicc/cookies.txt（一行原始 Cookie 头），chmod 600。
可用 VPUSH_CICC_COOKIE_FILE 覆盖路径（不要把 Cookie 写进仓库）。

两种落盘模式（中间层默认关，不改存储机 CLI）：

  经典 / 存储机（默认，VPUSH_ARM_MIDDLEWARE 未设或为 0）：
    --root 默认 /srv/vpush-ima/local
    local/cicc-research/.vpush-local-library.json
    local/cicc-research/.vpush-local-meta.jsonl   # 列表摘要/标签 sidecar，扫描器按 *_<id>.pdf 匹配
    local/cicc-research/<品类>/<MMDD>/<中文原名>_<id>.pdf
    属主 99:100（仅当 --root 仍是 /srv/vpush-ima/local 且以 root 跑）

  ARM 中间层（--arm-middleware 或 VPUSH_ARM_MIDDLEWARE=1）：
    --root 改写为 VPUSH_ARM_STAGING_ROOT，默认 /data/vpush-ima-cache/staging
    staging/local/cicc-research/YYYY/MM/DD/<sanitized>_<id>.pdf
    staging/local/cicc-research/YYYY/MM/DD/<sanitized>_<id>.json  # 可选 sidecar，对齐日后 lab.sqlite
    不 chown 99:100（除非仍指向经典 /srv/vpush-ima/local）
    熔断 paused.json 写在 $CACHE_ROOT/.cicc/ 或 $VPUSH_ARM_STAGING_ROOT/.cicc/
    （不在 ARM 上 mkdir /srv/vpush-ima）
    不直写 NFS / 115 / OpenList；上传只走 puller
    puller 入库后会清掉 staging，所以下载前另查 $CACHE_ROOT/manifest/lab.sqlite
    （只读）：报告 id 已是 uploaded/hot 就跳过，不再每天把 --days 窗口整段重下

目录契约见 docs/superpowers/specs/2026-08-29-local-storage-library-mount-design.md
与 docs/arm-middleware.md。
"""

import argparse
import fcntl
import http.client
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://www.research.cicc.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = BASE + "/zh_CN/reportList"
CHOWN_UID, CHOWN_GID = 99, 100
TZ_BJ = timezone(timedelta(hours=8))
PAGE_SIZE = 50
SLEEP_PAGE, SLEEP_DL = 0.6, 0.25
MAX_PAGES = 2000
CLASSIC_PAUSED_FILE = "/srv/vpush-ima/local/.cicc/paused.json"
# Tests may replace this; write_paused / clear honor an override.
PAUSED_FILE = CLASSIC_PAUSED_FILE
DEFAULT_COOKIE_FILE = "/root/cicc/cookies.txt"
DEFAULT_STORAGE_ROOT = "/srv/vpush-ima/local"
DEFAULT_ARM_STAGING_ROOT = "/data/vpush-ima-cache/staging"
CLASSIC_CHOWN_ROOT = "/srv/vpush-ima/local"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def middleware_enabled(cli_flag: bool = False) -> bool:
    """中间层默认关；--arm-middleware 或 VPUSH_ARM_MIDDLEWARE=1 才打开。"""
    return bool(cli_flag) or env_flag("VPUSH_ARM_MIDDLEWARE")


def resolve_cookie_file(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value)
    env = os.environ.get("VPUSH_CICC_COOKIE_FILE", "").strip()
    return Path(env or DEFAULT_COOKIE_FILE)


def resolve_output_root(cli_root: str | None, *, middleware: bool) -> Path:
    """中间层未显式 --root 时改写到 staging；经典模式保持 /srv/vpush-ima/local。"""
    if cli_root:
        return Path(cli_root)
    if middleware:
        env = os.environ.get("VPUSH_ARM_STAGING_ROOT", "").strip()
        return Path(env or DEFAULT_ARM_STAGING_ROOT)
    return Path(DEFAULT_STORAGE_ROOT)


def should_fix_owner(root: Path) -> bool:
    """只在经典存储机根上 chown 99:100；staging / 其它 --root 不改属主。"""
    return os.geteuid() == 0 and str(root) == CLASSIC_CHOWN_ROOT


def date_parts(publish_time: str) -> tuple[str, str, str]:
    """publishTime → (YYYY, MM, DD)；非法则 unknown/00/00。"""
    day = publish_date(publish_time)
    if len(day) >= 10 and day[4] == "-" and day[7] == "-":
        return day[0:4], day[5:7], day[8:10]
    return "unknown", "00", "00"


def resolve_paused_file(*, middleware: bool | None = None) -> Path:
    """Classic storage pause path, or ARM cache/staging when middleware is on.

    Do not mkdir a fake ``/srv/vpush-ima`` tree on ARM. Tests that replace
    ``PAUSED_FILE`` keep that override.
    """
    if PAUSED_FILE != CLASSIC_PAUSED_FILE:
        return Path(PAUSED_FILE)
    if middleware is None:
        middleware = middleware_enabled()
    if not middleware:
        return Path(CLASSIC_PAUSED_FILE)
    cache = os.environ.get("CACHE_ROOT", "").strip()
    if cache:
        return Path(cache) / ".cicc" / "paused.json"
    staging = os.environ.get("VPUSH_ARM_STAGING_ROOT", "").strip()
    if staging:
        return Path(staging) / ".cicc" / "paused.json"
    return Path(DEFAULT_ARM_STAGING_ROOT) / ".cicc" / "paused.json"


def write_paused(reason: str, detail: str) -> None:
    """熔断前尽力记录原因（quota=配额满 / auth=登录失效），供状态展示与增量门控。
    脚本可能以非 root 跑（目录属主 99:100），写失败静默忽略，不改变退出行为。"""
    try:
        p = resolve_paused_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".paused.tmp.{os.getpid()}")
        tmp.write_text(json.dumps({"reason": reason, "ts": int(time.time()),
                                   "detail": detail[:200]}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass

# 网页一级品类 → 库 slug（规格约束 slug 仅小写字母数字连字符）
SLUG_MAP = {
    "宏观经济": "cicc-macro",
    "市场策略": "cicc-strategy",
    "全球研究": "cicc-global",
    "行业研究": "cicc-industry",
    "公司研究": "cicc-company",
    "量化及ESG": "cicc-quant-esg",
    "大宗商品": "cicc-commodity",
    "外汇研究": "cicc-fx",
    "固定收益": "cicc-bond",
    "中金研究院": "cicc-institute",
    "其他": "cicc-other",
}

DEFAULT_BODY = {
    "input": "", "searchField": "titleSeg",
    "analyst": {"value": "", "name": ""}, "author": [],
    "pubTimeStart": None, "pubTimeEnd": None,
    "stock": {"value": "", "name": ""},
    "portalCategoryId": "", "subPortalCategoryId": {},
    "industriesIds": "", "subIndustriesIds": {},
    "reportNumCode": "", "level": [""], "levelChange": [""],
    "cusPageRange": "", "currencyIds": "", "commodityType": "",
    "page": 1, "size": PAGE_SIZE,
    "authorId": "", "secCode": "", "minPageCount": "", "maxPageCount": "",
}


def x_time() -> str:
    u = int(time.time() * 1000) // 10
    return str(u) + str(u % 97).zfill(2)


class Session:
    """同 host keep-alive 连接：每篇 detail+fetchPdf 两次请求复用一次 TLS 握手。
    连接被服务端关闭/异常时重建并重试。"""

    def __init__(self, cookie: str):
        self.cookie = cookie.strip().rstrip(";")
        self.conn: http.client.HTTPSConnection | None = None

    def request(self, path: str, *, body: dict | None = None,
                raw: bool = False, tries: int = 3) -> bytes | dict:
        headers = {"User-Agent": UA, "Cookie": self.cookie, "X-Time": x_time(),
                   "Referer": REFERER, "Origin": BASE}
        payload_body = None
        if body is not None:
            payload_body = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        last_err: Exception = RuntimeError("unreachable")
        for attempt in range(1, tries + 1):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(BASE.removeprefix("https://"), timeout=60)
                self.conn.request("POST" if body is not None else "GET", path,
                                  body=payload_body, headers=headers)
                resp = self.conn.getresponse()
                payload = resp.read()
                if resp.status != 200:
                    detail = payload[:200].decode("utf-8", "replace")
                    if resp.status in (401, 412) or "40010" in detail:
                        write_paused("auth", f"HTTP {resp.status} 登录态失效")
                        sys.exit(f"登录态失效（HTTP {resp.status}）：请更新 Cookie 后重跑。")
                    if "400013" in detail:
                        write_paused("quota", "code 400013 本月配额已满")
                        sys.exit("本月研报下载数量已达上限（code 400013）：等配额重置后重跑即可续传。")
                    raise RuntimeError(f"HTTP {resp.status} {detail}")
                if raw:
                    return payload
                obj = json.loads(payload)
                code = obj.get("code")
                if code == 0:
                    return obj
                if code == 40010:
                    write_paused("auth", "code 40010 登录态失效")
                    sys.exit("登录态失效（code 40010）：请更新 Cookie 文件后重跑。")
                if code == 400013:
                    write_paused("quota", "code 400013 本月配额已满")
                    sys.exit("本月研报下载数量已达上限（code 400013）：等配额重置后重跑即可续传。")
                raise RuntimeError(f"api code={code} msg={obj.get('msg') or obj.get('desc')}")
            except (http.client.HTTPException, OSError, TimeoutError, json.JSONDecodeError) as e:
                self.conn = None  # 连接失效：重建后重试
                last_err = e
            if attempt < tries:
                time.sleep(2 * attempt)
        raise last_err


def filter_by_keywords(items: list[dict], keywords: list[str]) -> list[dict]:
    """标题关键词白名单：任一关键词（大小写不敏感）命中即保留；空白名单=全部保留。"""
    kw = [k.strip().lower() for k in keywords if k and k.strip()]
    if not kw:
        return items
    return [it for it in items
            if any(k in str(it.get("title") or "").lower() for k in kw)]


def sanitize_title(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title)
    title = re.sub(r"\s+", " ", title).strip().strip(".")
    return title


def fit_bytes(name: str, budget: int) -> str:
    raw = name.encode("utf-8")
    if len(raw) <= budget:
        return name
    cut = budget
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut].decode("utf-8", "ignore")


def day_dir(publish_time: str) -> str:
    dt = datetime.fromisoformat(publish_time)
    return (dt.astimezone(TZ_BJ)).strftime("%m%d")


LIB_SLUG, LIB_NAME = "cicc-research", "中金点睛"
SIDECAR_NAME = ".vpush-local-meta.jsonl"


def publish_date(publish_time: str) -> str:
    """列表 publishTime → 北京日期 YYYY-MM-DD；非法则空串。"""
    raw = str(publish_time or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(TZ_BJ).strftime("%Y-%m-%d")
    except ValueError:
        day = raw[:10]
        return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else ""


def category_id_names(param: dict) -> dict:
    """param.treeData / industriesData 的 id→中文名（含子节点）。"""
    id_name: dict = {}
    for node in list(param.get("treeData") or []) + list(param.get("industriesData") or []):
        if not isinstance(node, dict):
            continue
        nid, nname = node.get("id"), node.get("name")
        if nid is not None and nname:
            id_name[nid] = nname
            id_name[str(nid)] = nname
        for ch in node.get("children") or []:
            if not isinstance(ch, dict):
                continue
            cid, cname = ch.get("id"), ch.get("name")
            if cid is not None and cname:
                id_name[cid] = cname
                id_name[str(cid)] = cname
    return id_name


def sidecar_row(item: dict, id_name: dict, cat_name: str = "") -> dict:
    """列表项 → sidecar 一行，字段对齐 cicc_meta_backfill / 本地库规格 §11。"""
    tags: list[str] = []
    extra = list(item.get("documentLabels") or [])
    for raw in [item.get("reportType"), cat_name, *extra]:
        tag = str(raw).strip() if raw else ""
        if tag and tag not in tags:
            tags.append(tag)
    for pid in item.get("portalCategoryIds") or []:
        name = id_name.get(pid) or id_name.get(str(pid))
        if name and name not in tags:
            tags.append(name)
    authors = []
    for analyst in item.get("analysts") or []:
        if isinstance(analyst, dict) and analyst.get("name"):
            authors.append(str(analyst["name"]).strip())
        elif isinstance(analyst, str) and analyst.strip():
            authors.append(analyst.strip())
    day = publish_date(item.get("publishTime") or "")
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or "")[:2000],
        "tags": tags[:5],
        "day": day.replace("-", "")[4:] if len(day) >= 10 else "unknown",
        "publish": day,
        "authors": " ".join(authors),
    }


def load_sidecar(path: Path) -> dict:
    rows: dict = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return rows
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
        if rid:
            rows[rid] = row
    return rows


def merge_sidecar(path: Path, updates: dict, *, fix_owner: bool = False) -> int:
    """把本轮 upsert 合并进 sidecar（文件锁，多进程分片不会互相覆盖）。"""
    if not updates:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(".vpush-local-meta.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        rows = load_sidecar(path)
        rows.update(updates)
        tmp = path.with_name(f".vpush-local-meta.jsonl.tmp.{os.getpid()}")
        tmp.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows.values()),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        if fix_owner:
            os.chown(path, CHOWN_UID, CHOWN_GID)
            os.chmod(path, 0o640)
            try:
                os.chown(lock_path, CHOWN_UID, CHOWN_GID)
            except OSError:
                pass
        return len(rows)


def pdf_basename(title: str, rid: int) -> str:
    return fit_bytes(sanitize_title(title), 200 - len(f"_{rid}")) + f"_{rid}.pdf"


def target_path(root: Path, cat_name: str, publish_time: str, title: str, rid: int,
                *, middleware: bool = False) -> Path:
    """经典：root/cicc-research/<品类>/<MMDD>/<中文名>_<id>.pdf
    中间层：root/local/cicc-research/YYYY/MM/DD/<sanitized>_<id>.pdf"""
    name = pdf_basename(title, rid)
    if middleware:
        year, month, day = date_parts(publish_time)
        return root / "local" / LIB_SLUG / year / month / day / name
    return root / LIB_SLUG / cat_name / day_dir(publish_time) / name


def arm_relpath(pdf_path: Path) -> str:
    """staging 下的 local/... 相对路径；找不到 local 则只留文件名。"""
    parts = pdf_path.parts
    if "local" in parts:
        return "/".join(parts[parts.index("local"):])
    return pdf_path.name


LAB_DONE_STATUSES = ("uploaded", "hot")
_RID_IN_NAME = re.compile(r"_(\d+)\.pdf$")


def lab_sqlite_path() -> Path:
    """puller 的 lab.sqlite；VPUSH_LAB_SQLITE 可覆盖，否则 $CACHE_ROOT/manifest/lab.sqlite。"""
    override = os.environ.get("VPUSH_LAB_SQLITE", "").strip()
    if override:
        return Path(override)
    root = os.environ.get("CACHE_ROOT", "").strip() or "/data/vpush-ima-cache"
    return Path(root) / "manifest" / "lab.sqlite"


def load_ingested_ids(db: Path) -> set[str] | None:
    """只读查 lab.sqlite，返回已入库（uploaded/hot）的中金报告 id。

    按文件名里的 _<id>.pdf 认，不按整条路径：标题改字或日期分片变了也算同一篇。
    同一篇每次下载 sha1 都不同（尺寸一样），puller 按 sha1 认不出重复，只能在这里挡。
    库不存在或读失败返回 None：照旧全下，不因为清单坏了漏采。"""
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            marks = ",".join("?" * len(LAB_DONE_STATUSES))
            rows = conn.execute(
                f"SELECT rel_path FROM files WHERE rel_path LIKE ? AND status IN ({marks})",
                (f"local/{LIB_SLUG}/%", *LAB_DONE_STATUSES),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"warning: 读不到 {db}（{e}），本轮不跳过已入库", file=sys.stderr)
        return None
    ids = set()
    for (rel,) in rows:
        m = _RID_IN_NAME.search(rel or "")
        if m:
            ids.add(m.group(1))
    return ids


def write_arm_item_sidecar(pdf_path: Path, row: dict, cat_name: str = "") -> None:
    """中间层每篇一份 JSON，字段对齐 sidecar_row，供日后 lab.sqlite 摄入。"""
    payload = dict(row)
    if cat_name and "category" not in payload:
        payload["category"] = cat_name
    payload["relpath"] = arm_relpath(pdf_path)
    dest = pdf_path.with_suffix(".json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, dest)


def prepare_target_dir(path: Path, *, fix_owner: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if fix_owner:
        for directory in (path.parent, path):
            os.chown(directory, CHOWN_UID, CHOWN_GID)
            os.chmod(directory, 0o750)


def setup_library(root: Path, slug: str, name: str, *, fix_owner: bool) -> None:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    marker = d / ".vpush-local-library.json"
    if not marker.exists():
        marker.write_text(json.dumps({
            "name": name, "enabled": False, "tags": ["中金研报"],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if fix_owner:
        for p in [d, marker]:
            os.chown(p, CHOWN_UID, CHOWN_GID)
        os.chmod(d, 0o750)
        os.chmod(marker, 0o640)


def fetch_param(sess: Session) -> dict:
    return sess.request("/reports/api/v3/param")["data"]


def list_page(sess: Session, cat_id: int, page: int, start: str | None, end: str | None) -> dict:
    body = dict(DEFAULT_BODY)
    body["portalCategoryId"] = str(cat_id)
    body["pubTimeStart"] = start
    body["pubTimeEnd"] = end
    body["page"] = page
    return sess.request("/reports/api/v3/page", body=body)["data"]


def viewer_pdf(sess: Session, rid: int) -> bytes:
    """在线阅读流：detail → signatureUrl(/v3/fetchPdf/<token>)，不计入月度下载配额
    （2026-09 实测：写不写 download/list 记录 309→309，内容与 download 版逐页一致且
    无下载水印）。detail 接口本身是读操作，不占配额。"""
    data = sess.request(f"/reports/api/v3/detail?id={rid}")["data"]
    sig = data.get("signatureUrl")
    if not sig:
        raise RuntimeError(f"detail 未返回 signatureUrl（id={rid}）")
    path = sig.replace(BASE, "")
    payload = sess.request(path, raw=True, tries=2)
    if not payload.startswith(b"%PDF"):
        raise RuntimeError("fetchPdf 非 PDF 响应: " + payload[:200].decode("utf-8", "replace"))
    return payload


def download_pdf(sess: Session, rid: int) -> bytes:
    """配额版下载（默认不用）：GET /v3/download/<id>，计入月度 300 篇配额（400013）。"""
    payload = sess.request(f"/reports/api/v3/download/{rid}", raw=True, tries=2)
    if not payload.startswith(b"%PDF"):
        text = payload[:200].decode("utf-8", "replace")
        if b"400013" in payload[:200]:
            write_paused("quota", "code 400013 本月配额已满")
            sys.exit("本月研报下载数量已达上限（code 400013）：等配额重置后重跑即可续传。")
        raise RuntimeError("非 PDF 响应: " + text)
    return payload


# 中金下载时 iTextSharp 注入的水印贴图尺寸：756x192=身份贴片（*先伟 **邮箱 + 时间戳，
# 每页平铺约 90 次），380x138=页中大灰色 CICC logo。识别按尺寸（资源名跨页会变）。
WM_SIZES = {(756, 192), (380, 138)}
_wm_warned = False


def strip_watermark_result(payload: bytes) -> tuple[bytes, str]:
    """移除已知中金贴图水印，并验证页面结构与文本层保持一致。"""
    global _wm_warned
    try:
        import fitz
    except ImportError:
        if not _wm_warned:
            print("WARN: 未装 python3-fitz/pymupdf，本次不去水印", file=sys.stderr)
            _wm_warned = True
        return payload, "watermark_unavailable"
    doc = None
    try:
        doc = fitz.open(stream=payload, filetype="pdf")
        if _pdf_has_unsupported_watermark_features(doc):
            return payload, "unsupported_features"
        stripped = False
        for page in doc:
            wm = [img for img in page.get_images(full=True)
                  if (img[2], img[3]) in WM_SIZES and doc.xref_stream(img[0])]
            if not wm:
                continue
            for xref in page.get_contents():  # 直接引用时顺便移除 Do；嵌套 Form 内引用靠清空图流处理
                cont = doc.xref_stream(xref)
                hit = False
                for img in wm:
                    pat = (f"/{img[7]} Do").encode("latin-1")
                    if pat in cont:
                        cont = cont.replace(pat, b"n ")  # 空操作，保持 q/Q 平衡
                        hit = True
                if hit:
                    doc.update_stream(xref, cont)
            for image_xref, smask_xref in {(img[0], img[1]) for img in wm}:
                doc.update_stream(image_xref, b"")
                if smask_xref:
                    doc.update_stream(smask_xref, b"")
            stripped = True
        if not stripped:
            return payload, "not_watermarked"
        out = doc.tobytes(garbage=0, deflate=True, use_objstms=0)
        rejection = pdf_structure_result(payload, out)
        return (payload, rejection) if rejection else (out, "watermark_stripped")
    except Exception as e:  # noqa: BLE001 — 去水印失败不阻断采集
        print(f"WARN: 去水印失败，保留原文件: {e}", file=sys.stderr)
        return payload, "watermark_error"
    finally:
        if doc is not None:
            doc.close()


def strip_watermark(payload: bytes) -> bytes:
    return strip_watermark_result(payload)[0]


COMPRESS_RATIO = 0.90  # 压缩后体积低于原件 90% 才采用；已优化的 PDF 压不动，自动跳过（幂等）
COMPRESS_MIN_SAVING = 256 * 1024
IMA_FLAT_MIN_BYTES = 2 * 1024 * 1024
IMA_FLAT_MARKER = b"ima-flat-v3"
IMA_FLAT_FONT = "Microsoft YaHei Regular"
_gs_warned = False


def _pdf_has_unsupported_features(doc) -> bool:
    if doc.is_form_pdf or doc.embfile_count() or doc.get_ocgs():
        return True
    catalog = doc.pdf_catalog()
    if any(doc.xref_get_key(catalog, key)[0] != "null"
           for key in ("Perms", "AcroForm", "OpenAction", "AA", "Names", "Collection",
                       "StructTreeRoot", "MarkInfo", "PageLabels")):
        return True
    return any(
        any(page.annots() or ()) or any(page.widgets() or ()) or page.get_links()
        for page in doc
    )


def _pdf_has_unsupported_watermark_features(doc) -> bool:
    if doc.is_form_pdf or doc.embfile_count() or doc.get_ocgs():
        return True
    catalog = doc.pdf_catalog()
    if any(doc.xref_get_key(catalog, key)[0] != "null"
           for key in ("Perms", "AcroForm", "OpenAction", "AA", "Names", "Collection",
                       "PageLabels")):
        return True
    return any(
        any(page.annots() or ()) or any(page.widgets() or ())
        for page in doc
    )


def _pdf_documents_structure_result(da, db, *, watermark: bool = False) -> str:
    unsupported = (_pdf_has_unsupported_watermark_features if watermark
                   else _pdf_has_unsupported_features)
    if unsupported(da) or unsupported(db):
        return "unsupported_features"
    if len(da) != len(db) or da.get_toc(simple=True) != db.get_toc(simple=True):
        return "content_mismatch"
    if watermark:
        for key in ("StructTreeRoot", "MarkInfo"):
            if (da.xref_get_key(da.pdf_catalog(), key) !=
                    db.xref_get_key(db.pdf_catalog(), key)):
                return "content_mismatch"
    boxes = ("mediabox", "cropbox", "bleedbox", "trimbox", "artbox")
    for pa, pb in zip(da, db):
        if pa.rotation != pb.rotation:
            return "content_mismatch"
        if watermark and pa.get_links() != pb.get_links():
            return "content_mismatch"
        for box in boxes:
            if any(abs(x - y) > 0.01 for x, y in zip(
                    getattr(pa, box), getattr(pb, box))):
                return "content_mismatch"
        if re.sub(r"\s+", "", pa.get_text()) != re.sub(r"\s+", "", pb.get_text()):
            return "content_mismatch"
    return ""


def pdf_structure_result(a: bytes, b: bytes) -> str:
    """校验页面结构和文本层；去水印允许渲染按预期变化。"""
    try:
        import fitz
    except ImportError:
        return "verify_unavailable"
    try:
        with fitz.open(stream=a, filetype="pdf") as da, fitz.open(
                stream=b, filetype="pdf") as db:
            return _pdf_documents_structure_result(da, db, watermark=True)
    except Exception:
        return "verify_error"


def pdf_equivalence_result(a: bytes, b: bytes) -> str:
    """返回空串表示可安全替换，否则返回可记录的拒绝或临时错误原因。"""
    try:
        import fitz
    except ImportError:
        return "verify_unavailable"
    try:
        with fitz.open(stream=a, filetype="pdf") as da, fitz.open(
                stream=b, filetype="pdf") as db:
            rejection = _pdf_documents_structure_result(da, db)
            if rejection:
                return rejection
            matrix = fitz.Matrix(96 / 72, 96 / 72)
            for pa, pb in zip(da, db):
                pix_a = pa.get_pixmap(matrix=matrix, colorspace=fitz.csRGB,
                                      alpha=False, annots=False)
                pix_b = pb.get_pixmap(matrix=matrix, colorspace=fitz.csRGB,
                                      alpha=False, annots=False)
                if ((pix_a.width, pix_a.height, pix_a.stride) !=
                        (pix_b.width, pix_b.height, pix_b.stride) or
                        pix_a.samples != pix_b.samples):
                    return "content_mismatch"
            return ""
    except Exception:
        return "verify_error"


def pdf_equivalent(a: bytes, b: bytes) -> bool:
    return not pdf_equivalence_result(a, b)


def _compression_rejection(original: bytes, candidate: bytes) -> str:
    if (len(candidate) >= len(original) * COMPRESS_RATIO or
            len(original) - len(candidate) < COMPRESS_MIN_SAVING):
        return "ratio_rejected"
    return pdf_equivalence_result(original, candidate)


def _subset_ima_flat(data: bytes) -> tuple[bytes | None, str]:
    if len(data) < IMA_FLAT_MIN_BYTES or IMA_FLAT_MARKER not in data:
        return None, "not_ima_flat"
    try:
        import fitz
    except ImportError:
        return None, "subset_unavailable"
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.metadata.get("subject") != IMA_FLAT_MARKER.decode():
                return None, "not_ima_flat"
            if not hasattr(doc, "subset_fonts"):
                return None, "subset_unavailable"
            if not any(font[3] == IMA_FLAT_FONT
                       for page in doc for font in page.get_fonts(full=True)):
                return None, "font_already_subset"
            doc.subset_fonts(verbose=False)
            return doc.tobytes(garbage=4, deflate=True, use_objstms=1), ""
    except Exception:
        return None, "subset_error"


def _ghostscript_compress(data: bytes) -> tuple[bytes | None, str]:
    global _gs_warned
    gs = shutil.which("gs")
    if not gs:
        if not _gs_warned:
            print("WARN: 未装 ghostscript，跳过压缩", file=sys.stderr)
            _gs_warned = True
        return None, "gs_unavailable"
    try:
        with tempfile.TemporaryDirectory() as td:
            src, out = os.path.join(td, "s.pdf"), os.path.join(td, "o.pdf")
            with open(src, "wb") as f:
                f.write(data)
            r = subprocess.run(
                [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
                 "-dPDFSETTINGS=/prepress", "-dAutoRotatePages=/None",
                 "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={out}", src],
                capture_output=True, timeout=600, check=False)
            if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                return None, "gs_error"
            with open(out, "rb") as f:
                return f.read(), ""
    except Exception:  # noqa: BLE001 — 压缩失败不阻断采集
        return None, "gs_error"


def _pdf_rewrite_guard_result(data: bytes) -> str:
    try:
        import fitz
    except ImportError:
        return "verify_unavailable"
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "unsupported_features" if _pdf_has_unsupported_features(doc) else ""
    except Exception:
        return "verify_error"


def compress_pdf_result(data: bytes) -> tuple[bytes, str]:
    """无损条件压缩并返回结果原因；任何失败都保留原文。"""
    if not data.startswith(b"%PDF"):
        return data, "not_pdf"
    guard = _pdf_rewrite_guard_result(data)
    if guard:
        return data, guard

    subset, subset_reason = _subset_ima_flat(data)
    if subset is not None:
        rejection = _compression_rejection(data, subset)
        if not rejection:
            return subset, "compressed_subset"
        subset_reason = rejection

    candidate, gs_reason = _ghostscript_compress(data)
    if candidate is not None:
        rejection = _compression_rejection(data, candidate)
        if not rejection:
            return candidate, "compressed_gs"
        return data, rejection
    if subset_reason not in {"not_ima_flat", "font_already_subset"}:
        return data, subset_reason
    return data, gs_reason


def compress_pdf(data: bytes) -> bytes:
    """IMA-flat 先做字体子集化，其他 PDF 使用 gs /prepress；失败保留原文。"""
    return compress_pdf_result(data)[0]


def load_filters_file(path: Path) -> tuple[list[str], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("filters must be an object")
    result = []
    for key in ("categories", "keywords"):
        values = data.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"{key} must be a string list")
        result.append(list(dict.fromkeys(v.strip() for v in values if v.strip())))
    return result[0], result[1]


def write_completion_marker(path: Path, command_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".completed.{os.getpid()}")
    tmp.write_text(json.dumps({"id": command_id}), encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cookie-file", default=None,
                    help="Cookie 文件（默认 /root/cicc/cookies.txt；可用 VPUSH_CICC_COOKIE_FILE 覆盖）")
    ap.add_argument("--root", default=None,
                    help="输出根。默认 /srv/vpush-ima/local；中间层改为 "
                         "VPUSH_ARM_STAGING_ROOT（默认 /data/vpush-ima-cache/staging）")
    ap.add_argument("--arm-middleware", action="store_true",
                    help="写 ARM staging 日期分片（或设 VPUSH_ARM_MIDDLEWARE=1）；默认关")
    ap.add_argument("--days", type=int, default=0, help="最近 N 天（含今天），0=不限")
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD（如 2026-01-01 采今年），优先于 --days")
    ap.add_argument("--all", action="store_true", help="全量，不按日期过滤")
    ap.add_argument("--categories", default="", help="逗号分隔的一级品类名，默认全部")
    ap.add_argument("--keywords", default="", help="逗号分隔的标题关键词白名单，默认不过滤")
    ap.add_argument("--filters-file", default="",
                    help="dispatch 生成的 JSON 过滤配置；优先于逗号分隔参数")
    ap.add_argument("--completion-file", default="",
                    help="成功结束时原子写入的命令完成标记")
    ap.add_argument("--limit", type=int, default=0, help="本次最多下载多少个 PDF（调试用）")
    ap.add_argument("--endpoint", choices=["viewer", "download"], default="viewer",
                    help="取 PDF 路径：viewer=在线阅读流 fetchPdf（不计月度配额，默认）；download=旧下载接口（计 300/月配额）")
    ap.add_argument("--page-start", type=int, default=1,
                    help="每品类从第几页开始列（同品类拆多进程对跑用）")
    ap.add_argument("--dry-run", action="store_true", help="只列清单不下载")
    ap.add_argument("--compress", action="store_true",
                    help="下载时同步 gs /prepress 压缩（1核机上单篇可达几十秒，默认关；推荐采完后用 "
                         "pdf_backfill_compress.py --root /srv/vpush-ima/local/cicc-research "
                         "--strip-watermark 低优先级回刷）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        assert x_time().isdigit() and len(x_time()) >= 13
        assert sanitize_title('A/B：C"今年的<报告>') == "A B：C 今年的 报告"  # 全角保留，非法字符换空格
        assert fit_bytes("中" * 300, 20).encode().__len__() <= 20
        assert day_dir("2026-08-29T12:43:03Z") == "0829"  # UTC+8
        assert day_dir("2026-08-29T17:43:03Z") == "0830"  # 跨日归北京日期
        p = target_path(Path("/x"), "宏观经济", "2026-08-01T00:00:00Z", "标题/1", 42)
        assert str(p) == "/x/cicc-research/宏观经济/0801/标题 1_42.pdf", str(p)
        arm = target_path(Path(DEFAULT_ARM_STAGING_ROOT), "宏观经济",
                          "2026-08-01T00:00:00Z", "标题/1", 42, middleware=True)
        assert str(arm) == (
            "/data/vpush-ima-cache/staging/local/cicc-research/2026/08/01/标题 1_42.pdf"
        ), str(arm)
        # UTC 17:43 → 北京次日：经典 MMDD=0830，中间层日期分片=2026/08/30
        classic_next = target_path(Path("/x"), "公司研究", "2026-08-29T17:43:03Z", "宁德", 7)
        assert str(classic_next) == "/x/cicc-research/公司研究/0830/宁德_7.pdf"
        arm_next = target_path(Path("/s"), "公司研究", "2026-08-29T17:43:03Z", "宁德", 7,
                               middleware=True)
        assert str(arm_next) == "/s/local/cicc-research/2026/08/30/宁德_7.pdf"
        assert resolve_output_root(None, middleware=False) == Path(DEFAULT_STORAGE_ROOT)
        assert resolve_output_root(None, middleware=True) == Path(DEFAULT_ARM_STAGING_ROOT)
        assert resolve_cookie_file(None) == Path(DEFAULT_COOKIE_FILE)
        assert should_fix_owner(Path("/data/vpush-ima-cache/staging")) is False
        row = sidecar_row({
            "id": 42,
            "title": "宁德时代深度",
            "summary": "动力电池",
            "reportType": "深度报告",
            "documentLabels": ["新能源"],
            "portalCategoryIds": [7],
            "publishTime": "2026-08-29T17:43:03Z",
            "analysts": [{"name": "张三"}],
        }, {7: "电力设备"}, "公司研究")
        assert row["id"] == "42"
        assert row["publish"] == "2026-08-30"  # UTC 17:43 → 北京 0830
        assert row["day"] == "0830"
        assert row["tags"] == ["深度报告", "公司研究", "新能源", "电力设备"]
        assert row["authors"] == "张三"
        assert row["summary"] == "动力电池"
        try:
            import fitz
        except ImportError:
            fitz = None
        if fitz:  # 去水印：造一个只含水印尺寸图的 PDF，断言渲染后无任何可见像素
            d = fitz.open()
            pg = d.new_page()
            px = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 756, 192))
            px.set_rect(px.irect, (0, 0, 0))  # 黑块，若未剥离则渲染必非全白
            pg.insert_image(fitz.Rect(0, 0, 378, 96), pixmap=px)
            buf = io.BytesIO()
            d.save(buf)
            out = fitz.open(stream=strip_watermark(buf.getvalue()), filetype="pdf")
            assert len(out) == 1
            pix = out[0].get_pixmap(dpi=72)
            assert set(pix.samples) == {255}, "水印未被剥离"
            empty = fitz.open(stream=buf.getvalue(), filetype="pdf")
            empty.update_stream(empty[0].get_images(full=True)[0][0], b"")
            empty_bytes = empty.tobytes()
            assert strip_watermark(empty_bytes) == empty_bytes, "空水印资源不应触发重写"
            # 内容等价判断：同文本等价，异文本不等价
            assert pdf_equivalent(buf.getvalue(), buf.getvalue())
            d2 = fitz.open()
            pg2 = d2.new_page()
            pg2.insert_text((72, 72), "different")
            b2 = io.BytesIO()
            d2.save(b2)
            assert not pdf_equivalent(buf.getvalue(), b2.getvalue())
        assert compress_pdf(b"not a pdf") == b"not a pdf"  # 非 PDF 原样返回
        print("self-test ok")
        return

    cookie_path = resolve_cookie_file(args.cookie_file)
    if not cookie_path.exists():
        sys.exit(f"Cookie 文件不存在: {cookie_path}")
    sess = Session(cookie_path.read_text(encoding="utf-8"))
    middleware = middleware_enabled(args.arm_middleware)
    root = resolve_output_root(args.root, middleware=middleware)
    fix_owner = should_fix_owner(root)
    if fix_owner:
        root.mkdir(parents=True, exist_ok=True)
        os.chown(root, CHOWN_UID, CHOWN_GID)
        os.chmod(root, 0o750)

    wanted = [c.strip() for c in args.categories.split(",") if c.strip()]
    keywords = [k for k in (args.keywords or "").split(",") if k.strip()]
    if args.filters_file:
        wanted, keywords = load_filters_file(Path(args.filters_file))
    param = fetch_param(sess)  # 列表接口是读操作：能成功即登录态有效
    all_cats = param.get("treeData") or []
    id_name = category_id_names(param)
    try:
        os.remove(resolve_paused_file(middleware=middleware))  # 清掉上次熔断标记（再熔断会重写）
    except OSError:
        pass
    if wanted:
        cats = [c for c in all_cats if c["name"] in wanted]
        missing = set(wanted) - {c["name"] for c in cats}
        if missing:
            sys.exit(f"未知品类: {missing}；可选: {[c['name'] for c in all_cats]}")
    else:
        cats = all_cats

    end = date.today().strftime("%Y-%m-%d")
    start = args.since or ((date.today() - timedelta(days=args.days - 1)).strftime("%Y-%m-%d") if args.days else None)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "ingested": 0}
    stop = False
    # 幂等靠磁盘文件名（含报告 id，唯一）；中间层另查 lab.sqlite 已入库 id（只读）
    ingested_ids = load_ingested_ids(lab_sqlite_path()) if middleware else None

    if not middleware:
        setup_library(root, LIB_SLUG, LIB_NAME, fix_owner=fix_owner)
    sidecar_path = root / LIB_SLUG / SIDECAR_NAME
    pending_meta: dict = {}

    def flush_sidecar() -> None:
        nonlocal pending_meta
        if pending_meta and not middleware:
            merge_sidecar(sidecar_path, pending_meta, fix_owner=fix_owner)
            pending_meta = {}
        elif middleware:
            pending_meta = {}

    for cat in cats:
        name = cat["name"]
        if stop:
            break
        page, total_seen = args.page_start, 0
        while True:
            data = list_page(sess, cat["id"], page, start, None if args.all else end)
            page_items = data.get("content") or []
            items = filter_by_keywords(page_items, keywords)
            if page == 1:
                print(f"[{name}] total={data.get('totalElements')} pages={data.get('totalPages')}")
            if not page_items:
                break
            for it in items:
                total_seen += 1
                rid = it["id"]
                if args.limit and stats["downloaded"] >= args.limit:
                    stop = True
                    break
                tp = target_path(root, name, it["publishTime"], it["title"], rid,
                                 middleware=middleware)
                row = sidecar_row(it, id_name, name)
                if row["id"] and not middleware:
                    pending_meta[row["id"]] = row
                    if len(pending_meta) >= 20:
                        flush_sidecar()
                if tp.exists():
                    if middleware and row["id"] and not tp.with_suffix(".json").exists():
                        write_arm_item_sidecar(tp, row, name)
                    stats["skipped"] += 1
                    continue
                if ingested_ids is not None and str(rid) in ingested_ids:
                    stats["skipped"] += 1  # 计入「已存在跳过」，汇总行格式不变
                    stats["ingested"] += 1
                    continue
                if args.dry_run:
                    print(f"  DRY {tp.relative_to(root)}")
                    continue
                try:
                    fetch = download_pdf if args.endpoint == "download" else viewer_pdf
                    payload = strip_watermark(fetch(sess, rid))  # 无损去水印，毫秒级
                    if args.compress:
                        payload = compress_pdf(payload)
                    prepare_target_dir(tp.parent, fix_owner=fix_owner)
                    tp.write_bytes(payload)
                    if fix_owner:
                        os.chown(tp, CHOWN_UID, CHOWN_GID)
                        os.chmod(tp, 0o640)
                    if middleware and row["id"]:
                        write_arm_item_sidecar(tp, row, name)
                    stats["downloaded"] += 1
                    if stats["downloaded"] % 20 == 0:
                        print(f"  ... {stats['downloaded']} 下载 / {stats['skipped']} 已存在 / {stats['failed']} 失败")
                except Exception as e:  # noqa: BLE001 — 单个失败不中断整批
                    stats["failed"] += 1
                    print(f"  FAIL {rid} {it['title'][:40]}: {e}", file=sys.stderr)
                time.sleep(SLEEP_DL)
            if stop or page >= MAX_PAGES or len(page_items) < PAGE_SIZE:
                break
            page += 1
            time.sleep(SLEEP_PAGE)
        flush_sidecar()
        print(f"[{name}] 列出 {total_seen} 篇" + ("（中止）" if stop else ""))

    flush_sidecar()
    if ingested_ids is not None:
        print(f"lab.sqlite 已入库跳过 {stats['ingested']}（已入库 id {len(ingested_ids)} 个）")
    print(f"完成：下载 {stats['downloaded']}，已存在跳过 {stats['skipped']}，失败 {stats['failed']}")
    if stats["failed"]:
        sys.exit(1)
    if args.completion_file:
        completion_path = Path(args.completion_file)
        write_completion_marker(completion_path, completion_path.stem.removeprefix("completed-"))


if __name__ == "__main__":
    main()
