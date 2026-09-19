import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Config
from app.db import DB
from app.main import create_app

_reg_code_seq = 0


def make_client(name="test.db", config=None):
    tmp = tempfile.mkdtemp()
    app = create_app(config=config, db_path=Path(tmp) / name)
    return TestClient(app)


def register(client, username="testadmin", password="secret1234", expect=200, code=None):
    global _reg_code_seq
    if code is None:
        _reg_code_seq += 1
        code = f"TEST{_reg_code_seq:04d}"
        client.app.state.db.add_register_code(code)
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "code": code},
    )
    assert resp.status_code == expect, resp.text
    return resp


def auth_headers(client, username="testadmin", password="secret1234"):
    data = register(client, username, password).json()
    # 测试辅助：注册后通过 DB 提升为管理员（生产环境只能由管理员指定）
    client.app.state.db.update_user(data["user"]["id"], is_admin=True)
    token = data["token"]
    return {"Authorization": f"Bearer {token}"}


def user_headers(client, username, password="pass123456"):
    token = register(client, username, password).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_app_exposes_shared_news_service():
    client = make_client("news-service.db")
    service = client.app.state.news_service
    assert service.db is client.app.state.db
    service.close()


def insert_news_article(db, source_id, published_at, external_id=None, images=None):
    feed_id = db.list_news_feeds(source_id=source_id)[0]["id"]
    return db.upsert_news_article({
        "source_id": source_id,
        "feed_id": feed_id,
        "external_id": external_id or f"article-{source_id}-{published_at}",
        "title": "测试财经新闻",
        "url": "https://example.com/article",
        "author": "作者",
        "summary": "摘要",
        "content_html": "<p>正文</p>",
        "images": images or [],
        "published_at": published_at,
        "fetched_at": published_at,
        "content_hash": "hash",
    })


def test_news_list_and_seen_anchor_are_user_scoped():
    client = make_client("news-api.db")
    first_headers = user_headers(client, "news_first")
    second_headers = user_headers(client, "news_second")
    db = client.app.state.db
    first_uid = db.get_user_by_username("news_first")["id"]
    second_uid = db.get_user_by_username("news_second")["id"]
    source_ids = [row["id"] for row in db.list_news_sources()]
    db.set_user_news_sources(first_uid, source_ids[:1])
    db.set_user_news_sources(second_uid, [])
    article_id = insert_news_article(db, source_ids[0], "2026-09-01T10:00:00+00:00")

    sources = client.get("/api/news/sources", headers=first_headers)
    assert sources.status_code == 200
    assert sources.json()["items"][0]["selected"] is True
    first = client.get("/api/news", headers=first_headers).json()
    assert first["items"][0]["id"] == article_id
    assert first["items"][0]["is_new"] is False
    assert "view_started_at" in first
    assert client.post(
        "/api/news/seen", headers=first_headers,
        json={"view_started_at": first["view_started_at"]},
    ).status_code == 200
    assert client.get(f"/api/news/{article_id}", headers=second_headers).status_code == 404
    assert client.get("/api/news", headers=second_headers).json()["items"] == []


def test_news_source_selection_and_invalid_selection_are_authenticated():
    client = make_client("news-source-api.db")
    headers = user_headers(client, "news_source_user")
    db = client.app.state.db
    uid = db.get_user_by_username("news_source_user")["id"]
    source_ids = [row["id"] for row in db.list_news_sources()]
    response = client.put("/api/me", headers=headers, json={"news_source_ids": []})
    assert response.status_code == 200
    assert db.list_user_news_source_ids(uid) == []
    response = client.put("/api/me", headers=headers, json={"news_source_ids": [999999]})
    assert response.status_code == 400
    assert db.list_user_news_source_ids(uid) == []
    response = client.put("/api/me", headers=headers, json={"news_source_ids": source_ids[:1]})
    assert response.status_code == 200
    assert db.list_user_news_source_ids(uid) == source_ids[:1]
    assert client.get("/api/news/sources").status_code == 401


def test_admin_news_source_hard_delete_works_without_archive():
    client = make_client("news-delete-api.db")
    headers = auth_headers(client)
    db = client.app.state.db
    source_id = db.add_news_source("硬删媒体")
    assert client.delete(
        f"/api/admin/news/sources/{source_id}", headers={"Authorization": "Bearer nope"}
    ).status_code == 401
    assert client.delete(
        f"/api/admin/news/sources/{source_id}", headers=headers
    ).status_code == 200
    assert db.get_news_source(source_id) is None
    assert client.delete(
        f"/api/admin/news/sources/{source_id}", headers=headers
    ).status_code == 404


def test_news_seen_rejects_naive_timestamp_and_moves_forward_only():
    client = make_client("news-seen-api.db")
    headers = user_headers(client, "news_seen_user")
    assert client.post(
        "/api/news/seen", headers=headers,
        json={"view_started_at": "2026-09-01T10:00:00"},
    ).status_code == 400
    assert client.post(
        "/api/news/seen", headers=headers,
        json={"view_started_at": "2026-09-01T10:00:00+00:00"},
    ).status_code == 200
    assert client.post(
        "/api/news/seen", headers=headers,
        json={"view_started_at": "2026-09-01T09:00:00+00:00"},
    ).status_code == 200
    assert client.post(
        "/api/news/seen", headers=headers,
        json={"view_started_at": "2999-01-01T00:00:00+00:00"},
    ).status_code == 400




def test_news_disabled_cache_archived_detail_and_image_headers(monkeypatch):
    client = make_client("news-permissions.db")
    headers = user_headers(client, "news_permission_user")
    db = client.app.state.db
    uid = db.get_user_by_username("news_permission_user")["id"]
    source_id = db.list_news_sources()[0]["id"]
    feed_id = db.list_news_feeds(source_id=source_id)[0]["id"]
    db.set_user_news_sources(uid, [source_id])
    article_id = db.upsert_news_article({
        "source_id": source_id, "feed_id": feed_id, "external_id": "permission-1",
        "title": "Permission", "url": "https://example.com/article", "author": "",
        "summary": "", "content_html": '<img data-news-image-index="0">',
        "images": ["https://feed.example/image.jpg"],
        "published_at": "2026-09-01T00:00:00+00:00",
        "fetched_at": "2026-09-01T00:00:00+00:00", "content_hash": "permission",
    })
    db.update_news_source(source_id, enabled=False)
    assert client.get("/api/news", headers=headers).json()["items"][0]["id"] == article_id

    monkeypatch.setattr("app.url_safety._resolve_host_ips", lambda host: ["93.184.216.34"])
    client.app.state.news_service.client = __import__("httpx").Client(
        transport=__import__("httpx").MockTransport(
            lambda request: __import__("httpx").Response(
                200, headers={"content-type": "image/jpeg"}, content=b"jpeg"
            )
        ),
        trust_env=False,
    )
    image = client.get(f"/api/news/{article_id}/images/0", headers=headers)
    assert image.status_code == 200
    assert image.headers["cache-control"] == "private, max-age=86400"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.content == b"jpeg"
    assert client.get(f"/api/news/{article_id}/images/0").status_code == 401

    db.set_news_source_archived(source_id, True)
    assert client.get(f"/api/news/{article_id}", headers=headers).status_code == 404


def test_admin_kols_pagination_and_filters():
    """管理大V列表：分页 + 关键词/平台/分类/状态筛选 + 权限。"""
    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    cid = db.add_category("财经")
    kids = []
    for i in range(12):
        kids.append(db.add_kol("xueqiu", f"测试大V{i}", f"uid{i}", category_id=cid if i < 6 else None))
    db.add_kol("twitter", "X博主", "xuser")
    db.set_kols_enabled(kids[:3], False)

    # 分页
    data = client.get("/api/admin/kols?limit=5&offset=0", headers=admin_headers).json()
    assert data["total"] == 13 and len(data["items"]) == 5
    assert len(data["ids"]) == 13
    assert set(data["ids"]) >= {item["id"] for item in data["items"]}
    assert all("subscriber_count" in item for item in data["items"])
    page2 = client.get("/api/admin/kols?limit=5&offset=5", headers=admin_headers).json()
    assert page2["items"][0]["id"] != data["items"][0]["id"]
    # 关键词（昵称/外部ID）
    data = client.get("/api/admin/kols?q=大V1", headers=admin_headers).json()
    assert data["total"] == 3  # 大V1 + 大V10 + 大V11
    assert all("大V1" in k["name"] for k in data["items"])
    data = client.get("/api/admin/kols?q=uid1", headers=admin_headers).json()
    assert data["total"] == 3  # uid1/uid10/uid11
    # 平台 + 分类 + 状态
    twitter = client.get("/api/admin/kols?platform=twitter", headers=admin_headers).json()
    assert twitter["total"] == 1
    assert twitter["ids"] == [twitter["items"][0]["id"]]
    assert client.get(f"/api/admin/kols?category_id={cid}", headers=admin_headers).json()["total"] == 6
    assert client.get("/api/admin/kols?status=0", headers=admin_headers).json()["total"] == 3
    # 非法状态
    assert client.get("/api/admin/kols?status=2", headers=admin_headers).status_code == 400
    # 普通用户无权限
    uh = user_headers(client, "adminkols_user")
    assert client.get("/api/admin/kols", headers=uh).status_code == 403
    # 公开目录 /api/kols 不受影响（仍返回全部）
    assert len(client.get("/api/kols", headers=admin_headers).json()) == 13


def test_admin_kols_batch_actions():
    """批量操作：启用/停用/优先/次要/改分类/删除。"""
    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    cid = db.add_category("财经")
    kids = [db.add_kol("xueqiu", f"批量{i}", f"b{i}") for i in range(4)]

    def batch(ids, action, value=None):
        return client.post(
            "/api/admin/kols/batch", headers=admin_headers,
            json={"ids": ids, "action": action, "value": value},
        )

    assert batch([], "enable").status_code == 400  # 空选择
    assert batch(kids, "nonsense").status_code == 400  # 非法操作
    assert batch(kids, "disable").status_code == 200
    assert all(not db.get_kol(k)["enabled"] for k in kids)
    assert batch(kids, "enable").status_code == 200
    assert all(db.get_kol(k)["enabled"] for k in kids)
    assert batch(kids, "priority", True).status_code == 200
    assert all(db.get_kol(k)["priority"] and not db.get_kol(k)["secondary"] for k in kids)
    assert batch(kids, "secondary", True).status_code == 200
    assert all(db.get_kol(k)["secondary"] and not db.get_kol(k)["priority"] for k in kids)
    assert batch(kids, "normal").status_code == 200
    assert all(not db.get_kol(k)["priority"] and not db.get_kol(k)["secondary"] for k in kids)
    assert batch(kids, "secondary", True).status_code == 200
    assert batch(kids, "priority", False).status_code == 200
    assert all(not db.get_kol(k)["priority"] and db.get_kol(k)["secondary"] for k in kids)
    assert batch(kids, "secondary", False).status_code == 200
    assert all(not db.get_kol(k)["priority"] and not db.get_kol(k)["secondary"] for k in kids)
    assert batch(kids[:2], "category", cid).status_code == 200
    assert db.get_kol(kids[0])["category_id"] == cid and db.get_kol(kids[2])["category_id"] is None
    # 批量删除：级联清理订阅/帖子
    db.add_subscription(1, kids[0])
    db.insert_post("xueqiu", kids[0], "bp1", "t", "c", "u", "")
    assert batch(kids[:2], "delete").status_code == 200
    assert db.get_kol(kids[0]) is None and db.get_kol(kids[1]) is None
    assert db.get_kol(kids[2]) is not None
    # 普通用户无权限
    uh = user_headers(client, "batch_user")
    assert client.post("/api/admin/kols/batch", headers=uh, json={"ids": kids, "action": "enable"}).status_code == 403


def test_kol_crud_api():
    client = make_client()
    headers = auth_headers(client)

    assert client.get("/api/kols", headers=headers).json() == []

    resp = client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "大V", "external_id": "123"}
    )
    assert resp.status_code == 200
    kid = resp.json()["id"]

    assert (
        client.post(
            "/api/kols", headers=headers, json={"platform": "facebook", "name": "x", "external_id": "1"}
        ).status_code
        == 400
    )

    resp = client.put(f"/api/kols/{kid}", headers=headers, json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] == 0

    assert client.delete(f"/api/kols/{kid}", headers=headers).status_code == 200
    assert client.get("/api/kols", headers=headers).json() == []


def test_admin_toggle_kol_silent():
    """管理员切换星球的 silent（静默/推送）标志，详情接口应反映最新值。"""
    client = make_client()
    headers = auth_headers(client)
    kid = client.app.state.db.add_kol("zsxq", "前沿收录", "28888112822211", silent=True)
    # 初始：静默
    detail = client.get(f"/api/kols/{kid}", headers=headers).json()
    assert detail["silent"] == 1
    # 开启推送（silent=false）
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"silent": False})
    assert r.status_code == 200
    assert client.get(f"/api/kols/{kid}", headers=headers).json()["silent"] == 0
    # 再关回静默
    client.put(f"/api/kols/{kid}", headers=headers, json={"silent": True})
    assert client.get(f"/api/kols/{kid}", headers=headers).json()["silent"] == 1


def test_kol_add_with_xueqiu_homepage_link():
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "xueqiu", "name": "大V", "external_id": "https://xueqiu.com/u/8790885129"},
    )
    assert resp.status_code == 200
    assert resp.json()["external_id"] == "8790885129"


def test_combination_holdings_and_nav_endpoints():
    """组合持仓/净值端点读 cube_snapshots；无快照返回空；详情页附带 quote。"""
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "combination", "name": "伯言-A股", "external_id": "ZH3623878"},
    )
    kid = resp.json()["id"]

    # 无快照：空数据 + 空时间
    assert client.get(f"/api/kols/{kid}/holdings", headers=headers).json() == {"holdings": [], "cash": None, "updated_at": ""}
    assert client.get(f"/api/kols/{kid}/nav", headers=headers).json() == {"series": [], "benchmark": [], "updated_at": ""}

    db = client.app.state.db
    db.set_cube_snapshot(kid, "holdings", [{"name": "贵州茅台", "symbol": "SH600519", "weight": 5.2}])
    db.set_cube_snapshot(kid, "nav", [{"date": "2026-07-01", "value": 1.0}, {"date": "2026-07-02", "value": 1.05}])
    db.set_cube_snapshot(kid, "quote", {"net_value": 1.8472, "day_percent_gain": 0.55})

    h = client.get(f"/api/kols/{kid}/holdings", headers=headers).json()
    assert h["holdings"][0]["name"] == "贵州茅台" and h["updated_at"]
    n = client.get(f"/api/kols/{kid}/nav", headers=headers).json()
    assert len(n["series"]) == 2 and n["updated_at"]

    kol = client.get(f"/api/kols/{kid}", headers=headers).json()
    assert kol["quote"] == {"net_value": 1.8472, "day_percent_gain": 0.55}
    assert kol["quote_at"]

    # 新快照形状：dict + cash / benchmark；旧 list 快照仍可读
    db.set_cube_snapshot(kid, "holdings", {"holdings": [{"name": "现金组合", "symbol": "SZ000001", "weight": 80.0, "prev": 70.0}], "cash": 20.0})
    db.set_cube_snapshot(kid, "nav", {"series": [{"date": "2026-07-01", "value": 1.0}], "benchmark": [{"date": "2026-07-01", "value": 4000.0}]})
    h = client.get(f"/api/kols/{kid}/holdings", headers=headers).json()
    assert h["cash"] == 20.0 and h["holdings"][0]["prev"] == 70.0
    n = client.get(f"/api/kols/{kid}/nav", headers=headers).json()
    assert n["benchmark"][0]["value"] == 4000.0

    cat = client.get("/api/catalog?platform=combination", headers=headers).json()
    item = next(k for k in cat if k["id"] == kid)
    assert item["quote"]["day_percent_gain"] == 0.55


def test_combination_endpoints_require_visibility():
    """持仓/净值端点与帖子端点同权限：不可见大V 404。"""
    client = make_client()
    admin_headers = auth_headers(client)
    kid = client.post(
        "/api/kols",
        headers=admin_headers,
        json={"platform": "combination", "name": "私有组合", "external_id": "ZH000001"},
    ).json()["id"]
    client.app.state.db.update_kol(kid, is_private=True)
    other = user_headers(client, "otheruser")
    assert client.get(f"/api/kols/{kid}/holdings", headers=other).status_code == 404
    assert client.get(f"/api/kols/{kid}/nav", headers=other).status_code == 404
    assert client.get(f"/api/kols/{kid}", headers=other).status_code == 404


def test_add_x_kol_auto_resolves_name_and_avatar(monkeypatch):
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "resolve_x_profile",
        lambda external_id, cookie="", db=None: {
            "name": "SemiAnalysis",
            "avatar_url": "https://pbs.twimg.com/x_400x400.jpg",
            "screen_name": "SemiAnalysis_",
        },
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "twitter", "name": "", "external_id": "https://x.com/SemiAnalysis_"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "SemiAnalysis"
    assert resp.json()["avatar_url"] == "https://pbs.twimg.com/x_400x400.jpg"


def test_batch_import_x_kol_auto_resolves_name(monkeypatch):
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "resolve_x_profile",
        lambda external_id, cookie="", db=None: {
            "name": "elonmusk",
            "avatar_url": "https://pbs.twimg.com/musk.jpg",
            "screen_name": "elonmusk",
        },
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={"platform": "twitter", "lines": "https://x.com/elonmusk"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] == 1
    assert resp.json()["ids"]
    assert len(resp.json()["ids"]) == 1
    kols = client.get("/api/kols", headers=headers).json()
    assert any(k["name"] == "elonmusk" for k in kols)


def test_batch_import_auto_detects_platform_per_line(monkeypatch):
    """批量导入按链接自动识别平台；纯 UID 不再回退默认平台。"""
    from app import api as api_mod

    monkeypatch.setattr(api_mod, "resolve_weibo_profile", lambda external_id, cookie="", db=None: {"name": "微博用户", "avatar_url": ""})
    monkeypatch.setattr(
        api_mod,
        "resolve_x_profile",
        lambda external_id, cookie="", db=None: {"name": "X用户", "avatar_url": ""},
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={
            "lines": "\n".join([
                "雪球大V https://xueqiu.com/u/10001",
                "https://xueqiu.com/P/ZH100002",
                "微博大V https://weibo.com/u/1642591402",
                "https://x.com/elonmusk",
                "纯数字ID 20005",
            ]),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] == 4, body
    assert body["failed"]
    assert any("20005" in f["line"] and "无法识别平台" in f["error"] for f in body["failed"])
    kols = client.get("/api/kols", headers=headers).json()
    by_ext = {k["external_id"]: k for k in kols}
    assert by_ext["10001"]["platform"] == "xueqiu"
    assert by_ext["ZH100002"]["platform"] == "combination"
    assert by_ext["1642591402"]["platform"] == "weibo"
    assert by_ext["elonmusk"]["platform"] == "twitter"  # X 存 screen name 而非完整 URL
    assert "https://x.com/elonmusk" not in by_ext
    assert "20005" not in by_ext


def test_batch_import_normalizes_weibo_mobile_and_rejects_tweet_url(monkeypatch):
    """m.weibo.cn 链接识别为微博；X 推文/系统页链接整行失败。"""
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod, "resolve_weibo_profile", lambda external_id, cookie="", db=None: {"name": "微博用户", "avatar_url": ""}
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={
            "platform": "xueqiu",
            "lines": "\n".join([
                "https://m.weibo.cn/u/1642591402",
                "https://x.com/elonmusk/status/123",
                "https://x.com/home",
            ]),
        },
    )
    body = resp.json()
    assert body["ok"] == 1, body
    assert len(body["failed"]) == 2  # 两条 X 系统/推文链接失败
    kols = {k["external_id"]: k for k in client.get("/api/kols", headers=headers).json()}
    assert kols["1642591402"]["platform"] == "weibo"


def test_add_x_kol_stores_screen_name(monkeypatch):
    """单条添加 X 主页链接时，外部 ID 存 screen name 而非完整 URL。"""
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "resolve_x_profile",
        lambda external_id, cookie="", db=None: {"name": "Semi", "avatar_url": ""},
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "twitter", "name": "", "external_id": "https://x.com/SemiAnalysis_"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_id"] == "SemiAnalysis_"


def test_batch_import_weibo_with_nickname_fetches_avatar(monkeypatch):
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "resolve_weibo_profile",
        lambda external_id, cookie="", db=None: {
            "name": "新浪娱乐",
            "avatar_url": "https://wx1.sinaimg.cn/avatar.jpg",
            "uid": "1642591402",
        },
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={"platform": "weibo", "lines": "新浪娱乐 https://weibo.com/u/1642591402"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] == 1
    kols = client.get("/api/kols", headers=headers).json()
    target = next(k for k in kols if k["platform"] == "weibo")
    assert target["name"] == "新浪娱乐"
    assert target["avatar_url"] == "https://wx1.sinaimg.cn/avatar.jpg"


def test_add_weibo_kol_auto_resolves_name_and_avatar(monkeypatch):
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "resolve_weibo_profile",
        lambda uid, cookie="", db=None: {
            "name": "wu2198",
            "avatar_url": "https://wx1.sinaimg.cn/orj360/wb.jpg",
            "uid": uid,
        },
    )
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "weibo", "name": "", "external_id": "https://weibo.com/u/123456"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "wu2198"
    assert resp.json()["avatar_url"] == "https://wx1.sinaimg.cn/orj360/wb.jpg"


def test_stats_api():
    client = make_client()
    headers = auth_headers(client)
    client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "A", "external_id": "1", "priority": True}
    )
    client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "B", "external_id": "2", "secondary": True}
    )
    stats = client.get("/api/stats", headers=headers).json()
    assert stats["kols"] == 2
    assert stats["enabled_kols"] == 2
    assert stats["priority_kols"] == 1
    assert stats["secondary_kols"] == 1
    assert stats["users"] == 1
    assert stats["posts"] == 0
    assert "polling_interval_seconds" in stats


def test_recommendations_api_sorted_by_subscribers():
    client = make_client()
    admin = auth_headers(client)
    hot = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "热门", "external_id": "1"}
    ).json()["id"]
    cold = client.post(
        "/api/kols", headers=admin, json={"platform": "weibo", "name": "冷门", "external_id": "2"}
    ).json()["id"]
    u1 = user_headers(client, "sub_aa")
    u2 = user_headers(client, "sub_bb")
    for h in (u1, u2):
        assert client.post("/api/subscriptions", headers=h, json={"kol_id": hot, "type": "post"}).status_code == 200
    recs = client.get("/api/recommendations", headers=u1).json()
    assert [r["id"] for r in recs] == [hot, cold]
    assert recs[0]["subscriber_count"] == 2
    assert recs[0]["subscribed"] is True
    assert recs[1]["subscribed"] is False


def test_recommendations_unsubscribed_only():
    """右侧栏用 unsubscribed=1，只返回当前用户未订的大V。"""
    client = make_client()
    admin = auth_headers(client)
    hot = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "热门", "external_id": "1"}
    ).json()["id"]
    cold = client.post(
        "/api/kols", headers=admin, json={"platform": "weibo", "name": "冷门", "external_id": "2"}
    ).json()["id"]
    u1 = user_headers(client, "sub_aa")
    assert client.post("/api/subscriptions", headers=u1, json={"kol_id": hot, "type": "post"}).status_code == 200
    recs = client.get("/api/recommendations?unsubscribed=1", headers=u1).json()
    assert [r["id"] for r in recs] == [cold]
    assert all(not r["subscribed"] for r in recs)


def test_stats_x_direct_mode():
    client = make_client()
    headers = auth_headers(client)
    data = client.get("/api/stats", headers=headers).json()
    twitter = next(s for s in data["sources"] if s["platform"] == "twitter")
    assert twitter["direct_mode"] == "unknown"

    client.app.state.db.set_setting("x_direct_last_fallback_at", "1785900000")
    client.app.state.db.set_setting("x_direct_fallback_reason", "401 Unauthorized")
    data = client.get("/api/stats", headers=headers).json()
    twitter = next(s for s in data["sources"] if s["platform"] == "twitter")
    assert twitter["direct_mode"] == "fallback"
    assert "401" in twitter["direct_fallback_reason"]
    # 普通用户无权访问
    normal = user_headers(client, "viewer")
    assert client.get("/api/stats", headers=normal).status_code == 403


def test_kol_priority_toggle():
    client = make_client()
    headers = auth_headers(client)
    kid = client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "A", "external_id": "1", "priority": True}
    ).json()["id"]
    assert client.get(f"/api/kols/{kid}", headers=headers).json()["priority"] == 1
    resp = client.put(f"/api/kols/{kid}", headers=headers, json={"priority": False})
    assert resp.json()["priority"] == 0


def test_bind_code_api():
    client = make_client()
    headers = user_headers(client, "someone")
    me = client.get("/api/me", headers=headers).json()
    resp = client.post("/api/me/bind-code", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["code"]) == 8
    assert data["code"].isalnum()
    assert data["expires_in_seconds"] == 600
    row = client.app.state.db.get_bind_code(data["code"])
    assert row["user_id"] == me["id"]
    stored = client.app.state.db._rows("SELECT code FROM bind_codes")[0]["code"]
    assert stored != data["code"]
    assert len(stored) == 64


def test_bind_code_issue_rate_limit():
    client = make_client()
    headers = user_headers(client, "someone")
    for _ in range(3):
        assert client.post("/api/me/bind-code", headers=headers).status_code == 200
    resp = client.post("/api/me/bind-code", headers=headers)
    assert resp.status_code == 429


def test_system_logs_api():
    client = make_client()
    admin_headers = auth_headers(client)
    resp = client.get("/api/admin/system-logs?limit=50", headers=admin_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json()["lines"], list)
    # 级别 + 关键词过滤参数（供网页/Agent 精准取数）
    resp = client.get(
        "/api/admin/system-logs?level=ERROR&q=test&limit=50",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["lines"], list)
    # 非法级别拒绝
    assert (
        client.get("/api/admin/system-logs?level=BOGUS", headers=admin_headers).status_code
        == 400
    )
    # 普通用户无权限
    uh = user_headers(client, "syslog_user")
    assert client.get("/api/admin/system-logs", headers=uh).status_code == 403


def test_recent_logs_debug_exact_match():
    """级别筛选：DEBUG 精确匹配（只显示 DEBUG 行），其余级别为及以上。"""
    from app import logging_setup
    from app.logging_setup import recent_logs

    with logging_setup._ring_lock:
        logging_setup._ring.clear()
        logging_setup._ring.append("2026-08-11 10:00:00.000 DEBUG app.a [t] debug line")
        logging_setup._ring.append("2026-08-11 10:00:01.000 INFO app.b [t] info line")
        logging_setup._ring.append("2026-08-11 10:00:02.000 WARNING app.c [t] warn line")
        logging_setup._ring.append("2026-08-11 10:00:03.000 ERROR app.d [t] error line")
    assert len(recent_logs(level="DEBUG")) == 1  # 只显示 DEBUG，不混入 INFO
    assert len(recent_logs(level="INFO")) == 3
    assert len(recent_logs(level="WARNING")) == 2
    assert len(recent_logs(level="ERROR")) == 1


def test_error_logs_persist_and_filter(monkeypatch):
    """错误记录：WARNING+ 落库、级别过滤、跨重启语义（DB 存储）。

    断言只看本用例写的那批（logger=app.test）：慢机器上 app.db 的 slow query
    WARNING 也会落进同一张表，全局断言会随机多出一行（CI 慢磁盘上必现）。
    """
    from app import db as db_module

    # 阀值压到 0：每条语句都产生 slow query WARNING，持续复现 CI 的慢磁盘场景
    monkeypatch.setattr(db_module, "_SLOW_QUERY_SECONDS", 0.0)
    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    db.record_error_log("WARNING", "app.test", "磁盘快满了")
    db.record_error_log("ERROR", "app.test", "抓取失败 traceback")
    db.record_error_log("INFO", "app.test", "普通信息不入错误记录")

    def own_logs(query=""):
        resp = client.get(f"/api/admin/error-logs?q=app.test{query}", headers=admin_headers)
        assert resp.status_code == 200
        return resp.json()["logs"]

    assert [l["level"] for l in own_logs()] == ["INFO", "ERROR", "WARNING"]  # 新->旧
    # 级别过滤：ERROR+ 只看 ERROR（含 CRITICAL）
    assert [l["level"] for l in own_logs("&level=ERROR")] == ["ERROR"]
    # 关键词过滤
    resp = client.get("/api/admin/error-logs?q=磁盘", headers=admin_headers)
    assert [l["message"] for l in resp.json()["logs"]] == ["磁盘快满了"]
    # 普通用户无权限
    uh = user_headers(client, "errlog_user")
    assert client.get("/api/admin/error-logs", headers=uh).status_code == 403


