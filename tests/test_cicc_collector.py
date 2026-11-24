"""中金采集控制：NFS 控制目录读写、触发命令、每日增量开关、admin API。"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cicc_collector import CiccControl, from_env
from app.main import create_app
from scripts import cicc_report_collector


@pytest.fixture
def ctrl(tmp_path):
    archive = tmp_path / "archive"
    (archive / "local" / ".cicc" / "commands").mkdir(parents=True)
    return CiccControl(str(archive)), archive


def test_status_missing_file_reports_stale(ctrl):
    ctl, _ = ctrl
    data = ctl.status()
    assert data["available"] is True
    assert data["stale"] is True


def test_status_parses_storage_side_file(ctrl):
    ctl, archive = ctrl
    status_path = archive / "local" / ".cicc" / "status.json"
    status_path.write_text(json.dumps({
        "ts": time.time(), "running": 2, "files_total": 100,
        "schedule_enabled": True, "logs": {"y2026_company": "... 20 下载"},
        "commands": [], "compress_running": 0, "last_incremental": None,
    }), encoding="utf-8")
    data = ctl.status()
    assert data["stale"] is False
    assert data["running"] == 2
    assert data["files_total"] == 100


def test_trigger_writes_atomic_command_file(ctrl):
    ctl, archive = ctrl
    result = ctl.trigger("year", "kale")
    assert result == {"queued": "year"}
    files = list((archive / "local" / ".cicc" / "commands").glob("*.json"))
    assert len(files) == 1
    cmd = json.loads(files[0].read_text(encoding="utf-8"))
    assert cmd["mode"] == "year"
    assert cmd["actor"] == "kale"
    assert not list((archive / "local" / ".cicc" / "commands").glob(".tmp*"))


def test_trigger_rejects_unknown_mode(ctrl):
    ctl, _ = ctrl
    with pytest.raises(ValueError):
        ctl.trigger("rm -rf /", "kale")


def test_schedule_toggle(ctrl):
    ctl, archive = ctrl
    flag = archive / "local" / ".cicc" / "incremental.enabled"
    assert ctl.schedule_enabled() is False
    assert ctl.set_schedule(True) == {"schedule_enabled": True}
    assert flag.exists()
    assert ctl.schedule_enabled() is True
    assert ctl.set_schedule(False) == {"schedule_enabled": False}
    assert not flag.exists()


def test_read_schedule_rejects_out_of_range_stored_time(ctrl):
    ctl, archive = ctrl
    status_path = archive / "local" / ".cicc" / "status.json"
    status_path.write_text(json.dumps({
        "ts": time.time(), "storage": {"schedule": {"time": "24:99"}},
    }), encoding="utf-8")

    assert ctl.read_schedule()["time"] == "03:00"


def test_from_env_none_without_archive(monkeypatch):
    monkeypatch.delenv("IMA_ARCHIVE_ROOT", raising=False)
    assert from_env() is None


def _admin_client():
    tmp = tempfile.mkdtemp()
    os.environ["DAV_UI_ONLY"] = "1"
    os.environ["IMA_ARCHIVE_ROOT"] = tempfile.mkdtemp()
    os.environ.pop("IMA_PULL_URL", None)
    cmd_dir = Path(os.environ["IMA_ARCHIVE_ROOT"]) / "local" / ".cicc" / "commands"
    cmd_dir.mkdir(parents=True)
    app = create_app(config=None, db_path=Path(tmp) / "cicc.db")
    return TestClient(app), cmd_dir


def _admin_headers(client):
    client.app.state.db.add_register_code("CICC01")
    resp = client.post("/api/auth/register", json={
        "username": "admin1", "password": "secret1234", "code": "CICC01"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    client.app.state.db.update_user(data["user"]["id"], is_admin=True)
    return {"Authorization": f"Bearer {data['token']}"}


def test_prepare_target_dir_repairs_category_parent(monkeypatch, tmp_path):
    target = tmp_path / "local" / "cicc-research" / "金融工程" / "0903"
    target.parent.mkdir(parents=True)
    target.parent.chmod(0o700)
    chowns = []
    monkeypatch.setattr(
        cicc_report_collector.os,
        "chown",
        lambda path, uid, gid: chowns.append((Path(path), uid, gid)),
    )

    cicc_report_collector.prepare_target_dir(target, fix_owner=True)

    assert target.is_dir()
    assert chowns == [
        (target.parent, cicc_report_collector.CHOWN_UID, cicc_report_collector.CHOWN_GID),
        (target, cicc_report_collector.CHOWN_UID, cicc_report_collector.CHOWN_GID),
    ]
    assert target.parent.stat().st_mode & 0o777 == 0o750
    assert target.stat().st_mode & 0o777 == 0o750


def test_load_filters_file_preserves_commas_and_rejects_invalid_values(tmp_path):
    path = tmp_path / "filters.json"
    path.write_text(json.dumps({
        "categories": ["公司研究"],
        "keywords": ["alpha,beta", "半导体"],
    }, ensure_ascii=False), encoding="utf-8")

    assert cicc_report_collector.load_filters_file(path) == (
        ["公司研究"], ["alpha,beta", "半导体"],
    )

    path.write_text(json.dumps({"categories": [], "keywords": [123]}), encoding="utf-8")
    with pytest.raises(ValueError, match="keywords"):
        cicc_report_collector.load_filters_file(path)


def test_write_completion_marker_is_atomic(tmp_path):
    path = tmp_path / "completed.json"
    cicc_report_collector.write_completion_marker(path, "command-id")

    assert json.loads(path.read_text(encoding="utf-8")) == {"id": "command-id"}
    assert not list(tmp_path.glob(".completed.*"))


@pytest.mark.parametrize("first_matches", [False, True])
@pytest.mark.parametrize("download_fails", [False, True])
def test_collector_filtered_pagination_and_completion(tmp_path, monkeypatch,
                                                     first_matches, download_fails):
    import sys

    m = cicc_report_collector
    cookie = tmp_path / "fixture-cookie.txt"
    cookie.write_text("offline-fixture")
    marker = tmp_path / "completed-test.json"
    monkeypatch.setattr(sys, "argv", ["collector", "--cookie-file", str(cookie),
                                     "--root", str(tmp_path), "--keywords", "match",
                                     "--completion-file", str(marker)])
    monkeypatch.setattr(m, "Session", lambda _: object())
    monkeypatch.setattr(m, "PAUSED_FILE", str(tmp_path / "paused.json"))
    monkeypatch.setattr(m, "fetch_param", lambda _: {"treeData": [{"id": 1, "name": "test"}]})
    monkeypatch.setattr(m, "PAGE_SIZE", 2)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    monkeypatch.setattr(m, "strip_watermark", lambda value: value)
    pages, downloads = [], []

    def row(rid, title):
        return {"id": rid, "title": title, "publishTime": "2026-09-05T01:00:00Z"}

    def list_page(_sess, _cat, page, *_dates):
        pages.append(page)
        return {"content": {1: [row(1, "match" if first_matches else "other"), row(2, "other")],
                            2: [row(3, "match")]}[page]}

    def download(_sess, rid):
        downloads.append(rid)
        if download_fails:
            raise RuntimeError("offline download failure")
        return b"%PDF-fixture"

    monkeypatch.setattr(m, "list_page", list_page)
    monkeypatch.setattr(m, "viewer_pdf", download)
    if download_fails:
        with pytest.raises(SystemExit) as exc:
            m.main()
        assert exc.value.code == 1
        assert not marker.exists()
    else:
        m.main()
        assert json.loads(marker.read_text()) == {"id": "test"}
    assert pages == [1, 2]
    assert downloads == ([1, 3] if first_matches else [3])


def test_collector_middleware_writes_date_shard_and_json(tmp_path, monkeypatch):
    import sys

    m = cicc_report_collector
    cookie = tmp_path / "fixture-cookie.txt"
    cookie.write_text("offline-fixture")
    staging = tmp_path / "staging"
    monkeypatch.delenv("VPUSH_ARM_MIDDLEWARE", raising=False)
    monkeypatch.delenv("VPUSH_LAB_SQLITE", raising=False)
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path / "cache"))  # 没有 lab.sqlite：照旧全下
    monkeypatch.setattr(sys, "argv", [
        "collector", "--arm-middleware", "--cookie-file", str(cookie),
        "--root", str(staging), "--keywords", "match",
    ])
    monkeypatch.setattr(m, "Session", lambda _: object())
    monkeypatch.setattr(m, "PAUSED_FILE", str(tmp_path / "paused.json"))
    monkeypatch.setattr(m, "fetch_param", lambda _: {"treeData": [{"id": 1, "name": "宏观经济"}]})
    monkeypatch.setattr(m, "PAGE_SIZE", 2)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    monkeypatch.setattr(m, "strip_watermark", lambda value: value)
    chowns = []
    monkeypatch.setattr(m.os, "chown", lambda *a, **k: chowns.append(a))

    def list_page(_sess, _cat, page, *_dates):
        return {"content": [{"id": 42, "title": "match 标题",
                             "publishTime": "2026-08-01T00:00:00Z",
                             "summary": "摘要"}]} if page == 1 else {"content": []}

    monkeypatch.setattr(m, "list_page", list_page)
    monkeypatch.setattr(m, "viewer_pdf", lambda *_: b"%PDF-arm")
    m.main()

    pdf = staging / "local" / "cicc-research" / "2026" / "08" / "01" / "match 标题_42.pdf"
    sidecar = pdf.with_suffix(".json")
    assert pdf.read_bytes() == b"%PDF-arm"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["id"] == "42"
    assert data["relpath"] == "local/cicc-research/2026/08/01/match 标题_42.pdf"
    assert data["category"] == "宏观经济"
    assert not (staging / "cicc-research" / ".vpush-local-library.json").exists()
    assert chowns == []
    classic = staging / "cicc-research" / "宏观经济" / "0801" / "match 标题_42.pdf"
    assert not classic.exists()


def _lab_sqlite(path: Path, rows: list[tuple[str, str]]) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, rel_path TEXT NOT NULL UNIQUE,"
                 " status TEXT NOT NULL)")
    conn.executemany("INSERT INTO files (rel_path, status) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def test_load_ingested_ids_reads_done_cicc_rows_only(tmp_path):
    m = cicc_report_collector
    db = tmp_path / "lab.sqlite"
    _lab_sqlite(db, [
        ("local/cicc-research/2026/11/22/图说大宗_405662.pdf", "hot"),
        ("local/cicc-research/2026/11/21/老标题_405600.pdf", "uploaded"),
        ("local/cicc-research/2026/11/23/上传失败_405700.pdf", "failed"),
        ("local/cicc-research/2026/11/23/还在暂存_405701.pdf", "staging"),
        ("local/ima-reports/2026/11/23/别的库_405702.pdf", "hot"),
    ])
    assert m.load_ingested_ids(db) == {"405662", "405600"}
    assert m.load_ingested_ids(tmp_path / "missing.sqlite") is None
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not a database")
    assert m.load_ingested_ids(broken) is None


def test_collector_middleware_skips_ids_already_in_lab_sqlite(tmp_path, monkeypatch, capsys):
    import sys

    m = cicc_report_collector
    cookie = tmp_path / "fixture-cookie.txt"
    cookie.write_text("offline-fixture")
    staging = tmp_path / "staging"
    db = tmp_path / "lab.sqlite"
    # 42 已入库（标题和日期分片都和这次列表不同，也要认出来）；43 上传失败，要重下
    _lab_sqlite(db, [("local/cicc-research/2026/07/31/旧标题_42.pdf", "hot"),
                     ("local/cicc-research/2026/08/01/match 二_43.pdf", "failed")])
    monkeypatch.delenv("VPUSH_ARM_MIDDLEWARE", raising=False)
    monkeypatch.setenv("VPUSH_LAB_SQLITE", str(db))
    monkeypatch.setattr(sys, "argv", [
        "collector", "--arm-middleware", "--cookie-file", str(cookie), "--root", str(staging),
    ])
    monkeypatch.setattr(m, "Session", lambda _: object())
    monkeypatch.setattr(m, "PAUSED_FILE", str(tmp_path / "paused.json"))
    monkeypatch.setattr(m, "fetch_param", lambda _: {"treeData": [{"id": 1, "name": "宏观经济"}]})
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    monkeypatch.setattr(m, "strip_watermark", lambda value: value)
    downloads = []

    def list_page(_sess, _cat, page, *_dates):
        return {"content": [
            {"id": 42, "title": "match 一", "publishTime": "2026-08-01T00:00:00Z"},
            {"id": 43, "title": "match 二", "publishTime": "2026-08-01T00:00:00Z"},
        ]} if page == 1 else {"content": []}

    def download(_sess, rid):
        downloads.append(rid)
        return b"%PDF-arm"

    monkeypatch.setattr(m, "list_page", list_page)
    monkeypatch.setattr(m, "viewer_pdf", download)
    m.main()

    assert downloads == [43]
    day = staging / "local" / "cicc-research" / "2026" / "08" / "01"
    assert not (day / "match 一_42.pdf").exists()
    assert (day / "match 二_43.pdf").exists()
    out = capsys.readouterr().out
    assert "lab.sqlite 已入库跳过 1" in out
    assert "完成：下载 1，已存在跳过 1，失败 0" in out



def test_admin_cicc_api(tmp_path, monkeypatch):
    monkeypatch.delenv("IMA_PULL_URL", raising=False)
    monkeypatch.setenv("IMA_ARCHIVE_ROOT", str(tmp_path / "archive"))
    (tmp_path / "archive" / "local" / ".cicc" / "commands").mkdir(parents=True)
    os.environ["DAV_UI_ONLY"] = "1"
    app = create_app(config=None, db_path=Path(tmp_path) / "cicc.db")
    client = TestClient(app)
    headers = _admin_headers(client)

    r = client.get("/api/admin/cicc/status", headers=headers)
    assert r.status_code == 200
    assert r.json()["available"] is True
    assert r.json()["stale"] is True

    r = client.post("/api/admin/cicc/trigger", json={"mode": "compress"},
                    headers=headers)
    assert r.status_code == 200
    assert r.json() == {"queued": "compress"}
    cmds = list((tmp_path / "archive" / "local" / ".cicc" / "commands").glob("*.json"))
    assert len(cmds) == 1

    r = client.post("/api/admin/cicc/trigger", json={"mode": "bogus"},
                    headers=headers)
    assert r.status_code == 400

    r = client.put("/api/admin/cicc/schedule", json={"enabled": True},
                   headers=headers)
    assert r.status_code == 200
    assert (tmp_path / "archive" / "local" / ".cicc" / "incremental.enabled").exists()
