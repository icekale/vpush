export function createAdminNewsView(dependencies) {
  const {
    $,
    state,
    api,
    flash,
    escapeHtml,
    routeStillActive,
    currentRouteSeq,
    currentAdminSeq,
    stopStatsTimer,
    statsTabsHtml,
    emptyState,
    renderSidebar,
    renderTopbar,
    renderBottomNav,
    trapFocus,
    REFRESH_ICON,
    PLUS_ICON,
    PLATFORM_LABELS,
    CHEVRON_UP_ICON,
    CHEVRON_DOWN_ICON,
  } = dependencies;

  const adminNewsState = {
    settings: null,
    sources: [],
    selectedId: 0,
    q: "",
    status: "all",
    showArchived: false,
    busy: false,
  };
  let _adminNewsLoadSeq = 0;

  function adminNewsSourceStatus(source) {
    const feeds = (source.feeds || []).filter((feed) => !feed.archived_at && feed.enabled);
    if (!source.enabled || !feeds.length) return "paused";
    if (feeds.some((feed) => feed.consecutive_failures > 0 && !feed.last_success_at)) return "unavailable";
    if (feeds.some((feed) => feed.consecutive_failures > 0)) return "delayed";
    return "ok";
  }

  function adminNewsStatusLabel(status) {
    return ({ all: "全部", ok: "正常", paused: "已暂停", delayed: "有延迟", unavailable: "暂不可用" })[status] || status;
  }

  function adminNewsFilteredSources() {
    const q = adminNewsState.q.trim().toLowerCase();
    return adminNewsState.sources.filter((source) => {
      if (!adminNewsState.showArchived && source.archived_at) return false;
      if (q && !(source.name || "").toLowerCase().includes(q)) return false;
      return adminNewsState.status === "all" || adminNewsSourceStatus(source) === adminNewsState.status;
    });
  }

  function adminNewsSourceRowHtml(source) {
    const status = adminNewsSourceStatus(source);
    return `<button type="button" class="news-admin-source-row ${source.id === adminNewsState.selectedId ? "is-selected" : ""} ${source.archived_at ? "is-archived" : ""}" onclick="selectAdminNewsSource(${source.id})">
      <span class="news-admin-source-name">${escapeHtml(source.name)}</span>
      <span class="news-admin-source-meta"><span class="news-admin-status news-admin-status-${status}">${adminNewsStatusLabel(status)}</span><span>${source.article_count || 0} 篇</span></span>
    </button>`;
  }

  function adminNewsFeedRowHtml(feed) {
    const status = feed.archived_at ? "已归档" : (feed.enabled ? (feed.consecutive_failures ? "有延迟" : "启用") : "已停用");
    const error = feed.last_error_detail ? `<p class="news-admin-feed-error">${escapeHtml(feed.last_error_detail)}</p>` : "";
    return `<div class="news-admin-feed-row ${feed.archived_at ? "is-archived" : ""}">
      <div class="news-admin-feed-main">
        <div class="news-admin-feed-title"><strong>${escapeHtml(feed.name)}</strong><span class="news-admin-status">${status}</span></div>
        <div class="news-admin-feed-url">${escapeHtml(feed.url)}</div>
        <div class="news-admin-feed-meta">最近成功：${escapeHtml(feed.last_success_at || "无记录")} · 连续失败：${Number(feed.consecutive_failures || 0)}</div>
        ${error}
      </div>
      <div class="news-admin-feed-actions">
        ${feed.archived_at ? `<button type="button" class="btn-ghost" onclick="restoreAdminNewsFeed(${feed.id})">恢复</button>` : `
          <button type="button" class="btn-ghost" onclick="openNewsFeedModal(${feed.source_id}, ${feed.id})">编辑</button>
          <button type="button" class="btn-ghost" onclick="refreshAdminNewsFeed(${feed.id})" title="刷新 Feed" aria-label="刷新 Feed">${REFRESH_ICON}</button>
          <button type="button" class="btn-ghost" onclick="toggleAdminNewsFeed(${feed.id}, ${feed.enabled ? "false" : "true"})">${feed.enabled ? "停用" : "启用"}</button>
          <button type="button" class="btn-ghost danger" onclick="archiveAdminNewsFeed(${feed.id})">归档</button>`}
      </div>
    </div>`;
  }

  function renderAdminNews() {
    const target = $("#admin-body");
    if (!target) return;
    const visible = adminNewsFilteredSources();
    const selected = adminNewsState.sources.find((source) => source.id === adminNewsState.selectedId)
      || visible.find((source) => !source.archived_at)
      || visible[0];
    if (selected && selected.id !== adminNewsState.selectedId) adminNewsState.selectedId = selected.id;
    const detail = selected ? `
      <section class="news-admin-detail-panel">
        <header class="news-admin-detail-head">
          <div><p class="section-kicker">媒体</p><h2 class="section-title">${escapeHtml(selected.name)}</h2>
            <p class="section-meta">${selected.archived_at ? "已归档，用户暂不可读" : (selected.enabled ? "正在采集" : "已停用，仅保留历史文章")}</p></div>
          <div class="toolbar news-admin-actions">
            ${selected.archived_at ? `<button type="button" class="btn-normal" onclick="restoreAdminNewsSource(${selected.id})">恢复媒体</button>
              <button type="button" class="btn-ghost danger" onclick="deleteAdminNewsSource(${selected.id})">彻底删除</button>` : `
              <button type="button" class="btn-ghost" onclick="openNewsSourceModal(${selected.id})">编辑媒体</button>
              <button type="button" class="btn-ghost" onclick="toggleAdminNewsSource(${selected.id}, ${selected.enabled ? "false" : "true"})">${selected.enabled ? "停用采集" : "启用采集"}</button>
              <button type="button" class="btn-ghost danger" onclick="archiveAdminNewsSource(${selected.id})">归档</button>
              <button type="button" class="btn-ghost danger" onclick="deleteAdminNewsSource(${selected.id})">彻底删除</button>`}
          </div>
        </header>
        <div class="news-admin-metrics">
          <span>文章 <strong>${selected.article_count || 0}</strong></span>
          <span>Feed <strong>${(selected.feeds || []).filter((feed) => !feed.archived_at).length}</strong></span>
          <span>状态 <strong>${adminNewsStatusLabel(adminNewsSourceStatus(selected))}</strong></span>
        </div>
        <div class="news-admin-feed-head"><div><h3>Feed 地址</h3><p class="section-meta">一个媒体可以配置多个公网 RSS/Atom Feed。</p></div>
          ${selected.archived_at ? "" : `<button type="button" class="btn-normal" onclick="openNewsFeedModal(${selected.id})">${PLUS_ICON} 添加 Feed</button>`}
        </div>
        <div class="news-admin-feeds">${(selected.feeds || []).length ? selected.feeds.map(adminNewsFeedRowHtml).join("") : emptyState("还没有配置 Feed")}</div>
      </section>` : `<section class="news-admin-detail-panel">${emptyState("选择一个媒体开始管理")}</section>`;
    const settings = adminNewsState.settings || { enabled: true, visible: true, refresh_interval_minutes: 10 };
    const selectedStatus = adminNewsState.status;
    target.innerHTML = `${statsTabsHtml("news")}
      <div id="st-news" class="news-admin-page">
        <section class="section-panel news-admin-settings">
          <div><h2 class="section-title">财经资讯</h2><p class="section-meta">共享采集所有启用 Feed；用户按媒体主动选择来源。</p></div>
          <div class="news-admin-settings-controls">
            <label class="switch"><input id="news-global-enabled" type="checkbox" ${settings.enabled ? "checked" : ""} onchange="saveAdminNewsSettings()"><span class="track"></span><span>启用财经新闻采集</span></label>
            <label class="switch"><input id="news-global-visible" type="checkbox" ${settings.visible ? "checked" : ""} onchange="saveAdminNewsSettings()"><span class="track"></span><span>向用户显示财经新闻</span></label>
            <label class="news-admin-interval">刷新周期 <input id="news-global-interval" class="form-control" type="number" min="5" max="1440" value="${Number(settings.refresh_interval_minutes) || 10}"> 分钟</label>
            <button type="button" class="btn-ghost" onclick="saveAdminNewsSettings()">保存设置</button>
            <button type="button" class="btn-normal" onclick="refreshAllAdminNews()">${REFRESH_ICON} 刷新全部</button>
          </div>
        </section>
        <div class="news-admin-layout">
          <aside class="news-admin-source-rail">
            <div class="news-admin-source-toolbar"><input class="form-control" type="search" placeholder="搜索媒体" value="${escapeHtml(adminNewsState.q)}" oninput="updateAdminNewsQuery(this.value)">
              <select class="form-control" aria-label="媒体状态" onchange="updateAdminNewsStatus(this.value)">
                ${["all", "ok", "paused", "delayed", "unavailable"].map((status) => `<option value="${status}" ${selectedStatus === status ? "selected" : ""}>${adminNewsStatusLabel(status)}</option>`).join("")}
              </select>
              <label class="news-admin-archived-toggle"><input type="checkbox" ${adminNewsState.showArchived ? "checked" : ""} onchange="updateAdminNewsArchived(this.checked)"> 显示已归档</label>
            </div>
            <button type="button" class="btn-normal news-admin-add-source" onclick="openNewsSourceModal()">${PLUS_ICON} 新增媒体</button>
            <div class="news-admin-source-list">${visible.length ? visible.map(adminNewsSourceRowHtml).join("") : emptyState("没有匹配的媒体")}</div>
          </aside>
          ${detail}
        </div>
      </div>`;
  }

  async function loadAdminNews(seq = currentRouteSeq()) {
    if (!routeStillActive(seq)) return false;
    const loadSeq = ++_adminNewsLoadSeq;
    stopStatsTimer();
    try {
      const [settings, sources] = await Promise.all([
        api("/api/admin/news/settings"),
        api(`/api/admin/news/sources?include_archived=${adminNewsState.showArchived ? "1" : "0"}`),
      ]);
      if (!routeStillActive(seq) || loadSeq !== _adminNewsLoadSeq) return false;
      adminNewsState.settings = settings;
      state.newsVisible = settings.visible !== false;
      adminNewsState.sources = sources.items || [];
      if (!adminNewsState.sources.some((source) => source.id === adminNewsState.selectedId)) adminNewsState.selectedId = 0;
      renderSidebar(state.user);
      renderTopbar(state.user);
      renderBottomNav(state.user);
      renderAdminNews();
      return true;
    } catch (err) {
      if (!routeStillActive(seq) || loadSeq !== _adminNewsLoadSeq) return false;
      $("#admin-body").innerHTML = emptyState("加载失败: " + err.message, `<div><button type="button" class="btn-normal" onclick="loadAdminNews()">重试</button></div>`);
      return false;
    }
  }

  function selectAdminNewsSource(sourceId) {
    adminNewsState.selectedId = Number(sourceId);
    renderAdminNews();
  }

  async function saveAdminNewsSettings() {
    const seq = currentRouteSeq();
    const enabled = $("#news-global-enabled")?.checked;
    const visible = $("#news-global-visible")?.checked;
    const interval = Number($("#news-global-interval")?.value);
    if (!Number.isInteger(interval) || interval < 5 || interval > 1440) {
      flash("刷新周期必须为 5-1440 分钟", "error");
      return;
    }
    try {
      await api("/api/admin/news/settings", { method: "PATCH", body: JSON.stringify({ enabled, visible, refresh_interval_minutes: interval }) });
      if (!routeStillActive(seq)) return;
      flash("财经新闻设置已保存");
      await loadAdminNews(seq);
    } catch (err) {
      flash(err.message, "error");
    }
  }

  async function refreshAllAdminNews() {
    const seq = currentRouteSeq();
    try {
      const result = await api("/api/admin/news/refresh", { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash(`已提交 ${result.accepted_feed_ids.length} 个 Feed，忙碌 ${result.busy_feed_ids.length} 个`);
      await loadAdminNews(seq);
    } catch (err) {
      flash(err.message, "error");
    }
  }

  async function refreshAdminNewsFeed(feedId) {
    const seq = currentRouteSeq();
    try {
      await api(`/api/admin/news/feeds/${feedId}/refresh`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash("Feed 刷新已提交");
      await loadAdminNews(seq);
    } catch (err) {
      flash(err.message, "error");
    }
  }

  async function refreshAdminNewsSource(sourceId) {
    const seq = currentRouteSeq();
    try {
      const result = await api(`/api/admin/news/sources/${sourceId}/refresh`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash(`已提交 ${result.accepted_feed_ids.length} 个 Feed`);
      await loadAdminNews(seq);
    } catch (err) {
      flash(err.message, "error");
    }
  }

  async function toggleAdminNewsSource(sourceId, enabled) {
    const seq = currentRouteSeq();
    try {
      await api(`/api/admin/news/sources/${sourceId}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      if (!routeStillActive(seq)) return;
      flash(enabled ? "媒体已启用" : "媒体已停用");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function toggleAdminNewsFeed(feedId, enabled) {
    const seq = currentRouteSeq();
    try {
      await api(`/api/admin/news/feeds/${feedId}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      if (!routeStillActive(seq)) return;
      flash(enabled ? "Feed 已启用" : "Feed 已停用");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function archiveAdminNewsSource(sourceId) {
    const seq = currentRouteSeq();
    if (!confirm("归档媒体后将停止采集并对用户隐藏，确认继续？")) return;
    try {
      await api(`/api/admin/news/sources/${sourceId}/archive`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash("媒体已归档");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function restoreAdminNewsSource(sourceId) {
    const seq = currentRouteSeq();
    try {
      await api(`/api/admin/news/sources/${sourceId}/restore`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash("媒体已恢复");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function deleteAdminNewsSource(sourceId) {
    const seq = currentRouteSeq();
    if (!confirm("彻底删除将同时清除该媒体的 Feed、全部历史文章与用户订阅记录，且不可恢复，确认继续？")) return;
    try {
      await api(`/api/admin/news/sources/${sourceId}`, { method: "DELETE" });
      if (!routeStillActive(seq)) return;
      adminNewsState.selectedId = null;
      flash("媒体已彻底删除");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function archiveAdminNewsFeed(feedId) {
    const seq = currentRouteSeq();
    if (!confirm("归档 Feed 后将停止采集，确认继续？")) return;
    try {
      await api(`/api/admin/news/feeds/${feedId}/archive`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash("Feed 已归档");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  async function restoreAdminNewsFeed(feedId) {
    const seq = currentRouteSeq();
    try {
      await api(`/api/admin/news/feeds/${feedId}/restore`, { method: "POST" });
      if (!routeStillActive(seq)) return;
      flash("Feed 已恢复");
      await loadAdminNews(seq);
    } catch (err) { flash(err.message, "error"); }
  }

  function closeNewsModal(mask) {
    if (mask) mask.remove();
    else document.querySelector(".news-admin-modal")?.remove();
  }

  function openNewsSourceModal(sourceId = 0) {
    const source = adminNewsState.sources.find((item) => item.id === Number(sourceId));
    const mask = document.createElement("div");
    mask.className = "modal-mask news-admin-modal";
    mask.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="news-source-modal-title">
      <h3 id="news-source-modal-title">${source ? "编辑媒体" : "新增媒体"}</h3>
      <label class="form-label">媒体名称<input id="news-source-name" class="form-control" maxlength="60" value="${escapeHtml(source?.name || "")}"></label>
      <p class="muted">新增媒体不会自动加入任何用户的新闻流；启用全文采集前请确认内容许可。</p>
      <div class="toolbar"><button type="button" class="btn-normal" id="news-source-save">保存</button><button type="button" class="btn-ghost" data-close>取消</button></div>
    </div>`;
    document.body.appendChild(mask);
    const close = () => closeNewsModal(mask);
    trapFocus(mask, close);
    mask.addEventListener("click", (event) => { if (event.target === mask) close(); });
    mask.querySelector("[data-close]").addEventListener("click", close);
    mask.querySelector("#news-source-save").addEventListener("click", async () => {
      const name = mask.querySelector("#news-source-name").value.trim();
      if (!name) { flash("媒体名称不能为空", "error"); return; }
      const seq = currentRouteSeq();
      const button = mask.querySelector("#news-source-save");
      button.disabled = true;
      try {
        await api(source ? `/api/admin/news/sources/${source.id}` : "/api/admin/news/sources", {
          method: source ? "PATCH" : "POST", body: JSON.stringify({ name }),
        });
        if (!routeStillActive(seq)) return;
        close();
        flash("媒体已保存");
        await loadAdminNews(seq);
      } catch (err) { flash(err.message, "error"); button.disabled = false; }
    });
    mask.querySelector("#news-source-name").focus();
  }

  function openNewsFeedModal(sourceId, feedId = 0) {
    const source = adminNewsState.sources.find((item) => item.id === Number(sourceId));
    if (!source) return;
    const feed = (source.feeds || []).find((item) => item.id === Number(feedId));
    const mask = document.createElement("div");
    mask.className = "modal-mask news-admin-modal";
    mask.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="news-feed-modal-title">
      <h3 id="news-feed-modal-title">${feed ? "编辑 Feed" : "新增 Feed"}</h3>
      <label class="form-label">Feed 名称<input id="news-feed-name" class="form-control" maxlength="80" value="${escapeHtml(feed?.name || "")}"></label>
      <label class="form-label">公网 RSS/Atom URL<input id="news-feed-url" class="form-control" maxlength="2048" type="url" value="${escapeHtml(feed?.url || "")}" placeholder="https://example.com/feed.xml"></label>
      <p class="muted">只接受公网 HTTP(S) Feed；保存前会使用正式 SSRF 防护验证。管理员须确认全文内容许可。</p>
      <div id="news-feed-preview" class="news-admin-preview" aria-live="polite"></div>
      <div class="toolbar"><button type="button" class="btn-normal" id="news-feed-save">验证并保存</button><button type="button" class="btn-ghost" data-close>取消</button></div>
    </div>`;
    document.body.appendChild(mask);
    const close = () => closeNewsModal(mask);
    trapFocus(mask, close);
    mask.addEventListener("click", (event) => { if (event.target === mask) close(); });
    mask.querySelector("[data-close]").addEventListener("click", close);
    mask.querySelector("#news-feed-save").addEventListener("click", () => validateNewsFeedDraft(source.id, feed?.id || 0, mask));
    mask.querySelector("#news-feed-name").focus();
  }

  async function validateNewsFeedDraft(sourceId, feedId = 0, mask = document.querySelector(".news-admin-modal")) {
    if (!mask) return;
    const name = mask.querySelector("#news-feed-name").value.trim();
    const url = mask.querySelector("#news-feed-url").value.trim();
    const button = mask.querySelector("#news-feed-save");
    const preview = mask.querySelector("#news-feed-preview");
    if (!name || !url) { flash("请填写 Feed 名称和 URL", "error"); return; }
    button.disabled = true;
    try {
      const validation = await api("/api/admin/news/feeds/validate", { method: "POST", body: JSON.stringify({ url }) });
      preview.innerHTML = `<p><strong>${escapeHtml(validation.format)}</strong> · ${escapeHtml(validation.title || "无标题")}</p>${(validation.entries || []).map((entry) => `<p>${escapeHtml(entry.title)} · ${escapeHtml(entry.published_at || "")}<br>${escapeHtml(entry.text || "暂无纯文本预览")}</p>`).join("") || "<p>有效 Feed，暂无条目</p>"}`;
      const feed = adminNewsState.sources.flatMap((source) => source.feeds || []).find((item) => item.id === Number(feedId));
      await api(feedId ? `/api/admin/news/feeds/${feedId}` : `/api/admin/news/sources/${sourceId}/feeds`, {
        method: feedId ? "PATCH" : "POST", body: JSON.stringify(feedId ? { name, url } : { name, url }),
      });
      if (!routeStillActive(currentRouteSeq())) return;
      closeNewsModal(mask);
      flash("Feed 已验证并保存");
      await loadAdminNews(currentRouteSeq());
    } catch (err) { flash(err.message, "error"); button.disabled = false; }
  }

  let _adminPostsSeq = 0;
  const _adminPosts = [];
  let _adminPostsOffset = 0;
  let _adminPostsHasMore = true;
  const _adminPostsExpanded = new Set();
  let _adminKolsOptions = null;

  async function _adminKolsSelect() {
    // 大V下拉选项（按平台分组），只拉一次缓存；选中态不烤进 HTML，由 renderAdminPosts 按状态回填
    if (_adminKolsOptions) return _adminKolsOptions;
    const kols = await api("/api/kols");
    const groups = {};
    for (const k of kols) {
      const g = PLATFORM_LABELS[k.platform] || k.platform || "其他";
      (groups[g] = groups[g] || []).push(k);
    }
    _adminKolsOptions = Object.entries(groups)
      .map(([g, list]) => `<optgroup label="${escapeHtml(g)}">${list.map((k) =>
        `<option value="${k.id}">${escapeHtml(k.name)}</option>`).join("")}</optgroup>`)
      .join("");
    return _adminKolsOptions;
  }

  function renderAdminPosts() {
    const kolsHtml = `<option value="">全部大V</option>` + (_adminKolsOptions || "");
    if (!routeStillActive(currentAdminSeq())) return;
    $("#admin-body").innerHTML = `
      <section class="section-panel">
        <header class="section-head">
          <div><h2 class="section-title">帖子列表</h2><p class="section-meta">已加载 ${_adminPosts.length} 条 · 点击内容展开全文 · 按大V/平台/关键词筛选</p></div>
          <div class="toolbar" style="margin-top:12px">
            <input id="ad-posts-q" class="form-control" style="margin:0;width:240px" placeholder="搜索标题/内容关键词" value="${escapeHtml(state.adminPostsQ || "")}" onkeydown="if(event.key==='Enter')adminFilterPosts()">
            <select id="ad-posts-platform" class="form-control" style="margin:0;width:auto" onchange="adminFilterPosts()">
              <option value="">全部平台</option>
              <option value="xueqiu" ${state.adminPostsPlatform === "xueqiu" ? "selected" : ""}>雪球</option>
              <option value="weibo" ${state.adminPostsPlatform === "weibo" ? "selected" : ""}>微博</option>
              <option value="twitter" ${state.adminPostsPlatform === "twitter" ? "selected" : ""}>X</option>
              <option value="zsxq" ${state.adminPostsPlatform === "zsxq" ? "selected" : ""}>知识星球</option>
            </select>
            <select id="ad-posts-kol" class="form-control" style="margin:0;width:auto" onchange="adminFilterPosts()">${kolsHtml}</select>
            <button class="btn-normal" onclick="adminFilterPosts()">筛选</button>
          </div>
        </header>
        <div class="table-wrap">
          <table>
            <thead><tr><th scope="col">ID</th><th scope="col">大V</th><th scope="col">分类</th><th scope="col">内容</th><th scope="col">时间</th><th scope="col">链接</th></tr></thead>
            <tbody>${_adminPosts.map(postRowHtml).join("")}</tbody>
          </table>
        </div>
        ${_adminPostsHasMore
          ? `<div class="toolbar" style="margin-top:14px;justify-content:center"><button class="btn-normal" onclick="adminPostsLoadMore()">加载更多</button></div>`
          : `<p class="muted" style="text-align:center;margin-top:14px">已加载全部</p>`}
      </section>`;
    const kolSelect = $("#ad-posts-kol");
    if (kolSelect) kolSelect.value = state.adminPostsKolId ? String(state.adminPostsKolId) : "";
  }

  function postRowHtml(p) {
    const expanded = _adminPostsExpanded.has(p.id);
    const body = (p.title ? p.title + "\n" : "") + (p.content || "");
    const safeUrl = /^https?:\/\//i.test(p.url || "") ? p.url : "";
    return `
      <tr${expanded ? ' style="background:var(--color-surface-accent-soft)"' : ""}>
        <td>${p.id}</td><td>${escapeHtml(p.kol_name)}</td>
        <td>${escapeHtml(p.category_name || "")}</td>
        <td class="post-cell" onclick="adminTogglePost(${p.id})" title="点击展开/收起全文" role="button" tabindex="0" aria-expanded="${expanded}" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();adminTogglePost(${p.id})}">
          <pre class="content-cell">${escapeHtml(body.slice(0, expanded ? 100000 : 120))}</pre>
          <span class="muted">${expanded ? `${CHEVRON_UP_ICON} 收起` : (body.length > 120 ? `${CHEVRON_DOWN_ICON} 展开全文` : "")}</span>
        </td>
        <td>${escapeHtml(p.published_at)}</td>
        <td>${safeUrl ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener">原文</a>` : ""}</td>
      </tr>
      ${expanded ? `<tr><td colspan="6"><div class="post-detail">
          <p class="muted" style="margin-bottom:8px">类型：${p.post_type === "reply" ? "回复" : "原帖"} · 平台：${escapeHtml(p.platform)} · 外部ID：${escapeHtml(p.external_id)} · 图片：${(p.images || []).length} 张</p>
          <pre class="content-cell">${escapeHtml(body)}</pre>
        </div></td></tr>` : ""}`;
  }

  async function loadAdminPosts(reset = true) {
    const seq = ++_adminPostsSeq;
    const params = new URLSearchParams({ limit: "100", offset: String(reset ? 0 : _adminPostsOffset) });
    if (state.adminPostsQ) params.set("q", state.adminPostsQ);
    if (state.adminPostsPlatform) params.set("platform", state.adminPostsPlatform);
    if (state.adminPostsKolId) params.set("kol_id", state.adminPostsKolId);
    const [posts, kolsHtml] = await Promise.all([api(`/api/posts?${params}`), _adminKolsSelect()]);
    if (seq !== _adminPostsSeq) return; // 筛选条件已变，丢弃过期响应
    if (reset) {
      _adminPosts.length = 0;
      _adminPostsOffset = 0;
      _adminPostsHasMore = true;
    }
    _adminPosts.push(...posts);
    _adminPostsOffset += posts.length;
    _adminPostsHasMore = posts.length >= 100;
    _adminKolsOptions = kolsHtml;
    renderAdminPosts();
  }

  function adminPostsLoadMore() {
    loadAdminPosts(false);
  }

  function adminTogglePost(id) {
    if (_adminPostsExpanded.has(id)) _adminPostsExpanded.delete(id);
    else _adminPostsExpanded.add(id);
    renderAdminPosts();
  }

  async function adminFilterPosts() {
    state.adminPostsQ = $("#ad-posts-q").value.trim();
    state.adminPostsPlatform = $("#ad-posts-platform").value;
    state.adminPostsKolId = $("#ad-posts-kol").value;
    loadAdminPosts(true);
  }

  function updateAdminNewsQuery(query) {
    adminNewsState.q = query;
    renderAdminNews();
  }

  function updateAdminNewsStatus(status) {
    adminNewsState.status = status;
    renderAdminNews();
  }

  function updateAdminNewsArchived(showArchived) {
    adminNewsState.showArchived = showArchived;
    loadAdminNews();
  }
  return {
    loadAdminNews,
    loadAdminPosts,
    selectAdminNewsSource,
    saveAdminNewsSettings,
    refreshAllAdminNews,
    refreshAdminNewsFeed,
    toggleAdminNewsSource,
    toggleAdminNewsFeed,
    archiveAdminNewsSource,
    restoreAdminNewsSource,
    deleteAdminNewsSource,
    archiveAdminNewsFeed,
    restoreAdminNewsFeed,
    openNewsSourceModal,
    openNewsFeedModal,
    updateAdminNewsQuery,
    updateAdminNewsStatus,
    updateAdminNewsArchived,
    adminFilterPosts,
    adminPostsLoadMore,
    adminTogglePost,
  };
}