def test_error_db_handler_captures_warnings():
    """ErrorDbHandler 已挂到 root：WARNING 流向持久化 sink，INFO 不进。"""
    import logging

    from app import logging_setup

    captured = []
    logging_setup.register_error_sink(
        lambda rec: captured.append((rec.levelname, rec.name, rec.getMessage()))
    )
    logging_setup.setup_logging(level="INFO")  # 幂等：handler 只挂一次
    logging.getLogger("app.test").warning("测试告警")
    logging.getLogger("app.test").info("普通信息不该进")
    assert ("WARNING", "app.test", "测试告警") in captured
    assert not any("普通信息" in c[2] for c in captured)


def test_error_db_handler_does_not_reenter_itself():
    """sink 写库期间产生的 WARNING 不再回灌：慢库上否则会自激循环。"""
    import logging

    from app import logging_setup

    captured = []

    def sink(record):
        captured.append(record.getMessage())
        # 模拟「写库超阀值 → 又一条 slow query WARNING」
        logging.getLogger("app.db").warning("slow query 900ms: INSERT INTO error_logs")

    logging_setup.setup_logging(level="INFO")  # 幂等：handler 只挂一次
    previous = logging_setup._error_sink
    logging_setup.register_error_sink(sink)
    try:
        logging.getLogger("app.test").warning("外层告警")
    finally:
        logging_setup.register_error_sink(previous)

    assert captured == ["外层告警"]


def test_no_rsshub_fallback_in_frontend_or_compose():
    """X 不再走 RSSHub：界面与 compose 不得再宣传备用通道。"""
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "备用RSS" not in app_js
    assert "RSSHub" not in app_js
    for name in ("docker-compose.prod.yml", "docker-compose.unraid.yml"):
        text = Path(name).read_text(encoding="utf-8")
        assert "dav-rsshub" not in text
        assert "RSSHUB_BASE" not in text


def test_frontend_fallback_version_matches_backend():
    """接口失败/离线时，侧栏兜底版本不能落后于后端发布版本。"""
    import re
    from pathlib import Path

    from app.version import APP_VERSION

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    match = re.search(r'^const APP_VERSION = "([^"]+)";', app_js, re.MULTILINE)
    assert match and match.group(1) == APP_VERSION


def test_version_api(monkeypatch):
    from app.version import APP_VERSION

    # 模拟 GitHub 上有比当前版本更新的版本（对版本号提升免疫）
    parts = [int(x) for x in APP_VERSION.split(".") if x.isdigit()] or [0]
    latest = ".".join(str(x) for x in (parts[:-1] + [parts[-1] + 1]))
    monkeypatch.setattr("app.version.latest_github_version", lambda db: (latest, True))
    client = make_client()
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == APP_VERSION
    assert data["latest"] == latest
    assert data["update_available"] is True
    assert "github.com" in data["url"]


def test_kol_request_requires_category(monkeypatch):
    """用户申请必须带已有分类；审批沿用申请上的分类。"""
    monkeypatch.setattr("app.kol_requests.resolve_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    db = client.app.state.db
    headers = user_headers(client, "askcatreq")
    admin = auth_headers(client)
    cid = db.add_category("宏观")

    missing = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "90001"},
    )
    assert missing.status_code == 400
    assert "分类" in missing.json()["detail"]

    bad = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "90002", "category_id": 99999},
    )
    assert bad.status_code == 400
    assert "分类" in bad.json()["detail"]

    ok = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "90003", "category_id": cid},
    )
    assert ok.status_code == 200
    pending = client.get("/api/admin/kol-requests?status=pending", headers=admin).json()
    assert pending[0]["category_id"] == cid
    assert pending[0]["category_name"] == "宏观"
    approved = client.post(f"/api/admin/kol-requests/{pending[0]['id']}/approve", headers=admin)
    assert approved.status_code == 200
    assert approved.json()["category_id"] == cid


def test_kol_request_input_validation():
    """申请输入过滤：无效信息拒绝、平台链接自动甄别并提示纠错。"""
    client = make_client()
    headers = user_headers(client, "valuser")
    db = client.app.state.db
    cid = db.add_category("宏观")

    def submit(platform, external_id):
        return client.post(
            "/api/kol-requests",
            headers=headers,
            json={"platform": platform, "external_id": external_id, "category_id": cid},
        )

    def bad(platform, external_id, hint):
        resp = submit(platform, external_id)
        assert resp.status_code == 400, resp.text
        assert hint in resp.json()["detail"], resp.json()["detail"]

    # 各平台合法输入：链接提取 ID / 纯 ID 直通
    assert submit("xueqiu", "https://xueqiu.com/u/1001").status_code == 200
    assert submit("xueqiu", "12345").status_code == 200
    assert submit("combination", "https://xueqiu.com/P/ZH900001").status_code == 200
    assert submit("combination", "ZH900002").status_code == 200
    assert submit("weibo", "https://weibo.com/u/2001").status_code == 200
    assert submit("weibo", "https://m.weibo.cn/u/2002").status_code == 200
    assert submit("twitter", "https://x.com/elonmusk").status_code == 200
    assert submit("twitter", "@jack").status_code == 200
    assert submit("twitter", "twitter.com/nasa").status_code == 200
    assert submit("zsxq", "https://wx.zsxq.com/dweb2/index/group/28888112822211").status_code == 200
    assert submit("zsxq", "28888112822212").status_code == 200

    # 无效信息：拒绝并提示
    bad("xueqiu", "https://xueqiu.com/S/SH600000", "无法识别")
    bad("xueqiu", "https://xueqiu.com/u/abc", "无法识别")
    bad("combination", "https://xueqiu.com/P/123", "无法识别")
    bad("combination", "abc", "无法识别")
    bad("weibo", "https://weibo.com/n/某博主", "无法识别")
    bad("twitter", "https://x.com/i/flow/login", "系统页面")
    bad("twitter", "https://x.com/search?q=btc", "系统页面")
    bad("twitter", "https://x.com/elonmusk/status/123", "推文")
    bad("twitter", "这个不是链接", "无法识别")
    bad("xueqiu", "", "请输入")

    # 平台甄别：链接属于其他平台时提示切换平台
    bad("xueqiu", "https://xueqiu.com/P/ZH900003", "雪球组合")
    bad("combination", "https://xueqiu.com/u/1002", "雪球")
    bad("xueqiu", "https://twitter.com/elonmusk", "「X」")
    bad("weibo", "https://x.com/elonmusk", "「X」")
    bad("twitter", "https://weibo.com/u/2003", "微博")
    bad("xueqiu", "https://wx.zsxq.com/group/28888112822213", "知识星球")
    bad("zsxq", "https://example.com/foo", "无法识别")

    # 通过的申请都存了归一化后的 ID
    ids = {r["external_id"] for r in db.list_kol_requests()}
    assert "1001" in ids and "ZH900001" in ids and "ZH900002" in ids
    assert {"elonmusk", "jack", "nasa", "28888112822211", "28888112822212"} <= ids
    assert not any(r["external_id"].startswith("http") for r in db.list_kol_requests())


def test_kol_request_notifies_admins(monkeypatch):
    client = make_client()
    auth_headers(client)
    admin = client.app.state.db.get_user_by_username("testadmin")
    # 自建 bot token 让通知分支与本地 config.yaml 解耦（CI 无 config.yaml 也能过）
    client.app.state.db.update_user(admin["id"], telegram_chat_id="111", telegram_bot_token="tok")
    sent = []

    class FakeTG:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)

        def send_text(self, text, reply_markup=None):
            sent.append((text, reply_markup))

    monkeypatch.setattr("app.notifiers.telegram.TelegramNotifier", FakeTG)
    headers = user_headers(client, "requser")
    cid = client.app.state.db.add_category("宏观")
    resp = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "https://xueqiu.com/u/999999", "category_id": cid},
    )
    assert resp.status_code == 200
    assert any("新的大V添加申请" in t and "999999" in t for t, _ in sent)


def test_kol_request_notify_tg_only_when_bound(monkeypatch):
    """管理员同时绑 TG 和飞书时，大V申请只推 TG（带审批按钮），不重复推飞书。"""
    client = make_client()
    auth_headers(client)
    admin = client.app.state.db.get_user_by_username("testadmin")
    client.app.state.db.update_user(
        admin["id"], telegram_chat_id="111", telegram_bot_token="tok", feishu_chat_id="oc_abc"
    )
    sent_tg, sent_fs = [], []

    class FakeTG:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)

        def send_text(self, text, reply_markup=None):
            sent_tg.append((text, reply_markup))

    class FakeFS:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)

        def send_text(self, text):
            sent_fs.append(text)

    monkeypatch.setattr("app.notifiers.telegram.TelegramNotifier", FakeTG)
    monkeypatch.setattr("app.notifiers.feishu.FeishuNotifier", FakeFS)
    headers = user_headers(client, "requser2")
    cid = client.app.state.db.add_category("宏观")
    resp = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "https://xueqiu.com/u/777777", "category_id": cid},
    )
    assert resp.status_code == 200
    assert len(sent_tg) == 1 and sent_fs == []  # 已绑 TG 不再重复推飞书
    text, keyboard = sent_tg[0]
    assert "添加审批" in text and "求添加" not in text
    callback = [b["callback_data"] for row in keyboard for b in row]
    assert any(c.startswith("approve:") for c in callback)
    assert any(c.startswith("reject:") for c in callback)


def test_resolve_profile_non_ascii_id_returns_empty():
    """雪球昵称等非 ASCII ID：resolve_profile 回退空 dict，不抛 UnicodeEncodeError。"""
    from app.fetchers.xueqiu import resolve_profile

    assert resolve_profile("两刀插肋") == {}


def test_approve_garbage_request_returns_clear_error():
    """旧申请（未经验证入库的垃圾 ID，如雪球昵称）点通过时提示无效，不 500 崩溃。"""
    client = make_client()
    admin_headers = auth_headers(client)
    u = register(client, "garbuser", "pass123456")
    db = client.app.state.db
    # 绕过提交校验，直接构造老式垃圾申请（雪球昵称而非数字 ID）
    cid = db.add_category("宏观")
    req_id = db.add_kol_request("xueqiu", "两刀插肋", u.json()["user"]["id"], category_id=cid)
    resp = client.post(f"/api/admin/kol-requests/{req_id}/approve", headers=admin_headers)
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "两刀插肋" in detail and "无效" in detail and "拒绝" in detail
    assert db.get_kol_request(req_id)["status"] == "pending"  # 未被错误上架
    assert db.get_kol_by_external("xueqiu", "两刀插肋") is None


def test_approve_legacy_at_twitter_request(monkeypatch):
    """旧 twitter 申请（@用户名 原样入库，未经归一化）审批二次校验应放行。"""
    monkeypatch.setattr("app.kol_requests.resolve_x_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    admin_headers = auth_headers(client)
    u = register(client, "atuser", "pass123456")
    db = client.app.state.db
    cid = db.add_category("宏观")
    req_id = db.add_kol_request("twitter", "@elonmusk", u.json()["user"]["id"], category_id=cid)
    resp = client.post(f"/api/admin/kol-requests/{req_id}/approve", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert db.get_kol_by_external("twitter", "@elonmusk") is not None
    assert db.get_kol_request(req_id)["status"] == "approved"


def test_kol_request_tg_fail_falls_back_to_feishu(monkeypatch):
    """TG 通知发送失败时回退飞书，管理员不至于收不到申请。"""
    client = make_client()
    auth_headers(client)
    admin = client.app.state.db.get_user_by_username("testadmin")
    client.app.state.db.update_user(
        admin["id"], telegram_chat_id="111", telegram_bot_token="tok", feishu_chat_id="oc_abc"
    )
    sent_fs = []

    class FailTG:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)

        def send_text(self, text, reply_markup=None):
            raise RuntimeError("tg 挂了")

    class FakeFS:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)

        def send_text(self, text):
            sent_fs.append(text)

    monkeypatch.setattr("app.notifiers.telegram.TelegramNotifier", FailTG)
    monkeypatch.setattr("app.notifiers.feishu.FeishuNotifier", FakeFS)
    headers = user_headers(client, "requser3")
    cid = client.app.state.db.add_category("宏观")
    resp = client.post(
        "/api/kol-requests",
        headers=headers,
        json={"platform": "xueqiu", "external_id": "https://xueqiu.com/u/666666", "category_id": cid},
    )
    assert resp.status_code == 200
    assert len(sent_fs) == 1  # TG 失败后回退飞书


def test_tg_callback_approve_reject_kol_request(monkeypatch):
    """TG 审批按钮回调：管理员点通过/拒绝直接生效，非管理员被拒绝。"""
    monkeypatch.setattr("app.kol_requests.resolve_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    auth_headers(client)
    db = client.app.state.db
    admin = db.get_user_by_username("testadmin")
    db.update_user(admin["id"], telegram_chat_id="111")
    u = register(client, "askuser", "pass123456")
    uid = u.json()["user"]["id"]
    cid = db.add_category("宏观")
    req_id = db.add_kol_request("xueqiu", "888888", uid, category_id=cid)

    from app.telegram_bot import TelegramBot

    bot = TelegramBot(db, "tok", "secret")
    calls = []
    bot._call = lambda method, **params: calls.append((method, params))

    def click(chat_id, data):
        calls.clear()
        bot.handle_update({
            "callback_query": {
                "id": "cq1", "data": data, "from": {"username": "tgadmin"},
                "message": {"chat": {"id": chat_id}, "message_id": 5},
            }
        })

    # 管理员点「通过」：先选分类，未上架
    click("111", f"approve:{req_id}")
    assert db.get_kol_by_external("xueqiu", "888888") is None
    assert db.get_kol_request(req_id)["status"] == "pending"
    assert any(m == "editMessageText" and "分类" in p.get("text", "") for m, p in calls)
    markup = next(p.get("reply_markup", "") for m, p in calls if m == "editMessageText")
    assert f"apcat:{req_id}:0" in markup

    # 选「未分类」：上架 + 审批状态更新
    click("111", f"apcat:{req_id}:0")
    assert db.get_kol_by_external("xueqiu", "888888") is not None
    assert db.get_kol_request(req_id)["status"] == "approved"
    assert any(m == "editMessageText" and "已通过" in p.get("text", "") for m, p in calls)

    # 管理员点「拒绝」
    req2 = db.add_kol_request("xueqiu", "888889", uid, category_id=cid)
    click("111", f"reject:{req2}")
    assert db.get_kol_request(req2)["status"] == "rejected"
    assert any(m == "editMessageText" and "已拒绝" in p.get("text", "") for m, p in calls)

    # 非管理员点按钮：拒绝操作并提示
    click("999", f"approve:{req2}")
    assert db.get_kol_request(req2)["status"] == "rejected"  # 状态未被改动
    assert any(m == "editMessageText" and "只有管理员" in p.get("text", "") for m, p in calls)

    # 重复审批（已处理）提示失败
    click("111", f"approve:{req2}")
    assert any(m == "editMessageText" and "审批失败" in p.get("text", "") for m, p in calls)


def test_tg_callback_approve_kol_request_with_category(monkeypatch):
    """管理员审批时选分类，上架后 category_id 写入。"""
    monkeypatch.setattr("app.kol_requests.resolve_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    auth_headers(client)
    db = client.app.state.db
    admin = db.get_user_by_username("testadmin")
    db.update_user(admin["id"], telegram_chat_id="111")
    u = register(client, "askcat", "pass123456")
    uid = u.json()["user"]["id"]
    cat_id = db.add_category("宏观")
    req_id = db.add_kol_request("xueqiu", "777777", uid, "待分类大V", category_id=cat_id)

    from app.telegram_bot import TelegramBot

    bot = TelegramBot(db, "tok", "secret")
    calls = []
    bot._call = lambda method, **params: calls.append((method, params))
    bot.handle_update({
        "callback_query": {
            "id": "cq1", "data": f"approve:{req_id}", "from": {"username": "tgadmin"},
            "message": {"chat": {"id": "111"}, "message_id": 5},
        }
    })
    markup = next(p.get("reply_markup", "") for m, p in calls if m == "editMessageText")
    assert f"apcat:{req_id}:{cat_id}" in markup
    assert "宏观" in markup

    calls.clear()
    bot.handle_update({
        "callback_query": {
            "id": "cq2", "data": f"apcat:{req_id}:{cat_id}", "from": {"username": "tgadmin"},
            "message": {"chat": {"id": "111"}, "message_id": 5},
        }
    })
    kol = db.get_kol_by_external("xueqiu", "777777")
    assert kol is not None
    assert kol["category_id"] == cat_id
    assert db.get_kol(kol["id"])["category_name"] == "宏观"
    assert any("宏观" in p.get("text", "") and "已通过" in p.get("text", "") for m, p in calls)


def test_push_channels_api():
    client = make_client()
    headers = user_headers(client, "chuser")
    # 默认空 = 全部已绑定渠道推送
    assert client.get("/api/me", headers=headers).json()["push_channels"] == ""
    # 设置合法渠道
    resp = client.put("/api/me", headers=headers, json={"push_channels": "telegram,feishu"})
    assert resp.status_code == 200
    assert resp.json()["push_channels"] == "telegram,feishu"
    # 非法渠道被拒绝
    resp = client.put("/api/me", headers=headers, json={"push_channels": "sms"})
    assert resp.status_code == 400
    # 清空恢复默认（全部）
    resp = client.put("/api/me", headers=headers, json={"push_channels": ""})
    assert resp.status_code == 200
    assert resp.json()["push_channels"] == ""


def test_dnd_api_validation():
    client = make_client()
    headers = user_headers(client, "dnduser")
    resp = client.put("/api/me", headers=headers, json={"dnd_start": "23:00", "dnd_end": "07:00"})
    assert resp.status_code == 200
    me = resp.json()
    assert me["dnd_start"] == "23:00" and me["dnd_end"] == "07:00"
    # 非法时间格式被拒绝
    resp = client.put("/api/me", headers=headers, json={"dnd_start": "99:99"})
    assert resp.status_code == 400
    # 关闭：清空
    resp = client.put("/api/me", headers=headers, json={"dnd_start": "", "dnd_end": ""})
    assert resp.status_code == 200
    assert resp.json()["dnd_start"] == "" and resp.json()["dnd_end"] == ""


def test_favorite_api():
    client = make_client()
    admin = auth_headers(client)
    headers = user_headers(client, "favuser")
    kid = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "A", "external_id": "fav1"}
    ).json()["id"]
    kid2 = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "B", "external_id": "fav2"}
    ).json()["id"]
    client.post("/api/subscriptions", headers=headers, json={"kol_id": kid})

    resp = client.put(f"/api/subscriptions/{kid}/favorite", headers=headers, json={"favorite": True})
    assert resp.status_code == 200
    cat = client.get("/api/catalog", headers=headers).json()
    assert next(k for k in cat if k["id"] == kid)["favorite"] is True
    assert next(k for k in cat if k["id"] == kid2)["favorite"] is False
    # 未订阅的大V不能标星
    assert (
        client.put(f"/api/subscriptions/{kid2}/favorite", headers=headers, json={"favorite": True}).status_code
        == 404
    )
    # 取消特别关注
    client.put(f"/api/subscriptions/{kid}/favorite", headers=headers, json={"favorite": False})
    cat = client.get("/api/catalog", headers=headers).json()
    assert next(k for k in cat if k["id"] == kid)["favorite"] is False
    # 免打扰穿透开关
    resp = client.put("/api/me", headers=headers, json={"dnd_allow_favorite": True})
    assert resp.status_code == 200
    assert resp.json()["dnd_allow_favorite"] is True


def test_secondary_api():
    client = make_client()
    admin = auth_headers(client)
    headers = user_headers(client, "secuser")
    kid = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "A", "external_id": "sec1"}
    ).json()["id"]
    kid2 = client.post(
        "/api/kols", headers=admin, json={"platform": "xueqiu", "name": "B", "external_id": "sec2"}
    ).json()["id"]
    client.post("/api/subscriptions", headers=headers, json={"kol_id": kid})

    resp = client.put(f"/api/subscriptions/{kid}/secondary", headers=headers, json={"secondary": True})
    assert resp.status_code == 200
    cat = client.get("/api/catalog", headers=headers).json()
    assert next(k for k in cat if k["id"] == kid)["secondary"] is True
    assert next(k for k in cat if k["id"] == kid2)["secondary"] is False
    # 未订阅的大V不能设次要
    assert (
        client.put(f"/api/subscriptions/{kid2}/secondary", headers=headers, json={"secondary": True}).status_code
        == 404
    )
    # 取消次要
    client.put(f"/api/subscriptions/{kid}/secondary", headers=headers, json={"secondary": False})
    cat = client.get("/api/catalog", headers=headers).json()
    assert next(k for k in cat if k["id"] == kid)["secondary"] is False
    # 我的订阅接口：返回个人 secondary（而非 kols 全局列，刷新后不丢状态）
    client.put(f"/api/subscriptions/{kid}/secondary", headers=headers, json={"secondary": True})
    subs = client.get("/api/my/subscriptions", headers=headers).json()
    assert next(k for k in subs if k["id"] == kid)["secondary"] == 1
    # 我的订阅接口：返回个人 secondary（而非 kols 全局列，刷新后不丢状态）
    client.put(f"/api/subscriptions/{kid}/secondary", headers=headers, json={"secondary": True})
    subs = client.get("/api/my/subscriptions", headers=headers).json()
    assert next(k for k in subs if k["id"] == kid)["secondary"] == 1


def test_hide_images_api_defaults_toggles_and_is_subscription_scoped():
    client = make_client()
    admin = auth_headers(client)
    first_headers = user_headers(client, "hide-images-first")
    second_headers = user_headers(client, "hide-images-second")
    kid = client.post(
        "/api/kols",
        headers=admin,
        json={
            "platform": "xueqiu",
            "name": "A",
            "external_id": "hide-images-1",
        },
    ).json()["id"]
    other_kid = client.post(
        "/api/kols",
        headers=admin,
        json={
            "platform": "xueqiu",
            "name": "B",
            "external_id": "hide-images-2",
        },
    ).json()["id"]
    for headers in (first_headers, second_headers):
        assert client.post(
            "/api/subscriptions", headers=headers, json={"kol_id": kid}
        ).status_code == 200

    first_sub = client.get(
        "/api/my/subscriptions", headers=first_headers
    ).json()[0]
    second_sub = client.get(
        "/api/my/subscriptions", headers=second_headers
    ).json()[0]
    assert first_sub["hide_images"] == 0
    assert second_sub["hide_images"] == 0

    response = client.put(
        f"/api/subscriptions/{kid}/hide-images",
        headers=first_headers,
        json={"hide_images": True},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get(
        "/api/my/subscriptions", headers=first_headers
    ).json()[0]["hide_images"] == 1
    assert client.get(
        "/api/my/subscriptions", headers=second_headers
    ).json()[0]["hide_images"] == 0

    response = client.put(
        f"/api/subscriptions/{kid}/hide-images",
        headers=first_headers,
        json={"hide_images": False},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get(
        "/api/my/subscriptions", headers=first_headers
    ).json()[0]["hide_images"] == 0

    assert client.put(
        f"/api/subscriptions/{other_kid}/hide-images",
        headers=first_headers,
        json={"hide_images": True},
    ).status_code == 404
    assert client.put(
        "/api/subscriptions/999999/hide-images",
        headers=first_headers,
        json={"hide_images": True},
    ).status_code == 404

    assert client.delete(
        f"/api/subscriptions/{kid}", headers=first_headers
    ).status_code == 200
    assert client.put(
        f"/api/subscriptions/{kid}/hide-images",
        headers=first_headers,
        json={"hide_images": True},
    ).status_code == 404


def test_change_password_api():
    client = make_client()
    headers = user_headers(client, "pwuser")

    # 原密码错误
    resp = client.post(
        "/api/me/password",
        headers=headers,
        json={"old_password": "wrong", "new_password": "newpass123"},
    )
    assert resp.status_code == 400 and "原密码" in resp.json()["detail"]

    # 新密码太短
    resp = client.post(
        "/api/me/password",
        headers=headers,
        json={"old_password": "pass123456", "new_password": "123"},
    )
    assert resp.status_code == 400 and "至少10位" in resp.json()["detail"]

    # 正常修改后旧 token 失效，响应带回新 token
    resp = client.post(
        "/api/me/password",
        headers=headers,
        json={"old_password": "pass123456", "new_password": "newpass123"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    new_token = resp.json()["token"]
    assert new_token
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.get("/api/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "pwuser", "password": "pass123456"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "pwuser", "password": "newpass123"}).status_code == 200


def test_logout_revokes_server_token():
    client = make_client()
    headers = user_headers(client, "logoutuser")
    assert client.get("/api/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.post("/api/auth/logout", headers=headers).status_code == 401


def test_channel_claim_conflict():
    client = make_client()
    headers_a = user_headers(client, "user_a")
    headers_b = user_headers(client, "user_b")
    wc_hook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wc1"

    assert client.put("/api/me", headers=headers_a, json={"telegram_chat_id": "111"}).status_code == 200
    resp = client.put("/api/me", headers=headers_b, json={"telegram_chat_id": "111"})
    assert resp.status_code == 400 and "已绑定" in resp.json()["detail"]

    assert client.put("/api/me", headers=headers_a, json={"feishu_open_id": "ou_1"}).status_code == 200
    resp = client.put("/api/me", headers=headers_b, json={"feishu_open_id": "ou_1"})
    assert resp.status_code == 400

    assert client.put("/api/me", headers=headers_a, json={"wecom_webhook": wc_hook}).status_code == 200
    resp = client.put("/api/me", headers=headers_b, json={"wecom_webhook": wc_hook})
    assert resp.status_code == 400 and "企业微信" in resp.json()["detail"]

    # 非法 webhook 地址被拒绝
    resp = client.put("/api/me", headers=headers_b, json={"wecom_webhook": "https://example.com/x"})
    assert resp.status_code == 400

    # 解绑自己的渠道不受影响
    assert client.put("/api/me", headers=headers_a, json={"telegram_chat_id": ""}).status_code == 200
    assert client.put("/api/me", headers=headers_a, json={"wecom_webhook": ""}).status_code == 200


def test_me_update_validation_is_atomic():
    client = make_client()
    headers = user_headers(client, "atomicme")

    resp = client.put(
        "/api/me",
        headers=headers,
        json={"telegram_chat_id": "123456", "push_channels": "telegram,invalid"},
    )

    assert resp.status_code == 400
    assert client.get("/api/me", headers=headers).json()["telegram_chat_id"] == ""


def test_custom_telegram_bot_bind(monkeypatch):
    from app import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_resolve_telegram_bot",
        lambda token: ("my_bot", "777", ""),
    )
    client = make_client()
    h = user_headers(client, "custom_bot")
    assert client.put("/api/me", headers=h, json={"telegram_bot_token": "123:abc"}).status_code == 200
    me = client.get("/api/me", headers=h).json()
    assert me["custom_telegram_bot"] is True
    assert me["telegram_chat_id"] == "777"

    # 未先给自己的 bot 发消息 → 绑定失败并提示
    monkeypatch.setattr(
        api_mod,
        "_resolve_telegram_bot",
        lambda token: ("", "", "请先给你的机器人发一条消息"),
    )
    resp = client.put(
        "/api/me", headers=h, json={"telegram_bot_token": "999:abc"}
    )
    assert resp.status_code == 400 and "请先" in resp.json()["detail"]

    # 解绑自建机器人
    assert client.put("/api/me", headers=h, json={"telegram_bot_token": ""}).status_code == 200
    assert client.get("/api/me", headers=h).json()["custom_telegram_bot"] is False


def test_resolve_telegram_bot_parses_chat_id(monkeypatch):
    from app import api as api_mod

    calls = []

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append(url)
            if "getMe" in url:
                return FakeResp({"ok": True, "result": {"username": "my_bot"}})
            return FakeResp(
                {
                    "ok": True,
                    "result": [
                        {"message": {"chat": {"id": 111}}},
                        {"message": {"chat": {"id": 777}}},
                    ],
                }
            )

    monkeypatch.setattr("httpx.Client", FakeClient)
    username, chat_id, err = api_mod._resolve_telegram_bot("tok")
    assert username == "my_bot"
    assert chat_id == "777"  # 取最新的会话
    assert err == ""
    assert any("getMe" in u for u in calls)
    assert any("getUpdates" in u for u in calls)


def test_login_rate_limit():
    client = make_client()
    for _ in range(8):
        assert client.post("/api/auth/login", json={"username": "nobody", "password": "wrong123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "wrong123"}).status_code == 429


def _turnstile_cfg():
    cfg = Config()
    cfg.web.turnstile_site_key = "0x4AAAAAAEpFC-pepUf7vzMN"
    cfg.web.turnstile_secret = "test-secret"
    cfg.web.turnstile_hostnames = "vpush.net"
    return cfg


def test_turnstile_sitekey_hidden_until_secret():
    client = make_client("ts-off.db")
    assert client.get("/api/auth/turnstile").json() == {"sitekey": ""}


def test_turnstile_blocks_login_and_register_without_token():
    client = make_client("ts-deny.db", config=_turnstile_cfg())
    assert client.get("/api/auth/turnstile").json()["sitekey"] == "0x4AAAAAAEpFC-pepUf7vzMN"
    assert client.post(
        "/api/auth/login", json={"username": "nobody", "password": "wrong123"}
    ).status_code == 403
    assert client.post(
        "/api/auth/register",
        json={"username": "alice01", "password": "pass123456", "code": "NOPE"},
    ).status_code == 403


def test_turnstile_login_ok_when_siteverify_passes(monkeypatch):
    seen = []

    def fake_verify(**kwargs):
        seen.append(kwargs)
        return True

    monkeypatch.setattr("app.api.verify_turnstile", fake_verify)
    client = make_client("ts-ok.db", config=_turnstile_cfg())
    register(client, "alice01", "pass123456")
    resp = client.post(
        "/api/auth/login",
        json={
            "username": "alice01",
            "password": "pass123456",
            "cf-turnstile-response": "tok",
        },
    )
    assert resp.status_code == 200, resp.text
    assert seen[-1]["token"] == "tok"
    assert seen[-1]["action"] == "login"
    assert seen[0]["action"] == "register"


def test_verify_turnstile_checks_action_and_hostname(monkeypatch):
    captured = {}

    class DummyResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "action": "login", "hostname": "vpush.net"}

    class DummyClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            return DummyResp()

    monkeypatch.setattr("app.api.httpx.Client", DummyClient)
    from app.api import TURNSTILE_SITEVERIFY_URL, verify_turnstile

    assert verify_turnstile(
        secret="s", token="tok", action="login", hostnames={"vpush.net"}, ip="1.2.3.4"
    )
    assert captured["url"] == TURNSTILE_SITEVERIFY_URL
    assert captured["data"]["response"] == "tok"
    assert captured["data"]["remoteip"] == "1.2.3.4"
    assert not verify_turnstile(secret="s", token="tok", action="register", hostnames={"vpush.net"})
    assert not verify_turnstile(secret="s", token="", action="login", hostnames={"vpush.net"})


def test_admin_turnstile_toggle_and_tokens(monkeypatch):
    monkeypatch.setattr("app.api.verify_turnstile", lambda **k: True)
    client = make_client("ts-toggle.db", config=_turnstile_cfg())
    headers = auth_headers(client, "boss01")
    assert client.get("/api/auth/turnstile").json()["sitekey"] == "0x4AAAAAAEpFC-pepUf7vzMN"

    status = client.get("/api/admin/turnstile", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    assert body["active"] is True
    assert body["secret_set"] is True
    assert "secret" not in body
    assert client.get("/api/stats", headers=headers).json()["turnstile"]["secret_set"] is True

    off = client.put("/api/admin/turnstile", headers=headers, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["active"] is False
    assert off.json()["secret_set"] is True
    assert client.get("/api/auth/turnstile").json() == {"sitekey": ""}
    login = client.post(
        "/api/auth/login", json={"username": "boss01", "password": "secret1234"}
    )
    assert login.status_code == 200, login.text

    on = client.put(
        "/api/admin/turnstile",
        headers=headers,
        json={"enabled": True, "sitekey": "0xNEWKEY", "hostnames": "vpush.net"},
    )
    assert on.status_code == 200, on.text
    assert on.json()["sitekey"] == "0xNEWKEY"
    assert on.json()["active"] is True
    assert client.get("/api/auth/turnstile").json()["sitekey"] == "0xNEWKEY"


def test_admin_turnstile_save_requires_keys_and_rejects_non_admin():
    client = make_client("ts-keys.db")
    user = user_headers(client, "plain01")
    assert client.put("/api/admin/turnstile", headers=user, json={"enabled": True}).status_code in (401, 403)
    headers = auth_headers(client, "boss02")
    missing = client.put("/api/admin/turnstile", headers=headers, json={"enabled": True})
    assert missing.status_code == 400
    bad_host = client.put(
        "/api/admin/turnstile",
        headers=headers,
        json={
            "enabled": True,
            "sitekey": "0xabc",
            "secret": "sec",
            "hostnames": "https://vpush.net",
        },
    )
    assert bad_host.status_code == 400
    saved = client.put(
        "/api/admin/turnstile",
        headers=headers,
        json={
            "enabled": True,
            "sitekey": "0xabc",
            "secret": "sec",
            "hostnames": "vpush.net",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["active"] is True
    keep = client.put(
        "/api/admin/turnstile",
        headers=headers,
        json={"enabled": True, "sitekey": "0xabc", "secret": "", "hostnames": "vpush.net"},
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["secret_set"] is True
    assert "secret" not in keep.json()


def test_admin_turnstile_hostnames_not_defaulted():
    client = make_client("ts-hosts.db")
    headers = auth_headers(client, "boss-h")
    off = client.put("/api/admin/turnstile", headers=headers, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["hostnames"] == ""
    missing = client.put(
        "/api/admin/turnstile",
        headers=headers,
        json={"enabled": True, "sitekey": "0xabc", "secret": "sec"},
    )
    assert missing.status_code == 400


def test_admin_delete_user_cascades():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    reg = register(client, "doomed")
    uid = reg.json()["user"]["id"]
    doomed_headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    kid = client.post("/api/kols", headers=admin_headers, json={"platform": "xueqiu", "name": "A", "external_id": "1"}).json()["id"]
    client.post("/api/subscriptions", headers=doomed_headers, json={"kol_id": kid})

    # 不能删除自己
    self_id = client.get("/api/me", headers=admin_headers).json()["id"]
    assert client.delete(f"/api/users/{self_id}", headers=admin_headers).status_code == 400

    resp = client.delete(f"/api/users/{uid}", headers=admin_headers)
    assert resp.status_code == 200
    remaining = client.get("/api/users", headers=admin_headers).json()
    assert all(u["id"] != uid for u in remaining)
    # 订阅关系一并清除
    assert client.app.state.db.list_subscriptions(uid) == []


def test_admin_user_update_validation_is_atomic():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    reg = register(client, "atomicuser")
    uid = reg.json()["user"]["id"]

    resp = client.put(
        f"/api/users/{uid}",
        headers=admin_headers,
        json={"is_admin": True, "password": "123"},
    )

    assert resp.status_code == 400
    assert client.app.state.db.get_user(uid)["is_admin"] == 0


def test_admin_reset_password():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    old_headers = user_headers(client, "victim", "oldpass123")

    users = client.get("/api/users", headers=admin_headers).json()
    uid = next(u["id"] for u in users if u["username"] == "victim")
    # 新密码太短
    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"password": "123"}).status_code == 400
    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"password": "newpass456"}).status_code == 200
    assert client.get("/api/me", headers=old_headers).status_code == 401
    assert client.post("/api/auth/login", json={"username": "victim", "password": "oldpass123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "victim", "password": "newpass456"}).status_code == 200


def test_admin_rename_username():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    user_headers(client, "victim")

    users = client.get("/api/users", headers=admin_headers).json()
    uid = next(u["id"] for u in users if u["username"] == "victim")

    # 过短 / 重名
    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"username": "x"}).status_code == 400
    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"username": "boss01"}).status_code == 400
    # 改成自己的原名（不改动）应放行
    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"username": "victim"}).status_code == 200

    assert client.put(f"/api/users/{uid}", headers=admin_headers, json={"username": "renamed"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "victim", "password": "pass123456"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "renamed", "password": "pass123456"}).status_code == 200


def test_admin_user_list_hides_credentials():
    """管理员用户列表不得返回 feed_token / bark_key / wecom_webhook / llm_api_key 原文。

    这些是用户私有凭证，只应在「当前用户自己的」接口（/api/me、登录/注册响应）中返回。
    """
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    reg = register(client, "victim")
    uid = reg.json()["user"]["id"]
    # 给 victim 写入敏感字段
    client.app.state.db.update_user(
        uid,
        feed_token="topsecretfeedtoken123",
        bark_key="AaBbCcDdEeFf1234567890",
        wecom_webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wc1",
        llm_api_key="sk-llmsecret123",
    )
    rows = client.get("/api/users", headers=admin_headers).json()
    victim = next(u for u in rows if u["id"] == uid)
    for secret in ("feed_token", "bark_key", "wecom_webhook", "llm_api_key", "llm_api_base"):
        assert secret not in victim, f"管理员列表不应返回 {secret}"
    assert victim["username"] == "victim"
    assert victim["wecom_bound"] is True and victim["bark_bound"] is True

    # 管理员更新用户的响应同样不能带凭证原文
    resp = client.put(f"/api/users/{uid}", headers=admin_headers, json={"username": "victim2"})
    assert resp.status_code == 200
    for secret in ("feed_token", "bark_key", "wecom_webhook", "llm_api_key"):
        assert secret not in resp.json(), f"管理员更新响应不应返回 {secret}"

    # 当前用户自己的 /api/me 仍返回设置页面需要的字段，但凭证一律掩码
    me = client.get("/api/me", headers={"Authorization": f"Bearer {reg.json()['token']}"}).json()
    assert me["bark_key"] == "AaBb……7890"
    assert "feed_token" not in me
    assert me["wecom_webhook"] == "http……=wc1"  # 首尾各留 4 位供辨认
    assert me["llm_api_key"] == me["llm_api_key"][:4] + "……" + me["llm_api_key"][-4:]


def test_admin_test_push(monkeypatch):
    sent = []

    class FakeTelegram:
        def __init__(self, config, chat_id=None, client=None, **kwargs):
            self.chat_id = chat_id
            self.client = type("C", (), {"close": lambda self: None})()

        def send_text(self, text):
            sent.append(("tg", self.chat_id, text))

    class FakeFeishu:
        def __init__(self, config, open_id=None, chat_id=None, client=None, **kwargs):
            self.client = type("C", (), {"close": lambda self: None})()

        def send_text(self, text):
            sent.append(("fs", text))

    monkeypatch.setattr("app.notifiers.telegram.TelegramNotifier", FakeTelegram)
    monkeypatch.setattr("app.notifiers.feishu.FeishuNotifier", FakeFeishu)

    cfg = Config()
    cfg.notifiers.telegram.bot_token = "t"
    cfg.notifiers.feishu.app_id = "a"
    cfg.notifiers.feishu.app_secret = "s"
    client = make_client(config=cfg)
    admin_headers = auth_headers(client, "boss01")
    reg = register(client, "victim")
    uid = reg.json()["user"]["id"]
    victim_headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    client.put(
        "/api/me",
        headers=victim_headers,
        json={"telegram_chat_id": "111", "feishu_open_id": "ou_1", "feishu_chat_id": "oc_1"},
    )

    resp = client.post(
        "/api/admin/test-push",
        headers=admin_headers,
        json={"user_id": uid, "message": "hi"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == [
        {"channel": "telegram", "ok": True},
        {"channel": "feishu", "ok": True},
    ]
    assert sent == [("tg", "111", "【测试推送】hi"), ("fs", "【测试推送】hi")]


def test_weibo_qr_login(monkeypatch):
    class FakeResp:
        def __init__(self, payload):
            # 模拟新浪 SSO 的 JSONP 响应
            self.text = f"window.CB && CB({json.dumps(payload, ensure_ascii=False)});"

    class FakeCookie:
        def __init__(self, name, value, domain=""):
            self.name, self.value, self.domain = name, value, domain

    class FakeCookies:
        def __init__(self, cookies):
            self.jar = cookies

        def get(self, key, default=None):
            matches = [c for c in self.jar if c.name == key]
            return matches[0].value if matches else default

        def items(self):
            return [(c.name, c.value) for c in self.jar]

    class FakeClient:
        def __init__(self, **kwargs):
            self.cookies = FakeCookies([])
            self.headers = {}
            self._check_calls = 0

        def get(self, url, params=None):
            if "qrcode/image" in url:
                return FakeResp({"retcode": 20000000, "data": {"qrid": "Q1", "image": "//qr.example/q.png"}})
            if "qrcode/check" in url:
                self._check_calls += 1
                if self._check_calls == 1:
                    return FakeResp({"retcode": 50114001, "data": None})
                return FakeResp({"retcode": 20000000, "data": {"alt": "ALT"}})
            if "login.php" in url:
                return FakeResp({"crossDomainUrlList": ["http://cross.example/1"]})
            if "cross.example" in url:
                # 模拟跨域登录后出现同名多域的 SUB cookie（httpx 的 get/items 会冲突）
                self.cookies = FakeCookies(
                    [
                        FakeCookie("SUB", "s1", ".weibo.com"),
                        FakeCookie("SUB", "s2", "login.sina.com.cn"),
                        FakeCookie("SUBP", "p1", ".weibo.com"),
                    ]
                )
            return FakeResp({})

        def close(self):
            pass

    monkeypatch.setattr("app.weibo_qr.httpx.Client", FakeClient)
    client = make_client()
    headers = auth_headers(client)
    start = client.post("/api/admin/weibo-qr/start", headers=headers)
    assert start.status_code == 200
    assert start.json()["qrurl"] == "https://qr.example/q.png"
    qrid = start.json()["qrid"]
    pending = client.get(f"/api/admin/weibo-qr/status?qrid={qrid}", headers=headers)
    assert pending.status_code == 200 and pending.json()["status"] == "pending"
    status = client.get(f"/api/admin/weibo-qr/status?qrid={qrid}", headers=headers)
    assert status.status_code == 200 and status.json()["status"] == "ok"
    assert client.app.state.db.get_setting("weibo_cookie") == "SUB=s1; SUBP=p1"


def test_batch_import_kols(monkeypatch):
    # 昵称已填的行也会补查头像，测试里避免真实网络请求
    monkeypatch.setattr("app.api.resolve_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={
            "platform": "xueqiu",
            "lines": (
                "https://xueqiu.com/8790885129\n"
                "段永平 https://xueqiu.com/u/12345\n"
                "67890\n"
                "不是链接也不是ID\n"
            ),
        },
    )
    data = resp.json()
    assert resp.status_code == 200
    assert data["total"] == 4 and data["ok"] == 2
    assert len(data["failed"]) == 2
    assert any("67890" in f["line"] and "无法识别平台" in f["error"] for f in data["failed"])
    kols = client.get("/api/kols", headers=headers).json()
    names = {k["name"] for k in kols}
    assert "段永平" in names
    assert any(k["external_id"] == "8790885129" for k in kols)


def test_batch_import_auto_fills_xueqiu_nickname(monkeypatch):
    client = make_client()
    headers = auth_headers(client)
    monkeypatch.setattr(
        "app.api.resolve_profile",
        lambda uid, cookie="", db=None: (
            {"screen_name": "自动昵称", "avatar_url": "https://x/avatar.png"}
            if uid == "55555"
            else {}
        ),
    )
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={
            "platform": "xueqiu",
            "lines": (
                "https://xueqiu.com/u/55555\n"
                "https://xueqiu.com/u/66666\n"
                "段永平 https://xueqiu.com/u/77777"
            ),
        },
    )
    assert resp.status_code == 200 and resp.json()["ok"] == 3
    kols = client.get("/api/kols", headers=headers).json()
    names = {k["external_id"]: k["name"] for k in kols}
    assert names["55555"] == "自动昵称"
    assert next(k for k in kols if k["external_id"] == "55555")["avatar_url"] == "https://x/avatar.png"
    assert names["66666"] == "xueqiu_66666"  # 解析失败退回兜底名
    assert names["77777"] == "段永平"  # 已填昵称不覆盖


def test_weibo_kol_link_normalized():
    client = make_client()
    headers = auth_headers(client)
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "weibo", "name": "wu2198", "external_id": "https://weibo.com/u/1216826604"},
    )
    assert resp.status_code == 200
    kol = resp.json()
    assert kol["external_id"] == "1216826604"

    resp = client.put(
        f"/api/kols/{kol['id']}",
        headers=headers,
        json={"external_id": "https://weibo.com/u/9999999999"},
    )
    assert resp.status_code == 200
    assert resp.json()["external_id"] == "9999999999"

    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={"platform": "weibo", "lines": "微博大V2 https://weibo.com/u/8888888888"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] == 1
    assert resp.json()["failed"] == []


def test_xueqiu_cookie_write_and_batch_rss_url(monkeypatch, tmp_path):
    seed = {}
    monkeypatch.setattr("app.api.write_xueqiu_seed_cookie", lambda cookie: seed.update(cookie=cookie))
    client = make_client()
    headers = auth_headers(client)

    status = client.get("/api/admin/xueqiu-cookie", headers=headers)
    assert status.status_code == 200 and status.json()["set"] is False

    resp = client.post(
        "/api/admin/xueqiu-cookie",
        headers=headers,
        json={"cookie": "xq_a_token=abc; u=123"},
    )
    assert resp.status_code == 200
    assert seed == {"cookie": "xq_a_token=abc; u=123"}
    status = client.get("/api/admin/xueqiu-cookie", headers=headers).json()
    assert status["set"] is True and status["preview"] == "已配置"

    assert (
        client.post("/api/admin/xueqiu-cookie", headers=headers, json={"cookie": "  "}).status_code
        == 400
    )

    tw = client.get("/api/admin/twitter-cookie", headers=headers).json()
    assert tw["set"] is False
    assert (
        client.post(
            "/api/admin/twitter-cookie",
            headers=headers,
            json={"cookie": "guest_id=abc"},
        ).status_code
        == 400
    )
    assert client.post(
        "/api/admin/twitter-cookie",
        headers=headers,
        json={"cookie": "auth_token=a; ct0=b; lang=zh-CN"},
    ).status_code == 200
    tw = client.get("/api/admin/twitter-cookie", headers=headers).json()
    assert tw["set"] is True and tw["preview"] == "已配置"
    stats = client.get("/api/stats", headers=headers).json()
    assert stats["twitter_cookie"]["set"] is True

    # 批量导入支持 X 主页地址
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={"platform": "twitter", "lines": "Elon Musk https://x.com/elonmusk"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] == 1
    assert resp.json()["failed"] == []
    kols = client.get("/api/kols", headers=headers).json()
    assert any(k["external_id"] == "elonmusk" for k in kols)


def test_cookie_status_never_returns_credential_bytes(monkeypatch):
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    secrets = {
        "xueqiu_cookie": "xueqiu-SYNTHETIC-SECRET",
        "weibo_cookie": "weibo-SYNTHETIC-SECRET",
        "twitter_cookie": "auth_token=twitter-SYNTHETIC-SECRET; ct0=token",
        "zsxq_cookie": "zsxq-SYNTHETIC-SECRET",
        "ima_cookie": "ima-SYNTHETIC-SECRET",
    }
    for key, value in secrets.items():
        db.set_setting(key, value)

    responses = [
        client.get("/api/admin/xueqiu-cookie", headers=headers).json(),
        client.get("/api/admin/twitter-cookie", headers=headers).json(),
        client.get("/api/admin/zsxq-cookie", headers=headers).json(),
        client.get("/api/admin/ima-credentials", headers=headers).json()["cookie"],
    ]
    stats = client.get("/api/stats", headers=headers).json()
    responses.extend([
        stats["xueqiu_cookie"],
        stats["weibo_cookie"],
        stats["twitter_cookie"],
        stats["zsxq_cookie"],
        stats["ima_credentials"]["cookie"],
    ])
    for response in responses:
        assert response["preview"] == "已配置"
        assert all(secret not in str(response) for secret in secrets.values())

    db.set_setting("twitter_cookie", "")
    db.set_setting("zsxq_cookie", "")
    db.set_setting("ima_cookie", "")
    monkeypatch.setenv("TWITTER_COOKIE", "twitter-ENV-SYNTHETIC-SECRET")
    monkeypatch.setenv("ZSXQ_COOKIE", "zsxq-ENV-SYNTHETIC-SECRET")
    monkeypatch.setenv("IMA_COOKIE", "ima-ENV-SYNTHETIC-SECRET")
    env_stats = client.get("/api/stats", headers=headers).json()
    assert env_stats["twitter_cookie"]["preview"] == "已配置"
    assert env_stats["zsxq_cookie"]["preview"] == "已配置"
    assert env_stats["ima_credentials"]["cookie"] == {
        "set": True,
        "updated_at": "",
        "preview": "已配置",
        "from_env": True,
    }
    assert "twitter-ENV-SYNTHETIC-SECRET" not in str(env_stats)
    assert "zsxq-ENV-SYNTHETIC-SECRET" not in str(env_stats)
    assert "ima-ENV-SYNTHETIC-SECRET" not in str(env_stats)
    zsxq_env = client.get("/api/admin/zsxq-cookie", headers=headers).json()
    twitter_env = client.get("/api/admin/twitter-cookie", headers=headers).json()
    ima_env = client.get("/api/admin/ima-credentials", headers=headers).json()["cookie"]
    assert zsxq_env["preview"] == "已配置" and zsxq_env["from_env"] is True
    assert twitter_env["preview"] == "已配置" and twitter_env["from_env"] is True
    assert ima_env["preview"] == "已配置" and ima_env["from_env"] is True
    assert "zsxq-ENV-SYNTHETIC-SECRET" not in str(zsxq_env)
    assert "twitter-ENV-SYNTHETIC-SECRET" not in str(twitter_env)
    assert "ima-ENV-SYNTHETIC-SECRET" not in str(ima_env)



def test_ima_api_key_status_never_returns_credential_bytes():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    db.set_setting("ima_openapi_clientid", "client-id-preview")

    for api_key in ("ima-OPENAPI-SYNTHETIC-SECRET", "short"):
        db.set_setting("ima_openapi_apikey", api_key)
        response = client.get("/api/admin/ima-credentials", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["openapi_apikey"] == {"set": True}
        assert api_key not in str(data)
        assert data["openapi_clientid"]["set"] is True
        assert data["openapi_clientid"]["preview"] == "client-id-pr…"

    db.set_setting("ima_openapi_apikey", "")
    data = client.get("/api/admin/ima-credentials", headers=headers).json()
    assert data["openapi_apikey"] == {"set": False}


def test_admin_can_clear_saved_cookies(monkeypatch):
    """管理员可清除已保存 Cookie；未知源拒绝，空清除可重复。"""
    seed = {}
    monkeypatch.setattr("app.api.write_xueqiu_seed_cookie", lambda cookie: seed.update(cookie=cookie))
    client = make_client()
    headers = auth_headers(client)
    user = register(client, "plainuser", "pass123456")
    user_headers = {"Authorization": f"Bearer {user.json()['token']}"}

    assert client.delete("/api/admin/cookies/nope", headers=headers).status_code == 400
    assert client.delete("/api/admin/cookies/xueqiu", headers=user_headers).status_code == 403

    assert (
        client.post(
            "/api/admin/xueqiu-cookie",
            headers=headers,
            json={"cookie": "xq_a_token=abc; u=123"},
        ).status_code
        == 200
    )
    resp = client.delete("/api/admin/cookies/xueqiu", headers=headers)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert seed.get("cookie") == ""
    assert client.get("/api/admin/xueqiu-cookie", headers=headers).json()["set"] is False
    assert client.get("/api/stats", headers=headers).json()["xueqiu_cookie"]["set"] is False
    assert client.delete("/api/admin/cookies/xueqiu", headers=headers).status_code == 200

    assert (
        client.post(
            "/api/admin/twitter-cookie",
            headers=headers,
            json={"cookie": "auth_token=a; ct0=b"},
        ).status_code
        == 200
    )
    assert client.delete("/api/admin/cookies/twitter", headers=headers).status_code == 200
    assert client.get("/api/admin/twitter-cookie", headers=headers).json()["set"] is False

    assert (
        client.post(
            "/api/admin/zsxq-cookie",
            headers=headers,
            json={"cookie": "zsxq_access_token=abc"},
        ).status_code
        == 200
    )
    assert client.delete("/api/admin/cookies/zsxq", headers=headers).status_code == 200
    assert client.get("/api/admin/zsxq-cookie", headers=headers).json()["set"] is False

    client.app.state.db.set_setting("weibo_cookie", "SUB=x")
    client.app.state.db.set_setting("weibo_cookie_updated_at", "1")
    assert client.delete("/api/admin/cookies/weibo", headers=headers).status_code == 200
    assert not (client.app.state.db.get_setting("weibo_cookie") or "")
    assert client.get("/api/stats", headers=headers).json()["weibo_cookie"]["set"] is False

    assert (
        client.post(
            "/api/admin/ima-credentials",
            headers=headers,
            json={"cookie": "IMA-TOKEN=t; IMA-UID=1", "openapi_clientid": "cid", "openapi_apikey": "key"},
        ).status_code
        == 200
    )
    assert client.delete("/api/admin/cookies/ima", headers=headers).status_code == 200
    ima = client.get("/api/admin/ima-credentials", headers=headers).json()
    assert ima["cookie"]["set"] is False
    assert ima["openapi_clientid"]["set"] is True
    assert client.get("/api/stats", headers=headers).json()["ima_credentials"]["cookie"]["set"] is False


def test_kol_request_flow(monkeypatch):
    # 审批时会自动解析昵称/头像，测试里避免真实网络请求
    monkeypatch.setattr("app.kol_requests.resolve_profile", lambda uid, cookie="", db=None: {})
    client = make_client()
    admin_headers = auth_headers(client)
    u1 = register(client, "user01", "pass123456")
    u_headers = {"Authorization": f"Bearer {u1.json()['token']}"}

    cid = client.app.state.db.add_category("宏观")
    # 用户提交申请（雪球主页链接自动提取 UID）
    resp = client.post(
        "/api/kol-requests",
        headers=u_headers,
        json={"platform": "xueqiu", "external_id": "https://xueqiu.com/u/55555", "category_id": cid},
    )
    assert resp.status_code == 200
    # 重复申请被拦截
    resp = client.post(
        "/api/kol-requests",
        headers=u_headers,
        json={"platform": "xueqiu", "external_id": "55555", "category_id": cid},
    )
    assert resp.status_code == 400 and "处理中" in resp.json()["detail"]
    # 已存在的大V不允许申请
    client.app.state.db.add_kol("weibo", "已有", "66666")
    resp = client.post(
        "/api/kol-requests",
        headers=u_headers,
        json={"platform": "weibo", "external_id": "66666", "category_id": cid},
    )
    assert resp.status_code == 400 and "已在目录中" in resp.json()["detail"]

    mine = client.get("/api/my/kol-requests", headers=u_headers).json()
    assert len(mine) == 1 and mine[0]["status"] == "pending" and mine[0]["external_id"] == "55555"

    pending = client.get("/api/admin/kol-requests?status=pending", headers=admin_headers).json()
    assert len(pending) == 1 and pending[0]["requester"] == "user01"

    req_id = pending[0]["id"]
    approved = client.post(f"/api/admin/kol-requests/{req_id}/approve", headers=admin_headers)
    assert approved.status_code == 200
    assert approved.json()["platform"] == "xueqiu" and approved.json()["external_id"] == "55555"
    # 审批通过后自动订阅申请人
    assert approved.json()["id"] in client.app.state.db.subscribed_kol_ids(u1.json()["user"]["id"])

    done = client.get("/api/admin/kol-requests", headers=admin_headers).json()
    assert done[0]["status"] == "approved" and done[0]["handled_at"]

    # 再次审批同一申请会 404
    assert (
        client.post(f"/api/admin/kol-requests/{req_id}/approve", headers=admin_headers).status_code
        == 404
    )


def test_kol_visibility_acl():
    client = make_client()
    admin_headers = auth_headers(client)
    r1 = register(client, "usera1", "pass123456")
    r2 = register(client, "userb1", "pass123456")
    u1_id = r1.json()["user"]["id"]
    user1_headers = {"Authorization": f"Bearer {r1.json()['token']}"}
    user2_headers = {"Authorization": f"Bearer {r2.json()['token']}"}
    db = client.app.state.db

    public_id = db.add_kol("xueqiu", "公开大V", "1")
    private_id = db.add_kol("xueqiu", "私有大V", "2")
    db.update_kol(private_id, is_private=True)
    db.set_kol_acl(private_id, [u1_id])

    cat1 = client.get("/api/catalog", headers=user1_headers).json()
    cat2 = client.get("/api/catalog", headers=user2_headers).json()
    assert {k["id"] for k in cat1} == {public_id, private_id}
    assert {k["id"] for k in cat2} == {public_id}

    # u2 无法订阅私有大V，u1 可以
    assert (
        client.post(
            "/api/subscriptions",
            headers=user2_headers,
            json={"kol_id": private_id},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/subscriptions",
            headers=user1_headers,
            json={"kol_id": private_id},
        ).status_code
        == 200
    )
    # u2 直接访问私有大V详情也是 404
    assert client.get(f"/api/kols/{private_id}", headers=user2_headers).status_code == 404

    detail = client.get(f"/api/kols/{private_id}", headers=admin_headers).json()
    assert detail["is_private"] == 1 and detail["visible_users"] == ["usera1"]

    # 管理员把白名单换成 u2，u1 立刻不可见
    resp = client.put(
        f"/api/kols/{private_id}",
        headers=admin_headers,
        json={"visible_users": ["userb1"]},
    )
    assert resp.status_code == 200
    assert private_id not in {k["id"] for k in client.get("/api/catalog", headers=user1_headers).json()}
    assert private_id in {k["id"] for k in client.get("/api/catalog", headers=user2_headers).json()}

    # 转为公开后所有人可见
    client.put(f"/api/kols/{private_id}", headers=admin_headers, json={"is_private": False})
    assert private_id in {k["id"] for k in client.get("/api/catalog", headers=user1_headers).json()}
    assert private_id in {k["id"] for k in client.get("/api/catalog", headers=user2_headers).json()}


def test_register_requires_invite_code():
    client = make_client()
    admin_headers = auth_headers(client)

    # 不带注册码
    resp = client.post(
        "/api/auth/register",
        json={"username": "nocode", "password": "secret1234"},
    )
    assert resp.status_code == 400 and "邀请码" in resp.json()["detail"]

    # 无效注册码
    resp = client.post(
        "/api/auth/register",
        json={"username": "badcode", "password": "secret1234", "code": "NOPE1234"},
    )
    assert resp.status_code == 400 and "无效或已被使用" in resp.json()["detail"]

    # 管理员生成 3 个注册码
    resp = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 3, "note": "测试"},
    )
    assert resp.status_code == 200
    codes = resp.json()["codes"]
    assert len(codes) == 3 and len(set(codes)) == 3

    codes_list = client.get("/api/admin/register-codes", headers=admin_headers).json()
    assert all(c["code"] in codes or c["note"] == "测试" for c in codes_list if c["note"] == "测试")

    # 用生成码注册成功，且码被消费
    resp = client.post(
        "/api/auth/register",
        json={"username": "invited", "password": "secret1234", "code": codes[0]},
    )
    assert resp.status_code == 200
    row = next(
        c for c in client.get("/api/admin/register-codes", headers=admin_headers).json() if c["code"] == codes[0]
    )
    assert row["used_by"] and row["used_by_name"] == "invited"

    # 同一注册码不能再用
    resp = client.post(
        "/api/auth/register",
        json={"username": "invited2", "password": "secret1234", "code": codes[0]},
    )
    assert resp.status_code == 400 and "无效或已被使用" in resp.json()["detail"]


def test_generate_register_codes_sets_batch_and_expiry():
    client = make_client()
    admin_headers = auth_headers(client)
    resp = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 2, "note": "朋友", "expires_in_days": 7},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2 and len(body["codes"]) == 2
    assert body["note"] == "朋友"
    assert body["batch_id"]
    assert body["expires_at"]
    rows = [
        r
        for r in client.get("/api/admin/register-codes", headers=admin_headers).json()
        if r["code"] in body["codes"]
    ]
    assert len(rows) == 2
    assert rows[0]["batch_id"] == rows[1]["batch_id"] == body["batch_id"]
    assert all(r["expires_at"] == body["expires_at"] for r in rows)
    assert all(r["created_by_name"] == "testadmin" for r in rows)

    never = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "expires_in_days": None},
    ).json()
    never_row = next(
        r
        for r in client.get("/api/admin/register-codes", headers=admin_headers).json()
        if r["code"] == never["codes"][0]
    )
    assert never["expires_at"] in (None, "")
    assert never_row["expires_at"] in (None, "")

    bad = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "note": "x" * 41},
    )
    assert bad.status_code == 400
    bad_days = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "expires_in_days": 14},
    )
    assert bad_days.status_code == 400


def test_register_expired_and_revoked_codes_have_distinct_errors():
    client = make_client()
    db = client.app.state.db
    db.add_register_code("EXPIRED1")
    db._execute(
        "UPDATE register_codes SET expires_at = datetime('now', '-1 day') WHERE code = 'EXPIRED1'"
    )
    resp = client.post(
        "/api/auth/register",
        json={"username": "expire1", "password": "secret1234", "code": "EXPIRED1"},
    )
    assert resp.status_code == 400 and "已过期" in resp.json()["detail"]

    db.add_register_code("REVOKED1")
    db.revoke_register_code("REVOKED1")
    resp = client.post(
        "/api/auth/register",
        json={"username": "revoke1", "password": "secret1234", "code": "REVOKED1"},
    )
    assert resp.status_code == 400 and "已作废" in resp.json()["detail"]


def test_me_translate_twitter_pref_swaps_feed_text():
    client = make_client()
    headers = user_headers(client, "reader")
    db = client.app.state.db
    me = client.get("/api/me", headers=headers).json()
    assert me["translate_twitter"] is True
    uid = me["id"]
    kid = db.add_kol("twitter", "Semi", "semi")
    db.add_subscription(uid, kid)
    db.insert_post(
        "twitter",
        kid,
        "2092100432948781527",
        "中文标题",
        "中文译文",
        "https://x.com/a/status/1",
        "",
        title_src="Hello title",
        content_src="Hello original",
    )
    feed = client.get("/api/my/feed", headers=headers).json()
    assert feed[0]["title"] == "中文标题" and feed[0]["content"] == "中文译文"
    assert client.put("/api/me", headers=headers, json={"translate_twitter": False}).status_code == 200
    assert client.get("/api/me", headers=headers).json()["translate_twitter"] is False
    feed = client.get("/api/my/feed", headers=headers).json()
    assert feed[0]["title"] == "Hello title" and feed[0]["content"] == "Hello original"
    kol_posts = client.get(f"/api/kols/{kid}/posts", headers=headers).json()
    assert kol_posts[0]["content"] == "Hello original"


def test_me_translate_pref_swaps_truth_feed_text():
    client = make_client()
    headers = user_headers(client, "reader")
    db = client.app.state.db
    uid = client.get("/api/me", headers=headers).json()["id"]
    kid = db.add_kol("truth", "Trump", "realDonaldTrump")
    db.add_subscription(uid, kid)
    db.insert_post(
        "truth",
        kid,
        "117213723499789673",
        "中文标题",
        "中文译文",
        "https://truthsocial.com/@realDonaldTrump/posts/117213723499789673",
        "",
        title_src="Hello title",
        content_src="Hello original",
    )
    feed = client.get("/api/my/feed", headers=headers).json()
    assert feed[0]["title"] == "中文标题" and feed[0]["content"] == "中文译文"
    assert feed[0]["content_src"] == "Hello original"
    assert client.put("/api/me", headers=headers, json={"translate_twitter": False}).status_code == 200
    feed = client.get("/api/my/feed", headers=headers).json()
    assert feed[0]["title"] == "Hello title" and feed[0]["content"] == "Hello original"


def test_me_includes_push_guide():
    cfg = Config()
    cfg.notifiers.telegram.bot_username = "dav_bot"
    cfg.notifiers.feishu.bot_name = "大V订阅机器人"
    client = make_client(config=cfg)
    headers = auth_headers(client)
    me = client.get("/api/me", headers=headers).json()
    assert me["push_guide"] == {"telegram_bot_username": "dav_bot", "feishu_bot_name": "大V订阅机器人"}


def test_empty_password_login_rejected():
    client = make_client()
    # 机器人/微信自动创建的账号没有密码
    client.app.state.db.add_user("tg_bot_user", "", telegram_chat_id="111")
    resp = client.post(
        "/api/auth/login",
        json={"username": "tg_bot_user", "password": ""},
    )
    assert resp.status_code == 401
    resp = client.post(
        "/api/auth/login",
        json={"username": "tg_bot_user", "password": "guess"},
    )
    assert resp.status_code == 401


def test_register_password_too_long_rejected():
    client = make_client()
    client.app.state.db.add_register_code("LONG1")
    resp = client.post(
        "/api/auth/register",
        json={"username": "longpw", "password": "x" * 200, "code": "LONG1"},
    )
    assert resp.status_code == 400 and "密码最长" in resp.json()["detail"]


def test_revoke_register_code():
    client = make_client()
    admin_headers = auth_headers(client)
    codes = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 2, "note": "revoke"},
    ).json()["codes"]
    resp = client.delete(f"/api/admin/register-codes/{codes[0]}", headers=admin_headers)
    assert resp.status_code == 200
    remaining = client.get("/api/admin/register-codes", headers=admin_headers).json()
    row0 = next(c for c in remaining if c["code"] == codes[0])
    assert row0["revoked_at"]
    assert not row0["used_by"]
    post_resp = client.post(
        f"/api/admin/register-codes/{codes[0]}/revoke", headers=admin_headers
    )
    assert post_resp.status_code == 400
    register(client, "usedit", code=codes[1])
    assert client.delete(f"/api/admin/register-codes/{codes[1]}", headers=admin_headers).status_code == 400
    assert client.post(
        f"/api/admin/register-codes/{codes[1]}/revoke", headers=admin_headers
    ).status_code == 400


def test_revoke_register_code_rejects_failed_update():
    client = make_client()
    admin_headers = auth_headers(client)
    codes = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "note": "race"},
    ).json()["codes"]
    db = client.app.state.db

    def lose_race(code):
        db._execute(
            "UPDATE register_codes SET used_by = 1 WHERE code = ?",
            (code.strip().upper(),),
        )
        return False

    db.revoke_register_code = lose_race
    resp = client.post(
        f"/api/admin/register-codes/{codes[0]}/revoke", headers=admin_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "该注册码已被使用，不能删除"
    logs = db.list_admin_logs()
    assert not any(
        log["action"] == "revoke_register_code" and log["target"] == codes[0]
        for log in logs
    )


def test_revoke_unused_in_batch_and_patch_note():
    client = make_client()
    admin_headers = auth_headers(client)
    body = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 3, "note": "批"},
    ).json()
    register(client, "batchu1", code=body["codes"][0])
    resp = client.post(
        f"/api/admin/register-code-batches/{body['batch_id']}/revoke-unused",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 2
    rows = {
        r["code"]: r
        for r in client.get("/api/admin/register-codes", headers=admin_headers).json()
        if r["code"] in body["codes"]
    }
    assert not rows[body["codes"][0]]["revoked_at"]
    assert rows[body["codes"][0]]["used_by"]
    assert rows[body["codes"][1]]["revoked_at"]
    assert rows[body["codes"][2]]["revoked_at"]
    patch = client.patch(
        f"/api/admin/register-codes/{body['codes'][0]}",
        headers=admin_headers,
        json={"note": "给张三"},
    )
    assert patch.status_code == 200
    assert patch.json()["note"] == "给张三"
    too_long = client.patch(
        f"/api/admin/register-codes/{body['codes'][0]}",
        headers=admin_headers,
        json={"note": "x" * 41},
    )
    assert too_long.status_code == 400


def test_catalog_sorted_by_priority_and_activity():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    normal_id = db.add_kol("xueqiu", "普通A", "1")
    priority_id = db.add_kol("xueqiu", "优先P", "2", priority=True)
    old_post_id = db.insert_post("xueqiu", normal_id, "p1", "t", "c", "u", "")
    db._execute("UPDATE posts SET fetched_at = datetime('now', '-1 day') WHERE id = ?", (old_post_id,))
    db._execute("UPDATE kols SET last_post_at = datetime('now', '-1 day') WHERE id = ?", (normal_id,))
    active_id = db.add_kol("xueqiu", "活跃B", "3")
    db.insert_post("xueqiu", active_id, "p2", "t2", "c2", "u", "")

    ids = [k["id"] for k in client.get("/api/catalog", headers=headers).json()]
    assert ids[0] == priority_id
    assert ids.index(active_id) < ids.index(normal_id)


def test_stats_include_source_health():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    import time as _time

    now = int(_time.time())
    # 有启用的雪球大V：source_ok 新 → 正常；source_ok 旧 → 状态降为「近期无成功」
    db.add_kol("xueqiu", "A", "1")
    db.set_setting("source_ok_xueqiu", str(now - 60))
    db.set_setting("source_err_weibo", "登录失败")
    db.set_setting("source_fails_weibo", "3")
    stats = client.get("/api/stats", headers=headers).json()
    sources = {s["platform"]: s for s in stats["sources"]}
    assert sources["xueqiu"]["ok"] is True
    assert sources["weibo"]["ok"] is False and sources["weibo"]["consecutive_fails"] == 3
    assert sources["weibo"]["last_error"] == "登录失败"

    # source_ok 超出新鲜度窗口（2×轮询间隔，至少 5 分钟）→ 状态转 false
    db.set_setting("source_ok_xueqiu", str(now - 3600))
    stats = client.get("/api/stats", headers=headers).json()
    sources = {s["platform"]: s for s in stats["sources"]}
    assert sources["xueqiu"]["ok"] is False


def test_stats_no_enabled_kol_not_stale():
    """平台没有启用大V时，source_ok 即使陈旧也不判「近期无成功」。"""
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    import time as _time

    now = int(_time.time())
    db.set_setting("source_ok_weibo", str(now - 3600))
    stats = client.get("/api/stats", headers=headers).json()
    sources = {s["platform"]: s for s in stats["sources"]}
    assert sources["weibo"]["ok"] is True


def test_polling_config_get_and_update():
    client = make_client()
    headers = auth_headers(client)
    cfg = client.get("/api/admin/polling-config", headers=headers).json()
    assert cfg["interval_seconds"] > 0 and cfg["daily_report_hour"] == 20
    assert cfg["telegram_rich_messages"] is True
    assert cfg["truth_interval_seconds"] == 0  # 默认 0 = 跟随优先档

    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"truth_interval_seconds": 15},
    )
    assert resp.status_code == 200
    assert resp.json()["truth_interval_seconds"] == 15
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"truth_interval_seconds": 601},
    )
    assert resp.status_code == 400

    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"interval_seconds": 120, "digest_interval_seconds": 300, "daily_report_hour": 21},
    )
    assert resp.status_code == 200
    assert resp.json()["interval_seconds"] == 120
    assert resp.json()["digest_interval_seconds"] == 300
    assert resp.json()["daily_report_hour"] == 21
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"translate_twitter_content": True},
    )
    assert resp.json()["translate_twitter_content"] is True
    assert client.get("/api/admin/polling-config", headers=headers).json()["translate_twitter_content"] is True
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"telegram_rich_messages": False},
    )
    assert resp.json()["telegram_rich_messages"] is False
    assert client.get("/api/admin/polling-config", headers=headers).json()["telegram_rich_messages"] is False
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"telegram_rich_messages": True},
    )
    assert resp.json()["telegram_rich_messages"] is True

    # 超范围被拒绝
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"daily_report_hour": 99},
    )
    assert resp.status_code == 400


def test_polling_config_frequency_tiers():
    """采集频率档位参数：GET 返回默认值、PUT 保存即时生效、超范围被拒。"""
    client = make_client()
    headers = auth_headers(client)
    cfg = client.get("/api/admin/polling-config", headers=headers).json()
    assert cfg["combination_base_seconds"] == 30
    assert cfg["combination_idle_cap_seconds"] == 120
    assert cfg["normal_idle_cap_seconds"] == 900
    assert cfg["priority_idle_cap_seconds"] == 180
    # 次要大V最低合并条数：默认 1（现行为），PUT 保存、超范围被拒
    assert cfg["secondary_min_digest_count"] == 1
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"secondary_min_digest_count": 3},
    )
    assert resp.status_code == 200
    assert resp.json()["secondary_min_digest_count"] == 3
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"secondary_min_digest_count": 0},
    )
    assert resp.status_code == 400
    assert cfg["x_fallback_cap_seconds"] == 1800
    # 次要大V档位
    assert cfg["secondary_interval_seconds"] == 900
    assert cfg["secondary_idle_cap_seconds"] == 3600
    assert cfg["secondary_digest_interval_seconds"] == 3600

    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={
            "combination_base_seconds": 45,
            "combination_idle_cap_seconds": 200,
            "normal_idle_cap_seconds": 600,
            "priority_idle_cap_seconds": 300,
            "x_fallback_cap_seconds": 900,
            "secondary_interval_seconds": 1800,
            "secondary_idle_cap_seconds": 5400,
            "secondary_digest_interval_seconds": 7200,
        },
    )
    assert resp.status_code == 200
    got = resp.json()
    assert got["combination_base_seconds"] == 45
    assert got["combination_idle_cap_seconds"] == 200
    assert got["normal_idle_cap_seconds"] == 600
    assert got["priority_idle_cap_seconds"] == 300
    assert got["x_fallback_cap_seconds"] == 900
    assert got["secondary_interval_seconds"] == 1800
    assert got["secondary_idle_cap_seconds"] == 5400
    assert got["secondary_digest_interval_seconds"] == 7200

    # 超范围被拒
    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"combination_base_seconds": 2},  # 低于下限 5
    )
    assert resp.status_code == 400


def test_polling_config_zsxq_fields():
    """知识星球翻页/间隔/预缓存：GET 默认、PUT 保存、超范围被拒。"""
    client = make_client()
    headers = auth_headers(client)
    cfg = client.get("/api/admin/polling-config", headers=headers).json()
    assert cfg["zsxq_max_pages"] == 3
    assert cfg["zsxq_fetch_delay_seconds"] == 1.0
    assert cfg["zsxq_file_delay_seconds"] == 1.0
    assert cfg["zsxq_prefetch_files"] is False

    resp = client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={
            "zsxq_max_pages": 5,
            "zsxq_fetch_delay_seconds": 0.5,
            "zsxq_file_delay_seconds": 2.0,
            "zsxq_prefetch_files": True,
        },
    )
    assert resp.status_code == 200
    got = resp.json()
    assert got["zsxq_max_pages"] == 5
    assert got["zsxq_fetch_delay_seconds"] == 0.5
    assert got["zsxq_file_delay_seconds"] == 2.0
    assert got["zsxq_prefetch_files"] is True
    assert client.get("/api/admin/polling-config", headers=headers).json()["zsxq_prefetch_files"] is True

    assert client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"zsxq_max_pages": 21},
    ).status_code == 400
    assert client.put(
        "/api/admin/polling-config",
        headers=headers,
        json={"zsxq_fetch_delay_seconds": 0.1},
    ).status_code == 400


def test_zsxq_cache_stats_and_purge():
    """stats 带附件缓存占用；purge 只删未引用文件。"""
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    files_dir = Path(db.path).parent / "zsxq_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "22.pdf").write_bytes(b"keep")
    (files_dir / "99.pdf").write_bytes(b"xxxx")
    kid = db.add_kol("zsxq", "g", "123")
    db.insert_post(
        "zsxq",
        kid,
        "t1",
        "t",
        "c",
        "u",
        "2026-08-20",
        detail={"files": [{"file_id": "22", "name": "a.pdf", "url": "/zsxq-files/22.pdf"}]},
    )
    stats = client.get("/api/stats", headers=headers).json()
    assert stats["zsxq_cache"]["files"] == 2
    assert stats["zsxq_cache"]["bytes"] == 8
    resp = client.post("/api/admin/zsxq-cache/purge", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["files"] == 1
    assert (files_dir / "22.pdf").exists()
    assert not (files_dir / "99.pdf").exists()


def test_posts_search_and_push_log_filters():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "大V甲", "1")
    post_id = db.insert_post("xueqiu", kid, "p1", "茅台又新高", "内容", "u", "")
    db.add_push_log(post_id, "telegram", "success", user_id=1)
    db.add_push_log(post_id, "feishu", "failed", "boom", user_id=1)

    hits = client.get("/api/posts?q=茅台", headers=headers).json()
    assert len(hits) == 1 and hits[0]["title"] == "茅台又新高"
    assert client.get("/api/posts?q=不存在", headers=headers).json() == []

    logs = client.get("/api/push-logs?channel=feishu&status=failed", headers=headers).json()
    assert len(logs) == 1 and logs[0]["channel"] == "feishu"
    logs = client.get("/api/push-logs?channel=telegram", headers=headers).json()
    assert len(logs) == 1


def test_posts_pagination_offset():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "大V", "1")
    for i in range(5):
        db.insert_post("xueqiu", kid, f"p{i}", f"标题{i}", "c", "u", "")
    page1 = client.get("/api/posts?limit=2&offset=0", headers=headers).json()
    page2 = client.get("/api/posts?limit=2&offset=2", headers=headers).json()
    assert len(page1) == 2 and len(page2) == 2
    ids1 = [p["id"] for p in page1]
    ids2 = [p["id"] for p in page2]
    assert not set(ids1) & set(ids2)  # 两页无重叠
    assert ids1[0] > ids1[1] > ids2[0]  # 按 id 倒序


def test_delete_kol_cascades_posts_and_logs():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "大V", "1")
    post_id = db.insert_post("xueqiu", kid, "p1", "t", "c", "u", "")
    db.add_push_log(post_id, "telegram", "success")
    uid = db.add_user("u1", "hash")
    db.add_subscription(uid, kid)

    assert client.delete(f"/api/kols/{kid}", headers=headers).status_code == 200
    assert db.count_posts() == 0
    assert db.list_push_logs(limit=10) == []
    assert db.list_subscriptions(uid) == []


def test_pagination_limits_are_bounded():
    """分页 limit 必须被钳制：负数/0 不得变成 SQLite 的无限制查询（LIMIT -1），最大不超过 500。"""
    client = make_client()
    headers = auth_headers(client)
    kid = client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "A", "external_id": "1"}
    ).json()["id"]
    # 造 3 条帖子；若 limit=-1 未被钳制（SQLite LIMIT -1 = 无限制）会返回全部 3 条
    for i in range(3):
        client.app.state.db.insert_post("xueqiu", kid, f"p{i}", f"t{i}", "c", "u", "")
    client.app.state.db.add_push_log(1, "telegram", "success", user_id=1)
    client.app.state.db.add_push_log(1, "telegram", "success", user_id=1)
    client.app.state.db.add_push_log(1, "telegram", "success", user_id=1)

    # 用户接口
    user_h = user_headers(client, "reader")
    client.post("/api/subscriptions", headers=user_h, json={"kol_id": kid})
    assert len(client.get("/api/my/feed?limit=-1", headers=user_h).json()) <= 1
    assert len(client.get("/api/my/feed?limit=0", headers=user_h).json()) <= 1
    assert len(client.get("/api/my/feed?limit=2", headers=user_h).json()) == 2
    assert len(client.get(f"/api/kols/{kid}/posts?limit=-1", headers=user_h).json()) <= 1
    assert len(client.get(f"/api/kols/{kid}/posts?limit=0", headers=user_h).json()) <= 1
    assert len(client.get(f"/api/kols/{kid}/posts?limit=2", headers=user_h).json()) == 2

    # 管理员接口
    assert len(client.get("/api/posts?limit=-1", headers=headers).json()) <= 1
    assert len(client.get("/api/posts?limit=0", headers=headers).json()) <= 1
    assert len(client.get("/api/posts?limit=2", headers=headers).json()) == 2
    assert len(client.get("/api/push-logs?limit=-1", headers=headers).json()) <= 1
    assert len(client.get("/api/push-logs?limit=0", headers=headers).json()) <= 1
    assert len(client.get("/api/push-logs?limit=2", headers=headers).json()) == 2
    assert len(client.get("/api/admin/logs?limit=-1", headers=headers).json()) <= 1
    assert len(client.get("/api/admin/logs?limit=0", headers=headers).json()) <= 1

    # 大 limit 上限 500
    assert len(client.get("/api/posts?limit=501", headers=headers).json()) == 3
    assert len(client.get("/api/push-logs?limit=501", headers=headers).json()) == 3
    # 负数 offset 安全钳制为 0
    assert len(client.get("/api/posts?limit=10&offset=-5", headers=headers).json()) == 3


def test_posts_and_push_logs_api():
    client = make_client()
    headers = auth_headers(client)
    kid = client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "A", "external_id": "1"}
    ).json()["id"]
    client.app.state.db.insert_post("xueqiu", kid, "p1", "t", "c", "u", "")
    assert client.get("/api/posts", headers=headers).json()[0]["title"] == "t"
    assert client.get("/api/push-logs", headers=headers).json() == []


def test_category_crud_and_kol_assignment():
    client = make_client()
    headers = auth_headers(client)

    resp = client.post("/api/categories", headers=headers, json={"name": "实盘"})
    assert resp.status_code == 200
    cid = resp.json()["id"]
    assert client.post("/api/categories", headers=headers, json={"name": "实盘"}).status_code == 400

    kid = client.post(
        "/api/kols",
        headers=headers,
        json={"platform": "xueqiu", "name": "A", "external_id": "1", "category_id": cid},
    ).json()["id"]
    assert client.get("/api/kols", headers=headers).json()[0]["category_name"] == "实盘"
    assert client.get(f"/api/kols?category_id={cid}", headers=headers).json()[0]["id"] == kid

    resp = client.put(f"/api/kols/{kid}", headers=headers, json={"category_id": None})
    assert resp.status_code == 200
    assert resp.json()["category_name"] is None

    assert client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "B", "external_id": "2", "category_id": 999}
    ).status_code == 400

    client.post(
        "/api/kols", headers=headers, json={"platform": "xueqiu", "name": "C", "external_id": "3", "category_id": cid}
    )
    assert client.delete(f"/api/categories/{cid}", headers=headers).status_code == 200
    kols = client.get("/api/kols", headers=headers).json()
    assert all(k["category_name"] is None for k in kols)


def test_auth_flow():
    client = make_client()
    # 注册用户默认不是管理员
    headers = user_headers(client, "admin01")
    me = client.get("/api/me", headers=headers).json()
    assert me["username"] == "admin01" and me["is_admin"] is False

    # 弱密码 / 重复用户名
    assert client.post("/api/auth/register", json={"username": "u2", "password": "123"}).status_code == 400
    assert client.post(
        "/api/auth/register", json={"username": "admin01", "password": "secret1234"}
    ).status_code == 400

    # 登录失败/成功
    assert client.post("/api/auth/login", json={"username": "admin01", "password": "wrong"}).status_code == 401
    token = client.post("/api/auth/login", json={"username": "admin01", "password": "pass123456"}).json()["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # 普通用户不能访问管理接口
    assert client.get("/api/kols", headers=headers).status_code == 403
    assert client.get("/api/posts", headers=headers).status_code == 403

    # 管理员在后台指定另一个用户为管理员
    admin_headers = auth_headers(client, "boss01", "secret1234")
    self_id = client.get("/api/me", headers=admin_headers).json()["id"]
    target_id = next(
        u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["id"] != self_id
    )
    resp = client.put(f"/api/users/{target_id}", headers=admin_headers, json={"is_admin": True})
    assert resp.status_code == 200 and resp.json()["is_admin"] is True
    # 不能取消自己的管理员权限
    assert client.put(f"/api/users/{self_id}", headers=admin_headers, json={"is_admin": False}).status_code == 400


def test_subscription_flow():
    client = make_client()
    admin_headers = auth_headers(client)
    kid = client.post(
        "/api/kols", headers=admin_headers, json={"platform": "xueqiu", "name": "超级鹿鼎公", "external_id": "8790885129"}
    ).json()["id"]

    customer_headers = user_headers(client, "customer")
    catalog = client.get("/api/catalog", headers=customer_headers).json()
    assert catalog[0]["subscribed"] is False

    assert client.post("/api/subscriptions", headers=customer_headers, json={"kol_id": kid}).status_code == 200
    catalog_item = client.get("/api/catalog", headers=customer_headers).json()[0]
    assert catalog_item["subscribed"] is True and catalog_item["subscribe_type"] == "post"
    assert client.get("/api/my/subscriptions", headers=customer_headers).json()[0]["id"] == kid

    # 订阅类型：切换到「回复」、再到「全部」
    assert (
        client.put(
            f"/api/subscriptions/{kid}",
            headers=customer_headers,
            json={"type": "reply"},
        ).status_code
        == 200
    )
    assert client.get("/api/catalog", headers=customer_headers).json()[0]["subscribe_type"] == "reply"
    assert (
        client.put(f"/api/subscriptions/{kid}", headers=customer_headers, json={"type": "both"}).status_code
        == 200
    )
    assert client.get("/api/my/subscriptions", headers=customer_headers).json()[0]["subscribe_type"] == "both"
    # 非法类型被拒绝
    assert (
        client.put(f"/api/subscriptions/{kid}", headers=customer_headers, json={"type": "bad"}).status_code
        == 400
    )
    # 未订阅的大V不能切类型
    other_kid = client.post(
        "/api/kols", headers=admin_headers, json={"platform": "xueqiu", "name": "另一大V", "external_id": "1"}
    ).json()["id"]
    assert (
        client.put(f"/api/subscriptions/{other_kid}", headers=customer_headers, json={"type": "both"}).status_code
        == 404
    )

    # 订阅后能看到该大V的动态
    client.app.state.db.insert_post("xueqiu", kid, "p1", "t", "c", "u", "")
    feed = client.get("/api/my/feed", headers=customer_headers).json()
    assert len(feed) == 1 and feed[0]["kol_name"] == "超级鹿鼎公"

    # images 存库为 JSON 文本，API 返回必须解析为数组（回归：网页时间线渲染崩溃）
    client.app.state.db.insert_post(
        "xueqiu", kid, "p2", "t2", "c2", "u2", "",
        images=["https://x.com/1.jpg", "https://x.com/2.jpg"],
    )
    feed = client.get("/api/my/feed", headers=customer_headers).json()
    p2 = next(p for p in feed if p["external_id"] == "p2")
    assert isinstance(p2["images"], list) and p2["images"] == [
        "https://x.com/1.jpg",
        "https://x.com/2.jpg",
    ]

    # 取消订阅后动态清空
    assert client.delete(f"/api/subscriptions/{kid}", headers=customer_headers).status_code == 200
    assert client.get("/api/my/feed", headers=customer_headers).json() == []

    # 大V详情与动态（未订阅也可查看）
    detail = client.get(f"/api/kols/{kid}", headers=customer_headers).json()
    assert detail["name"] == "超级鹿鼎公" and detail["subscribed"] is False
    posts = client.get(f"/api/kols/{kid}/posts", headers=customer_headers).json()
    assert len(posts) == 2
    p2_detail = next(p for p in posts if p["external_id"] == "p2")
    assert isinstance(p2_detail["images"], list) and p2_detail["images"] == [
        "https://x.com/1.jpg",
        "https://x.com/2.jpg",
    ]

    # 资料绑定
    resp = client.put(
        "/api/me",
        headers=customer_headers,
        json={"telegram_chat_id": "tg123", "feishu_open_id": "open456", "notify_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["telegram_chat_id"] == "tg123"


def test_feed_hide_images_is_user_scoped_and_keeps_stored_images():
    client = make_client()
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "图片大V", "feed-images")
    hidden = register(client, "feed_images_hidden").json()
    visible = register(client, "feed_images_visible").json()
    hidden_headers = {"Authorization": f"Bearer {hidden['token']}"}
    visible_headers = {"Authorization": f"Bearer {visible['token']}"}
    db.add_subscription(hidden["user"]["id"], kid)
    db.add_subscription(visible["user"]["id"], kid)
    db.set_subscription_hide_images(hidden["user"]["id"], kid, True)
    images = ["https://img.example.com/1.jpg", "https://img.example.com/2.jpg"]
    post_id = db.insert_post(
        "xueqiu", kid, "feed-images-post", "标题", "正文", "https://example.com/post", "",
        images=images,
    )

    hidden_post = client.get("/api/my/feed", headers=hidden_headers).json()[0]
    visible_post = client.get("/api/my/feed", headers=visible_headers).json()[0]

    assert hidden_post["images"] == []
    assert "hide_images" not in hidden_post
    assert visible_post["images"] == images
    assert json.loads(db.get_post(post_id)["images"]) == images


def test_kol_posts_hide_images_only_for_subscribed_user():
    client = make_client()
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "详情图片大V", "kol-post-images")
    hidden = register(client, "kol_images_hidden").json()
    visible = register(client, "kol_images_visible").json()
    unsubscribed = register(client, "kol_images_unsubscribed").json()
    hidden_headers = {"Authorization": f"Bearer {hidden['token']}"}
    visible_headers = {"Authorization": f"Bearer {visible['token']}"}
    unsubscribed_headers = {"Authorization": f"Bearer {unsubscribed['token']}"}
    db.add_subscription(hidden["user"]["id"], kid)
    db.add_subscription(visible["user"]["id"], kid)
    db.set_subscription_hide_images(hidden["user"]["id"], kid, True)
    images = ["https://img.example.com/detail.jpg"]
    db.insert_post(
        "xueqiu", kid, "kol-images-post", "标题", "正文", "https://example.com/post", "",
        images=images,
    )

    hidden_posts = client.get(f"/api/kols/{kid}/posts", headers=hidden_headers).json()
    visible_posts = client.get(f"/api/kols/{kid}/posts", headers=visible_headers).json()
    unsubscribed_posts = client.get(
        f"/api/kols/{kid}/posts", headers=unsubscribed_headers
    ).json()

    assert hidden_posts[0]["images"] == []
    assert visible_posts[0]["images"] == images
    assert unsubscribed_posts[0]["images"] == images


def test_my_feed_filters_and_pagination():
    """/api/my/feed 的 offset 分页与 platform/category/q/favorite 筛选（动态页加载更多/筛选）。"""
    client = make_client()
    db = client.app.state.db

    cid = db.add_category("财经")
    kid1 = db.add_kol("xueqiu", "雪球大V", "x1", category_id=cid)
    kid2 = db.add_kol("weibo", "微博大V", "w1", category_id=None)
    kid3 = db.add_kol("twitter", "X大V", "t1", category_id=None)

    reg = register(client, "feeduser")
    headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    uid = reg.json()["user"]["id"]
    for kid in (kid1, kid2, kid3):
        db.add_subscription(uid, kid, type="post")
    # 特别关注 kid1 所属的雪球大V
    db.set_subscription_favorite(uid, kid1, True)

    db.insert_post("xueqiu", kid1, "p1", "茅台财报", "茅台大涨了", "u1", "2026-08-07 10:00")
    db.insert_post("xueqiu", kid1, "p2", "降息预期", "降息落地", "u2", "2026-08-07 11:00")
    db.insert_post("weibo", kid2, "p3", "午后闲聊", "随便说说", "u3", "2026-08-07 12:00")
    db.insert_post("twitter", kid3, "p4", "ETF 观点", "ETF 资金流向", "u4", "2026-08-07 13:00")

    # 默认请求：不带新参数，行为与旧版一致（全部帖子、无分页叠加）
    feed = client.get("/api/my/feed", headers=headers).json()
    assert [p["external_id"] for p in feed] == ["p4", "p3", "p2", "p1"]

    # offset 分页：两页无重叠，且按 id 倒序
    page1 = client.get("/api/my/feed?limit=2&offset=0", headers=headers).json()
    page2 = client.get("/api/my/feed?limit=2&offset=2", headers=headers).json()
    ids1 = [p["id"] for p in page1]
    ids2 = [p["id"] for p in page2]
    assert ids1[0] > ids1[1] > ids2[0] > ids2[1]
    assert set(ids1).isdisjoint(set(ids2))

    # platform 筛选
    weibo = client.get("/api/my/feed?platform=weibo", headers=headers).json()
    assert [p["external_id"] for p in weibo] == ["p3"]
    assert client.get("/api/my/feed?platform=xueqiu", headers=headers).json()

    # category 筛选（kols.category_id 关联）
    cat_feed = client.get(f"/api/my/feed?category_id={cid}", headers=headers).json()
    assert [p["external_id"] for p in cat_feed] == ["p2", "p1"]

    # 关键词搜索（title/content LIKE）
    q_feed = client.get("/api/my/feed?q=茅台", headers=headers).json()
    assert [p["external_id"] for p in q_feed] == ["p1"]
    assert client.get("/api/my/feed?q=不存在", headers=headers).json() == []

    # favorite 筛选：只返回特别关注大V的动态
    fav_feed = client.get("/api/my/feed?favorite=1", headers=headers).json()
    assert [p["external_id"] for p in fav_feed] == ["p2", "p1"]

    # 组合筛选：favorite + platform + q 同时生效
    combined = client.get("/api/my/feed?favorite=1&platform=xueqiu&q=茅台", headers=headers).json()
    assert [p["external_id"] for p in combined] == ["p1"]

    # since_id：只返回比指定 id 新、且 48h 内发布的帖（X 式新帖检测/计数）；
    # 回灌旧帖 id 虽大也不算新帖
    id_p3 = next(p["id"] for p in feed if p["external_id"] == "p3")
    new_feed = client.get(f"/api/my/feed?since_id={id_p3}", headers=headers).json()
    assert new_feed == []
    from datetime import datetime, timedelta

    from app.fetchers.base import CN_TZ

    fresh_at = (datetime.now(CN_TZ) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    db.insert_post("twitter", kid3, "p5", "刚发的新帖", "新内容", "u5", fresh_at)
    new_feed = client.get(f"/api/my/feed?since_id={id_p3}", headers=headers).json()
    assert [p["external_id"] for p in new_feed] == ["p5"]
    # since_id 与筛选叠加
    new_xq = client.get(f"/api/my/feed?since_id={id_p3}&platform=xueqiu", headers=headers).json()
    assert new_xq == []

    # /api/categories 登录用户可读（供动态页分类下拉），无需管理员
    cats = client.get("/api/categories", headers=headers).json()
    assert any(c["id"] == cid and c["name"] == "财经" for c in cats)


def test_my_feed_hides_secondary_by_default():
    """/api/my/feed 默认隐藏次要大V动态；include_secondary=1 时显示；特别关注穿透。"""
    client = make_client()
    db = client.app.state.db

    normal_kid = db.add_kol("xueqiu", "普通大V", "n1")
    secondary_kid = db.add_kol("xueqiu", "次要大V", "s1", secondary=True)
    fav_secondary_kid = db.add_kol("xueqiu", "特别关注次要", "s2", secondary=True)

    reg = register(client, "secuser")
    headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    uid = reg.json()["user"]["id"]
    for kid in (normal_kid, secondary_kid, fav_secondary_kid):
        db.add_subscription(uid, kid, type="post")
    # 特别关注一个次要大V（favorite 穿透应始终显示）
    db.set_subscription_favorite(uid, fav_secondary_kid, True)
    # 另一个次要大V标个人次要（个人 secondary 同样默认隐藏）
    db.set_subscription_secondary(uid, secondary_kid, True)

    db.insert_post("xueqiu", normal_kid, "p1", "普通", "普通大V的帖子", "u1", "")
    db.insert_post("xueqiu", secondary_kid, "p2", "次要", "次要大V的帖子", "u2", "")
    db.insert_post("xueqiu", fav_secondary_kid, "p3", "关注", "特别关注的次要大V", "u3", "")

    # 默认：次要大V被隐藏，特别关注穿透可见
    feed = client.get("/api/my/feed", headers=headers).json()
    assert [p["external_id"] for p in feed] == ["p3", "p1"]

    # 开启 include_secondary：全部可见
    feed = client.get("/api/my/feed?include_secondary=1", headers=headers).json()
    assert [p["external_id"] for p in feed] == ["p3", "p2", "p1"]

    # 管理员视角同样默认隐藏（favorite 穿透是按用户的，管理员未标关注 → 全部次要隐藏）
    admin_reg = register(client, "secadmin")
    db.update_user(admin_reg.json()["user"]["id"], is_admin=True)
    admin_uid = admin_reg.json()["user"]["id"]
    for kid in (normal_kid, secondary_kid, fav_secondary_kid):
        db.add_subscription(admin_uid, kid, type="post")
    admin_headers = {"Authorization": f"Bearer {admin_reg.json()['token']}"}
    admin_feed = client.get("/api/my/feed", headers=admin_headers).json()
    assert [p["external_id"] for p in admin_feed] == ["p1"]


def test_my_feed_hides_zsxq_unless_filtered():
    """星球默认次要：不混入全部；角标或特别关注才看见。"""
    client = make_client()
    db = client.app.state.db
    xq = db.add_kol("xueqiu", "雪球大V", "x1")
    zq = db.add_kol("zsxq", "前沿信息收录", "28888112822211")
    kol = db.get_kol(zq)
    assert kol["secondary"] == 1 and kol["silent"] == 1
    reg = register(client, "zsxqfeed")
    headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    uid = reg.json()["user"]["id"]
    db.add_subscription(uid, xq, type="post")
    db.add_subscription(uid, zq, type="post")
    db.insert_post("xueqiu", xq, "xq1", "雪球帖", "正文", "u1", "2026-01-01 09:00")
    db.insert_post("zsxq", zq, "zq1", "#调研纪要#", "附件", "u2", "2026-01-01 09:00")

    feed = client.get("/api/my/feed", headers=headers).json()
    assert [p["external_id"] for p in feed] == ["xq1"]
    zsxq = client.get("/api/my/feed?platform=zsxq", headers=headers).json()
    assert [p["external_id"] for p in zsxq] == ["zq1"]
    db.set_subscription_favorite(uid, zq, True)
    feed = client.get("/api/my/feed", headers=headers).json()
    assert [p["external_id"] for p in feed] == ["zq1", "xq1"]
    daily = db.list_daily_posts([xq, zq], "2000-01-01 00:00", 15)
    assert [p["external_id"] for p in daily] == ["xq1"]


def test_admin_zsxq_cookie_and_add_from_link():
    client = make_client()
    headers = auth_headers(client)
    assert client.get("/api/admin/zsxq-cookie", headers=headers).json()["set"] is False
    assert (
        client.post(
            "/api/admin/zsxq-cookie",
            headers=headers,
            json={"cookie": "zsxq_access_token=abc123"},
        ).status_code
        == 200
    )
    cookie = client.get("/api/admin/zsxq-cookie", headers=headers).json()
    assert cookie["set"] is True
    assert client.get("/api/stats", headers=headers).json()["zsxq_cookie"]["set"] is True
    cid = client.app.state.db.add_category("知识星球")
    resp = client.post(
        "/api/kols",
        headers=headers,
        json={
            "platform": "zsxq",
            "name": "前沿",
            "external_id": "https://wx.zsxq.com/dweb2/index/group/28888112822211",
            "category_id": cid,
        },
    )
    assert resp.status_code == 200
    kol = resp.json()
    assert kol["external_id"] == "28888112822211"
    assert kol["secondary"] == 1 and kol["silent"] == 1


def test_wechat_login(monkeypatch):
    cfg = Config()
    cfg.wechat.app_id = "wx_app"
    cfg.wechat.app_secret = "wx_secret"
    tmp = tempfile.mkdtemp()
    app = create_app(config=cfg, db_path=Path(tmp) / "wx.db")
    client = TestClient(app)

    # 未配置时返回明确错误
    app2 = create_app(db_path=Path(tmp) / "wx2.db")
    assert (
        TestClient(app2).post("/api/auth/wechat", json={"code": "c"}).status_code == 400
    )

    monkeypatch.setattr(
        "app.wechat.code2session",
        lambda code, app_id, app_secret: {"openid": "openid_abc", "session_key": "k"},
    )
    resp = client.post("/api/auth/wechat", json={"code": "c1"})
    assert resp.status_code == 400
    assert "邀请码" in resp.json()["detail"]

    client.app.state.db.add_register_code("WXINVITE")
    resp = client.post("/api/auth/wechat", json={"code": "c1", "invite_code": "WXINVITE"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["username"].startswith("wx_")
    assert data["user"]["is_admin"] is False  # 小程序用户不会自动成为管理员

    # 再次登录返回同一用户，不必再带邀请码
    resp2 = client.post("/api/auth/wechat", json={"code": "c2"})
    assert resp2.json()["user"]["id"] == data["user"]["id"]
    assert client.app.state.db.get_user(data["user"]["id"])["last_login_at"]


def test_wechat_login_respects_allow_register(monkeypatch):
    cfg = Config()
    cfg.wechat.app_id = "wx_app"
    cfg.wechat.app_secret = "wx_secret"
    cfg.web.allow_register = False
    tmp = tempfile.mkdtemp()
    app = create_app(config=cfg, db_path=Path(tmp) / "wx-closed.db")
    client = TestClient(app)
    monkeypatch.setattr(
        "app.wechat.code2session",
        lambda code, app_id, app_secret: {"openid": "openid_closed", "session_key": "k"},
    )
    client.app.state.db.add_register_code("WXCLOSED")
    resp = client.post("/api/auth/wechat", json={"code": "c1", "invite_code": "WXCLOSED"})
    assert resp.status_code == 403
    assert "暂未开放注册" in resp.json()["detail"]


def test_add_combination_kol_auto_fills_name(monkeypatch):
    client = make_client()
    admin_headers = auth_headers(client)
    monkeypatch.setattr(
        "app.api.resolve_combination_profile",
        lambda symbol, cookie="", db=None: {
            "name": "伯言-A股",
            "owner_name": "伯言2020",
            "avatar_url": "",
        },
    )
    resp = client.post(
        "/api/kols",
        headers=admin_headers,
        json={
            "platform": "combination",
            "name": "",
            "external_id": "https://xueqiu.com/P/ZH3623878",
        },
    )
    assert resp.status_code == 200, resp.text
    kol = resp.json()
    assert kol["external_id"] == "ZH3623878"
    assert kol["name"] == "伯言-A股"
    assert kol["platform"] == "combination"


def test_old_db_migrates_category_column():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE kols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            name TEXT NOT NULL,
            external_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            kol_id INTEGER NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (platform, external_id)
        );
        CREATE TABLE push_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO kols (platform, name, external_id) VALUES ('xueqiu', '旧大V', '1')")
    conn.commit()
    conn.close()

    db = DB(path)
    cid = db.add_category("宏观")
    db.update_kol(1, category_id=cid)
    kol = db.get_kol(1)
    assert kol is not None and kol["category_name"] == "宏观"
    uid = db.add_user("admin", "hash", is_admin=True)
    db.add_subscription(uid, 1)
    assert db.subscribers_of_kol(1) == []
    db.close()


def test_old_db_migrates_wecom_column():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            wechat_openid TEXT NOT NULL DEFAULT '',
            telegram_chat_id TEXT NOT NULL DEFAULT '',
            feishu_open_id TEXT NOT NULL DEFAULT '',
            feishu_chat_id TEXT NOT NULL DEFAULT '',
            notify_enabled INTEGER NOT NULL DEFAULT 1,
            daily_report INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()

    db = DB(path)
    uid = db.add_user("wc", "hash")
    db.update_user(uid, wecom_webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wc1")
    wc = db.get_user_by_wecom_webhook("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wc1")
    assert wc is not None and wc["id"] == uid
    db.update_user(uid, telegram_bot_token="123:custom")
    tg = db.get_user_by_telegram_bot("123:custom")
    assert tg is not None and tg["id"] == uid
    db.close()


def test_healthz():
    client = make_client("api3.db")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_healthz_ok_when_ima_storage_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DAV_UI_ONLY", "1")
    archive_root = tmp_path / "archive"
    status_path = tmp_path / "missing-status.json"
    monkeypatch.setenv("IMA_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("IMA_STORAGE_STATUS_PATH", str(status_path))
    client = TestClient(create_app(db_path=tmp_path / "healthz-storage.sqlite"))
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/healthz/ima-storage")
    assert resp.status_code == 503
    payload = resp.json()
    assert set(payload) == {"status", "available"}
    assert payload["available"] is False
    assert "used_percent" not in payload
    assert "path" not in payload


def test_healthz_ima_storage_available(tmp_path, monkeypatch):
    import json
    import time

    monkeypatch.setenv("DAV_UI_ONLY", "1")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / ".vpush-ima-root").touch()
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "checked_at": int(time.time()),
                "available": True,
                "writable": True,
                "used_percent": 12,
                "inode_percent": 3,
                "monthly_tx_bytes": 99,
                "capacity_blocked": False,
                "reason": "",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IMA_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("IMA_STORAGE_STATUS_PATH", str(status_path))
    client = TestClient(create_app(db_path=tmp_path / "healthz-ok.sqlite"))
    resp = client.get("/healthz/ima-storage")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "available"
    assert payload["available"] is True
    assert "writable" not in payload
    assert "used_percent" not in payload


def test_update_kol_duplicate_external_id_rejected():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "A", "111")
    db.add_kol("xueqiu", "B", "222")
    resp = client.put(
        f"/api/kols/{kid}",
        headers=headers,
        json={"external_id": "222"},
    )
    assert resp.status_code == 400 and "相同的外部ID" in resp.json()["detail"]
    # 改成不冲突的 ID 允许
    resp = client.put(
        f"/api/kols/{kid}",
        headers=headers,
        json={"external_id": "333"},
    )
    assert resp.status_code == 200


def test_approve_request_auto_resolves_name_and_avatar(monkeypatch):
    client = make_client()
    admin_headers = auth_headers(client)
    u1 = register(client, "user01", "pass123456")
    u_headers = {"Authorization": f"Bearer {u1.json()['token']}"}
    cid = client.app.state.db.add_category("宏观")
    client.post(
        "/api/kol-requests",
        headers=u_headers,
        json={"platform": "xueqiu", "external_id": "https://xueqiu.com/u/55555", "category_id": cid},
    )
    monkeypatch.setattr(
        "app.kol_requests.resolve_profile",
        lambda uid, cookie="", db=None: {"screen_name": "自动昵称", "avatar_url": ""},
    )
    req_id = client.get("/api/admin/kol-requests?status=pending", headers=admin_headers).json()[0]["id"]
    approved = client.post(f"/api/admin/kol-requests/{req_id}/approve", headers=admin_headers)
    assert approved.status_code == 200
    assert approved.json()["name"] == "自动昵称"
    assert approved.json()["external_id"] == "55555"


def test_batch_import_xueqiu_fetches_avatar_even_with_nickname(monkeypatch):
    client = make_client()
    headers = auth_headers(client)
    monkeypatch.setattr(
        "app.api.resolve_profile",
        lambda uid, cookie="", db=None: {
            "screen_name": "自动昵称",
            "avatar_url": "https://xueqiu.com/avatar.png",
        },
    )
    monkeypatch.setattr(
        "app.api.cache_avatar",
        lambda db, kid, url: url,
    )
    resp = client.post(
        "/api/kols/batch",
        headers=headers,
        json={"platform": "xueqiu", "lines": "段永平 https://xueqiu.com/u/12345"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] == 1
    kols = client.get("/api/kols", headers=headers).json()
    target = next(k for k in kols if k["platform"] == "xueqiu")
    assert target["name"] == "段永平"
    assert target["avatar_url"] == "https://xueqiu.com/avatar.png"


def test_stats_returns_source_stability_fields():
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "A", "1")
    db.insert_post("xueqiu", kid, "p1", "t", "c", "u", "")
    # 一轮成功抓取 5 个大V + 一轮失败 1 个大V → 成功率按「尝试次数」= 5/6
    db.add_source_event("xueqiu", "ok", "ok=5 fail=0", ok_count=5)
    db.add_source_event("xueqiu", "fail", "fail=1 ok=0 err=boom", fail_count=1)
    db.set_setting("stats_retry_pending", "2")

    data = client.get("/api/stats", headers=headers).json()
    xq = next(s for s in data["sources"] if s["platform"] == "xueqiu")
    assert xq["ok_24h"] == 5 and xq["fail_24h"] == 1
    assert xq["success_rate_24h"] == 83
    assert len(data["recent_source_events"]) == 2
    assert data["retry_pending"] == 2
    assert any(h["id"] == kid and h["last_post_at"] for h in data["kol_health"])
    health = next(h for h in data["kol_health"] if h["id"] == kid)
    assert health["subscriber_count"] == 0
    assert data["pending_kol_requests"] == 0
    assert "push_alert_last_at" in data["alerts"]


def test_login_rate_limit_blocks_after_8_failures():
    client = make_client()
    user_headers(client, "victim")
    for _ in range(8):
        assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 429


def test_rate_limit_cleanup_removes_expired_entries():
    """限流字典必须按时间清理过期条目，而不是只删空列表（过期时间戳列表本身非空）。"""
    from app.api import _prune_window_dict

    now = 100000.0
    # a 的全部失败记录已过期（列表非空），b 仍在窗口内
    attempts = {"a": [now - 4000, now - 3900], "b": [now - 10]}
    _prune_window_dict(attempts, window=3600, now=now, max_entries=1000)
    assert "a" not in attempts, "过期记录（即使列表非空）应被清理"
    assert attempts["b"] == [now - 10]

    # 超上限时删除最旧条目而不是无限增长
    entries = {f"u{i}": [now - i] for i in range(10)}
    _prune_window_dict(entries, window=3600, now=now, max_entries=5)
    assert len(entries) == 5
    # u0 最新、u9 最旧；只保留最近 5 条（u0~u4），删掉最旧的 u5~u9
    assert set(entries) == {f"u{i}" for i in range(5)}

    # 空字典/全空列表兼容
    _prune_window_dict({}, window=3600, now=now, max_entries=1000)


def test_xff_spoof_cannot_bypass_rate_limit_without_trust_proxy():
    """未显式信任反代时，伪造 X-Forwarded-For 不能绕过限流。"""
    client = make_client()
    user_headers(client, "victim")
    for i in range(8):
        r = client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": f"1.1.1.{i}"},
        )
        assert r.status_code == 401
    r = client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "bad"},
        headers={"X-Forwarded-For": "9.9.9.9"},
    )
    assert r.status_code == 429


def test_xff_trusted_when_trust_proxy_enabled():
    """显式信任反代后，X-Forwarded-For 才作为分桶依据。"""
    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    user_headers(client, "victim")
    for _ in range(8):
        assert client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": "1.1.1.1"},
        ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "bad"},
        headers={"X-Forwarded-For": "1.1.1.1"},
    ).status_code == 429
    # 不同伪造 IP 是不同桶，不受影响
    assert client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "bad"},
        headers={"X-Forwarded-For": "2.2.2.2"},
    ).status_code == 401


def test_register_failures_count_toward_limit():
    client = make_client()
    for _ in range(8):
        r = client.post(
            "/api/auth/register",
            json={"username": f"u{_}", "password": "secret1234", "code": "INVALID"},
        )
        assert r.status_code == 400
    r = client.post(
        "/api/auth/register",
        json={"username": "u9", "password": "secret1234", "code": "INVALID"},
    )
    assert r.status_code == 429


def test_login_success_clears_failure_count():
    client = make_client()
    user_headers(client, "victim")
    for _ in range(7):
        assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 401
    # 尚未锁定时输入正确密码：登录成功并清零
    assert client.post("/api/auth/login", json={"username": "victim", "password": "pass123456"}).status_code == 200
    # 清零后连续错误仍是 401；若未清零，第 8/9 次错误会是 429
    assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 401


def test_login_locked_ip_rejects_even_correct_password():
    client = make_client()
    user_headers(client, "victim")
    for _ in range(8):
        assert client.post("/api/auth/login", json={"username": "victim", "password": "bad"}).status_code == 401
    # 锁定时即使密码正确也 429，不泄露密码有效性
    assert client.post("/api/auth/login", json={"username": "victim", "password": "pass123456"}).status_code == 429


def test_admin_delete_user_cleans_push_logs_and_acl():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    reg = register(client, "doomed")
    uid = reg.json()["user"]["id"]
    db = client.app.state.db
    kid = client.post(
        "/api/kols", headers=admin_headers,
        json={"platform": "xueqiu", "name": "A", "external_id": "1"},
    ).json()["id"]
    # 直接造数据：私有大V ACL + 一条推送日志
    db.set_kol_acl(kid, [uid])
    post_id = db.insert_post("xueqiu", kid, "p1", "t", "c", "u", "2026-08-06")
    db.add_push_log(post_id, "telegram", "success", user_id=uid)

    assert client.delete(f"/api/users/{uid}", headers=admin_headers).status_code == 200
    assert db.list_push_logs(user_id=uid) == []
    assert db.acl_user_ids(kid) == []


def test_db_busy_timeout_set():
    client = make_client()
    value = client.app.state.db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert value == 5000


def test_img_proxy_fetches_image(monkeypatch):
    """图片代理只允许应用实际使用的受信图床。"""
    import httpx as _httpx

    fake_resp = _httpx.Response(200, content=b"\xff\xd8\xfffakejpeg", headers={"content-type": "image/jpeg"})

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    resp = client.get("/api/img-proxy", params={"url": "https://pbs.twimg.com/x.jpg"})
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xfffakejpeg"
    assert resp.headers["content-type"] == "image/jpeg"


def test_img_proxy_rejects_non_image(monkeypatch):
    """图片代理：非图片内容（如 HTML）拒绝返回。"""
    import httpx as _httpx

    fake_resp = _httpx.Response(200, content=b"<html>not an image</html>", headers={"content-type": "text/html"})

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    resp = client.get("/api/img-proxy", params={"url": "https://pbs.twimg.com/page"})
    assert resp.status_code == 400


def test_img_proxy_streams_video_range(monkeypatch):
    """视频代理透传 Range，返回 206 + Content-Range，供 <video> 拖进度。"""
    import httpx as _httpx

    seen = {}
    body = b"V" * 1024
    fake_resp = _httpx.Response(
        206,
        content=body,
        headers={
            "content-type": "video/mp4",
            "content-range": "bytes 0-1023/34590354",
            "content-length": "1024",
        },
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            seen["headers"] = kwargs.get("headers") or {}
            seen["url"] = url

            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    video = "https://static-assets-1.truthsocial.com/media/clip.mp4"
    resp = client.get(
        "/api/img-proxy",
        params={"url": video},
        headers={"Range": "bytes=0-1023"},
    )
    assert resp.status_code == 206
    assert resp.content == body
    assert resp.headers["content-type"].startswith("video/mp4")
    assert resp.headers["content-range"] == "bytes 0-1023/34590354"
    assert resp.headers["accept-ranges"] == "bytes"
    assert seen["headers"].get("Range") == "bytes=0-1023"


def test_img_proxy_rejects_html_masquerading_as_mp4(monkeypatch):
    import httpx as _httpx

    fake_resp = _httpx.Response(
        200, content=b"<html>nope</html>", headers={"content-type": "text/html"}
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    resp = client.get(
        "/api/img-proxy",
        params={"url": "https://static-assets-1.truthsocial.com/x.mp4"},
    )
    assert resp.status_code == 400


def test_recommend_weight_orders_recommendations():
    """推荐位排序：recommend_weight 优先于订阅人数。"""
    client = make_client()
    headers = auth_headers(client)
    db = client.app.state.db
    low = db.add_kol("xueqiu", "多订阅", "low1")
    high = db.add_kol("xueqiu", "特朗普", "trump1")
    # 多订阅者的大V在纯订阅数排序下必胜；只给权重低票
    db.add_subscription(db.list_users()[0]["id"], low)
    resp = client.put(f"/api/kols/{high}", headers=headers, json={"recommend_weight": 100})
    assert resp.status_code == 200
    assert resp.json()["recommend_weight"] == 100
    rec = client.get("/api/recommendations", headers=headers).json()
    ids = [r["id"] for r in rec]
    assert ids[:2] == [high, low]
    # 权重清零后回到订阅数排序
    client.put(f"/api/kols/{high}", headers=headers, json={"recommend_weight": 0})
    rec = client.get("/api/recommendations", headers=headers).json()
    assert [r["id"] for r in rec][:2] == [low, high]
    negative = client.put(f"/api/kols/{high}", headers=headers, json={"recommend_weight": -5})
    assert negative.json()["recommend_weight"] == 0


def test_admin_imgbed_settings_roundtrip(monkeypatch):
    from app import imgbed as imgbed_mod

    probes = {"url": ""}

    def fake_probe(url):
        probes["url"] = url
        return True, ""

    monkeypatch.setattr(imgbed_mod, "probe", fake_probe)
    client = make_client()
    headers = auth_headers(client)
    empty = client.get("/api/admin/imgbed", headers=headers).json()
    assert empty["enabled"] is False
    assert empty["project"] == "CloudFlare-ImgBed"
    assert empty["token_set"] is False
    assert empty["retention_days"] == 30
    bad = client.put(
        "/api/admin/imgbed",
        headers=headers,
        json={"base_url": "http://img.example.com", "token": "imgbed_test"},
    )
    assert bad.status_code == 400
    saved = client.put(
        "/api/admin/imgbed",
        headers=headers,
        json={
            "base_url": "https://img.053727.xyz/",
            "token": "imgbed_testtoken",
            "channel_name": "vpush-imgbed",
            "folder": "vpush",
            "retention_days": 7,
        },
    )
    assert saved.status_code == 200
    data = saved.json()
    assert data["enabled"] is True
    assert data["base_url"] == "https://img.053727.xyz"
    assert data["token_set"] is True
    assert data["last_check_error"] == ""
    assert data["retention_days"] == 7
    assert probes["url"] == "https://img.053727.xyz"
    assert "token" not in data
    keep = client.put(
        "/api/admin/imgbed",
        headers=headers,
        json={"base_url": "https://img.053727.xyz", "token": ""},
    )
    assert keep.status_code == 200
    assert keep.json()["retention_days"] == 7
    bad_days = client.put(
        "/api/admin/imgbed",
        headers=headers,
        json={"base_url": "https://img.053727.xyz", "token": "", "retention_days": 3651},
    )
    assert bad_days.status_code == 400

    def failing_probe(url):
        return False, "ConnectError: down"

    monkeypatch.setattr(imgbed_mod, "probe", failing_probe)
    checked = client.put(
        "/api/admin/imgbed",
        headers=headers,
        json={"base_url": "https://img.053727.xyz", "token": ""},
    ).json()
    assert checked["last_check_error"] == "ConnectError: down"
    stats = client.get("/api/stats", headers=headers).json()
    assert stats["imgbed"]["enabled"] is True
    assert stats["imgbed"]["token_set"] is True
    assert stats["imgbed"]["last_check_error"] == "ConnectError: down"

    cleared = client.delete("/api/admin/imgbed", headers=headers)
    assert cleared.status_code == 200
    after = cleared.json()
    assert after["enabled"] is False
    assert after["base_url"] == ""
    assert after["token_set"] is False
    assert after["last_check_error"] == ""
    assert after["retention_days"] == 7
    assert client.get("/api/stats", headers=headers).json()["imgbed"]["enabled"] is False


def test_img_proxy_rejects_unlisted_public_host():
    client = make_client()
    assert client.get(
        "/api/img-proxy", params={"url": "https://example-cdn.com/x.jpg"}
    ).status_code == 400


def test_img_proxy_stops_reading_after_limit(monkeypatch):
    import httpx as _httpx

    class FakeResp:
        status_code = 200

        def __init__(self):
            self.headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"x" * (6 * 1024 * 1024)
            yield b"y" * (6 * 1024 * 1024)
            raise AssertionError("超过上限后不应继续读取")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return FakeResp()

                def __exit__(self, *args):
                    return False

            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    assert client.get(
        "/api/img-proxy", params={"url": "https://pbs.twimg.com/large.jpg"}
    ).status_code == 400


def test_img_proxy_rejects_unsafe_url(monkeypatch):
    """图片代理：内网地址（SSRF）拒绝转发。"""
    client = make_client()
    resp = client.get("/api/img-proxy", params={"url": "http://192.168.1.1/x.jpg"})
    assert resp.status_code == 400


def test_img_proxy_whitelisted_host_bypasses_dns_hijack(monkeypatch):
    """白名单图床（pbs.twimg.com）：DNS 被劫持到透明代理网段时仍能转发。"""
    import httpx as _httpx

    fake_resp = _httpx.Response(200, content=b"\xff\xd8\xffok", headers={"content-type": "image/jpeg"})

    class FakeClient:
        def __init__(self, *a, **k):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            fake_resp.iter_bytes = lambda: iter([fake_resp.content])
            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    resp = client.get("/api/img-proxy", params={"url": "https://pbs.twimg.com/media/x.jpg"})
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xffok"


def test_img_proxy_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr("app.url_safety._resolve_host_ips", lambda host: ["192.168.1.8"])
    client = make_client()
    assert client.get(
        "/api/img-proxy", params={"url": "https://pbs.twimg.com/media/x.jpg"}
    ).status_code == 400


def test_img_proxy_allows_transparent_proxy_range(monkeypatch):
    import httpx as _httpx

    fake_resp = _httpx.Response(200, content=b"\xff\xd8\xffok", headers={"content-type": "image/jpeg"})

    class FakeClient:
        def __init__(self, *a, **k):
            self.headers = {}

        def stream(self, method, url, **kwargs):
            class Stream:
                def __enter__(self):
                    return fake_resp

                def __exit__(self, *args):
                    return False

            fake_resp.iter_bytes = lambda: iter([fake_resp.content])
            return Stream()

        def close(self):
            pass

    monkeypatch.setattr("app.url_safety._resolve_host_ips", lambda host: ["198.18.0.1"])
    monkeypatch.setattr(_httpx, "Client", FakeClient)
    client = make_client()
    resp = client.get("/api/img-proxy", params={"url": "https://pbs.twimg.com/media/x.jpg"})
    assert resp.status_code == 200


def test_img_proxy_rate_limit_per_ip(monkeypatch):
    """匿名 img-proxy 按 IP 限速，避免公网刷带宽。"""
    monkeypatch.setattr("app.api.IMAGE_PROXY_MAX_PER_WINDOW", 3)
    client = make_client()
    params = {"url": "https://example-cdn.com/x.jpg"}
    for _ in range(3):
        assert client.get("/api/img-proxy", params=params).status_code == 400
    blocked = client.get("/api/img-proxy", params=params)
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after")
    assert "频繁" in blocked.json()["detail"]


def test_img_proxy_xff_cannot_bypass_rate_limit(monkeypatch):
    """未信任反代时，轮换 X-Forwarded-For 不能绕过 img-proxy 限速。"""
    monkeypatch.setattr("app.api.IMAGE_PROXY_MAX_PER_WINDOW", 3)
    client = make_client()
    params = {"url": "https://example-cdn.com/x.jpg"}
    for i in range(3):
        assert client.get(
            "/api/img-proxy",
            params=params,
            headers={"X-Forwarded-For": f"1.1.1.{i}"},
        ).status_code == 400
    assert client.get(
        "/api/img-proxy",
        params=params,
        headers={"X-Forwarded-For": "9.9.9.9"},
    ).status_code == 429


def test_img_proxy_rate_limit_buckets_trusted_xff(monkeypatch):
    """信任反代后，不同 X-Forwarded-For 分桶，互不影响。"""
    monkeypatch.setattr("app.api.IMAGE_PROXY_MAX_PER_WINDOW", 2)
    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    params = {"url": "https://example-cdn.com/x.jpg"}
    for _ in range(2):
        assert client.get(
            "/api/img-proxy",
            params=params,
            headers={"X-Forwarded-For": "1.1.1.1"},
        ).status_code == 400
    assert client.get(
        "/api/img-proxy",
        params=params,
        headers={"X-Forwarded-For": "1.1.1.1"},
    ).status_code == 429
    assert client.get(
        "/api/img-proxy",
        params=params,
        headers={"X-Forwarded-For": "2.2.2.2"},
    ).status_code == 400


def test_me_subscription_count():
    client = make_client()
    admin_headers = auth_headers(client)
    kid1 = client.post(
        "/api/kols", headers=admin_headers,
        json={"platform": "xueqiu", "name": "A", "external_id": "1"},
    ).json()["id"]
    kid2 = client.post(
        "/api/kols", headers=admin_headers,
        json={"platform": "xueqiu", "name": "B", "external_id": "2"},
    ).json()["id"]
    uh = user_headers(client, "user01")
    assert client.get("/api/me", headers=uh).json()["subscription_count"] == 0
    client.post("/api/subscriptions", headers=uh, json={"kol_id": kid1})
    client.post("/api/subscriptions", headers=uh, json={"kol_id": kid2})
    assert client.get("/api/me", headers=uh).json()["subscription_count"] == 2


def test_background_workers_flag(monkeypatch):
    """DAV_UI_ONLY=1 时关闭调度器/机器人后台任务，避免测试实例抢生产
    Telegram 机器人（getUpdates 409）或误发降级告警。"""
    from app.main import background_workers_enabled

    monkeypatch.delenv("DAV_UI_ONLY", raising=False)
    assert background_workers_enabled() is True
    monkeypatch.setenv("DAV_UI_ONLY", "1")
    assert background_workers_enabled() is False
    monkeypatch.setenv("DAV_UI_ONLY", "0")
    assert background_workers_enabled() is True


def test_ima_page_warmup_flag(monkeypatch):
    from app.main import ima_page_warmup_enabled

    monkeypatch.delenv("IMA_PAGE_WARMUP", raising=False)
    assert ima_page_warmup_enabled() is True
    monkeypatch.setenv("IMA_PAGE_WARMUP", "0")
    assert ima_page_warmup_enabled() is False
    monkeypatch.setenv("IMA_PAGE_WARMUP", "1")
    assert ima_page_warmup_enabled() is True


def test_start_ima_page_warmup_respects_flag(monkeypatch):
    from app import main as main_module

    created = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name
            self.start_count = 0
            created.append(self)

        def start(self):
            self.start_count += 1

    db = SimpleNamespace(warm_ima_document_page=lambda: None)
    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)

    monkeypatch.setenv("IMA_PAGE_WARMUP", "0")
    assert main_module.start_ima_page_warmup(db) is None
    assert created == []

    monkeypatch.setenv("IMA_PAGE_WARMUP", "1")
    thread = main_module.start_ima_page_warmup(db)
    assert created == [thread]
    assert thread.target is db.warm_ima_document_page
    assert thread.daemon is True
    assert thread.name == "ima-page-warmup"
    assert thread.start_count == 1


def test_passwordless_user_can_set_first_password():
    """微信/机器人自动创建的无密码账号：已持有会话即可首次设密，之后改密需旧密码。"""
    from app import auth

    client = make_client()
    db = client.app.state.db
    uid = db.add_user("wx_pwd_user", "", wechat_openid="openid_1")
    token = auth.create_token(uid, "wx_pwd_user", db.get_setting("token_secret"))
    headers = {"Authorization": f"Bearer {token}"}

    # 无密码账号无需旧密码即可首次设密
    resp = client.post(
        "/api/me/password",
        headers=headers,
        json={"old_password": "anything", "new_password": "newpass123"},
    )
    assert resp.status_code == 200

    # 首次设密立即撤销旧会话；重新登录后再改密必须校验旧密码。
    assert client.get("/api/me", headers=headers).status_code == 401
    login = client.post(
        "/api/auth/login", json={"username": "wx_pwd_user", "password": "newpass123"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    resp = client.post(
        "/api/me/password",
        headers=headers,
        json={"old_password": "wrong", "new_password": "another456"},
    )
    assert resp.status_code == 400 and "原密码" in resp.json()["detail"]


def test_register_username_case_insensitive_unique():
    """注册时不允许与已有用户名仅大小写不同的新账号。"""
    client = make_client()
    admin_headers = auth_headers(client, "boss01")

    assert register(client, "Alice1").status_code == 200
    # 大小写变体注册被拒
    resp = register(client, "alice1", expect=400)
    assert "用户名已存在" in resp.json()["detail"]
    resp = register(client, "ALICE1", expect=400)
    assert "用户名已存在" in resp.json()["detail"]

    # 管理员改名为已有用户名的大小写变体也被拒
    client.app.state.db.add_register_code("CODE1")
    resp = client.post(
        "/api/auth/register",
        json={"username": "bob001", "password": "secret1234", "code": "CODE1"},
    )
    assert resp.status_code == 200
    uid = next(
        u["id"] for u in client.get("/api/users", headers=admin_headers).json()
        if u["username"] == "bob001"
    )
    resp = client.put(
        f"/api/users/{uid}", headers=admin_headers, json={"username": "ALICE1"}
    )
    assert resp.status_code == 400 and "用户名已存在" in resp.json()["detail"]


def test_login_username_case_insensitive():
    """注册用 COLLATE NOCASE 判重，登录也必须大小写不敏感，否则同名不同大小写无法登录。"""
    client = make_client()
    register(client, "Yansy102", "secret1234")
    # 大小写变体登录成功
    resp = client.post("/api/auth/login", json={"username": "yansy102", "password": "secret1234"})
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "Yansy102"  # token 中保留数据库原始用户名
    resp = client.post("/api/auth/login", json={"username": "YANSY102", "password": "secret1234"})
    assert resp.status_code == 200
    # 密码错误仍被拒
    assert client.post(
        "/api/auth/login", json={"username": "yansy102", "password": "wrong123"}
    ).status_code == 401


def test_add_user_username_case_insensitive_unique():
    """机器人/微信自动建号也遵守大小写不敏感唯一约束。"""
    client = make_client()
    db = client.app.state.db
    db.add_user("TgUser", "", telegram_chat_id="1")
    try:
        db.add_user("tguser", "", telegram_chat_id="2")
        raise AssertionError("应拒绝大小写变体用户名")
    except ValueError as exc:
        assert "用户名已存在" in str(exc)


def test_security_headers():
    """基础安全响应头：防 MIME 嗅探 / 点击劫持 / Referer 泄露。"""
    client = make_client()
    resp = client.get("/healthz")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    csp = resp.headers.get("content-security-policy") or ""
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp

    # 静态页面同样带安全头
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers.get("x-content-type-options") == "nosniff"


# ---- 关键词提醒 API ----
def test_keywords_crud_api():
    client = make_client()
    headers = user_headers(client, "kw_user")

    # 默认空
    me = client.get("/api/me", headers=headers).json()
    assert me["keywords"] == []

    resp = client.put(
        "/api/me", headers=headers,
        json={"keywords": ["ETF", " 降息 ", "   ", "机器人"]},
    )
    assert resp.status_code == 200
    me = client.get("/api/me", headers=headers).json()
    assert me["keywords"] == ["ETF", "降息", "机器人"]  # 去空白/空项

    # 覆盖更新
    client.put("/api/me", headers=headers, json={"keywords": ["半导体"]})
    assert client.get("/api/me", headers=headers).json()["keywords"] == ["半导体"]
    assert client.get("/api/me", headers=headers).json()["keywords_match_reports"] is False


def test_keywords_limits_api():
    client = make_client()
    headers = user_headers(client, "kw_limit")

    too_many = [f"关键词{i}" for i in range(21)]
    resp = client.put("/api/me", headers=headers, json={"keywords": too_many})
    assert resp.status_code == 400
    assert "20" in resp.json()["detail"]

    resp = client.put(
        "/api/me", headers=headers,
        json={"keywords": ["字" * 51]},
    )
    assert resp.status_code == 400
    assert "50" in resp.json()["detail"]


# ---- Bark 绑定 API ----
def test_bark_key_bind_api():
    client = make_client()
    headers = user_headers(client, "bark_user")

    me = client.get("/api/me", headers=headers).json()
    assert me["bark_key"] == ""

    # 非法 key → 400
    resp = client.put("/api/me", headers=headers, json={"bark_key": "短"})
    assert resp.status_code == 400
    assert "Bark key 无效" in resp.json()["detail"]

    # 合法 key → 绑定成功；/me 回掩码而非明文
    resp = client.put(
        "/api/me", headers=headers,
        json={"bark_key": "AaBbCcDdEeFf1234567890"},
    )
    assert resp.status_code == 200
    masked = client.get("/api/me", headers=headers).json()["bark_key"]
    assert masked != "AaBbCcDdEeFf1234567890" and "AaBb" in masked and "7890" in masked

    # 普通用户只允许设备 key，不能提供服务端目标 URL。
    # （先原样回填掩码验证不覆盖真值，再提交非法 URL）
    assert client.put("/api/me", headers=headers, json={"bark_key": masked}).status_code == 200
    assert client.get("/api/me", headers=headers).json()["bark_key"] == masked
    resp = client.put(
        "/api/me", headers=headers,
        json={"bark_key": "https://api.day.app/AaBbCcDdEeFf1234567890"},
    )
    assert resp.status_code == 400
    assert client.get("/api/me", headers=headers).json()["bark_key"] == masked


def test_bark_key_duplicate_rejected():
    client = make_client()
    h1 = user_headers(client, "bark_a")
    h2 = user_headers(client, "bark_b")
    assert client.put("/api/me", headers=h1, json={"bark_key": "AaBbCcDdEeFf1234567890"}).status_code == 200
    resp = client.put("/api/me", headers=h2, json={"bark_key": "AaBbCcDdEeFf1234567890"})
    assert resp.status_code == 400
    assert "已绑定其他账号" in resp.json()["detail"]
    # 原绑定者不受影响；掩码提交不清除真实 key
    masked = client.get("/api/me", headers=h1).json()["bark_key"]
    assert masked != "AaBbCcDdEeFf1234567890"
    assert client.put("/api/me", headers=h1, json={"bark_key": masked}).status_code == 200
    assert client.get("/api/me", headers=h1).json()["bark_key"] == masked


# ---- 账号级失败锁定（防 IP 轮换爆破）----
def test_account_lock_blocks_distributed_bruteforce():
    """轮换 IP 绕过单 IP 限流时，账号级锁定仍然生效（即使密码正确也拒绝）。"""
    cfg = Config()
    cfg.web.trust_proxy = True  # 让 X-Forwarded-For 生效，模拟多 IP 攻击
    client = make_client(config=cfg)
    user_headers(client, "victim")

    # 10 次失败各用不同 IP（单 IP 各自不足 8 次，不会触发 IP 限流）
    for i in range(10):
        r = client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": f"1.1.1.{i}"},
        )
        assert r.status_code == 401, f"第 {i} 次失败应 401"
    # 账号已锁定：新 IP + 正确密码也 429，不泄露密码有效性
    r = client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "pass123456"},
        headers={"X-Forwarded-For": "2.2.2.2"},
    )
    assert r.status_code == 429
    assert "锁定" in r.json()["detail"]


def test_admin_account_locks_sooner():
    """管理员账号 3 次失败即锁定（阈值更敏感）。"""
    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    auth_headers(client, "boss01")  # 注册并提升为管理员，密码 secret123

    for i in range(3):
        r = client.post(
            "/api/auth/login",
            json={"username": "boss01", "password": "wrong"},
            headers={"X-Forwarded-For": f"3.3.3.{i}"},
        )
        assert r.status_code == 401
    r = client.post(
        "/api/auth/login",
        json={"username": "boss01", "password": "secret1234"},
        headers={"X-Forwarded-For": "4.4.4.4"},
    )
    assert r.status_code == 429
    assert "锁定" in r.json()["detail"]


def test_login_locked_writes_audit_log():
    """账号锁定时写操作日志，管理员可审计（IP、角色、失败次数）。"""
    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    user_headers(client, "victim")
    admin_headers = auth_headers(client, "boss01")

    for i in range(10):
        client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": f"5.5.5.{i}"},
        )
    logs = client.get("/api/admin/logs", headers=admin_headers).json()
    locked = [l for l in logs if l["action"] == "login_locked" and l["target"] == "victim"]
    assert len(locked) == 1
    assert "role=user" in locked[0]["detail"]
    assert "ip=" in locked[0]["detail"]


def test_account_success_clears_lock_count():
    """未到锁定阈值前输入正确密码：登录成功并清零失败计数。"""
    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    user_headers(client, "victim")

    for i in range(9):  # 阈值 10，9 次未锁
        assert client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": f"6.6.6.{i}"},
        ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "pass123456"},
        headers={"X-Forwarded-For": "7.7.7.7"},
    ).status_code == 200
    # 清零后重新失败仍是 401，不会因历史计数立即锁定
    assert client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "bad"},
        headers={"X-Forwarded-For": "8.8.8.8"},
    ).status_code == 401


def test_account_lock_expires_after_window(monkeypatch):
    """锁定到期后允许再次尝试，正确密码可登录。"""
    import app.api as api_mod

    cfg = Config()
    cfg.web.trust_proxy = True
    client = make_client(config=cfg)
    user_headers(client, "victim")

    for i in range(10):
        client.post(
            "/api/auth/login",
            json={"username": "victim", "password": "bad"},
            headers={"X-Forwarded-For": f"9.9.9.{i}"},
        )
    assert client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "pass123456"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    ).status_code == 429

    # 时间前进 20 分钟（> 15 分钟锁定窗口）
    real_time = api_mod.time.time
    fake_now = {"value": real_time() + 1200}
    monkeypatch.setattr(api_mod.time, "time", lambda: fake_now["value"])
    r = client.post(
        "/api/auth/login",
        json={"username": "victim", "password": "pass123456"},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert r.status_code == 200


def test_register_username_min_length_6():
    """新注册用户名最少 6 字符。"""
    client = make_client()
    db = client.app.state.db
    for i, name in enumerate(["ab", "abcde", "abcdefg"]):
        db.add_register_code(f"MINLEN{i}")
        resp = client.post(
            "/api/auth/register",
            json={"username": name, "password": "secret1234", "code": f"MINLEN{i}"},
        )
        expected = 200 if len(name) >= 6 else 400
        assert resp.status_code == expected, f"{name} 应 {expected}"
        if expected == 400:
            assert "用户名至少6位" in resp.json()["detail"]
    db.add_register_code("SHORTPW1")
    pw = client.post(
        "/api/auth/register",
        json={"username": "goodname", "password": "123", "code": "SHORTPW1"},
    )
    assert pw.status_code == 400
    assert pw.json()["detail"] == "密码至少10位"
    assert db.get_register_code("SHORTPW1")["used_by"] is None


def test_register_rejects_unreasonable_username():
    """空格、引号、符号不能当登录名；邀请码不被消耗。"""
    from app.auth import USERNAME_CHARSET_MSG

    client = make_client()
    db = client.app.state.db
    db.add_register_code("BADNAME1")
    resp = client.post(
        "/api/auth/register",
        json={"username": "ag's trend", "password": "secret1234", "code": "BADNAME1"},
    )
    assert resp.status_code == 400
    assert USERNAME_CHARSET_MSG in resp.json()["detail"]
    assert db.get_register_code("BADNAME1")["used_by"] is None
    assert db.get_user_by_username_ci("ag's trend") is None


def test_existing_unreasonable_username_can_still_login():
    """历史脏数据仍可登录，避免改规则把人锁在门外。"""
    client = make_client()
    db = client.app.state.db
    from app.auth import hash_password

    db.add_user("ag's trend", hash_password("secret1234"))
    assert client.post(
        "/api/auth/login",
        json={"username": "ag's trend", "password": "secret1234"},
    ).status_code == 200


def test_admin_rename_username_min_length_6():
    """管理员改名同样要求 6-30 位。"""
    client = make_client()
    admin_headers = auth_headers(client)
    reg = register(client, "victim")
    uid = reg.json()["user"]["id"]
    assert client.put(
        f"/api/users/{uid}", headers=admin_headers, json={"username": "abcde"}
    ).status_code == 400
    assert client.put(
        f"/api/users/{uid}", headers=admin_headers, json={"username": "ag's trend"}
    ).status_code == 400
    assert client.put(
        f"/api/users/{uid}", headers=admin_headers, json={"username": "abcdef"}
    ).status_code == 200


def test_push_channels_accepts_bark():
    """push_channels 白名单必须包含 bark（加 Bark 通道时漏改过这里）。"""
    client = make_client()
    headers = user_headers(client, "barkchan")
    # 绑定 Bark 后，推送通道选择里勾选 bark + telegram 应可保存
    client.put("/api/me", headers=headers, json={"bark_key": "AaBbCcDdEeFf1234567890"})
    client.put(
        "/api/me", headers=headers,
        json={"telegram_chat_id": "123"},
    )
    resp = client.put(
        "/api/me", headers=headers,
        json={"push_channels": "telegram,bark"},
    )
    assert resp.status_code == 200, resp.text
    assert client.get("/api/me", headers=headers).json()["push_channels"] == "telegram,bark"
    # 非法渠道仍拒绝
    assert client.put(
        "/api/me", headers=headers, json={"push_channels": "telegram,slack"}
    ).status_code == 400


# ---- 贴文标签 ----

def make_tagged_feed(client, admin_headers, user_headers, suffix="1"):
    """建大V + 用户订阅 + 两条已打标帖子，返回 kol_id；suffix 用于区分用例。"""
    db = client.app.state.db
    resp = client.post(
        "/api/kols", headers=admin_headers,
        json={"platform": "xueqiu", "name": f"标签大V{suffix}", "external_id": f"tagkol{suffix}"},
    )
    kid = resp.json()["id"]
    client.post("/api/subscriptions", headers=user_headers, json={"kol_id": kid})
    db.insert_post(
        platform="xueqiu", kol_id=kid, external_id=f"post1{suffix}",
        title="宏观展望", content="今日大盘整体上行", url="u", published_at="",
    )
    db.insert_post(
        platform="xueqiu", kol_id=kid, external_id=f"post2{suffix}",
        title="芯片进展", content="新工艺良率提升", url="u", published_at="",
    )
    # 取 id 后回写标签
    p1 = db.get_post_id("xueqiu", f"post1{suffix}")
    p2 = db.get_post_id("xueqiu", f"post2{suffix}")
    db.update_post_tags(p1, ["宏观", "大盘"])
    db.update_post_tags(p2, ["科技"])
    return kid


def test_post_tags_list_readable_by_any_user():
    client = make_client()
    headers = user_headers(client, "tagreader")
    resp = client.get("/api/tags", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # 默认词表是「标签+关键词」对象数组，包含 宏观/科技
    tag_names = [r["tag"] for r in data["tags"]]
    assert "宏观" in tag_names and "科技" in tag_names
    assert data["tags"][0]["keywords"]  # 默认规则带关键词
    # 常用股票名表（默认含宁德时代）+ 动态标签聚合（空库无）
    assert "宁德时代" in data["stock_names"]
    assert data["universe"]["count"] > 4000
    assert data["dynamic_tags"] == []
    assert data["stats"]["total"] == 0
    assert "maintain" in data
    assert data["maintain"]["last"] is None


def test_tag_vocabulary_update_admin_only():
    client = make_client()
    user = user_headers(client, "taguser")
    admin = auth_headers(client, "tagadmin")
    # 普通用户不能改词表
    assert client.put("/api/tags", headers=user, json={"tags": [{"tag": "宏观"}]}).status_code == 403
    # 管理员可保存（去空白、按 tag 去重、关键词清理空串；股票名单去重保存）
    resp = client.put(
        "/api/tags",
        headers=admin,
        json={
            "tags": [
                {"tag": " 宏观 ", "keywords": ["央行", " ", "降息"]},
                {"tag": "大盘", "keywords": []},
                {"tag": "宏观", "keywords": []},
            ],
            "stock_names": ["长鑫", "宁德时代", "长鑫", " "],
        },
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["tags"]
    assert [r["tag"] for r in saved] == ["宏观", "大盘"]
    assert saved[0]["keywords"] == ["央行", "降息"]
    assert resp.json()["stock_names"] == ["长鑫", "宁德时代"]
    # 空词表拒绝
    assert client.put("/api/tags", headers=admin, json={"tags": []}).status_code == 400


def test_tag_alias_accepts_universe_official_name():
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    response = client.put(
        "/api/tags",
        headers=admin,
        json={
            "tags": [{"tag": "科技", "keywords": ["芯片"]}],
            "stock_names": ["宁德时代"],
            "stock_aliases": [{"alias": "浦发", "stock": "浦发银行"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["stock_aliases"] == [{"alias": "浦发", "stock": "浦发银行"}]


def test_tag_alias_requires_known_stock_name():
    client = make_client()
    admin = auth_headers(client, "tagadmin")

    response = client.put(
        "/api/tags",
        headers=admin,
        json={
            "tags": [{"tag": "科技", "keywords": ["芯片"]}],
            "stock_names": ["宁德时代"],
            "stock_aliases": [{"alias": "宁王", "stock": "不存在股票"}],
        },
    )

    assert response.status_code == 400
    assert "股票名" in response.json()["detail"]


def test_tag_alias_rejects_conflicting_mapping():
    client = make_client()
    admin = auth_headers(client, "tagadmin")

    response = client.put(
        "/api/tags",
        headers=admin,
        json={
            "tags": [{"tag": "科技", "keywords": ["芯片"]}],
            "stock_names": ["宁德时代", "比亚迪"],
            "stock_aliases": [
                {"alias": "宁王", "stock": "宁德时代"},
                {"alias": "宁王", "stock": "比亚迪"},
            ],
        },
    )

    assert response.status_code == 400
    assert "映射冲突" in response.json()["detail"]


def test_tag_alias_deduplicates_identical_mapping():
    client = make_client()
    admin = auth_headers(client, "tagadmin")

    response = client.put(
        "/api/tags",
        headers=admin,
        json={
            "tags": [{"tag": "科技", "keywords": ["芯片"]}],
            "stock_names": ["宁德时代"],
            "stock_aliases": [
                {"alias": "宁王", "stock": "宁德时代"},
                {"alias": "宁王", "stock": "宁德时代"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["stock_aliases"] == [
        {"alias": "宁王", "stock": "宁德时代"}
    ]


def test_tag_stock_names_can_be_saved_without_tags():
    """管理员可只改常用股票名，不必连话题词表一起提交。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    db.set_stock_names(["宁德时代", "千岸科技"])
    db.set_stock_aliases(
        [
            {"alias": "宁王", "stock": "宁德时代"},
            {"alias": "岸科技", "stock": "千岸科技"},
        ]
    )

    response = client.put(
        "/api/tags",
        headers=admin,
        json={"stock_names": ["宁德时代", "五粮液", "宁德时代", " "]},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["stock_names"] == ["宁德时代", "五粮液"]
    assert data["stock_aliases"] == [{"alias": "宁王", "stock": "宁德时代"}]
    assert data["dropped_aliases"] == [{"alias": "岸科技", "stock": "千岸科技"}]
    assert data["excluded_stock_names"] == ["千岸科技"]
    assert [r["tag"] for r in data["tags"]]  # 话题词表未动
    listed = client.get("/api/tags", headers=admin).json()
    assert listed["excluded_stock_names"] == ["千岸科技"]


def test_tag_stock_names_reject_index_and_restore_exclusion():
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    db.set_stock_names(["宁德时代"])
    db.set_stock_name_exclusions(["五粮液"])

    bad = client.put(
        "/api/tags",
        headers=admin,
        json={"stock_names": ["宁德时代", "上证指数"]},
    )
    assert bad.status_code == 400
    assert "不是个股名" in bad.json()["detail"]
    assert db.get_stock_names() == ["宁德时代"]

    restored = client.put(
        "/api/tags",
        headers=admin,
        json={"stock_names": ["宁德时代", "五粮液"]},
    )
    assert restored.status_code == 200
    assert restored.json()["stock_names"] == ["宁德时代", "五粮液"]
    assert restored.json()["excluded_stock_names"] == []


def test_tag_vocabulary_update_keeps_stock_config_when_omitted():
    """只改话题词表时不传 stock 字段，不得误清空已保存的股票名和别名。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    db.set_stock_names(["宁德时代"])
    db.set_stock_aliases([{"alias": "宁王", "stock": "宁德时代"}])

    response = client.put(
        "/api/tags",
        headers=admin,
        json={"tags": [{"tag": "科技", "keywords": ["芯片"]}]},
    )

    assert response.status_code == 200
    assert response.json()["stock_names"] == ["宁德时代"]
    assert response.json()["stock_aliases"] == [
        {"alias": "宁王", "stock": "宁德时代"}
    ]


def test_feed_returns_tags_and_filters_by_tag():
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    user = user_headers(client, "taguser2")
    make_tagged_feed(client, admin, user)

    feed = client.get("/api/my/feed", headers=user).json()
    assert len(feed) == 2
    assert feed[0]["tags"] == ["科技"]
    assert feed[1]["tags"] == ["宏观", "大盘"]

    filtered = client.get("/api/my/feed?tag=宏观", headers=user).json()
    assert len(filtered) == 1
    assert filtered[0]["tags"] == ["宏观", "大盘"]

    filtered2 = client.get("/api/my/feed?tag=科技", headers=user).json()
    assert len(filtered2) == 1
    assert filtered2[0]["tags"] == ["科技"]


def test_backfill_tags_untagged_posts(monkeypatch):
    """全量回填：只处理未打标帖，已打标的不覆盖（规则打标，无需 LLM 配置）。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    user = user_headers(client, "taguser3")
    kid = make_tagged_feed(client, admin, user, suffix="b")

    # 追加一条未打标贴文
    db = client.app.state.db
    db.insert_post(
        platform="xueqiu", kol_id=kid, external_id="post3b",
        title="政策解读", content="新规出台", url="u", published_at="",
    )

    captured = {}

    def fake_rule(posts, rules):
        captured["posts"] = posts
        return {i: ["政策"] for i in range(len(posts))}

    monkeypatch.setattr("app.tagging.rule_tag_posts", fake_rule)
    resp = client.post("/api/tags/backfill", headers=admin, json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["processed"] == 1
    assert data["tagged"] == 1

    # 此前已打标的两条标签不被覆盖，只有新帖被回填
    all_posts = {p["id"]: p["tags"] for p in client.get("/api/posts", headers=admin).json()}
    assert all_posts[db.get_post_id("xueqiu", "post1b")] == ["宏观", "大盘"]
    assert all_posts[db.get_post_id("xueqiu", "post2b")] == ["科技"]
    assert all_posts[db.get_post_id("xueqiu", "post3b")] == ["政策"]



def test_private_kol_content_denied_on_feed():
    """公开转私有并撤销 ACL 后，动态 feed 不再返回该大V帖子。"""
    client = make_client()
    admin_headers = auth_headers(client)
    r = register(client, "privuser", "pass123456")
    uid = r.json()["user"]["id"]
    user_headers_h = {"Authorization": f"Bearer {r.json()['token']}"}
    db = client.app.state.db

    kid = db.add_kol("xueqiu", "公开大V", "priv1")
    db.add_subscription(uid, kid)
    db.insert_post("xueqiu", kid, "p1", "t", "公开帖子", "u", "")

    feed = client.get("/api/my/feed", headers=user_headers_h).json()
    assert any(p["external_id"] == "p1" for p in feed)

    # 转为私有并清空 ACL：feed 不再返回
    db.update_kol(kid, is_private=True)
    feed2 = client.get("/api/my/feed", headers=user_headers_h).json()
    assert all(p["external_id"] != "p1" for p in feed2)

    # 管理员订阅后仍可读（管理访问语义保留）
    admin_uid = db.get_user_by_username("testadmin")["id"]
    db.add_subscription(admin_uid, kid)
    admin_feed = client.get("/api/my/feed", headers=admin_headers).json()
    assert any(p["external_id"] == "p1" for p in admin_feed)


def test_subscribe_private_kol_via_api_still_denied_after_acl_revoke():
    """订阅接口对私有大V的 404 拦截在 ACL 撤销后依然生效（回归）。"""
    client = make_client()
    r = register(client, "privuser2", "pass123456")
    user_headers_h = {"Authorization": f"Bearer {r.json()['token']}"}
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "私有大V", "priv2")
    db.update_kol(kid, is_private=True)
    assert client.post("/api/subscriptions", headers=user_headers_h, json={"kol_id": kid}).status_code == 404


def test_account_lock_not_bypassable_by_username_case():
    """不同大小写的用户名共享失败计数——不能靠大小写变体绕过账号锁定。"""
    cfg = Config()
    cfg.web.trust_proxy = True  # 模拟多 IP，只触发账号级锁定
    client = make_client(config=cfg)
    user_headers(client, "caseuser")

    variants = ["caseuser", "CaseUser", "CASEUSER", "caseUSER"]
    # 10 次失败轮换 IP + 大小写变体，任何单键都不足阈值（若键未规范化即可绕过）
    for i in range(10):
        name = variants[i % len(variants)]
        r = client.post(
            "/api/auth/login",
            json={"username": name, "password": "bad"},
            headers={"X-Forwarded-For": f"3.3.3.{i}"},
        )
        assert r.status_code == 401, f"变体 {name} 失败应 401"
    # 10 次失败已跨大小写累积到锁定阈值：新 IP + 正确密码仍 429
    r = client.post(
        "/api/auth/login",
        json={"username": "caseuser", "password": "pass123456"},
        headers={"X-Forwarded-For": "4.4.4.4"},
    )
    assert r.status_code == 429
    assert "锁定" in r.json()["detail"]


def test_backfill_picks_untagged_older_posts(monkeypatch):
    """全量回填：最新已打标时仍处理更早的未打标帖，不被最近 N 条限制。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tagbf1")
    # 3 条帖子：前两条打标，第三条未打标
    id1 = db.insert_post("xueqiu", kid, "bf1", "t", "c", "u", "")
    id2 = db.insert_post("xueqiu", kid, "bf2", "t", "c", "u", "")
    id3 = db.insert_post("xueqiu", kid, "bf3", "t", "c", "u", "")
    db.update_post_tags(id1, ["宏观"])
    db.update_post_tags(id2, ["科技"])

    captured = {}

    def fake_rule(posts, rules):
        captured["posts"] = posts
        return {i: ["政策"] for i in range(len(posts))}

    monkeypatch.setattr("app.tagging.rule_tag_posts", fake_rule)
    resp = client.post("/api/tags/backfill", headers=admin, json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["processed"] == 1
    assert captured["posts"][0].external_id == "bf3"  # 未打标的第三条被处理
    # get_post 返回原始行，tags 为 JSON 文本
    assert db.get_post(id3)["tags"] == '["政策"]'
    assert db.list_posts(limit=3)[0]["tags"] == ["政策"]


def test_tag_filter_exact_element_match():
    """tag=宏观 只匹配完整标签元素，不误中「宏观经济」。"""
    client = make_client()
    r = register(client, "tagfltuser")
    token = r.json()["token"]
    user_headers_h = {"Authorization": f"Bearer {token}"}
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tagflt1")
    client.post("/api/subscriptions", headers=user_headers_h, json={"kol_id": kid})
    # 两条帖子：一条标签「宏观」，一条标签「宏观经济」
    p1 = db.insert_post("xueqiu", kid, "f1", "宏观展望", "宏观内容", "u", "")
    p2 = db.insert_post("xueqiu", kid, "f2", "宏观经济", "宏观分析", "u", "")
    db.update_post_tags(p1, ["宏观"])
    db.update_post_tags(p2, ["宏观经济"])

    feed = client.get("/api/my/feed", headers=user_headers_h).json()
    assert {p["external_id"] for p in feed} == {"f1", "f2"}

    filtered = client.get("/api/my/feed?tag=宏观", headers=user_headers_h).json()
    assert [p["external_id"] for p in filtered] == ["f1"]


def test_pending_backfill_does_not_rescan_processed_no_match_posts():
    """零命中帖标记为已处理（[]），回填不会重复扫描（曾死循环回归）。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tagloop1")
    # 2 帖：1 个命中「宏观」（央行），1 个不含任何关键词
    db.insert_post("xueqiu", kid, "lp1", "央行降息", "央行宣布降息", "u", "")
    db.insert_post("xueqiu", kid, "lp2", "无关内容", "今天天气不错", "u", "")

    # 第一次回填：全部扫一遍，命中 1 条；零命中帖也标记为已处理
    first = client.post("/api/tags/backfill", headers=admin, json={})
    assert first.status_code == 200, first.text
    assert first.json() == {"processed": 2, "tagged": 1}

    # 第二次回填：零命中帖已是 []，不再被重复扫描（曾死循环）
    second = client.post("/api/tags/backfill", headers=admin, json={})
    assert second.status_code == 200, second.text
    assert second.json() == {"processed": 0, "tagged": 0}

    # 命中的帖子确实写库了，零命中帖标记为已处理
    assert db.get_post(db.get_post_id("xueqiu", "lp1"))["tags"] == '["宏观"]'
    assert db.get_post(db.get_post_id("xueqiu", "lp2"))["tags"] == "[]"


def test_full_retag_replaces_existing_and_clears_stale_tags(monkeypatch):
    """mode=all 全量重算：覆盖已有标签，命中替换、零命中清空为 []。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tag-retag")
    old_id = db.insert_post("xueqiu", kid, "old", "旧标签", "无命中", "u", "")
    hit_id = db.insert_post("xueqiu", kid, "hit", "政策", "新规出台", "u", "")
    db.update_post_tags(old_id, ["已删除标签"])
    db.update_post_tags(hit_id, ["旧政策"])

    monkeypatch.setattr(
        "app.tagging.rule_tag_posts",
        lambda posts, rules: {
            i: (["政策"] if post.external_id == "hit" else [])
            for i, post in enumerate(posts)
        },
    )
    monkeypatch.setattr(
        "app.tagging.stock_tag_posts",
        lambda posts, names, aliases=None: {i: [] for i in range(len(posts))},
    )

    response = client.post(
        "/api/tags/backfill", headers=admin, json={"mode": "all"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"processed": 2, "tagged": 1}
    assert db.get_post(old_id)["tags"] == "[]"
    assert db.get_post(hit_id)["tags"] == '["政策"]'


def test_retag_rejects_unknown_mode():
    """回填模式只允许 pending/all，未知值返回 422。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")

    response = client.post(
        "/api/tags/backfill", headers=admin, json={"mode": "recent"}
    )

    assert response.status_code == 422


def test_maintain_tags_admin_only_and_optional_backfill(monkeypatch):
    """维护接口仅管理员；可顺带回填；结果写入 last。"""
    client = make_client()
    user = user_headers(client, "taguser-mt")
    admin = auth_headers(client, "tagadmin-mt")
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tag-mt")
    db.insert_post("xueqiu", kid, "mt1", "央行降息", "央行宣布降息", "u", "")

    monkeypatch.setattr(
        "app.tagging.run_tag_maintenance",
        lambda db, llm_config=None: {
            "cleaned": 0,
            "added_aliases": [{"alias": "宁王", "stock": "宁德时代"}],
            "added_stock_names": ["盐湖股份"],
            "removed_stock_names": ["上证指数"],
            "candidates": 3,
            "marks": 1,
            "llm_used": True,
            "error": None,
        },
    )

    assert client.post("/api/tags/maintain", headers=user, json={}).status_code == 403
    resp = client.post(
        "/api/tags/maintain", headers=admin, json={"backfill": "pending"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added_aliases"] == [{"alias": "宁王", "stock": "宁德时代"}]
    assert data["added_stock_names"] == ["盐湖股份"]
    assert data["removed_stock_names"] == ["上证指数"]
    assert data["backfill"] == {"processed": 1, "tagged": 1}
    assert data["at"]
    listed = client.get("/api/tags", headers=admin).json()
    assert listed["maintain"]["last"]["added_stock_names"] == ["盐湖股份"]
    assert listed["maintain"]["last"]["backfill"]["processed"] == 1
    assert isinstance(listed["maintain"]["llm_ready"], bool)


def test_maintain_tags_rejects_unknown_backfill():
    client = make_client()
    admin = auth_headers(client, "tagadmin-mt2")
    assert client.post(
        "/api/tags/maintain", headers=admin, json={"backfill": "recent"}
    ).status_code == 422


def test_maintain_tags_conflict_when_busy(monkeypatch):
    client = make_client()
    admin = auth_headers(client, "tagadmin-mt3")
    monkeypatch.setattr("app.tagging.try_run_tag_maintenance", lambda db, llm_config=None: None)
    resp = client.post("/api/tags/maintain", headers=admin, json={})
    assert resp.status_code == 409
    assert "正在进行" in resp.json()["detail"]


def test_backfill_merges_stock_tags(monkeypatch):
    """回填按话题+股票合并打标（$标记$ 提取的股票名也写入）。"""
    client = make_client()
    admin = auth_headers(client, "tagadmin")
    db = client.app.state.db
    kid = db.add_kol("xueqiu", "标签大V", "tagstk1")
    db.insert_post("xueqiu", kid, "stk1", "$中船特气(SH688146)$ 涨价", "央行降息，$中船特气(SH688146)$ 继续涨价", "u", "")

    resp = client.post("/api/tags/backfill", headers=admin, json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["processed"] == 1
    assert data["tagged"] == 1
    tags = db.list_posts(limit=5)[0]["tags"]
    # 话题「宏观」（央行/降息）+ 股票「中船特气」（$标记$）
    assert "宏观" in tags and "中船特气" in tags
    assert tags == ["宏观", "中船特气"]


def test_admin_toggle_secondary():
    client = make_client()
    headers = auth_headers(client)
    r = client.post(
        "/api/kols", headers=headers,
        json={"platform": "xueqiu", "name": "次要大V", "external_id": "999999"},
    )
    kid = r.json()["id"]
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"secondary": True})
    assert r.status_code == 200
    kol = r.json()
    assert kol["secondary"] == 1 and kol["priority"] == 0
    # 互斥：设 priority 清 secondary
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"priority": True})
    kol = r.json()
    assert kol["priority"] == 1 and kol["secondary"] == 0
    # 反向互斥：设 secondary 清 priority
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"secondary": True})
    kol = r.json()
    assert kol["secondary"] == 1 and kol["priority"] == 0


def test_add_kol_with_secondary():
    client = make_client()
    headers = auth_headers(client)
    r = client.post("/api/kols", headers=headers, json={
        "platform": "xueqiu", "name": "次要", "external_id": "888888", "secondary": True,
    })
    assert r.status_code == 200
    assert r.json()["secondary"] == 1


def test_partial_update_preserves_priority_and_secondary():
    """部分更新（只传 enabled）不得清除 priority/secondary 标记。"""
    client = make_client()
    headers = auth_headers(client)
    r = client.post(
        "/api/kols", headers=headers,
        json={"platform": "xueqiu", "name": "测试V", "external_id": "777777", "priority": True},
    )
    assert r.status_code == 200
    kid = r.json()["id"]
    # 先设 secondary（互斥会清 priority），再造一个 priority 场景
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"secondary": True})
    assert r.json()["secondary"] == 1
    # 只传 enabled 的部分更新
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"enabled": False})
    assert r.status_code == 200
    kol = r.json()
    assert kol["secondary"] == 1, f"secondary 被部分更新清除: {kol}"
    # priority 方向同样验证
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"priority": True})
    assert r.json()["priority"] == 1
    r = client.put(f"/api/kols/{kid}", headers=headers, json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["priority"] == 1, "priority 被部分更新清除"


def test_admin_user_list_includes_register_source():
    client = make_client()
    admin_headers = auth_headers(client)
    codes = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "note": "内部"},
    ).json()["codes"]
    register(client, "invitee", code=codes[0])
    rows = client.get("/api/users", headers=admin_headers).json()
    invitee = next(u for u in rows if u["username"] == "invitee")
    assert invitee["register_code"] == codes[0]
    assert invitee["register_note"] == "内部"
    uid = client.app.state.db.add_user("seeded1", "h")
    rows = client.get("/api/users", headers=admin_headers).json()
    seeded = next(u for u in rows if u["id"] == uid)
    assert seeded["register_code"] == ""
    assert seeded["register_note"] == ""
    assert invitee["origin"] == "invite"
    assert invitee["origin_label"] == "邀请码"
    assert invitee["has_password"] is True
    assert invitee["username_valid"] is True
    assert seeded["origin"] == "web"
    assert seeded["has_password"] is True
    client.app.state.db.update_user(uid, telegram_chat_id="888")
    seeded = next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == uid)
    assert seeded["origin"] == "web"
    assert seeded["telegram_bound"] is True
    # 邀请码用户后来绑定 Telegram，来源仍是邀请码
    client.app.state.db.update_user(invitee["id"], telegram_chat_id="999")
    invitee = next(
        u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == invitee["id"]
    )
    assert invitee["origin"] == "invite"
    assert invitee["telegram_bound"] is True


def test_admin_user_list_marks_bot_and_dirty_usernames():
    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    tg = db.add_user("tgname1", "", telegram_chat_id="111")
    dirty = db.add_user("ag's trend", "h")
    rows = {u["id"]: u for u in client.get("/api/users", headers=admin_headers).json()}
    assert rows[tg]["origin"] == "telegram"
    assert rows[tg]["has_password"] is False
    assert rows[tg]["last_login_at"] == ""
    assert rows[dirty]["username_valid"] is False
    assert "password_hash" not in rows[tg]
    assert "wechat_openid" not in rows[tg]


def test_cannot_delete_other_admin():
    client = make_client()
    admin_headers = auth_headers(client, "boss01")
    other = register(client, "admin02")
    oid = other.json()["user"]["id"]
    client.app.state.db.update_user(oid, is_admin=True)
    victim = register(client, "deletable")
    vid = victim.json()["user"]["id"]

    resp = client.delete(f"/api/users/{oid}", headers=admin_headers)
    assert resp.status_code == 400
    assert "不能删除管理员" in resp.json()["detail"]
    assert client.app.state.db.get_user(oid) is not None

    batch = client.post(
        "/api/admin/users/batch",
        headers=admin_headers,
        json={"ids": [oid, vid], "action": "delete"},
    )
    assert batch.status_code == 200
    assert batch.json()["count"] == 1
    assert batch.json()["skipped"] == 1
    assert client.app.state.db.get_user(oid) is not None
    assert client.app.state.db.get_user(vid) is None


def test_admin_user_list_newest_first_and_subscription_count():
    client = make_client()
    admin_headers = auth_headers(client)
    register(client, "olderu1")
    newer = register(client, "neweru1")
    uid = newer.json()["user"]["id"]
    kid = client.post(
        "/api/kols",
        headers=admin_headers,
        json={"platform": "xueqiu", "name": "A", "external_id": "1"},
    ).json()["id"]
    client.post(
        "/api/subscriptions",
        headers={"Authorization": f"Bearer {newer.json()['token']}"},
        json={"kol_id": kid},
    )
    rows = client.get("/api/users", headers=admin_headers).json()
    assert rows[0]["username"] == "neweru1"
    row = next(u for u in rows if u["id"] == uid)
    assert row["subscription_count"] == 1
    assert row["dnd_enabled"] is False
    older = next(u for u in rows if u["username"] == "olderu1")
    assert older["subscription_count"] == 0


def test_admin_users_batch_notify_and_delete():
    client = make_client()
    admin_headers = auth_headers(client)
    admin = client.get("/api/me", headers=admin_headers).json()
    a_headers = user_headers(client, "batchu_a")
    user_headers(client, "batchu_b")  # 建 b 账号供后续断言，凭据本身用不到
    users = client.get("/api/users", headers=admin_headers).json()
    uid_a = next(u["id"] for u in users if u["username"] == "batchu_a")
    uid_b = next(u["id"] for u in users if u["username"] == "batchu_b")

    def batch(ids, action):
        return client.post(
            "/api/admin/users/batch",
            headers=admin_headers,
            json={"ids": ids, "action": action},
        )

    assert batch([], "disable_notify").status_code == 400
    assert batch([uid_a], "nope").status_code == 400
    assert batch([uid_a, uid_b], "disable_notify").status_code == 200
    rows = {u["id"]: u for u in client.get("/api/users", headers=admin_headers).json()}
    assert rows[uid_a]["notify_enabled"] is False
    assert rows[uid_b]["notify_enabled"] is False
    assert batch([uid_a], "enable_notify").json()["count"] == 1
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == uid_a)["notify_enabled"] is True

    deleted = batch([admin["id"], uid_a, 999999], "delete")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["count"] == 1 and body["skipped"] == 2
    names = {u["username"] for u in client.get("/api/users", headers=admin_headers).json()}
    assert "batchu_a" not in names
    assert "batchu_b" in names
    assert admin["username"] in names
    assert client.get("/api/me", headers=a_headers).status_code in (401, 403)

    uh = user_headers(client, "batchu_norm")
    assert client.post(
        "/api/admin/users/batch",
        headers=uh,
        json={"ids": [uid_b], "action": "disable_notify"},
    ).status_code == 403


def test_admin_register_codes_batch_revoke_and_purge():
    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    gen = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 4, "note": "batch-rc"},
    ).json()
    available, to_use, to_revoke, to_expire = gen["codes"]
    register(client, "rc_used_user", code=to_use)
    assert client.post(
        f"/api/admin/register-codes/{to_revoke}/revoke", headers=admin_headers
    ).status_code == 200
    db._execute(
        "UPDATE register_codes SET expires_at = datetime('now', '-2 days') WHERE code = ?",
        (to_expire,),
    )

    def batch(codes, action):
        return client.post(
            "/api/admin/register-codes/batch",
            headers=admin_headers,
            json={"codes": codes, "action": action},
        )

    assert batch([], "revoke").status_code == 400
    assert batch([available], "nope").status_code == 400
    # 混选：只作废未用未废的；已用/已作废跳过
    mixed_revoke = batch([available, to_use, to_revoke], "revoke")
    assert mixed_revoke.status_code == 200
    assert mixed_revoke.json()["count"] == 1
    rows = {r["code"]: r for r in client.get("/api/admin/register-codes", headers=admin_headers).json()}
    assert rows[available]["revoked_at"]
    assert rows[to_use]["used_by"]
    # 可用码不能物理删除 → 全部不合法则 400
    fresh = client.post(
        "/api/admin/register-codes",
        headers=admin_headers,
        json={"count": 1, "note": "keep"},
    ).json()["codes"][0]
    assert batch([fresh], "delete").status_code == 400
    # 清废码：已用/已作废/已过期；混进可用则跳过
    purged = batch([to_use, to_revoke, to_expire, fresh], "delete")
    assert purged.status_code == 200
    assert purged.json()["count"] == 3
    left = {r["code"] for r in client.get("/api/admin/register-codes", headers=admin_headers).json()}
    assert to_use not in left and to_revoke not in left and to_expire not in left
    assert fresh in left
    invitee = next(u for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "rc_used_user")
    assert invitee["register_code"] == ""
    assert invitee["register_note"] == ""

    uh = user_headers(client, "rc_batch_norm")
    assert client.post(
        "/api/admin/register-codes/batch",
        headers=uh,
        json={"codes": [fresh], "action": "revoke"},
    ).status_code == 403


def test_login_sets_last_login_at_register_does_not():
    client = make_client()
    admin_headers = auth_headers(client)
    code = client.post(
        "/api/admin/register-codes", headers=admin_headers, json={"count": 1, "note": "inact"}
    ).json()["codes"][0]
    reg = register(client, "neverlogin", password="pass123456", code=code)
    uid = reg.json()["user"]["id"]
    db = client.app.state.db
    row = db.get_user(uid)
    assert not row.get("last_login_at")
    assert client.post(
        "/api/auth/login", json={"username": "neverlogin", "password": "pass123456"}
    ).status_code == 200
    row = db.get_user(uid)
    assert row["last_login_at"]


def test_me_updates_last_login_at():
    client = make_client()
    reg = register(client, "me_seen", password="pass123456").json()
    token, uid = reg["token"], reg["user"]["id"]
    assert not client.app.state.db.get_user(uid).get("last_login_at")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.app.state.db.get_user(uid)["last_login_at"]


def test_inactive_user_policy_and_list_flags():
    from app.db import days_until_purge

    client = make_client()
    admin_headers = auth_headers(client)
    db = client.app.state.db
    first = client.get("/api/admin/inactive-users-policy", headers=admin_headers).json()
    assert first["inactive_after_days"] == 90
    assert first["inactive_purge_after_days"] == 30
    assert first["customized"] is False
    assert first["marked_count"] == 0
    assert first["purge_count"] == 0
    assert db.get_setting("inactive_after_days") == "90"
    assert db.get_setting("inactive_purge_after_days") == "30"
    assert not db.inactive_policy_customized()
    uh = user_headers(client, "inact_norm")
    assert client.put(
        "/api/admin/inactive-users-policy",
        headers=uh,
        json={"inactive_after_days": 10, "inactive_purge_after_days": 5},
    ).status_code == 403
    saved = client.put(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        json={"inactive_after_days": 10, "inactive_purge_after_days": 5},
    )
    assert saved.status_code == 200
    assert saved.json()["customized"] is True
    assert saved.json()["inactive_after_days"] == 10
    assert client.put(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        json={"inactive_after_days": -1, "inactive_purge_after_days": 5},
    ).status_code == 400
    assert client.put(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        json={"inactive_after_days": 10, "inactive_purge_after_days": 3651},
    ).status_code == 400

    ghost = db.add_user("ghost90", "h")
    db._execute("UPDATE users SET created_at = datetime('now', '-12 days') WHERE id = ?", (ghost,))
    preview = client.get(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        params={"inactive_after_days": 10, "inactive_purge_after_days": 5},
    ).json()
    assert preview["inactive_after_days"] == 10
    assert preview["marked_count"] == 1
    assert preview["purge_count"] == 0
    tight = client.get(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        params={"inactive_after_days": 1, "inactive_purge_after_days": 1},
    ).json()
    assert tight["inactive_after_days"] == 10
    assert tight["marked_count"] == 1
    assert tight["purge_count"] == 1
    rows = {u["id"]: u for u in client.get("/api/users", headers=admin_headers).json()}
    assert rows[ghost]["inactive"] is True
    assert rows[ghost]["days_until_purge"] == days_until_purge(
        db.get_user(ghost)["created_at"], 10, 5
    )
    assert rows[ghost]["days_until_purge"] >= 0

    db.update_user(ghost, telegram_chat_id="1")
    rows = {u["id"]: u for u in client.get("/api/users", headers=admin_headers).json()}
    assert rows[ghost]["inactive"] is False
    assert rows[ghost]["days_until_purge"] is None

    ghost2 = db.add_user("ghost91", "h")
    db._execute("UPDATE users SET created_at = datetime('now', '-12 days') WHERE id = ?", (ghost2,))
    db.add_push_log(0, "telegram", "success", user_id=ghost2)
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == ghost2)["inactive"] is False

    ghost3 = db.add_user("ghost92", "h")
    db._execute("UPDATE users SET created_at = datetime('now', '-12 days') WHERE id = ?", (ghost3,))
    db.touch_last_login(ghost3)
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == ghost3)["inactive"] is False

    rss_only = db.add_user("rssghost", "h")
    kid = db.add_kol("xueqiu", "RSS大V", "rss1")
    db.add_subscription(rss_only, kid)
    db._execute("UPDATE users SET created_at = datetime('now', '-12 days') WHERE id = ?", (rss_only,))
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == rss_only)["inactive"] is False

    admin_id = client.get("/api/me", headers=admin_headers).json()["id"]
    db._execute(
        "UPDATE users SET created_at = datetime('now', '-12 days'), last_login_at = NULL WHERE id = ?",
        (admin_id,),
    )
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == admin_id)["inactive"] is False

    ghost4 = db.add_user("ghost93", "h")
    db._execute("UPDATE users SET created_at = datetime('now', '-12 days') WHERE id = ?", (ghost4,))
    g4_policy = client.put(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        json={"inactive_after_days": 10, "inactive_purge_after_days": 0},
    ).json()
    assert g4_policy["inactive_after_days"] == 10
    assert g4_policy["inactive_purge_after_days"] == 0
    assert g4_policy["customized"] is True
    assert g4_policy["purge_count"] == 0
    g4 = next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == ghost4)
    assert g4["inactive"] is True
    assert g4["days_until_purge"] is None

    assert client.put(
        "/api/admin/inactive-users-policy",
        headers=admin_headers,
        json={"inactive_after_days": 0, "inactive_purge_after_days": 5},
    ).json()["inactive_after_days"] == 0
    assert next(u for u in client.get("/api/users", headers=admin_headers).json() if u["id"] == ghost4)["inactive"] is False


def test_proxy_api_requires_admin():
    client = make_client()
    uh = user_headers(client, "normaluser")
    assert client.get("/api/admin/proxy-pools", headers=uh).status_code == 403
    assert client.get("/api/admin/proxy-routes", headers=uh).status_code == 403


def test_proxy_pool_import_masks_password():
    client = make_client()
    headers = auth_headers(client)
    created = client.post(
        "/api/admin/proxy-pools",
        headers=headers,
        json={"name": "海外", "kind": "static", "protocol": "socks5"},
    )
    assert created.status_code == 200
    pool_id = created.json()["id"]
    imported = client.post(
        f"/api/admin/proxy-pools/{pool_id}/import",
        headers=headers,
        json={"text": "socks5://alice:s3cret@1.2.3.4:1080\n1.2.3.4:1080:alice:s3cret"},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    items = client.get("/api/admin/proxies", headers=headers).json()["items"]
    assert len(items) == 1
    assert items[0]["password"] == "***"
    assert items[0]["username"] == "alice"
    assert items[0]["host"] == "1.2.3.4"
    assert "s3cret" not in json.dumps(items)


def test_proxy_extract_url_query_masked_in_list():
    client = make_client()
    headers = auth_headers(client)
    created = client.post(
        "/api/admin/proxy-pools",
        headers=headers,
        json={
            "name": "提取",
            "kind": "extract",
            "protocol": "http",
            "extract_url": "https://api.example.com/get?key=SUPERSECRET",
            "expire_seconds": 120,
            "refresh_interval_seconds": 60,
        },
    )
    assert created.status_code == 200
    listed = client.get("/api/admin/proxy-pools", headers=headers).json()["items"]
    assert listed[0]["extract_url_set"] is True
    assert "SUPERSECRET" not in listed[0]["extract_url"]
    detail = client.get(f"/api/admin/proxy-pools/{created.json()['id']}", headers=headers).json()
    assert "SUPERSECRET" in detail["extract_url"]


def test_proxy_routes_roundtrip_and_reject_bad_mode():
    client = make_client()
    headers = auth_headers(client)
    pool_id = client.post(
        "/api/admin/proxy-pools",
        headers=headers,
        json={"name": "X池", "kind": "static"},
    ).json()["id"]
    bad = client.put(
        "/api/admin/proxy-routes",
        headers=headers,
        json={"twitter": {"mode": "magic"}},
    )
    assert bad.status_code == 400
    ok = client.put(
        "/api/admin/proxy-routes",
        headers=headers,
        json={"twitter": {"mode": "pool", "pool_id": pool_id}},
    )
    assert ok.status_code == 200
    assert ok.json()["twitter"] == {"mode": "pool", "pool_id": pool_id}
    assert client.get("/api/admin/proxy-routes", headers=headers).json()["twitter"]["mode"] == "pool"


def test_proxy_extract_and_test_endpoints(monkeypatch):
    client = make_client()
    headers = auth_headers(client)
    pool_id = client.post(
        "/api/admin/proxy-pools",
        headers=headers,
        json={
            "name": "提取",
            "kind": "extract",
            "extract_url": "https://api.example.com/get",
            "expire_seconds": 60,
        },
    ).json()["id"]

    class FakeResp:
        text = "9.9.9.9:8080:bob:pw"
        status_code = 204

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url):
            return FakeResp()

        def close(self):
            return None

    monkeypatch.setattr("app.proxy.httpx.Client", FakeClient)
    extracted = client.post(f"/api/admin/proxy-pools/{pool_id}/extract", headers=headers)
    assert extracted.status_code == 200
    assert extracted.json()["imported"] == 1
    items = client.get(f"/api/admin/proxies?pool_id={pool_id}", headers=headers).json()["items"]
    assert items[0]["password"] == "***"
    probed = client.post(f"/api/admin/proxies/{items[0]['id']}/test", headers=headers)
    assert probed.status_code == 200
    assert probed.json()["ok"] is True


def test_zsxq_file_download_ascii_safe_disposition():
    """本地已缓存附件直接读磁盘经 FileResponse 返回；Content-Disposition 用 ASCII 安全 filename*。"""
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(config=None, db_path=tmp / "test.db"))
    state = client.app.state
    kid = state.db.add_kol("zsxq", "星球", "288")
    from app.fetchers.base import Post
    state.db.insert_posts_batch([
        Post(
            platform="zsxq", kol_id=kid, kol_name="星球", external_id="t1",
            title="pdf", content="c", url="https://wx.zsxq.com/group/288/t1",
            published_at="2026-08-20 10:00",
            detail={"files": [{"file_id": "181528458481522", "name": "红书20260820.pdf", "url": ""}]},
        )
    ])
    # 预置本地缓存文件 -> 走 FileResponse，不触发任何网络下载
    (tmp / "zsxq_files").mkdir(parents=True, exist_ok=True)
    (tmp / "zsxq_files" / "181528458481522.pdf").write_bytes(b"%PDF-1.7 data")

    h = auth_headers(client)
    resp = client.get("/api/media/zsxq-file/181528458481522", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.7 data"
    cd = resp.headers.get("content-disposition", "")
    assert "filename*=UTF-8''%E7%BA%A2" in cd

    token = h["Authorization"].replace("Bearer ", "")
    resp2 = client.get(f"/api/media/zsxq-file/181528458481522?token={token}")
    assert resp2.status_code == 200, resp2.text
    assert resp2.content == b"%PDF-1.7 data"


def test_query_token_rejected_on_json_api():
    client = make_client()
    headers = user_headers(client, "qtoken")
    token = headers["Authorization"].replace("Bearer ", "")
    assert client.get(f"/api/me?token={token}").status_code == 401
    assert client.get("/api/me", headers=headers).status_code == 200


def test_wscn_plain_body_strips_tags():
    from app.api import _wscn_plain_body

    assert _wscn_plain_body("<p>第一段</p><p><strong>重点</strong></p>") == "第一段\n重点"


def test_wscn_live_requires_auth():
    client = make_client("wscn_auth.db")
    assert client.get("/api/live/wscn").status_code == 401


def test_wscn_live_returns_normalized_items(monkeypatch):
    client = make_client("wscn_live.db")
    headers = auth_headers(client, "wscnuser", "secret1234")

    def fake_fetch(*, cursor: str = "", limit: int = 30):
        del cursor, limit
        return {
            "items": [{
                "id": 9,
                "score": 3,
                "highlight_title": "",
                "body": "测试快讯",
                "published_at": "2026-08-24T13:00:00+08:00",
                "url": "https://wallstreetcn.com/livenews/9",
            }],
            "next_cursor": "next",
            "polling_cursor": 9,
        }

    monkeypatch.setattr("app.api._fetch_wscn_lives", fake_fetch)
    resp = client.get("/api/live/wscn", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"][0]["score"] == 3
    assert data["polling_cursor"] == 9

    resp2 = client.get("/api/live/wscn?since_id=8", headers=headers)
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1

    resp3 = client.get("/api/live/wscn?since_id=9", headers=headers)
    assert resp3.json()["items"] == []


def test_wscn_live_rejects_freeform_cursor():
    """cursor 进缓存键与上游查询串，必须限定为短数字串（非法请求到不了取数层）。"""
    import time as _time

    from app.api import _WSCN_CACHE, _WSCN_LOCK, _wscn_evict_locked

    client = make_client("wscn_cursor.db")
    headers = auth_headers(client, "wscncur", "secret1234")
    for bad in ("abc", "1;drop", "x" * 17):
        resp = client.get(f"/api/live/wscn?cursor={bad}", headers=headers)
        assert resp.status_code == 400, bad

    # 淘汰：超上限按写入时间丢最旧（时间戳越小越旧）
    with _WSCN_LOCK:
        _WSCN_CACHE.clear()
        for i in range(70):
            _WSCN_CACHE[f"k{i}"] = (_time.time() - i, {})
        _wscn_evict_locked()
        assert len(_WSCN_CACHE) == 64
        assert "k0" in _WSCN_CACHE and "k69" not in _WSCN_CACHE


def test_wscn_fetch_reuses_cache_and_http_client(monkeypatch):
    from app import api as api_mod

    calls = []

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 20000,
                "data": {
                    "items": [{
                        "id": 1,
                        "score": 1,
                        "content_text": "hello",
                        "display_time": 0,
                        "uri": "https://wallstreetcn.com/livenews/1",
                    }],
                    "next_cursor": "",
                    "polling_cursor": 1,
                },
            }

    class FakeClient:
        def get(self, url, params=None):
            calls.append((url, params))
            return FakeResp()

    api_mod._WSCN_CACHE.clear()
    monkeypatch.setattr(api_mod, "_wscn_client", lambda: FakeClient())
    assert api_mod._WSCN_CACHE_TTL == 15
    first = api_mod._fetch_wscn_lives(limit=30)
    second = api_mod._fetch_wscn_lives(limit=30)
    assert first["items"][0]["id"] == 1
    assert second["items"][0]["body"] == "hello"
    assert len(calls) == 1


def test_wscn_serves_stale_cache_while_refreshing(monkeypatch):
    import threading

    from app import api as api_mod

    calls = []
    entered = threading.Event()
    release = threading.Event()

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 20000,
                "data": {
                    "items": [{
                        "id": 1,
                        "score": 1,
                        "content_text": "hello",
                        "display_time": 0,
                        "uri": "https://wallstreetcn.com/livenews/1",
                    }],
                    "next_cursor": "",
                    "polling_cursor": 1,
                },
            }

    class FakeClient:
        def get(self, url, params=None):
            calls.append(1)
            if len(calls) > 1:
                entered.set()
                release.wait(1)
            return FakeResp()

    api_mod._WSCN_CACHE.clear()
    api_mod._WSCN_REFRESHING.clear()
    monkeypatch.setattr(api_mod, "_wscn_client", lambda: FakeClient())
    api_mod._fetch_wscn_lives(limit=30)
    key = ":30"
    ts, data = api_mod._WSCN_CACHE[key]
    api_mod._WSCN_CACHE[key] = (ts - 120, data)
    stale = api_mod._fetch_wscn_lives(limit=30)
    assert stale["items"][0]["id"] == 1
    assert entered.wait(1)
    assert len(calls) == 2
    release.set()


def test_wscn_warmup_fetches_first_page_and_swallows_errors(monkeypatch):
    from app import api as api_mod

    called = []
    monkeypatch.setattr(api_mod, "_fetch_wscn_lives", lambda **kw: called.append(kw) or {"items": []})
    api_mod.warmup_wscn_live()
    assert called == [{"limit": 30}]
    monkeypatch.setattr(api_mod, "_fetch_wscn_lives", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    api_mod.warmup_wscn_live()  # 不抛


def test_wscn_refresh_does_not_hold_lock_during_http(monkeypatch):
    import threading

    from app import api as api_mod

    entered = threading.Event()
    release = threading.Event()

    def slow_load(cursor, limit):
        del cursor, limit
        entered.set()
        release.wait(1)
        return {"items": [], "next_cursor": "", "polling_cursor": 0}

    monkeypatch.setattr(api_mod, "_wscn_load", slow_load)
    api_mod._WSCN_REFRESHING.add(":30")
    t = threading.Thread(target=api_mod._wscn_refresh, args=("", 30, ":30"), daemon=True)
    t.start()
    assert entered.wait(1)
    assert api_mod._WSCN_LOCK.acquire(timeout=0.2)
    api_mod._WSCN_LOCK.release()
    release.set()
    t.join(1)


def test_wscn_refresh_home_skips_when_already_refreshing(monkeypatch):
    from app import api as api_mod

    called = []
    monkeypatch.setattr(api_mod, "_wscn_refresh", lambda *a: called.append(a))
    api_mod._WSCN_REFRESHING.clear()
    api_mod._WSCN_REFRESHING.add(":30")
    api_mod._wscn_refresh_home()
    assert called == []
    api_mod._WSCN_REFRESHING.clear()
    api_mod._wscn_refresh_home()
    assert called == [("", 30, ":30")]


def test_wscn_live_refresh_hooked_in_lifespan():
    src = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "start_wscn_live_refresh" in src
    assert "PYTEST_CURRENT_TEST" in src


def test_start_wscn_live_refresh_warms_then_loops(monkeypatch):
    from app import api as api_mod

    started = []

    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            started.append(target)

        def start(self):
            started.append("start")

    monkeypatch.setattr(api_mod, "warmup_wscn_live", lambda: started.append("warm"))
    monkeypatch.setattr(api_mod.threading, "Thread", FakeThread)
    api_mod.start_wscn_live_refresh()
    assert started == ["warm", api_mod._wscn_home_loop, "start"]


def test_ima_storage_admin_actions_conflict_when_local():
    client = make_client()
    headers = auth_headers(client)
    refresh = client.post("/api/admin/ima-storage/refresh", headers=headers)
    backup = client.post("/api/admin/ima-storage/backup", headers=headers)
    assert refresh.status_code == 409
    assert backup.status_code == 409
    assert "未启用远程归档" in refresh.json()["detail"]


def test_llm_models_lists_openai_compatible_ids(monkeypatch):
    client = make_client()
    headers = user_headers(client, "llm-user")
    monkeypatch.setattr("app.url_safety._resolve_host_ips", lambda host: ["93.184.216.34"])

    class FakeStream:
        status_code = 200
        headers = {"content-type": "application/json"}
        request = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield json.dumps({"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}).encode()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            assert method == "GET"
            assert str(url) == "https://93.184.216.34/v1/models"
            assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
            assert kwargs["headers"]["Host"] == "api.openai.com"
            return FakeStream()

    monkeypatch.setattr("httpx.Client", FakeClient)
    resp = client.post(
        "/api/me/llm-models",
        json={"llm_api_base": "https://api.openai.com/v1", "llm_api_key": "sk-test"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["gpt-4o", "gpt-4o-mini"]


def test_me_llm_test_returns_usage(monkeypatch):
    client = make_client()
    headers = user_headers(client, "llm-test-user")
    monkeypatch.setattr("app.url_safety._resolve_host_ips", lambda host: ["93.184.216.34"])

    def fake_probe(cfg):
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-test"
        assert cfg.api_format == "chat"
        return {
            "ok": True,
            "latency_ms": 12,
            "format": "chat",
            "model": "gpt-test",
            "usage": {"total_tokens": 9},
        }

    monkeypatch.setattr("app.llm.probe_llm", fake_probe)
    resp = client.post(
        "/api/me/llm-test",
        json={
            "llm_api_base": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
            "llm_model": "gpt-test",
            "llm_api_format": "chat",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["usage"]["total_tokens"] == 9


def test_me_saves_llm_api_format():
    client = make_client()
    headers = user_headers(client, "llm-format-user")
    resp = client.put(
        "/api/me",
        json={"llm_api_format": "openai-responses", "llm_model": "gpt-test"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["llm_api_format"] == "responses"
    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["llm_api_format"] == "responses"
    assert me.json()["llm_model"] == "gpt-test"


def test_llm_models_requires_key():
    client = make_client()
    headers = user_headers(client, "llm-empty")
    resp = client.post("/api/me/llm-models", json={}, headers=headers)
    assert resp.status_code == 400


def test_llm_models_rejects_intranet_base():
    client = make_client()
    headers = user_headers(client, "llm-private-user")
    resp = client.post(
        "/api/me/llm-models",
        json={"llm_api_base": "http://127.0.0.1:11434/v1", "llm_api_key": "sk-test"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_admin_can_save_and_list_models_from_trusted_intranet_base(monkeypatch):
    client = make_client()
    headers = auth_headers(client)
    seen = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "local-model"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            seen["url"] = url
            seen["authorization"] = kwargs["headers"]["Authorization"]
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)
    update = client.put(
        "/api/me",
        json={"llm_api_base": "http://127.0.0.1:11434/v1", "llm_api_key": "sk-local"},
        headers=headers,
    )
    response = client.post("/api/me/llm-models", json={}, headers=headers)

    assert update.status_code == 200
    assert response.status_code == 200
    assert response.json() == {"models": ["local-model"]}
    assert seen == {
        "url": "http://127.0.0.1:11434/v1/models",
        "authorization": "Bearer sk-local",
    }


def test_admin_news_feed_crud_archives_without_deleting_articles(monkeypatch):
    client = make_client("admin-news.db")
    headers = auth_headers(client)
    monkeypatch.setattr(client.app.state.news_service, "validate_feed", lambda url: {
        "format": "rss20", "title": "Fixture", "entries": []
    })
    source_response = client.post(
        "/api/admin/news/sources", headers=headers, json={"name": "路透市场"}
    )
    assert source_response.status_code == 200
    source = source_response.json()
    feed_response = client.post(
        f"/api/admin/news/sources/{source['id']}/feeds",
        headers=headers,
        json={"name": "市场", "url": "https://feed.example/rss"},
    )
    assert feed_response.status_code == 200
    feed = feed_response.json()
    assert feed["normalized_url"] == "https://feed.example/rss"
    listing = client.get("/api/admin/news/sources", headers=headers).json()
    listed = next(item for item in listing["items"] if item["id"] == source["id"])
    assert listed["feeds"][0]["id"] == feed["id"]
    assert client.post(
        f"/api/admin/news/feeds/{feed['id']}/archive", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/admin/news/feeds/{feed['id']}/restore", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/admin/news/sources/{source['id']}/archive", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/admin/news/sources/{source['id']}/restore", headers=headers
    ).status_code == 200


def test_admin_news_settings_bounds_and_permission():
    client = make_client("admin-news-settings.db")
    user = user_headers(client, "news_settings_user")
    assert client.get("/api/admin/news/settings", headers=user).status_code == 403
    admin = auth_headers(client)
    settings = client.get("/api/admin/news/settings", headers=admin)
    assert settings.status_code == 200
    assert settings.json() == {"enabled": True, "visible": True, "refresh_interval_minutes": 10}
    assert client.get("/api/me", headers=admin).json()["news_visible"] is True
    assert client.patch(
        "/api/admin/news/settings", headers=admin,
        json={"refresh_interval_minutes": 4},
    ).status_code == 400
    updated = client.patch(
        "/api/admin/news/settings", headers=admin,
        json={"enabled": False, "visible": False, "refresh_interval_minutes": 15},
    )
    assert updated.status_code == 200
    assert updated.json() == {"enabled": False, "visible": False, "refresh_interval_minutes": 15}
    assert client.get("/api/me", headers=admin).json()["news_visible"] is False
    assert client.app.state.db.get_setting("news_enabled") == "0"
    assert client.app.state.db.get_setting("news_visible") == "0"


def test_admin_news_audit_redacts_feed_query(monkeypatch):
    client = make_client("admin-news-audit.db")
    headers = auth_headers(client)
    monkeypatch.setattr(client.app.state.news_service, "validate_feed", lambda url: {
        "format": "atom10", "title": "Fixture", "entries": []
    })
    source_id = client.post(
        "/api/admin/news/sources", headers=headers, json={"name": "审计媒体"}
    ).json()["id"]
    response = client.post(
        f"/api/admin/news/sources/{source_id}/feeds", headers=headers,
        json={"name": "主源", "url": "https://feed.example/rss?secret=value"},
    )
    assert response.status_code == 200
    detail = client.app.state.db.list_admin_logs(10)[0]["detail"]
    assert "secret" not in detail and "value" not in detail


def test_admin_news_refresh_reports_busy_feed_ids(monkeypatch):
    client = make_client("admin-news-refresh.db")
    headers = auth_headers(client)
    calls = []
    monkeypatch.setattr(client.app.state.news_service, "submit_feed", lambda feed_id: calls.append(feed_id) or False)
    response = client.post("/api/admin/news/refresh", headers=headers)
    assert response.status_code == 202
    assert response.json()["accepted_feed_ids"] == []
    assert len(response.json()["busy_feed_ids"]) == 5
    assert len(calls) == 5
