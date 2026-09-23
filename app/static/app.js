import { escapeHtml, imgOnError, imgProxyUrl, imgSrcFor, jsString } from "./core/html.js";
import {
  ARROW_UP_ICON, BELL_ICON, BELL_OFF_ICON, BOOK_ICON, CHECK_CHECK_ICON, CHECK_ICON, CHEVRON_DOWN_ICON, CHEVRON_LEFT_ICON, CHEVRON_RIGHT_ICON,
  CHEVRON_UP_ICON, COPY_ICON, DATABASE_ICON, DASHBOARD_ICON, FOLDER_ICON,
  EYE_ICON, EYE_OFF_ICON, EXTERNAL_LINK_ICON, FEISHU_DATE_ICON, FILE_TEXT_ICON, FILTER_ICON,
  IMAGE_CARD_ICON,
  GEAR_ICON, GITHUB_ICON, GRID_ICON, HISTORY_ICON, HOME_ICON, KEY_ICON, LIST_ICON,
  MORE_ICON, NEWS_ICON, PAPERCLIP_ICON, PLUS_ICON, REFRESH_ICON, SEARCH_ICON, SEND_ICON, STAR_SVG,
  THEME_AUTO_ICON, THEME_MOON_ICON, THEME_SUN_ICON, TRASH_ICON, USER_ICON, USER_PLUS_ICON, USERS_ICON,
  V_ICON, WSCN_LIVE_ICON, X_ICON,
} from "./core/icons.js";
import { trapFocus } from "./core/dialog.js";
import {
  PLATFORM_ICONS as PLATFORM_ICONS_CONFIG,
  PLATFORM_LABELS as PLATFORM_LABELS_CONFIG,
  PLATFORM_SHORT_LABELS as PLATFORM_SHORT_LABELS_CONFIG,
  PLATFORM_TABS as PLATFORM_TABS_CONFIG,
} from "./core/platforms.js";
import { createLightbox } from "./core/lightbox.js";
import { createNewsView } from "./views/news.js";
import { createImaView } from "./views/ima.js";
import { createFeishuPersonalView } from "./views/feishu-personal.js";
import { createPushSettingsView } from "./views/push-settings.js";
import { createMarketView } from "./views/market.js";
import { createPostCardExport } from "./views/post-card-export.js";

const $ = (sel) => document.querySelector(sel);
$("#btn-back").innerHTML = CHEVRON_LEFT_ICON;

const { openLightbox, closeLightbox, lightboxStep } = createLightbox({
  escapeHtml,
  imgOnError,
  trapFocus,
  CHEVRON_LEFT_ICON,
  CHEVRON_RIGHT_ICON,
  X_ICON,
});

const PLATFORM_LABELS = { ...PLATFORM_LABELS_CONFIG, ima: "ima" };
const PLATFORM_SHORT_LABELS = { ...PLATFORM_SHORT_LABELS_CONFIG, ima: "ima" };
function platformShortLabel(p) {
  return p ? (PLATFORM_SHORT_LABELS[p] || PLATFORM_LABELS[p]) : "全部";
}
const PLATFORM_ICONS = PLATFORM_ICONS_CONFIG;
const CHANNEL_ICONS = {
  telegram: `<svg class="ch-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>`,
  feishu: `<svg class="ch-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5c-2 3.4-4.6 5.4-8.8 6.2 4.2.8 6.8 2.8 8.8 6.2 2-3.4 4.6-5.4 8.8-6.2-4.2-.8-6.8-2.8-8.8-6.2z"/></svg>`,
  wecom: `<svg class="ch-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 4c-4.42 0-8 3.02-8 6.75 0 2.13 1.22 4.02 3.12 5.26L6.2 19.5l3.66-1.83c.68.15 1.4.24 2.14.24 4.42 0 8-3.02 8-6.75S16.42 4 12 4z"/></svg>`,
  bark: `<svg class="ch-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>`,
  webpush: `<svg class="ch-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm0 2v8h16V5H4zm4 13h8v2H8v-2z"/></svg>`,
};
const GROK_TRANSLATE_ICON = `<svg class="p-tr-grok" viewBox="0 0 33 32" fill="currentColor" aria-hidden="true"><path d="M12.745 20.54l10.97-8.19c.539-.4 1.307-.244 1.564.38 1.349 3.288.746 7.241-1.938 9.955-2.683 2.714-6.417 3.31-9.83 1.954l-3.728 1.745c5.347 3.697 11.84 2.782 15.898-1.324 3.219-3.255 4.216-7.692 3.284-11.693l.008.009c-1.351-5.878.332-8.227 3.782-13.031L33 0l-4.54 4.59v-.014L12.743 20.544m-2.263 1.987c-3.837-3.707-3.175-9.446.1-12.755 2.42-2.449 6.388-3.448 9.852-1.979l3.72-1.737c-.67-.49-1.53-1.017-2.515-1.387-4.455-1.854-9.789-.931-13.41 2.728-3.483 3.523-4.579 8.94-2.697 13.561 1.405 3.454-.899 5.898-3.22 8.364C1.49 30.2.666 31.074 0 32l10.478-9.466"/></svg>`;
const CHANNEL_LABELS = { telegram: "Telegram", feishu: "飞书", wecom: "企业微信", bark: "Bark", webpush: "浏览器通知" };
const USER_CHANNEL_KEYS = ["telegram", "feishu", "wecom", "bark", "webpush"];
const APP_VERSION = "1.12.256";
const KEYWORDS_MAX_COUNT = 20;
const REPORT_WATCH_BLOCKED_TAGS = new Set([
  "中金研报", "宏观经济", "市场策略", "全球研究", "行业研究", "公司研究",
  "量化及ESG", "大宗商品", "外汇研究", "固定收益", "中金研究院", "其他",
]);
const TL_SOURCE_KEY = "timelineSource";
const PLATFORM_TABS = PLATFORM_TABS_CONFIG;
const STATS_TABS = ["config", "cookies", "imgbed", "plaza", "news", "proxies"];
const STALE_KOL_LIMIT = 10;
const STALE_KOL_HOURS = 48;
const TL_PLATFORMS = PLATFORM_TABS.map((p) => [p, p ? PLATFORM_LABELS[p] : "全部"]);
const state = {
  token: localStorage.getItem("dav_token") || "",
  user: null,
  catalog: [],
  platform: "",
  settingsTab: "push",
  newsSources: [],
  newsVisible: true,
  newsFilterSourceId: "",
  newsQuery: "",
  newsItems: [],
  newsOffset: 0,
  newsHasMore: false,
  newsRequestSeq: 0,
  newsObserver: null,
  newsImageUrls: new Map(),
  newsListKey: "",
  newsScrollY: 0,
  newsCollectionEnabled: true,
  newsUnreadOnly: false,
  newsUnreadCount: 0,
  newsTopic: "",
  newsProgressHandler: null,
  adminKolsPlatform: "",
  adminKols: [],
  adminKolsQ: "",
  adminKolsCategory: "",
  adminKolsStatus: "",
  adminKolsPage: 0,
  adminKolsTotal: 0,
  adminUsers: [],
  adminUsersQ: "",
  adminUsersFilter: "all",
  inactivePolicy: { inactive_after_days: 90, inactive_purge_after_days: 30, customized: false },
  homeQ: "",
  homeCategory: "",
  homeSubscribed: false,
  homeFavorite: false,
  timelineFavorite: false,
  timelineSecondary: false,
  timelinePlatform: "",
  timelineCategory: "",
  timelineTag: "",
  timelineQ: "",
  liveImportant: false,
  liveQ: "",
  timelineSource: (() => {
    try {
      const v = sessionStorage.getItem(TL_SOURCE_KEY);
      return v === "live" ? "live" : "kol";
    } catch {
      return "kol";
    }
  })(),
  imaDocumentsQuery: "",
  imaDocumentsDay: "",
  imaDocumentsDays: [],
  imaDocumentsHasMore: false,
  imaDocumentsGroup: "",
  imaDocumentsTag: "",
  imaCatalogSubscribed: [],
  imaCatalogAvailable: [],
  pageBackRoute: "",
};

const imaMountState = {
  groups: [],
  selectedGroupId: "",
  folderPanelGroupId: "",
  folderPanelTouched: false,
  drafts: new Map(),
  folders: new Map(),
  parents: new Map(),
  expanded: new Set(),
  loading: new Set(),
  errors: new Map(),
  folderRequests: new Map(),
  discoveryBusy: false,
  discoverySeq: 0,
  discoveryOwner: null,
  discoveryEntered: false,
  dirty: false,
  revision: 0,
  collectorDraft: null,
  collectorDraftRevision: "",
  collectorDirty: false,
  collectorRevision: 0,
  collectorConfirmedRevision: "",
  collectorConfirmedLiveRevision: -1,
  collectorConfirmedMountRevision: -1,
  saveOwner: null,
  requestSeq: 0,
  sessionGeneration: 0,
  generation: 0,
};

const imaCollectorPureCache = {
  uid: "",
  knowledge_base_id: "",
  root_folder_id: "",
  interval_seconds: 3600,
};

let _toastTimer = null;
// 操作反馈统一走 toast：成功 flash(msg)，失败 flash(msg, "error")。绑定码等需停留的内容仍写在页面上。
function flash(message, type = "success") {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.setAttribute("aria-live", "polite"); // 操作反馈对读屏可感知
    document.body.appendChild(el);
  }
  el.className = `toast ${type}`;
  el.textContent = message;
  el.classList.remove("hide");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    el.classList.add("hide");
    setTimeout(() => el.remove(), 320);
  }, 2600);
}

async function api(path, options = {}) {
  const requestToken = state.token;
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
  const resp = await fetch(path, { ...options, headers });
  // 登录/注册的 401 是「凭据错误」业务响应：透出后端 detail，不清会话
  if (resp.status === 401 && !path.startsWith("/api/auth/") && state.token === requestToken) {
    logout();
    throw new Error("登录已过期，请重新登录");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    // FastAPI 422 等校验错误的 detail 是数组/对象，直接拼接会显示 [object Object]
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : resp.statusText);
    throw new Error(msg);
  }
  return data;
}

async function apiBlob(path, options = {}) {
  const requestToken = state.token;
  const headers = { ...(options.headers || {}) };
  if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401 && !path.startsWith("/api/auth/") && state.token === requestToken) {
    logout();
    throw new Error("登录已过期，请重新登录");
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : resp.statusText);
    throw new Error(msg);
  }
  return resp.blob();
}

function revokeFeishuTimelineMediaUrls() {
  document.querySelectorAll("img[data-feishu-asset]").forEach((img) => {
    if (String(img.src || "").startsWith("blob:")) img.removeAttribute("src");
  });
  for (const url of _feishuTimelineMediaUrls) URL.revokeObjectURL(url);
  _feishuTimelineMediaUrls = [];
  _feishuTimelineMediaCache.clear();
}

function resetFeishuTimelineMedia() {
  _feishuMoreObserver?.disconnect();
  _feishuMoreObserver = null;
  _feishuAssetObserver?.disconnect();
  _feishuAssetObserver = null;
  clearTimeout(_feishuTimelineTimer);
  _feishuTimelineTimer = null;
  revokeFeishuTimelineMediaUrls();
  _feishuTimelineState = null;
}

function isStandalonePwa() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

function clearSessionCaches() {
  stopMarketQuotes();
  if (ciccView) ciccView.reset();
  clearImaPdfUrl();
  if (typeof stopTimelinePoll === "function") stopTimelinePoll();
  // 飞书扫码轮询不能跨会话存活：登出后它会每秒拿旧 token 打 401 循环
  if (typeof stopFeishuPersonalPoll === "function") stopFeishuPersonalPoll();
  fsPersonalState.owner = null;
  fsPersonalState.sessionId = "";
  fsPersonalState.bindCommand = "";
  fsPersonalState.bindExpiresAt = 0;
  fsPersonalState.verificationUri = "";
  fsPersonalState.qrUri = "";
  fsPersonalState.refreshInFlight = false;
  if (typeof wbQrTimer !== "undefined") {
    if (wbQrTimer) clearTimeout(wbQrTimer);
    wbQrTimer = null;
    wbQrSeq += 1;
  }
  clearNewsReaderState();
  _tlPosts.length = 0;
  _kolPosts.length = 0;
  _tlOffset = 0;
  _tlHasMore = true;
  _tlExpanded.clear();
  _tlShowSrc.clear();
  _tlLatestId = 0;
  _tlLoadedFilter = null;
  _tlSavedScrollY = 0;
  _tlPendingNew.length = 0;
  _tlPendingLatestId = 0;
  feishuPersonalView.pendingBind = null;
  state.timelineQ = "";
  state.timelineCategory = "";
  state.timelineTag = "";
  state.timelinePlatform = "";
  state.timelineFavorite = false;
  state.timelineSecondary = false;
  state.liveImportant = false;
  state.liveQ = "";
  if (typeof _livePosts !== "undefined") {
    _livePosts.length = 0;
    _liveCursor = "";
    _liveLatestId = 0;
    _livePendingNew.length = 0;
    _livePendingLatestId = 0;
    _liveInflight = null;
  }
  imaMountState.groups = [];
  imaMountState.selectedGroupId = "";
  imaMountState.drafts = new Map();
  imaMountState.folders = new Map();
  imaMountState.parents = new Map();
  imaMountState.expanded = new Set();
  imaMountState.loading = new Set();
  imaMountState.errors = new Map();
  imaMountState.folderRequests = new Map();
  imaMountState.discoveryBusy = false;
  imaMountState.discoverySeq += 1;
  imaMountState.discoveryOwner = null;
  imaMountState.discoveryEntered = false;
  imaMountState.dirty = false;
  imaMountState.revision += 1;
  imaMountState.collectorDraft = null;
  imaMountState.collectorDraftRevision = "";
  imaMountState.collectorDirty = false;
  imaMountState.collectorRevision = 0;
  imaMountState.collectorConfirmedRevision = "";
  imaMountState.collectorConfirmedLiveRevision = -1;
  imaMountState.collectorConfirmedMountRevision = -1;
  imaMountState.saveOwner = null;
  imaMountState.requestSeq += 1;
  imaMountState.sessionGeneration += 1;
  imaMountState.generation += 1;
  _lastAdminStatsSnapshot = null;
}

async function logout() {
  const token = state.token;
  try {
    if (token) await api("/api/auth/logout", { method: "POST" });
  } catch {
    /* 本地清会话优先，服务端失效失败不挡住登出 */
  }
  state.token = "";
  state.user = null;
  localStorage.removeItem("dav_token");
  clearSessionCaches();
  replaceRoute("timeline");
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  resetAuthButtons();
}

function resetAuthButtons() {
  // 登录/注册提交中按钮会 disabled；登出或切换模式后恢复默认态
  const loginBtn = $("#login-form")?.querySelector('button[type="submit"]');
  const regBtn = $("#register-form")?.querySelector('button[type="submit"]');
  if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = "登 录"; }
  if (regBtn) { regBtn.disabled = false; regBtn.textContent = "创建账号"; }
}

const USERNAME_RE = /^[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\-\u4e00-\u9fff]{5,29}$/;
const USERNAME_CHARSET_MSG = "用户名仅限中文、字母、数字、下划线和连字符，须以中文或字母开头";

function usernameRuleError(name) {
  const trimmed = (name || "").trim();
  if (trimmed.length < 6) return "用户名至少6位";
  if (trimmed.length > 30) return "用户名最长30位";
  if (!USERNAME_RE.test(trimmed)) return USERNAME_CHARSET_MSG;
  return "";
}

function avatarText(name) {
  return (name || "?").trim().slice(0, 1).toUpperCase();
}

// Truth Social 官方粉勾：压在头像右下角（站外唯一带认证标的平台）
const TRUTH_CHECK_SVG = `<svg class="vs-check" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#f0426b"/><path d="m6.6 12.6 3.4 3.4 7.4-8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function avatarHtml(name, url, platform) {
  const inner = url ? `<img class="kol-avatar" src="${escapeHtml(url)}" alt="" loading="lazy">` : `<div class="kol-avatar">${escapeHtml(avatarText(name))}</div>`;
  if (platform !== "truth") return inner;
  return `<span class="avatar-verified">${inner}${TRUTH_CHECK_SVG}</span>`;
}

// ---------- 壳 ----------
const NAV = [
  { group: "订阅", items: [
    { route: "timeline", icon: LIST_ICON, label: "最新动态" },
    { route: "news", icon: NEWS_ICON, label: "财经新闻", badge: "news" },
    { route: "knowledge", icon: BOOK_ICON, label: "研报中心" },
    { route: "home", icon: GRID_ICON, label: "订阅广场" },
    { route: "settings", icon: GEAR_ICON, label: "个人设置" },
  ]},
  { group: "管理", admin: true, items: [
    { route: "admin/content", icon: DASHBOARD_ICON, label: "内容管理", badge: "requests" },
    { route: "admin/stats", icon: BOOK_ICON, label: "数据源" },
    { route: "admin/knowledge", icon: BOOK_ICON, label: "研报设置" },
    { route: "admin/ops", icon: FILE_TEXT_ICON, label: "帖子与日志" },
    { route: "admin/account", icon: USERS_ICON, label: "用户与注册" },
  ]},
];

const SIDEBAR_SLIM_KEY = "sidebar-slim";

function sidebarIsSlim() {
  return document.documentElement.classList.contains("sidebar-slim");
}

function syncSidebarToggle() {
  const btn = $("#sidebar-toggle");
  if (!btn) return;
  const slim = sidebarIsSlim();
  btn.setAttribute("aria-expanded", slim ? "false" : "true");
  btn.setAttribute("aria-label", slim ? "展开侧栏" : "收起侧栏");
  btn.title = slim ? "展开侧栏" : "收起侧栏";
}

function toggleSidebarSlim() {
  if (window.matchMedia("(max-width: 900px)").matches) return;
  document.documentElement.classList.toggle("sidebar-slim", !sidebarIsSlim());
  try {
    localStorage.setItem(SIDEBAR_SLIM_KEY, sidebarIsSlim() ? "1" : "0");
  } catch {
    /* 无 localStorage 时只改本页 */
  }
  syncSidebarToggle();
}

function renderSidebar(user) {
  const navItemHtml = (item) => `
        <button class="nav-item" data-route="${item.route}" onclick="go('${item.route}')" title="${item.label}">
          <span class="nav-icon">${item.icon}</span>
          <span class="nav-label">${item.label}</span>
          ${item.badge ? `<span class="nav-badge" data-${item.badge}-badge hidden></span>` : ""}
        </button>`;
  const html = NAV.filter((g) => !g.admin || user.is_admin)
    .map((group) => `
      ${group.group ? `<div class="nav-group-label">${group.group}</div>` : ""}
      ${(group.items || []).filter((item) => item.route !== "news" || state.newsVisible).map(navItemHtml).join("")}
      ${(group.subs || []).map((sub) => `
        <details class="nav-sub" open>
          <summary class="nav-sub-label">${sub.label}${CHEVRON_RIGHT_ICON}</summary>
          ${sub.items.filter((item) => item.route !== "news" || state.newsVisible).map(navItemHtml).join("")}
        </details>`).join("")}
    `).join("");
  $("#sidebar-nav").innerHTML = html;
  $("#sidebar-user").innerHTML = `
    <div class="theme-switcher" id="theme-switcher"></div>
    <div class="sidebar-foot-links">
      <a id="sidebar-gh-link" class="sidebar-gh-link" href="https://github.com/icekale/vpush" target="_blank" rel="noopener" title="GitHub 项目">${GITHUB_ICON}</a>
      <span class="sidebar-user-meta" id="sidebar-version">v${APP_VERSION}</span>
    </div>
  `;
  renderThemeSwitcher();
  syncSidebarToggle();
  checkUpdate();
  ensureVersionRefreshCheck();
}

const MOBILE_NAV = [
  { route: "timeline", icon: HOME_ICON, label: "动态" },
  { route: "news", icon: NEWS_ICON, label: "财经新闻", badge: "news" },
  { route: "home", icon: GRID_ICON, label: "广场" },
  { route: "settings", icon: USER_ICON, label: "个人设置" },
];

let bottomNavLastY = 0;
let bottomNavTravel = 0;
let bottomNavRouteSignature = null;

function resetBottomNavScroll() {
  bottomNavLastY = Math.max(0, window.scrollY);
  bottomNavTravel = 0;
  const nav = $("#bottom-nav");
  if (nav) nav.inert = false;
}

function updateBottomNavScroll() {
  const nav = $("#bottom-nav");
  if (!nav) return;
  const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  // Clamp overscroll so bouncing at either edge does not reverse navigation state.
  const y = Math.max(0, Math.min(window.scrollY, maxY));
  const delta = y - bottomNavLastY;
  bottomNavLastY = y;
  if (window.innerWidth > 768 || maxY === 0 || y === 0) {
    resetBottomNavScroll();
    return;
  }
  if (!delta) return;
  bottomNavTravel = Math.sign(delta) === Math.sign(bottomNavTravel) ? bottomNavTravel + delta : delta;
  if (bottomNavTravel >= 24 || bottomNavTravel <= -8) {
    nav.inert = bottomNavTravel > 0;
    bottomNavTravel = 0;
  }
}

window.addEventListener("scroll", updateBottomNavScroll, { passive: true });
window.matchMedia("(max-width: 768px)").addEventListener("change", resetBottomNavScroll);

function playBottomNavFeedback(button) {
  button.onanimationend = (event) => {
    if (event.animationName === "bottom-nav-feedback") button.classList.remove("is-feedback");
  };
  button.getAnimations({ subtree: true }).forEach((animation) => animation.cancel());
  button.classList.remove("is-feedback");
  void button.offsetWidth;
  button.classList.add("is-feedback");
}

function goFromBottomNav(button, route) {
  playBottomNavFeedback(button);
  go(route);
}

function renderBottomNav(user) {
  resetBottomNavScroll();
  const tabs = MOBILE_NAV.filter((tab) => tab.route !== "news" || state.newsVisible);
  if (user.is_admin) tabs.push({ route: "more", icon: MORE_ICON, label: "更多" });
  const routeSignature = tabs.map((tab) => tab.route).join("|");
  const nav = $("#bottom-nav");
  if (routeSignature === bottomNavRouteSignature && nav.querySelector(".bnav-item")) {
    ensureMobilePlatformSwipe();
    return;
  }
  bottomNavRouteSignature = routeSignature;
  nav.innerHTML = tabs.map((t) => `
    <button class="bnav-item" data-route="${t.route}" aria-label="${t.label}" title="${t.label}" onclick="goFromBottomNav(this, '${t.route}')">
      <span class="bnav-icon">${t.icon}${t.badge ? `<span class="bnav-badge" data-${t.badge}-badge hidden></span>` : ""}</span>
    </button>`).join("");
  ensureMobilePlatformSwipe();
}

async function renderMore(seq) {
  if (!state.user.is_admin) { replaceRoute("timeline"); return; }
  setPageTitle("更多");
  const adminGroup = NAV.find((g) => g.admin) || { items: [], subs: [] };
  const adminItems = [
    ...(adminGroup.items || []),
    ...(adminGroup.subs || []).flatMap((s) => s.items || []),
  ];
  $("#main").innerHTML = `
    <section class="section-panel">
      <div class="more-grid">
        ${adminItems.map((item) => `
          <button class="more-item" onclick="go('${item.route}')">
            <span class="more-icon">${item.icon}</span>
            <span class="more-label">${escapeHtml(item.label)}</span>
          </button>`).join("")}
      </div>
    </section>`;
}

let _feishuTimelineTimer = null;
let _feishuTimelineMediaUrls = [];
const _feishuTimelineMediaCache = new Map();
let _feishuTimelineState = null;
let _feishuMoreObserver = null;
let _feishuAssetObserver = null;
let _feishuSourceLoadSeq = 0;
let _feishuPreviewSeq = 0;
let _feishuPreviewTimer = null;
let _feishuPreview = { url: "", data: null };
let _imaReaderSeq = 0;

function isReportWatchableTag(tag) {
  const name = String(tag || "").trim();
  return !!name && !REPORT_WATCH_BLOCKED_TAGS.has(name);
}

function userKeywordSet() {
  return new Set((state.user?.keywords || []).map((k) => String(k || "").trim()).filter(Boolean));
}

function feishuTimelineAssetHtml(asset, mediaId, groupId) {
  if (!asset?.id) return "";
  const name = asset.name || (asset.kind === "image" ? "文档图片" : "文档附件");
  if (String(asset.mime || "").startsWith("image/") || asset.kind === "image") {
    return `<a class="post-img-link" href="#" onclick="event.preventDefault();openLightbox(this.querySelector('img'))" aria-label="查看${escapeHtml(name)}"><img src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" data-feishu-asset="${escapeHtml(asset.id)}" data-media-id="${escapeHtml(mediaId)}" data-group-id="${escapeHtml(groupId)}" alt="${escapeHtml(name)}" loading="lazy"></a>`;
  }
  return `<button type="button" class="feishu-attachment" data-asset-id="${escapeHtml(asset.id)}" data-media-id="${escapeHtml(mediaId)}" data-group-id="${escapeHtml(groupId)}" data-name="${escapeHtml(name)}" onclick="downloadFeishuTimelineAsset(this)">${escapeHtml(name)}</button>`;
}

function feishuTimelineBlockHtml(block, mediaId, groupId, showSpeaker = true) {
  if (block.type === "table") {
    const rows = (block.rows || []).map((row, rowIndex) => {
      const tag = rowIndex === 0 ? "th" : "td";
      return `<tr>${(row || []).map((cell) => `<${tag}>${escapeHtml(cell?.text || "").replace(/\n/g, "<br>")}${(cell?.assets || []).map((asset) => feishuTimelineAssetHtml(asset, mediaId, groupId)).join("")}</${tag}>`).join("")}</tr>`;
    }).join("");
    return rows ? `<div class="feishu-entry-table" role="region" aria-label="文档表格" tabindex="0"><table><tbody>${rows}</tbody></table></div>` : "";
  }
  const speaker = String(block.speaker || "");
  const reply = String(block.reply_to || "");
  const text = String(block.text || "");
  const identity = speaker && showSpeaker
    ? `<div class="feishu-entry-speaker">${feishuSpeakerAvatarHtml(speaker)}<strong>${escapeHtml(feishuSourceDisplay(speaker).label)}</strong>${reply ? `<span class="feishu-entry-reply-to">↪ 回复 @${escapeHtml(reply)}</span>` : ""}</div>`
    : "";
  const assets = (block.assets || []).filter((asset) => asset && asset.id);
  const assetHtml = assets.length ? `<div class="post-images feishu-entry-assets">${assets.map((asset) => feishuTimelineAssetHtml(asset, mediaId, groupId)).join("")}</div>` : "";
  return `<div class="feishu-entry-block${speaker ? " has-speaker" : ""}${reply ? " is-reply" : ""}">${identity}${text ? `<p>${escapeHtml(text).replace(/\n/g, "<br>")}</p>` : ""}${assetHtml}</div>`;
}

function feishuEntryAuthor(entry) {
  const blocks = Array.isArray(entry?.blocks) ? entry.blocks : [];
  const speakers = [...new Set(blocks.map((block) => String(block?.speaker || "").trim()).filter(Boolean))];
  if (speakers.length !== 1 || blocks.some((block) => String(block?.reply_to || "").trim())) return "";
  return speakers[0];
}

function feishuBlockHasContent(block) {
  if (!block) return false;
  if (block.type === "table") {
    return (block.rows || []).some((row) => (row || []).some((cell) => String(cell?.text || "").trim() || (cell?.assets || []).some((asset) => asset?.id)));
  }
  const text = String(block.text || "").trim();
  if ((block.assets || []).some((asset) => asset?.id)) return true;
  if (!text) return false;
  if (text === "收起") return false;
  if (/^\d+日无更新$/.test(text)) return false;
  return true;
}

function feishuEntryHasContent(entry) {
  return (entry?.blocks || []).some(feishuBlockHasContent);
}

const FEISHU_SOURCE_DISPLAY = {
  "K神-2026": { label: "杨康平", avatar: "/feishu-yang.png?v=1" },
  "杨康平": { label: "杨康平", avatar: "/feishu-yang.png?v=1" },
  "Q神-档案库": { label: "失业期神", avatar: "/feishu-shiye.png?v=1" },
  "Q神": { label: "失业期神", avatar: "/feishu-shiye.png?v=1" },
  "失业期神": { label: "失业期神", avatar: "/feishu-shiye.png?v=1" },
};

function feishuSourceDisplay(title) {
  const raw = String(title || "").trim();
  return FEISHU_SOURCE_DISPLAY[raw] || { label: raw, avatar: "" };
}

function feishuSpeakerAvatarHtml(name) {
  const display = feishuSourceDisplay(name);
  if (display.avatar) return `<img class="feishu-speaker-avatar" src="${escapeHtml(display.avatar)}" alt="" loading="lazy">`;
  // ponytail: 未建映射的新发言人掉回首字色块，不断档
  return `<span class="feishu-speaker-avatar feishu-speaker-fallback" aria-hidden="true">${escapeHtml(display.label.slice(0, 1))}</span>`;
}

function feishuLiveItemHtml(entry, showSource) {
  const source = entry.source || {};
  const author = feishuEntryAuthor(entry);
  const sourceLabel = showSource && source.title ? `<div class="feishu-live-source">${escapeHtml(feishuSourceDisplay(source.title).label)}</div>` : "";
  const speaker = author ? `<div class="feishu-live-speaker">${feishuSpeakerAvatarHtml(author)}<strong>${escapeHtml(feishuSourceDisplay(author).label)}</strong><time datetime="${escapeHtml(entry.timestamp || "")}">${escapeHtml(entry.time || "")}</time></div>` : "";
  const blocks = (entry.blocks || []).filter(feishuBlockHasContent).map((block) => feishuTimelineBlockHtml(block, source.media_id || "", source.group_id || "", !author)).join("");
  return `<article class="live-item" data-entry-id="${escapeHtml(entry.id || "")}">
    <div class="live-main">${speaker}${sourceLabel}<div class="live-body">${blocks}</div></div>
  </article>`;
}

function feishuTimelineEntriesHtml(entries, showSource) {
  const visible = (entries || []).filter(feishuEntryHasContent);
  if (!visible.length) return emptyState("这个范围内还没有时间线记录");
  const grouped = [];
  for (const entry of visible) {
    const day = String(entry.day || String(entry.timestamp || "").slice(0, 10));
    if (!grouped.length || grouped.at(-1).day !== day) grouped.push({ day, items: [] });
    grouped.at(-1).items.push(entry);
  }
  return grouped.map((group) => `
    <div class="tl-group live-group" id="feishu-day-${escapeHtml(group.day)}">
      <div class="tl-group-head"><span>${escapeHtml(fmtImaDay(group.day) || group.day)}</span></div>
      <div class="live-feed">${group.items.map((entry) => feishuLiveItemHtml(entry, showSource)).join("")}</div>
    </div>`).join("");
}

function renderFeishuTimelineView() {
  const host = $("#feishu-timeline-body");
  if (!host || !_feishuTimelineState) return;
  const state = _feishuTimelineState;
  const { data, selectedGroup } = state;
  const raw = selectedGroup
    ? (data.entries || []).filter((entry) => entry.source?.group_id === selectedGroup)
    : (data.entries || []);
  const entries = raw.filter(feishuEntryHasContent);
  const fab = $("#feishu-latest-fab");
  if (fab) fab.textContent = "回到最新";
  updateFeishuTimelineToolbar();
  updateFeishuTimelineHeader();
  if (state.loading && !entries.length) {
    host.innerHTML = '<p class="ima-reader-status" role="status">正在载入时间线…</p>';
    renderFeishuTimelineDates([]);
    observeFeishuTimelineMore();
    return;
  }
  const showSourceLabels = !selectedGroup && (data.sources || []).length > 1;
  host.innerHTML = `${feishuTimelineEntriesHtml(entries, showSourceLabels)}${feishuTimelineMoreHtml()}`;
  loadFeishuTimelineImages();
  renderFeishuTimelineDates(entries);
  observeFeishuTimelineMore();
  toggleFeishuLatestFab();
}

function renderFeishuTimelineDates(entries) {
  const nav = $("#feishu-date-nav");
  if (!nav) return;
  const days = [...new Set((entries || []).map((entry) => entry.day).filter(Boolean))];
  nav.innerHTML = days.map((day) => `<a href="#feishu-day-${escapeHtml(day)}">${escapeHtml(fmtImaDay(day) || day)}</a>`).join("");
  const select = $("#feishu-date-select");
  if (select) select.innerHTML = `<option value="">跳到日期</option>${days.map((day) => `<option value="${escapeHtml(day)}">${escapeHtml(fmtImaDay(day) || day)}</option>`).join("")}`;
}

function feishuTimelineRequestPath(state, before = "") {
  const params = new URLSearchParams({ order: "latest" });
  params.set("window_days", "7");
  if (state.selectedGroup) params.set("group", state.selectedGroup);
  if (before) params.set("before", before);
  return `/api/ima-documents/timeline/all?${params.toString()}`;
}

function mergeFeishuTimelineEntries(current, incoming) {
  const seen = new Set(current.map((entry) => String(entry.id || "")));
  return [...current, ...incoming.filter((entry) => {
    const id = String(entry.id || "");
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  })];
}

function feishuTimelineMoreHtml() {
  const state = _feishuTimelineState;
  if (!state || (!state.hasMore && !state.error)) return "";
  const label = state.loading ? "正在加载…" : state.error ? "重试" : "加载更早";
  const error = state.error ? `<p class="feishu-timeline-load-error" role="alert">${escapeHtml(state.error)}</p>` : "";
  return `<div class="feishu-timeline-more feishu-timeline-sentinel"><button type="button" class="btn-normal" onclick="loadMoreFeishuTimeline()"${state.loading ? " disabled" : ""}>${label}</button>${error}</div>`;
}

async function loadFeishuTimelinePage(reset = false) {
  const current = _feishuTimelineState;
  if (!current || current.loading) return false;
  const before = reset ? "" : current.nextCursor;
  if (reset) revokeFeishuTimelineMediaUrls();
  const requestState = {
    ...current,
    loading: true,
    error: "",
    nextCursor: reset ? "" : current.nextCursor,
    hasMore: reset ? false : current.hasMore,
    data: reset ? { ...current.data, entries: [], notices: [] } : current.data,
  };
  _feishuTimelineState = requestState;
  renderFeishuTimelineView();
  try {
    const data = await api(feishuTimelineRequestPath(requestState, before));
    if (!routeStillActive(requestState.seq) || requestState.readerSeq !== _imaReaderSeq || _feishuTimelineState !== requestState) return false;
    _feishuTimelineState = {
      ...requestState,
      data: {
        ...data,
        entries: reset ? (data.entries || []) : mergeFeishuTimelineEntries(requestState.data.entries || [], data.entries || []),
        notices: reset ? (data.notices || []) : (requestState.data.notices || []),
      },
      nextCursor: String(data.next_cursor || ""),
      hasMore: !!data.has_more,
      loading: false,
      error: "",
    };
    renderFeishuTimelineView();
    return true;
  } catch (err) {
    if (!routeStillActive(requestState.seq) || requestState.readerSeq !== _imaReaderSeq || _feishuTimelineState !== requestState) return false;
    _feishuTimelineState = { ...requestState, loading: false, error: err.message || "时间线加载失败" };
    renderFeishuTimelineView();
    return false;
  }
}

async function loadMoreFeishuTimeline() {
  const state = _feishuTimelineState;
  if (!state || state.loading || (!state.hasMore && !state.error)) return;
  await loadFeishuTimelinePage(false);
}

function observeFeishuTimelineMore() {
  _feishuMoreObserver?.disconnect();
  _feishuMoreObserver = null;
  const host = $("#feishu-timeline-body");
  const sentinel = host?.querySelector(".feishu-timeline-sentinel");
  if (!host || !sentinel || typeof IntersectionObserver !== "function") return;
  _feishuMoreObserver = new IntersectionObserver((items) => {
    if (!_feishuTimelineState || _feishuTimelineState.loading) return;
    if (items.some((item) => item.isIntersecting)) loadMoreFeishuTimeline();
  }, { root: host, rootMargin: "480px 0px" });
  _feishuMoreObserver.observe(sentinel);
}

async function selectFeishuTimelineSource(groupId) {
  if (!_feishuTimelineState || _feishuTimelineState.loading) return;
  const current = _feishuTimelineState;
  const selectedGroup = String(groupId || "");
  if (selectedGroup === current.selectedGroup) return;
  _feishuTimelineState = {
    ...current,
    selectedGroup,
    data: { ...current.data, entries: [], notices: [] },
    nextCursor: "",
    hasMore: false,
    error: "",
  };
  updateFeishuTimelineToolbar();
  updateFeishuTimelineHeader();
  updateFeishuTimelineRoute();
  const loaded = await loadFeishuTimelinePage(true);
  if (!loaded && routeStillActive(current.seq) && current.readerSeq === _imaReaderSeq && _feishuTimelineState?.selectedGroup === selectedGroup) {
    const message = _feishuTimelineState.error || "来源切换失败";
    _feishuTimelineState = current;
    updateFeishuTimelineToolbar();
    updateFeishuTimelineHeader();
    updateFeishuTimelineRoute();
    renderFeishuTimelineView();
    flash(message, "error");
  }
}

function updateFeishuTimelineToolbar() {
  const host = $("#feishu-timeline-toolbar");
  if (!host || !_feishuTimelineState) return;
  const selected = String(_feishuTimelineState.selectedGroup || "");
  const pills = host.querySelectorAll(".feishu-source-pills .tl-pill");
  if (pills.length) {
    pills.forEach((btn) => {
      const on = String(btn.dataset.group || "") === selected;
      btn.classList.toggle("selected", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  } else {
    renderFeishuTimelineToolbar();
  }
}

function updateFeishuTimelineHeader() {
  if (!_feishuTimelineState) return;
  const { selectedGroup, data } = _feishuTimelineState;
  const sources = data?.sources || [];
  let title = "";
  let sourceUrl = "";
  if (selectedGroup) {
    const matched = sources.find((s) => String(s.group_id || "") === selectedGroup);
    title = feishuSourceDisplay(matched?.title || selectedGroup).label;
    sourceUrl = matched?.canonical_url || "";
  } else if (sources.length === 1) {
    title = feishuSourceDisplay(sources[0]?.title || "").label;
    sourceUrl = sources[0]?.canonical_url || "";
  } else {
    title = "全部时间线";
  }
  const titleEl = $(".ima-reader--feishu .ima-reader-title");
  if (titleEl) titleEl.textContent = title;
  setPageTitle(title);
  const openLink = $(".ima-reader--feishu .ima-reader-actions a[data-feishu-canonical]");
  if (openLink) {
    if (sourceUrl) {
      openLink.href = sourceUrl;
      openLink.removeAttribute("hidden");
    } else {
      openLink.setAttribute("hidden", "");
    }
  }
}

function updateFeishuTimelineRoute() {
  if (!_feishuTimelineState) return;
  const { selectedGroup, data, mediaId } = _feishuTimelineState;
  const sources = data?.sources || [];
  if (selectedGroup) {
    const matched = sources.find((s) => String(s.group_id || "") === selectedGroup);
    const targetMediaId = matched?.media_id || mediaId;
    if (targetMediaId) {
      replaceImaDocumentsRoute(imaDocumentReaderRoute(targetMediaId, selectedGroup));
    }
  } else {
    const targetMediaId = mediaId || sources[0]?.media_id;
    if (targetMediaId) {
      replaceImaDocumentsRoute(imaDocumentReaderRoute(targetMediaId, ""));
    }
  }
}

function jumpFeishuTimelineDay(day) {
  if (!day) return;
  document.getElementById(`feishu-day-${day}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function fetchFeishuTimelineAsset(img) {
  const seq = routeRenderSeq;
  const readerSeq = _imaReaderSeq;
  img.dataset.feishuLoading = "1";
  const mediaId = img.dataset.mediaId || "";
  const groupId = img.dataset.groupId || "";
  const assetId = img.dataset.feishuAsset || "";
  const cacheKey = `${mediaId}|${groupId}|${assetId}`;
  const cached = _feishuTimelineMediaCache.get(cacheKey);
  if (cached) {
    img.src = cached;
    return Promise.resolve();
  }
  const query = groupId ? `?group=${encodeURIComponent(groupId)}` : "";
  return apiBlob(`/api/ima-documents/${encodeURIComponent(mediaId)}/assets/${encodeURIComponent(assetId)}${query}`)
    .then((blob) => {
      if (!routeStillActive(seq) || readerSeq !== _imaReaderSeq || !img.isConnected) return;
      const url = URL.createObjectURL(blob);
      _feishuTimelineMediaUrls.push(url);
      _feishuTimelineMediaCache.set(cacheKey, url);
      img.src = url;
    })
    .catch(() => {
      const link = img.closest(".post-img-link");
      if (link) link.remove();
      else if (img.isConnected) img.remove();
    });
}

function loadFeishuTimelineImages() {
  _feishuAssetObserver?.disconnect();
  _feishuAssetObserver = null;
  const images = [...document.querySelectorAll("img[data-feishu-asset]:not([data-feishu-loading])")];
  if (!images.length) return;
  if (typeof IntersectionObserver !== "function") {
    images.forEach((img) => fetchFeishuTimelineAsset(img));
    return;
  }
  _feishuAssetObserver = new IntersectionObserver((items, observer) => {
    for (const item of items) {
      if (!item.isIntersecting) continue;
      observer.unobserve(item.target);
      fetchFeishuTimelineAsset(item.target);
    }
  }, { rootMargin: "600px 0px" });
  images.forEach((img) => _feishuAssetObserver.observe(img));
}

async function downloadZsxqFile(button) {
  button.disabled = true;
  try {
    const blob = await apiBlob(`/api/media/zsxq-file/${encodeURIComponent(button.dataset.fileId)}`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = button.dataset.name || "附件";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    flash(err.message || "附件下载失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function downloadFeishuTimelineAsset(button) {
  button.disabled = true;
  const query = button.dataset.groupId ? `?group=${encodeURIComponent(button.dataset.groupId)}` : "";
  try {
    const blob = await apiBlob(`/api/ima-documents/${encodeURIComponent(button.dataset.mediaId)}/assets/${encodeURIComponent(button.dataset.assetId)}${query}`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = button.dataset.name || "attachment";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    flash(err.message || "附件下载失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function loadFeishuTimeline(item, seq, readerSeq) {
  const query = routeQuery();
  const selectedGroup = query.has("doc_group") ? (query.get("doc_group") || "") : (item.group_id || "");
  const requestState = {
    mediaId: item.media_id || "",
    selectedGroup,
    order: "latest",
    seq,
    readerSeq,
  };
  const data = await api(feishuTimelineRequestPath(requestState));
  if (!routeStillActive(seq) || readerSeq !== _imaReaderSeq) return;
  _feishuTimelineState = {
    data,
    ...requestState,
    baseline: String(item.downloaded_at || ""),
    nextCursor: String(data.next_cursor || ""),
    hasMore: !!data.has_more,
    loading: false,
    error: "",
  };
  const panel = $("#ima-document-panel");
  if (!panel) return;
  panel.innerHTML = `<div class="feishu-timeline-layout"><main id="feishu-timeline-body" class="feishu-timeline-body"></main><nav id="feishu-date-nav" class="feishu-date-nav" aria-label="日期目录"></nav></div><button type="button" id="feishu-latest-fab" class="feishu-latest-fab" onclick="jumpFeishuTimelineLatest()">回到最新</button>`;
  renderFeishuTimelineToolbar();
  updateFeishuTimelineHeader();
  const body = $("#feishu-timeline-body");
  if (body) body.onscroll = () => toggleFeishuLatestFab();
  renderFeishuTimelineView();
  _feishuTimelineTimer = setTimeout(() => checkFeishuTimelineUpdate(item.media_id, item.group_id, _feishuTimelineState.baseline, seq, readerSeq), 60000);
}

function feishuSourcePillsHtml(sources, selectedGroup, target = "timeline") {
  const selected = String(selectedGroup || "");
  const items = sources.length > 1 ? [{ group_id: "", title: "全部" }, ...sources] : sources;
  if (!items.length) return "";
  return `<div class="tl-pills feishu-source-pills" role="radiogroup" aria-label="来源">${items.map((source) => {
    const id = String(source.group_id || "");
    const display = feishuSourceDisplay(source.title);
    const on = id === selected;
    const img = display.avatar ? `<img class="feishu-source-avatar" src="${escapeHtml(display.avatar)}" alt="">` : "";
    return `<button type="button" class="tl-pill${on ? " selected" : ""}${img ? " has-avatar" : ""}" role="radio" aria-checked="${on}" data-group="${escapeHtml(id)}" data-source-target="${target}" aria-label="${escapeHtml(display.label)}" onclick="selectFeishuSource(this)">${img}<span>${escapeHtml(display.label)}</span></button>`;
  }).join("")}</div>`;
}

function feishuTimelineToolbarHtml() {
  const state = _feishuTimelineState || {};
  const selectedGroup = state.selectedGroup || "";
  const sources = state.data?.sources || [];
  return `
    ${feishuSourcePillsHtml(sources, selectedGroup)}
    <label class="feishu-date-badge">${FEISHU_DATE_ICON}<select id="feishu-date-select" class="feishu-date-select" aria-label="跳到日期" onchange="jumpFeishuTimelineDay(this.value)"></select></label>`;
}

function renderFeishuTimelineToolbar() {
  const host = $("#feishu-timeline-toolbar");
  if (host && _feishuTimelineState) host.innerHTML = feishuTimelineToolbarHtml();
}

function toggleFeishuLatestFab() {
  const host = $("#feishu-timeline-body");
  const fab = $("#feishu-latest-fab");
  if (!host || !fab) return;
  fab.classList.toggle("is-visible", host.scrollTop > host.clientHeight * 1.5);
}

function jumpFeishuTimelineLatest() {
  const host = $("#feishu-timeline-body");
  if (!host) return;
  host.scrollTo({ top: 0, behavior: "smooth" });
  const fab = $("#feishu-latest-fab");
  if (fab) fab.classList.remove("is-visible");
}

function feishuAnchorEntry(host) {
  const hostTop = host.getBoundingClientRect().top;
  for (const el of host.querySelectorAll("[data-entry-id]")) {
    const rect = el.getBoundingClientRect();
    if (rect.bottom > hostTop + 4) return { id: el.dataset.entryId, delta: Math.max(0, rect.top - hostTop) };
  }
  return null;
}

function feishuRestoreEntry(host, anchor, fallbackTop) {
  if (anchor?.id) {
    const el = host.querySelector(`[data-entry-id="${CSS.escape(anchor.id)}"]`);
    if (el) {
      host.scrollTop = Math.max(0, el.offsetTop - anchor.delta);
      return;
    }
  }
  host.scrollTop = fallbackTop || 0;
}

function feishuTimelineCursorFromEntry(entry) {
  const raw = JSON.stringify({
    timestamp: String(entry?.timestamp || ""),
    id: String(entry?.id || ""),
  });
  const bytes = new TextEncoder().encode(raw);
  let bin = "";
  bytes.forEach((byte) => { bin += String.fromCharCode(byte); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function feishuTimelineUpdatePath(state) {
  return feishuTimelineRequestPath(state);
}

async function applyFeishuTimelineUpdate(button) {
  const current = _feishuTimelineState;
  const host = $("#feishu-timeline-body");
  const mediaId = button?.dataset?.mediaId || "";
  const groupId = button?.dataset?.groupId || "";
  if (!current || !host || !mediaId || current.loading) return;
  button.disabled = true;
  try {
    const metaPath = `/api/ima-documents/${encodeURIComponent(mediaId)}${groupId ? `?group=${encodeURIComponent(groupId)}` : ""}`;
    const [fresh, meta] = await Promise.all([
      api(feishuTimelineUpdatePath(current)),
      api(metaPath),
    ]);
    if (!routeStillActive(current.seq) || current.readerSeq !== _imaReaderSeq) return;
    const existing = new Set((current.data.entries || []).map((entry) => String(entry.id || "")));
    const added = (fresh.entries || []).filter((entry) => String(entry.id || "") && !existing.has(String(entry.id)));
    const anchor = feishuAnchorEntry(host);
    const scrollTop = host.scrollTop;
    _feishuTimelineState = {
      ...current,
      baseline: String(meta.downloaded_at || current.baseline || ""),
      data: {
        ...current.data,
        entries: [...added, ...(current.data.entries || [])],
      },
    };
    button.remove();
    renderFeishuTimelineView();
    feishuRestoreEntry(host, anchor, scrollTop);
    flash(added.length ? `已载入 ${added.length} 条新内容` : "已是最新内容");
    _feishuTimelineTimer = setTimeout(() => checkFeishuTimelineUpdate(mediaId, groupId, _feishuTimelineState.baseline, current.seq, current.readerSeq), 60000);
  } catch (err) {
    if (button.isConnected) button.disabled = false;
    flash(err.message || "载入新内容失败", "error");
  }
}

async function checkFeishuTimelineUpdate(mediaId, groupId, baseline, seq, readerSeq) {
  try {
    const item = await api(`/api/ima-documents/${encodeURIComponent(mediaId)}?group=${encodeURIComponent(groupId)}`);
    if (!routeStillActive(seq) || readerSeq !== _imaReaderSeq) return;
    const state = _feishuTimelineState;
    const known = state && state.seq === seq && state.readerSeq === readerSeq
      ? String(state.baseline ?? baseline)
      : String(baseline);
    if (String(item.downloaded_at || "") !== known) {
      const panel = $("#ima-document-panel");
      if (panel && !$("#feishu-new-content")) panel.insertAdjacentHTML("afterbegin", `<button type="button" id="feishu-new-content" class="feishu-new-content" data-media-id="${escapeHtml(mediaId)}" data-group-id="${escapeHtml(groupId)}" onclick="applyFeishuTimelineUpdate(this)">有新内容，点击载入</button>`);
      return;
    }
  } catch { /* 保持 last-good 阅读 */ }
  if (routeStillActive(seq) && readerSeq === _imaReaderSeq) {
    _feishuTimelineTimer = setTimeout(() => checkFeishuTimelineUpdate(mediaId, groupId, baseline, seq, readerSeq), 60000);
  }
}

async function checkUpdate() {
  try {
    const v = await api("/api/version");
    if (v.current && v.current !== APP_VERSION) {
      const refreshKey = `dav_version_refresh_${v.current}`;
      if (!sessionStorage.getItem(refreshKey)) {
        sessionStorage.setItem(refreshKey, "1");
        location.reload();
        return;
      }
    }
    const link = $("#sidebar-gh-link");
    const meta = $("#sidebar-version");
    if (!link || !meta) return;
    // 始终显示服务端返回的当前版本，避免本地硬编码版本过期
    meta.innerHTML = `v${escapeHtml(v.current)}`;
    if (v.update_available && v.latest) {
      link.classList.add("has-update");
      meta.innerHTML += ` <a class="sidebar-update" href="${escapeHtml(v.url)}" target="_blank" rel="noopener" title="有新版本">↑ ${escapeHtml(v.latest)}</a>`;
    }
  } catch {
    /* 更新检查失败不打扰，保留本地硬编码版本兜底 */
  }
}

function ensureVersionRefreshCheck() {
  if (ensureVersionRefreshCheck.bound) return;
  ensureVersionRefreshCheck.bound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") checkUpdate();
  });
  window.addEventListener("focus", checkUpdate);
}

function renderTopbar(user) {
  $("#topbar-user").innerHTML = `
    <button class="theme-toggle-btn" id="theme-toggle-btn" onclick="cycleTheme()" aria-label="切换主题" title="切换主题"></button>
    <div class="user-chip">
      <div class="user-avatar">${escapeHtml(avatarText(user.username))}</div>
      <div class="user-meta">
        <span class="user-name">${escapeHtml(user.username)}</span>
        <span class="user-role">${user.is_admin ? "管理员" : "订阅用户"}</span>
      </div>
    </div>
    <button class="topbar-logout" onclick="logout()">退出</button>`;
  updateThemeToggleIcon();
}

function setPageTitle(title, back = false, backRoute = "", backLabel = "") {
  $("#page-title").textContent = title;
  $("#btn-back").classList.toggle("hidden", !back);
  $("#btn-back").setAttribute("aria-label", backLabel || "返回上一页");
  state.pageBackRoute = back && backRoute ? String(backRoute) : "";
}

function emptyState(text, actionHtml = "") {
  return `<div class="empty">${escapeHtml(text)}${actionHtml}</div>`;
}

function homePanelHasFilters() {
  return !!(state.homeCategory || state.homeFavorite || (isMobileTimelineFilter() && (state.homeQ || state.homeSubscribed)));
}

function homeSubscribedToggleHtml() {
  return `<button type="button" id="home-sub-toggle" class="fav-toggle ${state.homeSubscribed ? "fav-on" : ""}" aria-pressed="${state.homeSubscribed}" onclick="toggleHomeSubscribed()">已订阅</button>`;
}

function homeScopeTogglesHtml(includeSubscribed) {
  return `<div class="home-scope-filters">
    ${includeSubscribed ? `<label class="switch home-scope-switch">
      <input type="checkbox" id="home-sub-toggle" ${state.homeSubscribed ? "checked" : ""} onchange="toggleHomeSubscribed()">
      <span class="track"></span><span>只看已订阅</span>
    </label>` : ""}
    <label class="switch home-scope-switch">
      <input type="checkbox" id="home-fav-toggle" ${state.homeFavorite ? "checked" : ""} onchange="toggleHomeFavorite()">
      <span class="track"></span><span>只看特别关注</span>
    </label>
  </div>`;
}

function homeFilterToggleHtml() {
  return `<button type="button" id="home-filter-toggle" class="fav-toggle home-filter-toggle ${homePanelHasFilters() ? "has-filter" : ""}" aria-label="筛选" aria-expanded="false" aria-controls="home-filter-panel" onclick="homeToggleFilter()">${FILTER_ICON}</button>`;
}

function homeFilterPanelHtml(mobile) {
  return `<div class="home-filter-content" id="home-filter-panel" hidden>
    ${mobile ? `<div class="search-bar home-search-bar">
      ${SEARCH_ICON}
      <input id="home-search" placeholder="搜索昵称或 ID" value="${escapeHtml(state.homeQ || "")}" oninput="homeSearch(this.value)">
    </div>` : ""}
    <div class="home-cats" id="home-cats"></div>
    ${homeScopeTogglesHtml(mobile)}
    <div class="home-filter-actions">
      <button class="btn-ghost" onclick="homeResetFilters()">清除筛选</button>
    </div>
  </div>`;
}

function toggleHomeSubscribed() {
  state.homeSubscribed = !state.homeSubscribed;
  const toggle = $("#home-sub-toggle");
  if (toggle?.matches('input[type="checkbox"]')) {
    toggle.checked = state.homeSubscribed;
  } else if (toggle) {
    toggle.classList.toggle("fav-on", state.homeSubscribed);
    toggle.setAttribute("aria-pressed", String(state.homeSubscribed));
  }
  renderHomeList();
}

function toggleHomeFavorite() {
  state.homeFavorite = !state.homeFavorite;
  const toggle = $("#home-fav-toggle");
  if (toggle) toggle.checked = state.homeFavorite;
  renderHomeList();
}

function homeToggleFilter() {
  const panel = $("#home-filter-panel");
  const btn = $("#home-filter-toggle");
  if (!panel || !btn) return;
  const open = panel.hasAttribute("hidden");
  panel.toggleAttribute("hidden", !open);
  btn.setAttribute("aria-expanded", String(open));
}

function homeMobilePlatformsHtml() {
  return tlPlazaEntries().map(([p, label]) => {
    const short = platformShortLabel(p);
    return `
    <button class="tl-pill ${state.platform === p ? "selected" : ""}"
      data-platform="${p}" aria-label="${label}" title="${label}"
      role="radio" aria-checked="${state.platform === p}">
      ${PLATFORM_ICONS[p || ""]}<span>${short}</span>
    </button>`;
  }).join("");
}

async function homePickMobilePlatform(platform) {
  state.platform = platform;
  const platforms = $("#home-mobile-platforms");
  if (platforms) platforms.innerHTML = homeMobilePlatformsHtml();
  await loadHomeKols(routeRenderSeq);
}

async function homeResetFilters() {
  state.homeQ = state.homeCategory = state.platform = "";
  state.homeSubscribed = state.homeFavorite = false;
  await renderHome(routeRenderSeq);
}

let _homeMobileWatchBound = false;
function ensureHomeMobileWatch() {
  if (_homeMobileWatchBound) return;
  _homeMobileWatchBound = true;
  window.matchMedia("(max-width: 768px)").addEventListener("change", () => {
    if (routePath() === "home" && $(".home-panel") && $("#kol-list")) renderHome(++routeRenderSeq);
  });
}

// ---------- 订阅广场 ----------
async function renderHome(seq) {
  setPageTitle("订阅广场");
  ensurePlazaPlatformSelection();
  ensureHomeMobileWatch();
  let onboardingHtml = "";
  if (state.user && !state.user.subscription_count) {
    try {
      const recs = await api("/api/recommendations");
      if (routeStillActive(seq) && recs.length) {
        onboardingHtml = `
          <section class="section-panel">
            <header class="section-head"><div>
              <h2 class="section-title">欢迎！先订阅几位大V</h2>
              <p class="section-meta">以下是最热门的大V；订阅后新帖会自动推送到你绑定的渠道。</p>
            </div></header>
            <div class="row" style="gap:12px;flex-wrap:wrap">${recs.map((rec) => `
              <div class="kol-item" style="flex:1;min-width:230px">
                ${avatarHtml(rec.name, rec.avatar_url)}
                <a class="kol-info" href="/kol/${rec.id}">
                  <div class="base">
                    <span class="name">${escapeHtml(rec.name)}</span>
                    <span class="tag">${PLATFORM_LABELS[rec.platform] || escapeHtml(rec.platform)}</span>
                    ${rec.category_name ? `<span class="tag">${escapeHtml(rec.category_name)}</span>` : ""}
                  </div>
                  <div class="desc">${rec.subscriber_count} 人订阅</div>
                </a>
                <button class="btn-sub ${rec.subscribed ? "subscribed" : ""}" onclick="quickSubscribe(${rec.id}, this)">
                  ${rec.subscribed ? "✓ 已订阅" : "订阅"}
                </button>
              </div>`).join("")}
            </div>
            <p class="muted" style="margin-top:12px">也可以先去<a href="/settings">绑定推送渠道</a>，再回来订阅。</p>
          </section>`;
      }
    } catch {
      /* 推荐加载失败不阻塞页面 */
    }
  }
  if (!routeStillActive(seq)) return; // 已切走：不写旧首页的 DOM
  const mobileHome = isMobileTimelineFilter();
  $("#main").innerHTML = `
    ${onboardingHtml}
    <section class="section-panel home-panel">
      <header class="section-head home-head">
        <div>
          <h2 class="section-title">全部大V</h2>
          <p class="section-meta" id="catalog-meta">加载中…</p>
        </div>
        ${mobileHome ? `
          <div class="icon-badge-bar" id="home-mobile-bar">
            <div class="tl-pills" id="home-mobile-platforms" role="radiogroup" aria-label="平台">
              ${homeMobilePlatformsHtml()}
            </div>
            ${homeFilterToggleHtml()}
          </div>
          ${homeFilterPanelHtml(true)}` : `
          <div class="toolbar" style="margin-top:12px">
            <div class="search-bar" style="flex:1;min-width:220px">
              ${SEARCH_ICON}
              <input id="home-search" placeholder="搜索昵称或 ID，即时过滤" value="${escapeHtml(state.homeQ || "")}" oninput="homeSearch(this.value)">
            </div>
            <div class="platform-tabs" id="platform-tabs"></div>
            ${homeSubscribedToggleHtml()}
            ${homeFilterToggleHtml()}
          </div>
          ${homeFilterPanelHtml(false)}`}
      </header>
      ${state.user?.is_admin ? "" : `
        <div class="request-banner">
          <div class="request-banner-icon">${PLUS_ICON}</div>
          <div class="request-banner-copy">
            <div class="title">想关注的大V不在列表里？</div>
            <div class="desc">提交申请，管理员审批通过后自动上架并通知你</div>
          </div>
          <button class="btn-normal" onclick="go('search')">申请添加</button>
        </div>`}
      <div id="kol-list" class="kol-grid"></div>
    </section>`;
  renderPlatformTabs();
  await loadHomeKols(seq);
}

function renderPlatformTabs() {
  const tabs = $("#platform-tabs");
  if (tabs) tabs.innerHTML = tlPlazaEntries().map(([p]) => platformTabHTML(p, state.platform, "home")).join("");
}

function categoryChipsHtml() {
  const cats = [...new Set(state.catalog.map((k) => k.category_name || ""))].filter(Boolean).sort();
  const chip = (c, label) => `<button class="cat-chip ${state.homeCategory === c ? "selected" : ""}" data-cat="${escapeHtml(c)}" onclick="pickHomeCategory(this.dataset.cat)">${escapeHtml(label)}</button>`;
  return chip("", "全部分类") + cats.map((c) => chip(c, c)).join("");
}

function pickHomeCategory(cat) {
  state.homeCategory = cat;
  renderHomeList();
}

let _homeSearchTimer = null;
function homeSearch(v) {
  state.homeQ = v.trim();
  clearTimeout(_homeSearchTimer);
  _homeSearchTimer = setTimeout(renderHomeList, 200);
}

function homeFilteredKols() {
  const q = state.homeQ.toLowerCase();
  return state.catalog.filter((k) => {
    if (state.homeSubscribed && !k.subscribed) return false;
    if (state.homeFavorite && !k.favorite) return false;
    if (state.homeCategory && k.category_name !== state.homeCategory) return false;
    if (!q) return true;
    return (k.name || "").toLowerCase().includes(q) || (k.external_id || "").toLowerCase().includes(q);
  });
}

function renderHomeList() {
  $("#home-filter-toggle")?.classList.toggle("has-filter", homePanelHasFilters());
  const cats = $("#home-cats");
  if (cats) cats.innerHTML = categoryChipsHtml();
  const meta = $("#catalog-meta");
  if (meta) meta.textContent = `共 ${state.catalog.length} 位大V · 已订阅 ${state.catalog.filter((k) => k.subscribed).length} 位`;
  const list = homeFilteredKols();
  const target = $("#kol-list");
  if (!target) return; // 已离开首页（如正在加载时切走），不写不存在的 DOM
  target.innerHTML = list.length
    ? groupedKolCards(list)
    : emptyState(state.catalog.length ? "没有匹配的大V" : "暂无大V，管理员可在管理后台添加");
}

function platformTabHTML(p, current, target) {
  const label = p ? PLATFORM_LABELS[p] : "全部";
  const short = platformShortLabel(p);
  return `<button class="platform-tab ${p === current ? "selected" : ""}" data-platform="${p}" data-platform-target="${target}"
    title="${label}" aria-label="${label}"
    onclick="selectPlatformTab(this)">${PLATFORM_ICONS[p || ""]}<span class="pt-label">${short}</span></button>`;
}

let _homeKolsSeq = 0;
async function loadHomeKols(routeSeq) {
  const seq = ++_homeKolsSeq;
  let kols;
  try {
    const params = state.platform ? `?platform=${state.platform}` : "";
    kols = await api(`/api/catalog${params}`);
  } catch (err) {
    if (seq !== _homeKolsSeq || !routeStillActive(routeSeq)) return;
    const list = $("#kol-list");
    if (list) list.innerHTML = emptyState("加载失败: " + err.message);
    return;
  }
  // 平台已切换或已离开首页：不写全局状态也不写 DOM，避免旧目录覆盖当前页面
  if (seq !== _homeKolsSeq || !routeStillActive(routeSeq)) return;
  state.catalog = kols;
  renderHomeList();
}

function groupedKolCards(kols) {
  const groups = {};
  for (const kol of kols) {
    const key = kol.category_name || "";
    (groups[key] = groups[key] || []).push(kol);
  }
  return Object.entries(groups)
    .map(([name, items]) => `
      <div class="group-head">
        <span style="font-weight:600;color:var(--color-text-strong)">${escapeHtml(name || "未分类")}</span>
        <span class="g-count">${items.length} 位</span>
      </div>
      ${items.map(kolCard).join("")}`)
    .join("");
}

async function switchPlatform(platform) {
  state.platform = platform;
  renderPlatformTabs();
  await loadHomeKols(routeRenderSeq);
}

function kolCard(kol) {
  const tags = [];
  if (kol.category_name) tags.push(`<span class="tag">${escapeHtml(kol.category_name)}</span>`);
  if (kol.platform === "combination" && kol.quote && kol.quote.day_percent_gain != null) {
    const gain = kol.quote.day_percent_gain;
    tags.push(`<span class="tag cube-day ${gain >= 0 ? "up" : "down"}">${gain >= 0 ? "+" : ""}${gain.toFixed(2)}%</span>`);
  }
  return `
    <div class="kol-card">
      <div class="kol-card-head">
        ${avatarHtml(kol.name, kol.avatar_url)}
        <div class="kol-card-info">
          <div class="p-name-line">
            <span class="name" title="${escapeHtml(kol.name)}">${escapeHtml(kol.name)}</span>
            <span class="p-platform" data-platform="${escapeHtml(kol.platform)}" role="img" aria-label="${escapeHtml(PLATFORM_LABELS[kol.platform] || kol.platform)}" title="${escapeHtml(PLATFORM_LABELS[kol.platform] || kol.platform)}">${PLATFORM_ICONS[kol.platform] || ""}</span>
          </div>
          ${tags.length ? `<div class="kol-card-meta">${tags.join("")}</div>` : ""}
          <div class="desc">外部 ID：${escapeHtml(kol.external_id)}${kol.enabled ? "" : " · 已停用"}</div>
        </div>
      </div>
      ${kol.subscribed && kol.platform === "xueqiu" ? `<div class="kol-card-subtype">${subTypeSwitchesHtml(kol.id, kol.subscribe_type || "post")}</div>` : ""}
      <div class="kol-card-actions">
        <button class="btn-sub ${kol.subscribed ? "subscribed" : ""}" onclick="toggleSubscribe(${kol.id}, this)">
          ${kol.subscribed ? "✓ 已订阅" : "订阅"}
        </button>
        ${kol.subscribed ? `<button class="fav-btn ${kol.favorite ? "fav-on" : ""}" onclick="toggleFavorite(${kol.id}, this)" title="特别关注：优先推送" aria-label="${kol.favorite ? "取消特别关注" : "设为特别关注"}">${STAR_SVG}</button>` : ""}
        ${kol.subscribed ? `<button class="fav-btn ${kol.secondary ? "sec-on" : "sec-off"}" onclick="toggleSecondary(${kol.id}, this)" title="次要：新帖合并进摘要推送（降频）" aria-label="${kol.secondary ? "取消次要" : "设为次要"}">${kol.secondary ? BELL_OFF_ICON : BELL_ICON}</button>` : ""}
        ${state.user?.is_admin ? `<button class="btn-sm danger kol-del" onclick="adminDeleteKolFromHome(${kol.id})" title="删除该大V" aria-label="删除该大V">${TRASH_ICON}</button>` : ""}
      </div>
    </div>`;
}

async function adminDeleteKolFromHome(kolId) {
  const kol = state.catalog.find((k) => k.id === kolId);
  if (!confirm(`确认删除该大V${kol ? `「${kol.name}」` : ""}？其订阅关系会一并移除。`)) return;
  try {
    await api(`/api/kols/${kolId}`, { method: "DELETE" });
    flash(`已删除「${kol ? kol.name : "该大V"}」`);
    await refreshKolsView(); // 按当前路由刷新，删除期间切走不会污染新页面
  } catch (err) {
    alert("删除失败: " + err.message);
  }
}

async function toggleSubscribe(kolId, btn) {
  const kol = state.catalog.find((k) => k.id === kolId);
  if (kol?.subscribed && !confirm(`取消订阅「${kol.name}」？将不再推送其新动态。`)) return;
  try {
    const wasSubscribed = kol ? kol.subscribed : btn.classList.contains("subscribed");
    if (wasSubscribed) {
      await api(`/api/subscriptions/${kolId}`, { method: "DELETE" });
    } else {
      await api("/api/subscriptions", { method: "POST", body: JSON.stringify({ kol_id: kolId, type: "post" }) });
    }
    flash(`已${wasSubscribed ? "退订" : "订阅"}「${kol ? kol.name : "该大V"}」`);
    if (kol) kol.subscribed = !wasSubscribed;
    await refreshKolsView();
  } catch (err) {
    alert("操作失败: " + err.message);
  }
}

async function toggleFavorite(kolId, btn) {
  const kol = state.catalog.find((k) => k.id === kolId);
  const next = !(kol ? kol.favorite : false);
  try {
    await api(`/api/subscriptions/${kolId}/favorite`, {
      method: "PUT",
      body: JSON.stringify({ favorite: next }),
    });
    if (kol) kol.favorite = next;
    if (btn) btn.classList.toggle("fav-on", next);
    flash(next ? "已加星标" : "已取消星标");
    if (isRoute("home")) renderHomeList();
  } catch (err) {
    alert("操作失败: " + err.message);
  }
}

async function toggleSecondary(kolId, btn) {
  const kol = state.catalog.find((k) => k.id === kolId);
  const next = !(kol ? kol.secondary : false);
  try {
    await api(`/api/subscriptions/${kolId}/secondary`, {
      method: "PUT",
      body: JSON.stringify({ secondary: next }),
    });
    if (kol) kol.secondary = next;
    if (btn) btn.classList.toggle("sec-on", next);
    flash(next ? "已设为次要（降频推送）" : "已取消次要");
    if (isRoute("home")) renderHomeList();
  } catch (err) {
    alert("操作失败: " + err.message);
  }
}

async function quickSubscribe(kolId, btn) {
  try {
    await api("/api/subscriptions", { method: "POST", body: JSON.stringify({ kol_id: kolId, type: "post" }) });
    btn.classList.add("subscribed");
    btn.textContent = "✓ 已订阅";
    btn.disabled = true;
    state.user.subscription_count = (state.user.subscription_count || 0) + 1;
    loadHomeKols(routeRenderSeq); // 订阅后重拉 catalog，已订阅置顶即时生效
  } catch (err) {
    alert("订阅失败: " + err.message);
  }
}

async function refreshKolsView() {
  // 发起前捕获当前路由令牌；完成后再写 DOM，避免局部刷新覆盖已切走的新路由
  const seq = routeRenderSeq;
  if (isRoute("home")) await loadHomeKols(seq); // 重拉 catalog，已订阅置顶即时生效
  else if (isRoute("kol/")) await renderKolPage(Number(routePath().split("/")[1] || 0), seq);
  else if (isRoute("search")) doSearch(seq);
}

function subTypeSwitchesHtml(kolId, current) {
  const cur = current || "post";
  const postOn = cur !== "reply";
  const replyOn = cur !== "post";
  return `
    <div class="sub-type-switches" data-kol="${kolId}">
      <label class="sub-type-switch">
        <input type="checkbox" ${postOn ? "checked" : ""} onchange="setSubscribeType(${kolId}, this)">
        <span>帖子</span>
      </label>
      <label class="sub-type-switch">
        <input type="checkbox" ${replyOn ? "checked" : ""} onchange="setSubscribeType(${kolId}, this)">
        <span>回复</span>
      </label>
    </div>`;
}

async function setSubscribeType(kolId, input) {
  const box = input.closest(".sub-type-switches");
  const boxes = box.querySelectorAll('input[type="checkbox"]');
  const postOn = boxes[0].checked;
  const replyOn = boxes[1].checked;
  if (!postOn && !replyOn) {
    input.checked = true; // 至少保留一种类型；取消订阅请点「已订阅」主按钮
    alert("请至少保留一种订阅类型；取消订阅请点「已订阅」按钮");
    return;
  }
  const type = postOn && replyOn ? "both" : postOn ? "post" : "reply";
  try {
    await api(`/api/subscriptions/${kolId}`, { method: "PUT", body: JSON.stringify({ type }) });
    const kol = state.catalog.find((k) => k.id === kolId);
    if (kol) {
      kol.subscribed = true;
      kol.subscribe_type = type;
    }
  } catch (err) {
    alert("切换订阅类型失败: " + err.message);
    refreshKolsView();
  }
}

// ---------- 动态 ----------
let _tlSeq = 0;
const _tlPosts = [];
const _kolPosts = [];
let _tlOffset = 0;
let _tlHasMore = true;
let _tlLoadingMore = false;
const _tlExpanded = new Set();
const _tlShowSrc = new Set();
let _tlTags = null;
let _tlDynamicTags = [];
let _tlLatestId = 0;        // 当前已加载的最新帖 id，用于后台检测新帖
let _tlLoadedFilter = null; // 缓存列表对应的筛选条件快照
let _tlSavedScrollY = 0;    // 离开动态页时的滚动位置，切回时恢复
const _tlPendingNew = [];     // 轮询拉到的新帖（点提示条时直接插到列表顶部）
let _tlPendingLatestId = 0; // 已拉取的新帖中最新 id，轮询去重
let _tlRefreshing = false;  // 刷新锁：防止连点/并发 poll 重复插入新帖
let _tlPollTimer = null;    // 新帖轮询定时器
let _tlWideWatchBound = false; // 1280px 断点只绑一次，避免开关留在已隐藏的栏里

let _liveSeq = 0;
const _livePosts = [];
let _liveCursor = "";
let _liveHasMore = true;
let _liveLoadingMore = false;
let _liveLatestId = 0;
const _livePendingNew = [];
let _livePendingLatestId = 0;
let _liveSavedScrollY = 0;
let _liveClockTimer = null;
let _liveInflight = null;
let _feedLoadObserver = null;
let _feedLoadFallback = null;

function isLiveTimeline() {
  return state.timelineSource === "live";
}

function feedScrollY() {
  return isLiveTimeline() ? _liveSavedScrollY : _tlSavedScrollY;
}

function feedPosts() {
  return isLiveTimeline() ? _livePosts : _tlPosts;
}

function feedPendingNew() {
  return isLiveTimeline() ? _livePendingNew : _tlPendingNew;
}

function renderFeed() {
  if (isLiveTimeline()) renderLiveFeed();
  else renderTimelineFeed();
}

function tlPersistSource() {
  try { sessionStorage.setItem(TL_SOURCE_KEY, state.timelineSource); } catch { /* */ }
}

function tlNewBadgeLabel() {
  return isLiveTimeline() ? "有新快讯" : "已发布";
}

function tlSyncNewBadgeMode() {
  const badge = $("#tl-new-badge");
  if (!badge) return;
  badge.classList.toggle("live-mode", isLiveTimeline());
  const label = badge.querySelector(".tl-new-badge-label");
  if (label) label.textContent = tlNewBadgeLabel();
}

function tlFilterKey() {
  return JSON.stringify([
    state.timelineQ || "", state.timelinePlatform || "",
    state.timelineCategory || "", state.timelineTag || "", state.timelineFavorite,
    state.timelineSecondary,
  ]);
}

// 生效筛选条件 → 可见 chip 列表：用户随时能看到自己被什么过滤着，逐个可移除
// label 直接存已转义文本（escapeHtml 在构造行完成，渲染处不再重复转义）
function tlPanelFilterOn() {
  return !!(state.timelineQ || state.timelineTag || state.timelineFavorite || state.timelineSecondary);
}

function tlActiveChips() {
  const chips = [];
  if (state.timelineFavorite) chips.push({ key: "favorite", label: "特别关注" });
  if (state.timelineSecondary) chips.push({ key: "secondary", label: "次要大V" });
  if (state.timelineQ) chips.push({ key: "q", label: `关键词：${escapeHtml(state.timelineQ)}` });
  if (state.timelineTag) chips.push({ key: "tag", label: `标签：${escapeHtml(state.timelineTag)}` });
  return chips;
}

function tlSnapshotFilters() {
  return {
    timelinePlatform: state.timelinePlatform,
    timelineFavorite: state.timelineFavorite,
    timelineSecondary: state.timelineSecondary,
    timelineQ: state.timelineQ,
    timelineTag: state.timelineTag,
    timelineCategory: state.timelineCategory,
  };
}

function tlPaintViewToggles() {
  const fav = $("#timeline-fav-toggle");
  if (fav) {
    fav.classList.toggle("fav-on", state.timelineFavorite);
    fav.setAttribute("aria-pressed", String(state.timelineFavorite));
  }
  const sec = $("#timeline-secondary-toggle");
  if (sec) {
    sec.classList.toggle("fav-on", state.timelineSecondary);
    sec.setAttribute("aria-pressed", String(state.timelineSecondary));
    sec.innerHTML = `${state.timelineSecondary ? EYE_ICON : EYE_OFF_ICON} 次要大V`;
  }
}

function tlSyncFilterChrome() {
  const btn = $("#tl-filter-toggle");
  if (btn) btn.classList.toggle("has-filter", tlPanelFilterOn());
  tlSyncActiveChips();
}

function tlRestoreFilters(snap) {
  if (!snap) return;
  state.timelinePlatform = snap.timelinePlatform;
  state.timelineFavorite = snap.timelineFavorite;
  state.timelineSecondary = snap.timelineSecondary;
  state.timelineQ = snap.timelineQ;
  state.timelineTag = snap.timelineTag;
  state.timelineCategory = snap.timelineCategory;
  const q = $("#tl-q"); if (q) q.value = state.timelineQ || "";
  const tag = $("#tl-tag"); if (tag) tag.value = state.timelineTag || "";
  const pills = $("#tl-pills");
  if (pills) pills.innerHTML = tlPillsHtml();
  tlPaintViewToggles();
  tlSyncFilterChrome();
}

function tlActiveChipsHtml() {
  const chips = tlActiveChips();
  if (!chips.length) return "";
  return `<div class="tl-active-chips">${chips.map((c) => `
    <span class="tl-active-chip">${c.label}<button class="tl-chip-x" data-tl-remove-filter="${c.key}" aria-label="移除${c.label}" title="移除该筛选">${X_ICON}</button></span>`).join("")}</div>`;
}

function tlRemoveFilter(key) {
  const revert = tlSnapshotFilters();
  if (key === "q") state.timelineQ = "";
  else if (key === "tag") {
    state.timelineTag = "";
    const tagSel = $("#tl-tag");
    if (tagSel) tagSel.value = "";
  } else if (key === "favorite") state.timelineFavorite = false;
  else if (key === "secondary") state.timelineSecondary = false;
  tlPaintViewToggles();
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
  renderRailTags(_tlDynamicTags.slice(0, 8));
}

const TL_SKELETON = `<div class="tl-skeleton">${Array(4).fill(`
    <div class="tl-sk-item">
      <div class="tl-sk-avatar"></div>
      <div class="tl-sk-lines">
        <div class="tl-sk-line" style="width:42%"></div>
        <div class="tl-sk-line" style="width:96%"></div>
        <div class="tl-sk-line" style="width:74%"></div>
      </div>
    </div>`).join("")}
  </div>`;


function plazaVisibleSet() {
  const list = state.user && Array.isArray(state.user.plaza_platforms)
    ? state.user.plaza_platforms
    : PLATFORM_TABS.filter(Boolean);
  return new Set(list);
}

function timelineVisibleSet() {
  const list = state.user && Array.isArray(state.user.timeline_platforms)
    ? state.user.timeline_platforms
    : plazaVisibleSet();
  return new Set(list);
}

function tlPlatformEntries(vis) {
  return TL_PLATFORMS.filter(([p]) => !p || vis.has(p));
}

function tlPlazaEntries() {
  return tlPlatformEntries(plazaVisibleSet());
}

function tlTimelineEntries() {
  return tlPlatformEntries(timelineVisibleSet());
}

let _platSwipe = null;

function mobilePlatformSwipeIgnore(el) {
  return !!el.closest("a, button, input, select, textarea, .tl-pills, .platform-tabs, .icon-badge-bar, .tl-filter-panel, .home-filter-content, .lightbox, .bottom-nav, .post-images");
}

function tlSwipeEntries() {
  const out = [];
  for (const entry of tlTimelineEntries()) {
    out.push(entry);
    if (!entry[0]) out.push(["live", "快讯"]);
  }
  return out;
}

function mobilePlatformSwipeSurface(el) {
  if ($("#tl-feed-panel") && el.closest(".tl-main")) return "timeline";
  if ($("#home-mobile-platforms") && ($("#kol-list")?.contains(el) || el.closest(".home-panel"))) return "home";
  return null;
}

function mobilePlatformSwipeContext(surface) {
  if (surface === "timeline") {
    return {
      current: () => (isLiveTimeline() ? "live" : state.timelinePlatform),
      apply: (p) => (p === "live" ? tlPickSource("live") : tlPickPlatform(p)),
      entries: tlSwipeEntries,
    };
  }
  if (surface === "home") return { current: () => state.platform, apply: (p) => homePickMobilePlatform(p), entries: tlPlazaEntries };
  return null;
}

function mobileSwipeAdjacent(current, dir, entries) {
  const idx = entries.findIndex(([p]) => p === current);
  const next = idx + dir;
  if (idx < 0 || next < 0 || next >= entries.length) return null;
  return entries[next][0];
}

function onPlatSwipeStart(e) {
  if (!isMobileTimelineFilter() || e.touches.length !== 1) {
    _platSwipe = null;
    return;
  }
  const t = e.target;
  if (!(t instanceof Element) || mobilePlatformSwipeIgnore(t)) {
    _platSwipe = null;
    return;
  }
  const surface = mobilePlatformSwipeSurface(t);
  if (!surface) {
    _platSwipe = null;
    return;
  }
  _platSwipe = { x: e.touches[0].clientX, y: e.touches[0].clientY, surface };
}

function onPlatSwipeEnd(e) {
  if (!_platSwipe) return;
  const start = _platSwipe;
  _platSwipe = null;
  if (!isMobileTimelineFilter()) return;
  const dx = e.changedTouches[0].clientX - start.x;
  const dy = e.changedTouches[0].clientY - start.y;
  if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
  const ctx = mobilePlatformSwipeContext(start.surface);
  if (!ctx) return;
  const next = mobileSwipeAdjacent(ctx.current(), dx < 0 ? 1 : -1, ctx.entries());
  if (next === null) return;
  ctx.apply(next);
}

function ensureMobilePlatformSwipe() {
  if (ensureMobilePlatformSwipe.bound) return;
  ensureMobilePlatformSwipe.bound = true;
  document.addEventListener("touchstart", onPlatSwipeStart, { passive: true });
  document.addEventListener("touchend", onPlatSwipeEnd, { passive: true });
}

function ensurePlazaPlatformSelection() {
  const plaza = plazaVisibleSet();
  const timeline = timelineVisibleSet();
  if (state.timelinePlatform && !timeline.has(state.timelinePlatform)) {
    state.timelinePlatform = "";
  }
  if (state.platform && !plaza.has(state.platform)) {
    state.platform = "";
  }
}

function isMobileTimelineFilter() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function isWideTimeline() {
  return window.matchMedia("(min-width: 1280px)").matches;
}

function ensureWideTimelineWatch() {
  if (_tlWideWatchBound) return;
  _tlWideWatchBound = true;
  window.matchMedia("(min-width: 1280px)").addEventListener("change", () => {
    if ($("#tl-feed-panel")) renderTimeline(routeRenderSeq);
  });
}

function tlViewTogglesHtml() {
  return `
          <button id="timeline-fav-toggle" class="fav-toggle ${state.timelineFavorite ? "fav-on" : ""}" aria-pressed="${state.timelineFavorite}" onclick="toggleTimelineFav()">${STAR_SVG} 特别关注</button>
          <button id="timeline-secondary-toggle" class="fav-toggle ${state.timelineSecondary ? "fav-on" : ""}" aria-pressed="${state.timelineSecondary}" onclick="toggleTimelineSecondary()" title="显示/隐藏次要大V动态（默认隐藏）">${state.timelineSecondary ? EYE_ICON : EYE_OFF_ICON} 次要大V</button>`;
}

function tlSearchBarHtml() {
  const live = isLiveTimeline();
  const q = live ? state.liveQ : state.timelineQ;
  const label = live ? "搜索快讯" : "搜索动态";
  return `<div class="search-bar tl-rail-search">
      ${SEARCH_ICON}
      <input id="tl-q" type="search" placeholder="${label}" value="${escapeHtml(q || "")}" aria-label="${label}" oninput="tlOnSearchInput(this.value)" onkeydown="if(event.key==='Enter')tlApplyRailSearch()">
    </div>`;
}

function tlOnSearchInput(value) {
  if (isLiveTimeline()) liveSearch(value);
}

function tlApplyRailSearch() {
  if (isLiveTimeline()) {
    const q = $("#tl-q");
    liveSearch(q ? q.value : state.liveQ);
    $("#tl-filterbar")?.classList.remove("open");
    return;
  }
  tlApplyFilter();
}

function tlSyncSearchBox() {
  const q = $("#tl-q");
  if (!q) return;
  const live = isLiveTimeline();
  q.value = live ? (state.liveQ || "") : (state.timelineQ || "");
  const label = live ? "搜索快讯" : "搜索动态";
  q.placeholder = label;
  q.setAttribute("aria-label", label);
}

function tlFilterActionsHtml() {
  return `<div class="tl-actions">
          <button id="tl-filter-toggle" class="fav-toggle ${tlPanelFilterOn() ? "has-filter" : ""}" aria-label="筛选" aria-expanded="false" aria-controls="tl-filter-panel" onclick="tlFilterPanel()">${FILTER_ICON}筛选</button>
        </div>`;
}

function tlFilterPanelHtml() {
  const live = isLiveTimeline();
  return `<div class="tl-filter-panel" id="tl-filter-panel">
        ${tlSearchBarHtml()}
        ${live ? "" : `<div class="tl-filter-views">${tlViewTogglesHtml()}</div>
        <div class="tl-filter-row">
          <select id="tl-tag" class="form-control" onchange="tlApplyFilter()"><option value="">全部标签</option></select>
        </div>`}
        <div class="tl-filter-actions">
          <button class="btn-ghost" onclick="tlResetFilters()">清除筛选</button>
          <button class="btn-normal" onclick="tlApplyFilter()">完成</button>
        </div>
      </div>`;
}

function syncTimelineSourceView() {
  if (!$("#tl-feed-panel") || !$("#tl-filterbar")) {
    renderTimeline(routeRenderSeq);
    return;
  }
  const live = isLiveTimeline();
  const wide = isWideTimeline();
  document.querySelector(".tl-layout")?.classList.toggle("live-mode", live);
  $("#tl-filterbar")?.classList.toggle("live-mode", live);
  const pills = $("#tl-pills");
  if (pills) pills.innerHTML = tlPillsHtml();
  $("#live-toolbar")?.remove();
  const bar = $("#tl-platform-bar");
  const actions = bar?.querySelector(".tl-actions");
  if (wide) actions?.remove();
  else if (bar && !actions) bar.insertAdjacentHTML("beforeend", tlFilterActionsHtml());
  const panel = $("#tl-filter-panel");
  if (wide) {
    panel?.remove();
    $("#tl-filterbar")?.classList.remove("open");
  } else if (panel) {
    panel.outerHTML = tlFilterPanelHtml();
  } else {
    const badge = $("#tl-new-badge");
    if (badge) badge.insertAdjacentHTML("beforebegin", tlFilterPanelHtml());
    else $("#tl-filterbar")?.insertAdjacentHTML("beforeend", tlFilterPanelHtml());
    if (!live) loadTimelineTags().catch(() => { _tlTags = []; _tlDynamicTags = []; });
  }
  $("#tl-filterbar")?.classList.remove("open");
  const filterBtn = $("#tl-filter-toggle");
  if (filterBtn) filterBtn.setAttribute("aria-expanded", "false");
  const feedPanel = $("#tl-feed-panel");
  const head = $("#live-feed-head");
  if (live) {
    if (head) head.outerHTML = liveFeedHeadHtml();
    else if (feedPanel) feedPanel.insertAdjacentHTML("afterbegin", liveFeedHeadHtml());
  } else {
    head?.remove();
  }
  tlSyncSearchBox();
  renderLiveRail();
  const chips = $("#tl-active-chips-wrap");
  if (chips) {
    chips.classList.toggle("is-hidden", live);
    chips.innerHTML = live ? "" : tlActiveChipsHtml();
  }
  const btn = $(".tl-new-badge-btn");
  if (btn) btn.setAttribute("aria-label", live ? "有新快讯，点击查看" : "有新动态，点击查看");
  $("#tl-new-badge")?.classList.toggle("live-mode", live);
  if (live) startLiveClock();
  else stopLiveClock();
  tlSyncNewBadgeMode();
  startTimelinePoll();
  if (live) {
    if (_livePosts.length) {
      renderLiveFeed();
      pollFeedUpdates();
    } else {
      loadTimeline(true, routeRenderSeq).then(() => pollFeedUpdates());
    }
    return;
  }
  if (_tlPosts.length && _tlLoadedFilter === tlFilterKey()) {
    renderTimelineFeed();
    pollFeedUpdates();
    if (wide) loadTimelineRail(routeRenderSeq);
  } else {
    loadTimeline(true, routeRenderSeq).then(() => {
      if (wide) loadTimelineRail(routeRenderSeq);
      pollFeedUpdates();
    });
  }
  prefetchLiveFeed();
}

function tlPickSource(source) {
  const next = source === "live" ? "live" : "kol";
  if (state.timelineSource === next) return;
  if (isLiveTimeline()) _liveSavedScrollY = window.scrollY;
  else _tlSavedScrollY = window.scrollY;
  state.timelineSource = next;
  tlPersistSource();
  stopTimelinePoll();
  if ($("#tl-feed-panel")) syncTimelineSourceView();
  else renderTimeline(routeRenderSeq);
}

async function renderTimeline(seq) {
  setPageTitle("最新动态");
  ensurePlazaPlatformSelection();
  ensureWideTimelineWatch();
  const live = isLiveTimeline();
  const reuse = live
    ? _livePosts.length > 0
    : (_tlPosts.length && _tlLoadedFilter === tlFilterKey());
  const wide = isWideTimeline();
  $("#main").innerHTML = `
    <div class="tl-layout${live ? " live-mode" : ""}">
    <div class="tl-main">
    <div class="tl-filterbar${live ? " live-mode" : ""}" id="tl-filterbar">
      <div class="tl-filterbar-top icon-badge-bar" id="tl-platform-bar">
        <div class="tl-pills" id="tl-pills" role="radiogroup" aria-label="平台和内容源">${tlPillsHtml()}</div>
        ${wide ? "" : tlFilterActionsHtml()}
      </div>
      ${wide ? "" : tlFilterPanelHtml()}
      <div class="tl-new-badge${live ? " live-mode" : ""}" id="tl-new-badge">
        <button class="tl-new-badge-btn" onclick="refreshTimeline()" aria-label="${live ? "有新快讯，点击查看" : "有新动态，点击查看"}">
          ${ARROW_UP_ICON}
          <span class="tl-badge-avatars" id="tl-new-avatars"></span>
          <span class="tl-new-badge-label">${tlNewBadgeLabel()}</span>
        </button>
      </div>
    </div>
    <div id="tl-active-chips-wrap"${live ? ' class="is-hidden"' : ""}>${live ? "" : tlActiveChipsHtml()}</div>
    <div class="tl-ima-entry">
      <button type="button" class="tl-ima-entry-btn" onclick="go('news')">
        <span class="tl-ima-entry-icon">${NEWS_ICON}</span>
        <span><strong>财经新闻</strong><small>打开财经新闻</small></span>
        <span class="nav-badge" data-news-badge hidden></span>
      </button>
      <button type="button" class="tl-ima-entry-btn" onclick="go('knowledge')">
        <span class="tl-ima-entry-icon">${BOOK_ICON}</span>
        <span><strong>研报中心</strong><small>打开研报中心</small></span>
      </button>
    </div>
    <section class="section-panel tl-feed-panel" id="tl-feed-panel">
      ${live ? liveFeedHeadHtml() : ""}
      <div id="feed">${reuse ? "" : TL_SKELETON}</div>
    </section>
    </div>
    ${wide ? `<aside class="tl-rail" id="tl-rail" aria-label="发现">
      <div class="tl-rail-head">${tlSearchBarHtml()}</div>
      <div class="tl-rail-body">
        <div class="tl-rail-card tl-rail-view" id="tl-rail-view">${tlViewTogglesHtml()}</div>
        <section class="tl-rail-card tl-market" id="tl-market" aria-labelledby="market-title"></section>
        <div id="tl-live-rail"></div>
        <div id="tl-rail-recs"></div>
        <div id="tl-rail-tags"></div>
      </div>
    </aside>` : ""}
    </div>`;
  tlSyncNewBadgeMode();
  startMarketQuotes();
  if (live) startLiveClock();
  else stopLiveClock();
  renderLiveRail();
  if (reuse) {
    renderFeed();
    window.scrollTo(0, feedScrollY());
    startTimelinePoll();
    pollFeedUpdates().then(() => autoConsumeTimelinePending(seq));
    if (!wide && !live) loadTimelineTags().catch(() => { _tlTags = []; _tlDynamicTags = []; });
    if (wide) loadTimelineRail(seq);
    if (!live) prefetchLiveFeed();
    return;
  }
  if (live) {
    _liveCursor = "";
    _liveHasMore = true;
    _liveLatestId = 0;
    try {
      await loadTimeline(true, seq);
      startTimelinePoll();
      pollFeedUpdates();
    } catch (err) {
      if (!routeStillActive(seq)) return;
      const feed = $("#feed");
      if (feed) feed.innerHTML = emptyState("加载失败: " + err.message,
        `<div><button class="btn-normal" onclick="renderTimeline()">重试</button></div>`);
    }
    return;
  }
  _tlPosts.length = 0;
  _tlOffset = 0;
  _tlHasMore = true;
  _tlLatestId = 0;
  try {
    if (!wide) {
      await loadTimelineTags().catch(() => { _tlTags = []; _tlDynamicTags = []; }); // 标签下拉失败降级，不阻塞 feed
    }
    await loadTimeline(true, seq);
    if (wide) loadTimelineRail(seq);
    startTimelinePoll();
    pollFeedUpdates();
    prefetchLiveFeed();
  } catch (err) {
    if (!routeStillActive(seq)) return;
    const feed = $("#feed");
    if (feed) feed.innerHTML = emptyState("加载失败: " + err.message,
      `<div><button class="btn-normal" onclick="renderTimeline()">重试</button></div>`);
  }
}

function startTimelinePoll() {
  stopTimelinePoll();
  ensureTimelineVisibilityPoll();
  const interval = isLiveTimeline() ? 15000 : 60000;
  _tlPollTimer = setInterval(pollFeedUpdates, interval);
}
function stopTimelinePoll() {
  if (_tlPollTimer) { clearInterval(_tlPollTimer); _tlPollTimer = null; }
  stopLiveClock();
}

function ensureTimelineVisibilityPoll() {
  if (ensureTimelineVisibilityPoll.bound) return;
  ensureTimelineVisibilityPoll.bound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (!$("#feed")) return;
    pollFeedUpdates();
  });
}

let _tlPollFeedBusy = false;

async function pollFeedUpdates() {
  // 计时器/visibilitychange/路由切入三路都会触发，防重叠轮询
  if (document.visibilityState === "hidden" || _tlPollFeedBusy) return;
  const live = isLiveTimeline();
  const latestId = live ? _liveLatestId : _tlLatestId;
  const pendingLatestId = live ? _livePendingLatestId : _tlPendingLatestId;
  const pendingNew = feedPendingNew();
  if (!latestId || !$("#feed") || (live && !isLiveTimeline()) || (!live && isLiveTimeline())) return;
  _tlPollFeedBusy = true;
  const seq = routeRenderSeq;
  try {
    let newer = [];
    if (live) {
      const params = new URLSearchParams({
        limit: "30",
        since_id: String(pendingLatestId || latestId),
      });
      const data = await api(`/api/live/wscn?${params}`, { signal: AbortSignal.timeout(15000) });
      if (!routeStillActive(seq) || !$("#feed") || !isLiveTimeline()) return;
      newer = data.items || [];
    } else {
      const params = new URLSearchParams({ limit: "50", since_id: String(pendingLatestId || latestId) });
      if (state.timelineQ) params.set("q", state.timelineQ);
      if (state.timelinePlatform) params.set("platform", state.timelinePlatform);
      if (state.timelineCategory) params.set("category_id", state.timelineCategory);
      if (state.timelineTag) params.set("tag", state.timelineTag);
      if (state.timelineFavorite) params.set("favorite", "1");
      if (state.timelineSecondary) params.set("include_secondary", "1");
      const posts = await api(`/api/my/feed?${params}`, { signal: AbortSignal.timeout(15000) });
      if (!routeStillActive(seq) || !$("#feed") || isLiveTimeline()) return;
      newer = posts.filter((p) => p.id > pendingLatestId);
    }
    if (!newer.length) return;
    const have = new Set(pendingNew.map((p) => p.id));
    for (const p of newer) {
      if (!have.has(p.id)) {
        have.add(p.id);
        pendingNew.push(p);
      }
    }
    if (live) _livePendingLatestId = Math.max(_livePendingLatestId, ...newer.map((p) => p.id));
    else _tlPendingLatestId = Math.max(_tlPendingLatestId, ...newer.map((p) => p.id));
    const unit = live ? "快讯" : "动态";
    const label = `${pendingNew.length} 条新${unit}，点击查看`;
    const btn = $(".tl-new-badge-btn");
    if (btn) {
      btn.title = label;
      btn.setAttribute("aria-label", label);
    }
    if (!live) {
      const avatars = $("#tl-new-avatars");
      if (avatars) avatars.innerHTML = tlBadgeAvatarsHtml(pendingNew);
    }
    if (live) {
      await autoConsumeLivePending(seq);
      // 顶部已并入或正在 refreshTimeline，都不要出胶囊，避免闪一下
      if (!feedPendingNew().length || _tlRefreshing) return;
    }
    $("#tl-new-badge")?.classList.add("show");
    $("#tl-feed-panel")?.classList.add("has-new");
  } catch { /* 轮询失败静默 */ } finally {
    _tlPollFeedBusy = false;
  }
}

// 新帖胶囊头像：去重取前 3 个（无头像用首字色块）；超出的作者不另画 +N，条数只在 aria-label
function tlBadgeAvatarsHtml(posts, max = 3) {
  const seen = new Set();
  const avs = [];
  for (const p of posts) {
    const key = p.kol_id || p.kol_name;
    if (seen.has(key)) continue;
    seen.add(key);
    if (avs.length >= max) break;
    avs.push(p.avatar_url
      ? `<img src="${escapeHtml(p.avatar_url)}" alt="" onerror="this.remove()">`
      : `<span class="ph">${escapeHtml(avatarText(p.kol_name))}</span>`);
  }
  return avs.join("");
}

// 切回动态页时自动并入增量新帖：顶部场景与点「新动态」胶囊完全一致（并入+回顶）；
// 深读 restored 位置超过阈值则不打扰，保留胶囊供手动查看
async function autoConsumeTimelinePending(seq) {
  if (!routeStillActive(seq) || isLiveTimeline()) return;
  if (!$("#tl-feed-panel") || !feedPendingNew().length) return;
  if (window.scrollY > 240) return;
  try {
    await refreshTimeline({ pollFirst: false });
  } catch (e) {
    console.warn("自动并入新帖失败，保留胶囊供重试", e);
  }
}

// 快讯在最新位置时自动并入；用户深读旧内容时保留新快讯提示。
async function autoConsumeLivePending(seq) {
  if (!routeStillActive(seq) || !isLiveTimeline()) return;
  if (!$("#tl-feed-panel") || !feedPendingNew().length) return;
  if (window.scrollY > 240) return;
  try {
    await refreshTimeline({ pollFirst: false });
  } catch (e) {
    console.warn("自动并入新帖失败，保留胶囊供重试", e);
  }
}

async function refreshTimeline({ pollFirst = true } = {}) {
  if (_tlRefreshing) return;
  _tlRefreshing = true;
  try {
    const live = isLiveTimeline();
    const pending = feedPendingNew();
    const posts = feedPosts();
    if (!pending.length) {
      await loadTimeline(true, routeRenderSeq);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    if (pollFirst) await pollFeedUpdates();
    const seen = new Set(posts.map((p) => p.id));
    const incoming = pending.filter((p) => !seen.has(p.id)).sort((a, b) => b.id - a.id);
    if (incoming.length) {
      posts.unshift(...incoming);
      if (live) {
        _liveLatestId = Math.max(_liveLatestId, _livePendingLatestId, ...incoming.map((p) => p.id));
        _livePendingNew.length = 0;
        _livePendingLatestId = 0;
      } else {
        _tlOffset += incoming.length;
        _tlLatestId = Math.max(_tlLatestId, _tlPendingLatestId);
        _tlPendingNew.length = 0;
        _tlPendingLatestId = 0;
      }
      renderFeed();
    }
    $("#tl-new-badge")?.classList.remove("show");
    $("#tl-feed-panel")?.classList.remove("has-new");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    // 渲染/网络异常：保留胶囊供重试（_tlRefreshing 已在 finally 复位）
    console.warn("动态页刷新异常", e);
  } finally {
    _tlRefreshing = false;
  }
}

function tlPillsHtml() {
  const liveSelected = isLiveTimeline();
  return tlSwipeEntries().map(([p, label]) => {
    if (p === "live") {
      return `
    <button class="tl-pill ${liveSelected ? "selected" : ""}" role="radio" data-platform="live" aria-label="快讯" title="快讯" aria-checked="${liveSelected}" onclick="tlPickSource('live')">
      ${WSCN_LIVE_ICON}<span>快讯</span>
    </button>`;
    }
    const selected = !liveSelected && state.timelinePlatform === p;
    const short = platformShortLabel(p);
    return `
    <button class="tl-pill ${selected ? "selected" : ""}" role="radio" data-platform="${p}" aria-label="${label}" title="${label}" aria-checked="${selected}">
      ${PLATFORM_ICONS[p || ""]}
      <span>${short}</span>
    </button>`;
  }).join("");
}

function tlPickPlatform(p) {
  const revert = tlSnapshotFilters();
  const leftLive = isLiveTimeline();
  if (leftLive) {
    state.timelineSource = "kol";
    tlPersistSource();
  }
  state.timelinePlatform = p;
  // 平台章与特别关注章互斥：切平台即退出「只看特别关注」视图
  if (state.timelineFavorite) state.timelineFavorite = false;
  if (leftLive) {
    stopTimelinePoll();
    if ($("#tl-feed-panel")) syncTimelineSourceView();
    else renderTimeline(routeRenderSeq);
    return;
  }
  const pills = $("#tl-pills");
  if (pills) pills.innerHTML = tlPillsHtml();
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
}

function tlSyncActiveChips() {
  const wrap = $("#tl-active-chips-wrap");
  if (wrap) wrap.innerHTML = tlActiveChipsHtml();
}

function tlFilterPanel() {
  const bar = $("#tl-filterbar");
  if (!bar) return;
  const open = bar.classList.toggle("open");
  const btn = $("#tl-filter-toggle");
  if (btn) btn.setAttribute("aria-expanded", String(open));
  if (open) $("#tl-q")?.focus();
}

function tlApplyFilter() {
  if (isLiveTimeline()) {
    tlApplyRailSearch();
    return;
  }
  const revert = tlSnapshotFilters();
  const q = $("#tl-q");
  if (q) state.timelineQ = q.value.trim();
  const tag = $("#tl-tag");
  if (tag) state.timelineTag = tag.value;
  state.timelineCategory = "";
  $("#tl-filterbar")?.classList.remove("open");
  const btn = $("#tl-filter-toggle");
  if (btn) btn.setAttribute("aria-expanded", "false");
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
}

function tlResetFilters() {
  if (isLiveTimeline()) {
    state.liveQ = "";
    tlSyncSearchBox();
    renderLiveFeed();
    $("#tl-filterbar")?.classList.remove("open");
    const fb = $("#tl-filter-toggle");
    if (fb) fb.setAttribute("aria-expanded", "false");
    return;
  }
  const revert = tlSnapshotFilters();
  state.timelineQ = "";
  state.timelineCategory = "";
  state.timelineTag = "";
  state.timelinePlatform = "";
  state.timelineFavorite = false;
  state.timelineSecondary = false;
  const q = $("#tl-q"); if (q) q.value = "";
  const tag = $("#tl-tag"); if (tag) tag.value = "";
  const pills = $("#tl-pills"); if (pills) pills.innerHTML = tlPillsHtml();
  const fb = $("#tl-filter-toggle"); if (fb) fb.setAttribute("aria-expanded", "false");
  $("#tl-filterbar")?.classList.remove("open");
  tlPaintViewToggles();
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
  renderRailTags(_tlDynamicTags.slice(0, 8));
}

// 点击帖子标签直接进入该标签筛选（复用 timelineTag 状态与筛选条）
function tlPickTag(tag) {
  const revert = tlSnapshotFilters();
  state.timelineTag = tag;
  const tagSel = $("#tl-tag");
  if (tagSel) tagSel.value = tag;
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
  renderRailTags(_tlDynamicTags.slice(0, 8));
}

async function loadTimelineTags() {
  if (!_tlTags) {
    const data = await api("/api/tags");
    // 词表是对象数组（{tag, keywords}）+ 贴文实际出现的动态标签（含股票名），下拉合并去重
    const vocabTags = (Array.isArray(data?.tags) ? data.tags : [])
      .map((r) => (typeof r === "string" ? r : r.tag)).filter(Boolean);
    const dynamicTags = Array.isArray(data?.dynamic_tags) ? data.dynamic_tags : [];
    _tlDynamicTags = dynamicTags;
    _tlTags = [...new Set([...vocabTags, ...dynamicTags])];
  }
  const sel = $("#tl-tag");
  if (!sel) return;
  sel.innerHTML = `<option value="">全部标签</option>` + _tlTags.map((t) =>
    `<option value="${escapeHtml(t)}" ${state.timelineTag === t ? "selected" : ""}>${escapeHtml(t)}</option>`).join("");
}

async function loadTimelineRail(routeSeq) {
  if (!$("#tl-rail")) return;
  try {
    const recs = await api("/api/recommendations?unsubscribed=1");
    if (!routeStillActive(routeSeq) || !$("#tl-rail")) return;
    renderRailRecs(Array.isArray(recs) ? recs : []);
  } catch (err) {
    const el = $("#tl-rail-recs");
    if (el) el.innerHTML = railFailHtml("推荐关注", "推荐加载失败", err);
  }
  try {
    let tags = _tlDynamicTags;
    if (!_tlTags) {
      const data = await api("/api/tags");
      tags = Array.isArray(data?.dynamic_tags) ? data.dynamic_tags : [];
      _tlDynamicTags = tags;
    }
    if (!routeStillActive(routeSeq) || !$("#tl-rail")) return;
    renderRailTags((tags || []).slice(0, 8));
  } catch (err) {
    const el = $("#tl-rail-tags");
    if (el) el.innerHTML = railFailHtml("热门标签", "标签加载失败", err);
  }
}

function railFailHtml(title, lead, err) {
  const detail = err?.message ? `：${escapeHtml(err.message)}` : "";
  return `<section class="tl-rail-card">
      <h3 class="tl-rail-title">${escapeHtml(title)}</h3>
      <div class="tl-rail-fail">
        <p class="muted">${escapeHtml(lead)}${detail}</p>
        <button type="button" class="btn-ghost" onclick="reloadTimelineRail()">重试</button>
      </div>
    </section>`;
}

function renderRailRecs(recs) {
  const el = $("#tl-rail-recs");
  if (!el) return;
  const list = recs.filter((r) => !r.subscribed).slice(0, 4);
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <section class="tl-rail-card">
      <h3 class="tl-rail-title">推荐关注</h3>
      ${list.map((r) => `
        <div class="tl-rail-rec">
          ${avatarHtml(r.name, r.avatar_url)}
          <div class="tl-rail-rec-info">
            <a class="tl-rail-rec-name" href="/kol/${r.id}">${escapeHtml(r.name)}</a>
            <div class="tl-rail-rec-meta">${escapeHtml(PLATFORM_LABELS[r.platform] || r.platform)}${r.category_name ? " · " + escapeHtml(r.category_name) : ""}</div>
          </div>
          <button type="button"
            class="btn-ghost tl-rail-subscribe"
            data-subscribed="0"
            aria-label="订阅${escapeHtml(r.name)}"
            onclick="railToggleSubscribe(${r.id}, this)">
            <span class="tl-rail-subscribe-state">订阅</span>
            <span class="tl-rail-subscribe-action" aria-hidden="true">退订</span>
          </button>
        </div>`).join("")}
      <a class="tl-rail-more" href="/search">显示更多</a>
    </section>`;
}

function renderRailTags(tags) {
  const el = $("#tl-rail-tags");
  if (!el) return;
  if (!tags.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <section class="tl-rail-card">
      <h3 class="tl-rail-title">热门标签</h3>
      <div class="tl-rail-tags">${tags.map((t) => `
        <button type="button" class="tl-rail-tag ${state.timelineTag === t ? "selected" : ""}" data-tag="${escapeHtml(t)}" onclick="tlPickTag(this.dataset.tag)">${escapeHtml(t)}</button>`).join("")}</div>
    </section>`;
}

async function railToggleSubscribe(kolId, btn) {
  if (!btn || btn.disabled) return;
  const subscribed = btn.dataset.subscribed === "1";
  const name = btn.closest(".tl-rail-rec")?.querySelector(".tl-rail-rec-name")?.textContent || "该大V";
  const restoreFocus = document.activeElement === btn && btn.matches(":focus-visible");
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  try {
    if (subscribed) {
      await api(`/api/subscriptions/${kolId}`, { method: "DELETE" });
    } else {
      await api("/api/subscriptions", {
        method: "POST",
        body: JSON.stringify({ kol_id: kolId, type: "post" }),
      });
    }
    const nextSubscribed = !subscribed;
    btn.dataset.subscribed = nextSubscribed ? "1" : "0";
    btn.classList.toggle("subscribed", nextSubscribed);
    btn.setAttribute("aria-label", `${nextSubscribed ? "退订" : "订阅"}${name}`);
    btn.title = nextSubscribed ? "点击退订" : "";
    const stateLabel = btn.querySelector(".tl-rail-subscribe-state");
    if (stateLabel) stateLabel.textContent = nextSubscribed ? "✓ 已订阅" : "订阅";
    if (state.user) {
      const delta = nextSubscribed ? 1 : -1;
      state.user.subscription_count = Math.max(0, (state.user.subscription_count || 0) + delta);
    }
    flash(`已${nextSubscribed ? "订阅" : "退订"}「${name}」`);
  } catch (err) {
    flash(`${subscribed ? "退订" : "订阅"}「${name}」失败: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    if (restoreFocus && btn.isConnected && document.activeElement === document.body) {
      btn.focus({ preventScroll: true });
    }
  }
}

async function loadTimeline(reset = true, routeSeq, opts) {
  opts = opts || {};
  const live = isLiveTimeline();
  if (!reset && (live ? _liveLoadingMore : _tlLoadingMore)) return;
  if (!reset) {
    if (live) _liveLoadingMore = true;
    else _tlLoadingMore = true;
  }
  const seq = live ? ++_liveSeq : ++_tlSeq;
  const pills = $("#tl-pills");
  if (reset) {
    const feed = $("#feed");
    if (feed) feed.innerHTML = TL_SKELETON;
    if (!live) pills?.setAttribute("aria-busy", "true");
  }
  try {
    if (live) {
      const params = new URLSearchParams({ limit: "30" });
      if (!reset && _liveCursor) params.set("cursor", _liveCursor);
      const data = await liveWscnRequest(params);
      if (seq !== _liveSeq || !$("#feed") || !routeStillActive(routeSeq) || !isLiveTimeline()) return;
      if (reset) _livePosts.length = 0;
      const items = data.items || [];
      _livePosts.push(...items);
      _liveCursor = data.next_cursor || "";
      _liveHasMore = !!(data.next_cursor && items.length);
      if (reset) {
        _liveLatestId = _livePosts[0]?.id || 0;
        _livePendingNew.length = 0;
        _livePendingLatestId = 0;
        $("#tl-new-badge")?.classList.remove("show");
        $("#tl-feed-panel")?.classList.remove("has-new");
      }
    } else {
      const params = new URLSearchParams({ limit: "50", offset: String(reset ? 0 : _tlOffset) });
      if (state.timelineQ) params.set("q", state.timelineQ);
      if (state.timelinePlatform) params.set("platform", state.timelinePlatform);
      if (state.timelineCategory) params.set("category_id", state.timelineCategory);
      if (state.timelineTag) params.set("tag", state.timelineTag);
      if (state.timelineFavorite) params.set("favorite", "1");
      if (state.timelineSecondary) params.set("include_secondary", "1");
      const posts = await api(`/api/my/feed?${params}`);
      if (seq !== _tlSeq || !$("#feed") || !routeStillActive(routeSeq)) return;
      if (reset) {
        _tlPosts.length = 0;
        _tlOffset = 0;
      }
      _tlPosts.push(...posts);
      _tlOffset += posts.length;
      _tlHasMore = posts.length >= 50;
      if (reset) {
        _tlLatestId = posts[0]?.id || 0;
        _tlLoadedFilter = tlFilterKey();
        _tlPendingNew.length = 0;
        _tlPendingLatestId = 0;
        $("#tl-new-badge")?.classList.remove("show");
        $("#tl-feed-panel")?.classList.remove("has-new");
      }
    }
    renderFeed();
  } catch (err) {
    const activeSeq = live ? _liveSeq : _tlSeq;
    if (seq !== activeSeq || !$("#feed") || !routeStillActive(routeSeq)) return;
    if (!live && reset && opts.revert) tlRestoreFilters(opts.revert);
    $("#feed").innerHTML = emptyState("加载失败: " + err.message,
      `<div><button class="btn-normal" onclick="reloadTimeline()">重试</button></div>`);
  } finally {
    if (reset && !live) pills?.removeAttribute("aria-busy");
    if (!reset) {
      if (live) _liveLoadingMore = false;
      else _tlLoadingMore = false;
    }
  }
}

function feedLoadMore() {
  if (isLiveTimeline()) {
    if (_liveLoadingMore || !_liveHasMore) return;
  } else if (_tlLoadingMore || !_tlHasMore) {
    return;
  }
  stopFeedAutoLoad();
  const sentinel = $("#feed-load-sentinel");
  if (sentinel) {
    sentinel.classList.add("is-loading");
    sentinel.setAttribute("aria-busy", "true");
    sentinel.innerHTML = `<span class="feed-load-spinner" aria-hidden="true"></span><span>正在加载更多…</span>`;
  }
  loadTimeline(false, routeRenderSeq);
}

function timelineLoadMore() {
  feedLoadMore();
}

function liveWscnRequest(params) {
  const key = params.toString();
  if (_liveInflight && _liveInflight.key === key) return _liveInflight.p;
  const p = api(`/api/live/wscn?${params}`).finally(() => {
    if (_liveInflight && _liveInflight.p === p) _liveInflight = null;
  });
  _liveInflight = { key, p };
  return p;
}

function prefetchLiveFeed() {
  if (isLiveTimeline() || _livePosts.length) return;
  const params = new URLSearchParams({ limit: "30" });
  liveWscnRequest(params).then((data) => {
    if (isLiveTimeline() || _livePosts.length) return;
    const items = data.items || [];
    if (!items.length) return;
    _livePosts.push(...items);
    _liveCursor = data.next_cursor || "";
    _liveHasMore = !!(data.next_cursor && items.length);
    _liveLatestId = _livePosts[0]?.id || 0;
  }).catch(() => { /* 预取失败时点进快讯再拉 */ });
}

function liveClockText(now = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
  return `${now.getMonth() + 1}月${now.getDate()}日，星期${weekdays[now.getDay()]}，${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function liveFeedHeadHtml() {
  return `<div class="live-feed-head" id="live-feed-head">
    <div class="live-toolbar-clock" id="live-clock" role="status" aria-live="off">${liveClockText()}</div>
    <span class="live-toolbar-divider" aria-hidden="true"></span>
    <label class="live-important-toggle" for="live-important">
      <input id="live-important" type="checkbox" ${state.liveImportant ? "checked" : ""} onchange="toggleLiveImportant(this.checked)">
      <span>只看重要的</span>
    </label>
  </div>`;
}

function updateLiveClock() {
  const clock = $("#live-clock");
  if (clock) clock.textContent = liveClockText();
}

function liveRailHtml() {
  const total = _livePosts.length;
  const important = _livePosts.filter((item) => Number(item.score) >= 2).length;
  const latest = _livePosts[0]?.published_at;
  const updated = latest ? fmtPublished(latest, true) : "暂无";
  return `<section class="tl-rail-card live-rail-card">
      <h3 class="tl-rail-title">快讯概览</h3>
      <dl class="live-rail-stats">
        <div><dt>已加载</dt><dd>${total} 条</dd></div>
        <div><dt>重要快讯</dt><dd>${important} 条</dd></div>
        <div><dt>最新快讯</dt><dd>${escapeHtml(updated)}</dd></div>
      </dl>
      <button type="button" class="btn-ghost live-rail-refresh" onclick="refreshTimeline()">${REFRESH_ICON} 刷新快讯</button>
    </section>`;
}

function renderLiveRail() {
  const el = $("#tl-live-rail");
  if (el) el.innerHTML = isLiveTimeline() ? liveRailHtml() : "";
}

function startLiveClock() {
  stopLiveClock();
  updateLiveClock();
  _liveClockTimer = setInterval(updateLiveClock, 1000);
}

function stopLiveClock() {
  if (_liveClockTimer) { clearInterval(_liveClockTimer); _liveClockTimer = null; }
}

function stopFeedAutoLoad() {
  _feedLoadObserver?.disconnect();
  _feedLoadObserver = null;
  if (_feedLoadFallback) {
    window.removeEventListener("scroll", _feedLoadFallback);
    _feedLoadFallback = null;
  }
}

function startFeedAutoLoad() {
  stopFeedAutoLoad();
  const sentinel = $("#feed-load-sentinel");
  const hasMore = isLiveTimeline() ? _liveHasMore : _tlHasMore;
  if (!sentinel || !hasMore) return;
  const load = () => feedLoadMore();
  if ("IntersectionObserver" in window) {
    _feedLoadObserver = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) load();
    }, { rootMargin: "400px 0px" });
    _feedLoadObserver.observe(sentinel);
    return;
  }
  _feedLoadFallback = () => {
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 400) load();
  };
  window.addEventListener("scroll", _feedLoadFallback, { passive: true });
  _feedLoadFallback();
}


function liveFilteredPosts() {
  const q = (state.liveQ || "").trim().toLowerCase();
  return _livePosts.filter((item) => {
    if (state.liveImportant && Number(item.score) < 2) return false;
    if (!q) return true;
    return `${item.highlight_title || ""} ${item.body || ""}`.toLowerCase().includes(q);
  });
}

function liveSearch(value) {
  state.liveQ = value;
  renderLiveFeed();
}

function toggleLiveImportant(checked) {
  state.liveImportant = !!checked;
  renderLiveFeed();
}

function liveFeedItem(item) {
  const score = Number(item.score) || 1;
  const title = item.highlight_title
    ? `<div class="live-title">${escapeHtml(item.highlight_title)}</div>`
    : "";
  const body = escapeHtml(item.body || "").replace(/\n/g, "<br>");
  return `
    <article class="live-item" data-score="${score}">
      <time class="live-time" datetime="${escapeHtml(item.published_at || "")}">${fmtPublished(item.published_at, true)}</time>
      <div class="live-main">
        ${title}
        <div class="live-body">${body}</div>
      </div>
    </article>`;
}

function renderLiveFeed() {
  const feed = $("#feed");
  if (!feed) return;
  const allPosts = _livePosts;
  const posts = liveFilteredPosts();
  const grouped = new Map();
  for (const p of posts) {
    const bucket = feedDateBucket(p.published_at);
    if (!grouped.has(bucket)) grouped.set(bucket, []);
    grouped.get(bucket).push(p);
  }
  const html = [...grouped.entries()].map(([bucket, list], gi) => `
    <div class="tl-group live-group">
      <div class="tl-group-head"><span>${escapeHtml(bucket)}</span>${gi === 0 ? `<span class="tl-group-count">已加载 ${posts.length}${posts.length === allPosts.length ? "" : ` / ${allPosts.length}`} 条快讯</span>` : ""}</div>
      <div class="live-feed">${list.map(liveFeedItem).join("")}</div>
    </div>`).join("");
  const footer = _liveHasMore && posts.length
    ? `<div id="feed-load-sentinel" class="tl-feed-more" role="status" aria-live="polite"></div>`
    : (posts.length ? `<p class="muted tl-feed-end">已加载全部</p>` : "");
  const empty = allPosts.length
    ? emptyState("没有匹配的快讯")
    : emptyState("暂无快讯", `<div><button class="btn-normal" onclick="reloadTimeline()">刷新</button></div>`);
  const attr = posts.length
    ? html + footer + `<p class="live-attribution muted">数据来源：<a href="https://wallstreetcn.com/live/global" target="_blank" rel="noopener">华尔街见闻 · 快讯</a></p>`
    : empty;
  feed.innerHTML = attr;
  renderLiveRail();
  startFeedAutoLoad();
}

function renderTimelineFeed() {
  const feed = $("#feed");
  if (!feed) return;
  const posts = _tlPosts;
  const visibleIds = new Set(posts.map((p) => p.id));
  for (const id of [..._tlExpanded]) {
    if (!visibleIds.has(id)) _tlExpanded.delete(id);
  }
  const grouped = new Map();
  for (const p of posts) {
    let bucket;
    try {
      bucket = feedDateBucket(p.published_at);
    } catch (e) {
      bucket = "未知时间";
    }
    if (!grouped.has(bucket)) grouped.set(bucket, []);
    grouped.get(bucket).push(p);
  }
  // 单帖渲染失败只降级该卡片，不拖垮整屏（否则胶囊会卡在「有新动态」且点击无效）
  const safePostCard = (p) => {
    try {
      return postCard(p);
    } catch (e) {
      console.warn("动态卡片渲染失败", p && p.id, e);
      return `<article class="tl-post"><div class="tl-post-body"><strong class="tl-post-title muted">（此条动态渲染失败）</strong></div></article>`;
    }
  };
  const html = [...grouped.entries()].map(([bucket, list], gi) => `
    <div class="tl-group">
      <div class="tl-group-head"><span>${escapeHtml(bucket)}</span>${gi === 0 ? `<span class="tl-group-count">已加载 ${_tlPosts.length} 条动态</span>` : ""}</div>
      ${list.map(safePostCard).join("")}
    </div>`).join("");
  const footer = _tlHasMore
    ? `<div id="feed-load-sentinel" class="tl-feed-more" role="status" aria-live="polite"></div>`
    : (posts.length ? `<p class="muted tl-feed-end">已加载全部</p>` : "");
  const hasFilter = state.timelineQ || state.timelinePlatform || state.timelineCategory || state.timelineTag;
  const emptyMsg = state.timelinePlatform === "zsxq"
    ? "星球动态不混入「全部」，订阅后会出现在这里"
    : state.timelineFavorite && !hasFilter
    ? "还没有特别关注大V的动态"
    : (hasFilter ? "没有符合条件的动态" : "还没有订阅任何大V");
  const emptyAction = hasFilter
    ? `<div><button class="btn-normal" onclick="tlResetFilters()">清除筛选</button></div>`
    : `<div><button class="btn-normal btn-add" onclick="go('home')">去订阅</button></div>`;
  feed.innerHTML = posts.length
    ? html + footer
    : emptyState(emptyMsg, emptyAction);
  startFeedAutoLoad();
  tlSyncActiveChips();
}

function toggleTimelineFav() {
  const revert = tlSnapshotFilters();
  state.timelineFavorite = !state.timelineFavorite;
  tlPaintViewToggles();
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
}

function toggleTimelineSecondary() {
  // 次要大V开关：默认关闭（动态页隐藏次要大V），开启后显示其动态。
  // 图标随状态切换：隐藏 = 划线眼睛（不看），显示 = 睁眼（看）。
  const revert = tlSnapshotFilters();
  state.timelineSecondary = !state.timelineSecondary;
  tlPaintViewToggles();
  tlSyncFilterChrome();
  loadTimeline(true, routeRenderSeq, { revert });
}

function tlReplacePostCard(id) {
  const post = _tlPosts.find((p) => p.id === id);
  const card = document.querySelector(`.post-item[data-post-id="${id}"]`);
  if (post && card) {
    const wrap = document.createElement("div");
    wrap.innerHTML = postCard(post).trim();
    const next = wrap.firstElementChild;
    if (next) {
      card.replaceWith(next);
      return;
    }
  }
  renderTimelineFeed();
}

function tlTogglePost(id) {
  if (_tlExpanded.has(id)) _tlExpanded.delete(id);
  else _tlExpanded.add(id);
  tlReplacePostCard(id);
}

function tlToggleOrigin(id) {
  if (_tlShowSrc.has(id)) _tlShowSrc.delete(id);
  else _tlShowSrc.add(id);
  tlReplacePostCard(id);
}

// published_at 支持 "YYYY-MM-DD HH:MM(:SS)"（雪球）与 RFC2822（微博/X 存 GMT/+0000），
// 解析成 Date 后按本地时区展示；无法解析返回 null（回退原样显示）
function parsePublished(s) {
  const raw = String(s || "").trim();
  if (!raw) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(raw);
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 8, +m[5], +(m[6] || 0)));
  const d = new Date(raw); // RFC2822 等 JS 可解析格式（带时区偏移，正确换算本地时间）
  return isNaN(d.getTime()) ? null : d;
}

function fmtPublished(s, clockOnly = false) {
  const d = parsePublished(s);
  if (!d) return escapeHtml(s || "");
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  if (clockOnly) return `${p(d.getHours())}:${p(d.getMinutes())}`;
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return `今天 ${p(d.getHours())}:${p(d.getMinutes())}`;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(d, yesterday)) return `昨天 ${p(d.getHours())}:${p(d.getMinutes())}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function feedDateBucket(s) {
  const d = parsePublished(s);
  if (!d) return "更早";
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const dateKey = (x) => `${x.getFullYear()}-${p(x.getMonth() + 1)}-${p(x.getDate())}`;
  const today = dateKey(now);
  const yesterday = dateKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const key = dateKey(d);
  if (key === today) return "今天";
  if (key === yesterday) return "昨天";
  // 今年内按具体日期分组（如 8月3日），跨年才归入「更早」
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return "更早";
}

function postFiles(post) {
  let d = post.detail;
  if (typeof d === "string" && d) {
    try { d = JSON.parse(d); } catch { return []; }
  }
  return Array.isArray(d?.files) ? d.files.filter((f) => f && (f.url || f.name)) : [];
}

function combinationDetailHtml(post) {
  let detail = post.detail;
  if (typeof detail === "string" && detail) {
    try { detail = JSON.parse(detail); } catch { return ""; }
  }
  if (!detail || typeof detail !== "object") return "";
  const stats = Array.isArray(detail.stats) ? detail.stats.filter((s) => Array.isArray(s) && s.length >= 2) : [];
  const actions = Array.isArray(detail.actions) ? detail.actions.filter((a) => a && typeof a === "object") : [];
  const holdings = Array.isArray(detail.holdings)
    ? detail.holdings.filter((h) => h && h.name && h.weight != null)
    : [];
  const sections = [];
  if (stats.length) {
    sections.push(`<div class="combo-stats">${stats.map(([key, value]) => `<span class="combo-stat"><b>${escapeHtml(key)}</b> ${escapeHtml(value)}</span>`).join("")}</div>`);
  }
  if (actions.length) {
    const rows = actions.map((a) => {
      const type = a.type || "调整";
      const stock = a.stock || a.symbol || "";
      const symbol = a.stock && a.symbol
        ? ` <span class="combo-sym">${escapeHtml(a.symbol)}</span>`
        : "";
      const price = a.price != null && String(a.price).trim()
        ? `<span class="combo-action-price">${escapeHtml(a.price)}</span>`
        : "";
      return `<div class="combo-action"><span class="combo-action-type">${escapeHtml(type)}</span><strong class="combo-action-name">${escapeHtml(stock)}${symbol}</strong><span class="combo-action-meta"><span class="combo-action-position">${escapeHtml(a.prev || "0.0%")} → ${escapeHtml(a.target || "0.0%")}</span>${price}</span></div>`;
    }).join("");
    sections.push(`<section class="combo-section"><h3 class="combo-section-title">调仓明细</h3><div class="combo-actions"><div class="combo-action combo-action-cols" aria-hidden="true"><span>操作</span><span>标的</span><span>仓位 / 成交价</span></div>${rows}</div></section>`);
  }
  if (holdings.length) {
    sections.push(`<section class="combo-section"><h3 class="combo-section-title">现有持仓</h3><div class="combo-holdings">${holdings.map((h) => {
      const name = h.name || h.symbol || "";
      const symbol = h.name && h.symbol ? ` <span class="combo-sym">${escapeHtml(h.symbol)}</span>` : "";
      return `<div class="combo-holding"><span>${escapeHtml(name)}${symbol}</span><span class="combo-w">${escapeHtml(h.weight)}%</span></div>`;
    }).join("")}</div></section>`);
  }
  if (detail.cash) sections.push(`<div class="combo-cash">现金 <b>${escapeHtml(detail.cash)}</b></div>`);
  return sections.length ? `<div class="combo-detail">${sections.join("")}</div>` : "";
}

function postCard(post) {
  const safeUrl = /^https?:\/\//i.test(post.url || "") ? post.url : "#";
  const comboHtml = post.platform === "combination" ? combinationDetailHtml(post) : "";
  const isCombination = !!comboHtml;
  const srcC = (post.content_src || "").trim();
  const srcT = (post.title_src || "").trim();
  const translated = !!(srcC && srcC !== (post.content || "").trim());
  const showSrc = translated && _tlShowSrc.has(post.id);
  const title = showSrc ? srcT : (post.title || "");
  const body = (showSrc ? srcC : (post.content || "")) || "（无正文）";
  const expanded = _tlExpanded.has(post.id);
  const shown = expanded ? body : body.slice(0, 200);
  // X 帖常 title==content（如纯链接帖），标题和正文都渲染会视觉重复，跳过标题；
  // 长文帖 title 常为 content 开头一段（截断），同样跳过避免重复展示。
  // 译文标题/正文来自两次独立翻译、措辞可能不同，前缀匹配要落在原文侧才稳
  const srcTitle = (post.title_src || title || "").trim();
  const srcBody = (post.content_src || body || "").trim();
  const titleDup = !!title && (
    title.trim() === body.trim()
    || body.trimStart().startsWith(title.trim())
    || !!(srcTitle && srcBody && srcBody.startsWith(srcTitle))
  );
  const trBar = translated ? `<div class="p-tr">${GROK_TRANSLATE_ICON}<span class="p-tr-label">翻译自英语</span><button type="button" class="p-tr-toggle" onclick="tlToggleOrigin(${post.id})">${showSrc ? "显示译文" : "显示原文"}</button></div>` : "";
  return `
    <div class="post-item" data-post-id="${post.id}">
      <div class="p-header">
        ${avatarHtml(post.kol_name, post.avatar_url, post.platform)}
        <div class="p-name-line">
          <a class="p-name" href="/kol/${post.kol_id}" title="${escapeHtml(post.kol_name)}">${escapeHtml(post.kol_name)}</a>
          <span class="p-platform" data-platform="${escapeHtml(post.platform)}" title="${escapeHtml(PLATFORM_LABELS[post.platform] || post.platform)}">
            ${PLATFORM_ICONS[post.platform] || ""}
          </span>
          <span class="p-time" title="${escapeHtml(post.published_at)}">${fmtPublished(post.published_at)}</span>
        </div>
      </div>
      ${isCombination ? `<div class="combo-post">${comboHtml}</div>` : `${trBar}${!titleDup && title ? `<div class="p-title">${escapeHtml(title)}</div>` : ""}
      <div class="p-content">${escapeHtml(shown)}${body.length > 200
        ? `<button class="post-expand-btn" onclick="tlTogglePost(${post.id})" aria-expanded="${expanded}" aria-label="${expanded ? "收起全文" : "展开全文"}" title="${expanded ? "收起全文" : "展开全文"}">${expanded ? `${CHEVRON_UP_ICON} 收起` : `${CHEVRON_DOWN_ICON} 展开全文`}</button>`
        : ""}</div>`}
      ${Array.isArray(post.images) && post.images.length ? `
        <div class="post-images">
          ${post.images.slice(0, 4).map((img) => (/\.(mp4|webm)(\?|$)/i.test(img) ? `
            <video class="post-video" src="${escapeHtml(imgSrcFor(img))}" controls playsinline preload="none"></video>` : `
            <a class="post-img-link" href="#" onclick="event.preventDefault();openLightbox(this.querySelector('img'))" aria-label="查看${escapeHtml(post.kol_name)}的配图"><img src="${escapeHtml(imgSrcFor(img))}" loading="lazy" alt="${escapeHtml(post.kol_name)} 的配图" onerror="imgOnError(this)"></a>`)).join("")}
          ${post.images.length > 4 ? `<span class="post-images-more">+${post.images.length - 4}</span>` : ""}
        </div>` : ""}
      ${postFiles(post).map((f) => {
        // 附件一律走鉴权路由（服务端校验订阅可见性，命中本地缓存时直接下发）；
        // 历史详情里缓存的 /zsxq-files/ 静态链接已随挂载移除，不再直连
        if (f.file_id) {
          return `<button type="button" class="p-file" data-file-id="${escapeHtml(String(f.file_id))}" data-name="${escapeHtml(f.name || "附件")}" onclick="downloadZsxqFile(this)" aria-label="下载附件 ${escapeHtml(f.name || "附件")}" title="下载附件 ${escapeHtml(f.name || "附件")}">${PAPERCLIP_ICON} ${escapeHtml(f.name || "附件")}</button>`;
        }
        const href = /^https?:\/\//i.test(f.url || "") ? f.url : "";
        return href
          ? `<a class="p-file" href="${escapeHtml(href)}" target="_blank" rel="noopener" aria-label="打开附件 ${escapeHtml(f.name || "附件")}" title="打开附件 ${escapeHtml(f.name || "附件")}">${PAPERCLIP_ICON} ${escapeHtml(f.name || "附件")}</a>`
          : `<span class="p-file" title="附件 ${escapeHtml(f.name || "附件")}">${PAPERCLIP_ICON} ${escapeHtml(f.name || "附件")}</span>`;
      }).join("")}
      <div class="p-meta">
        ${post.category_name ? `<span class="cat">${escapeHtml(post.category_name)}</span>` : ""}
        ${post.post_type === "reply" ? `<span class="cat">回复</span>` : ""}
        ${Array.isArray(post.tags) && post.tags.length
          ? post.tags.map((t) => `<button type="button" class="cat cat-tag post-tag-filter" data-tag="${escapeHtml(t)}" onclick="tlPickTag(this.dataset.tag)">${escapeHtml(t)}</button>`).join("")
          : ""}
        <div class="p-meta-actions">
        <button type="button" class="cat cat-export post-card-export" onclick="exportPostCard(${post.id}, event)" aria-label="图卡分享" title="图卡分享">图卡分享 ${IMAGE_CARD_ICON}</button>
        ${post.platform === "zsxq" ? "" : `<a class="cat" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener" aria-label="查看原文" title="查看原文">查看原文 ${EXTERNAL_LINK_ICON}</a>`}
        </div>
      </div>
    </div>`;
}

// ---------- 搜索 ----------
async function renderSearch(seq) {
  setPageTitle("搜索", true);
  const params = routeQuery();
  const query = params.get("q") || "";
  $("#main").innerHTML = `
    <section class="section-panel">
      <div class="search-bar" style="margin-bottom:16px">
        ${SEARCH_ICON}
        <input id="search-input" placeholder="输入昵称或 ID，回车搜索" value="${escapeHtml(query)}" onkeydown="if(event.key==='Enter')runSearch()">
        <button class="btn-ghost" onclick="runSearch()">搜索</button>
      </div>
      <div id="search-result" class="kol-grid">${emptyState("加载中…")}</div>
    </section>`;
  if (!state.user?.is_admin) {
    let cats = [];
    try { cats = await api("/api/categories"); } catch (err) { cats = []; }
    if (!routeStillActive(seq)) return;
    const catOptions = cats.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
    const askSection = document.createElement("div");
    askSection.innerHTML = `
      <section class="section-panel">
        <header class="section-head">
          <div><h2 class="section-title">申请添加大V</h2>
          <p class="section-meta">目录里没有的大V？提交申请，管理员审批通过后即可订阅。</p></div>
        </header>
        <div class="toolbar" style="margin-top:12px">
          <select id="ask-platform" class="form-control" style="margin:0;width:auto">
            <option value="xueqiu">雪球</option>
            <option value="combination">雪球组合</option>
            <option value="weibo">微博</option>
            <option value="twitter">X</option>
            <option value="zsxq">知识星球</option>
          </select>
          <select id="ask-category" class="form-control" style="margin:0;width:auto" aria-label="分类" required>
            <option value="">请选择分类</option>${catOptions}
          </select>
          <input id="ask-link" class="form-control" style="margin:0;flex:1;min-width:220px" placeholder="大V主页链接或 ID" oninput="onAskLinkInput()">
          <button class="btn-normal" onclick="submitAsk()">提交申请</button>
        </div>
        <div id="ask-result" class="muted" style="margin-top:12px"></div>
      </section>
      <section class="section-panel">
        <header class="section-head"><div><h2 class="section-title">我的申请</h2></div></header>
        <div id="my-asks"></div>
      </section>`;
    // 先取引用再 append：第一次 appendChild 会移动节点，children[1] 会随之失效
    const askPanel = askSection.firstElementChild;
    const myAskPanel = askSection.children[1];
    $("#main").appendChild(askPanel);
    $("#main").appendChild(myAskPanel);
    loadMyAsks(seq);
  }
  await doSearch(seq);
  if (!query && routeStillActive(seq)) $("#search-input")?.focus();
}


function detectAskPlatform(link) {
  // 与后端 _detect_platform_from_link 同规则：输入链接时自动甄别平台
  if (/(?:xueqiu\.com\/P\/|ZH\d)/.test(link)) return "combination";
  if (link.includes("xueqiu.com")) return "xueqiu";
  if (/weibo\.(com|cn)/.test(link)) return "weibo";
  if (/(^|[/:.])x\.com|twitter\.com/.test(link)) return "twitter";
  if (/(?:wx\.)?zsxq\.com/.test(link)) return "zsxq";
  if (/(^|[/:.])truthsocial\.com/.test(link)) return "truth";
  return "";
}

function onAskLinkInput() {
  // 粘贴链接时自动甄别平台：识别出其他平台则自动切换下拉并提示
  const link = $("#ask-link").value.trim();
  const detected = detectAskPlatform(link);
  const sel = $("#ask-platform");
  if (!detected || !sel || sel.value === detected) return;
  sel.value = detected;
  showAskResult(`已识别为「${PLATFORM_LABELS[detected]}」主页链接，平台已自动切换`, false);
}

function showAskResult(msg, isError) {
  const el = $("#ask-result");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("ask-error", !!isError);
  el.classList.toggle("ask-ok", !isError);
}

async function submitAsk() {
  const external_id = $("#ask-link").value.trim();
  const category_id = $("#ask-category") && $("#ask-category").value;
  if (!external_id) {
    showAskResult("请填写大V主页链接或 ID", true);
    return;
  }
  if (!category_id) {
    showAskResult("请选择分类", true);
    return;
  }
  try {
    await api("/api/kol-requests", {
      method: "POST",
      body: JSON.stringify({ platform: $("#ask-platform").value, external_id, category_id: Number(category_id) }),
    });
    $("#ask-link").value = "";
    showAskResult("已提交 ✅ 管理员审批通过后会自动出现在订阅广场", false);
    loadMyAsks();
  } catch (err) {
    showAskResult(err.message, true); // 后端返回具体的纠错提示（平台切换/链接格式）
  }
}

async function loadMyAsks(routeSeq) {
  try {
    const asks = await api("/api/my/kol-requests");
    if (!routeStillActive(routeSeq)) return; // 已切走：不写旧页面
    const statusMap = { pending: "待审批", approved: "已通过 ✅", rejected: "已拒绝" };
    $("#my-asks").innerHTML = asks.length
      ? `<div class="table-wrap"><table>
          <thead><tr><th scope="col">平台</th><th scope="col">外部 ID</th><th scope="col">分类</th><th scope="col">状态</th><th scope="col">提交时间</th></tr></thead>
          <tbody>${asks.map((a) => `
            <tr>
              <td>${PLATFORM_LABELS[a.platform] || escapeHtml(a.platform)}</td>
              <td>${escapeHtml(a.external_id)}</td>
              <td>${escapeHtml(a.category_name || "—")}</td>
              <td class="${a.status === "approved" ? "status-ok" : a.status === "rejected" ? "status-fail" : ""}">${statusMap[a.status] || escapeHtml(a.status)}</td>
              <td>${escapeHtml(fmtDbTime(a.created_at))}</td>
            </tr>`).join("")}</tbody>
        </table></div>`
      : emptyState("还没有提交过申请");
  } catch {
    /* 忽略加载失败 */
  }
}

async function doSearch(routeSeq) {
  const input = $("#search-input");
  if (!input) return;
  const keyword = input.value.trim().toLowerCase();
  try {
    const kols = await api("/api/catalog");
    if (!routeStillActive(routeSeq)) return;
    state.catalog = kols;
    const available = kols.filter((k) => !k.subscribed);
    const hits = keyword
      ? available.filter(
          (k) => (k.name || "").toLowerCase().includes(keyword)
            || (k.external_id || "").toLowerCase().includes(keyword)
        )
      : available;
    const target = $("#search-result");
    if (!target) return;
    target.innerHTML = hits.length
      ? hits.map(kolCard).join("")
      : emptyState(keyword ? "没有匹配的未订阅大V" : "所有大V都已订阅");
  } catch (err) {
    if (!routeStillActive(routeSeq)) return;
    const target = $("#search-result");
    if (target) target.innerHTML = emptyState("搜索失败: " + err.message);
  }
}

// ---------- 大V动态页 ----------
async function renderKolPage(kolId, seq) {
  setPageTitle("大V动态", true);
  $("#main").innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const kol = await api(`/api/kols/${kolId}`);
    const posts = await api(`/api/kols/${kolId}/posts?limit=50`);
    if (!routeStillActive(seq)) return; // 已切走：不写旧页面
    _kolPosts.length = 0;
    _kolPosts.push(...posts.map((p) => ({
      ...p,
      kol_name: p.kol_name || kol.name,
      avatar_url: p.avatar_url || kol.avatar_url,
      kol_external_id: p.kol_external_id || kol.external_id,
    })));
    const extra = kol.platform === "combination"
      ? await renderCombinationSnapshots(kol)
      : "";
    if (!routeStillActive(seq)) return;
    $("#main").innerHTML = `
      <section class="section-panel">
        <header class="section-head">
          <div>
            <h2 class="section-title">${escapeHtml(kol.name)} · 最近动态</h2>
            <p class="section-meta">外部 ID：${escapeHtml(kol.external_id)} · ${PLATFORM_LABELS[kol.platform] || escapeHtml(kol.platform)}${kol.category_name ? " · " + escapeHtml(kol.category_name) : ""}</p>
          </div>
          <div class="toolbar" style="margin-top:12px">
            ${kol.subscribed && kol.platform === "xueqiu" ? subTypeSwitchesHtml(kol.id, kol.subscribe_type || "post") : ""}
            <button class="btn-sub ${kol.subscribed ? "subscribed" : ""}" id="kol-sub-btn" onclick="toggleKolPageSubscribe(${kol.id})">
              ${kol.subscribed ? "✓ 已订阅" : "订阅"}
            </button>
          </div>
        </header>
        ${extra}
        <div id="kol-posts">${_kolPosts.length ? _kolPosts.map(postCard).join("") : emptyState("暂无动态")}</div>
      </section>`;
  } catch (err) {
    if (!routeStillActive(seq)) return;
    $("#main").innerHTML = emptyState("加载失败: " + err.message);
  }
}

async function renderCombinationSnapshots(kol) {
  try {
    const [holdings, nav] = await Promise.all([
      api(`/api/kols/${kol.id}/holdings`),
      api(`/api/kols/${kol.id}/nav`),
    ]);
    const q = kol.quote || {};
    const quoteHtml = q.day_percent_gain != null || q.net_value != null ? `
      <div class="cube-quote">
        <div class="cube-quote-item"><span class="cube-quote-label">净值</span><span class="cube-quote-value">${q.net_value == null ? "—" : q.net_value.toFixed(3)}</span></div>
        <div class="cube-quote-item"><span class="cube-quote-label">今日涨跌</span><span class="cube-quote-value ${q.day_percent_gain == null ? "" : (q.day_percent_gain >= 0 ? "up" : "down")}">${q.day_percent_gain == null ? "—" : (q.day_percent_gain >= 0 ? "+" : "") + q.day_percent_gain.toFixed(2) + "%"}</span></div>
        ${kol.quote_at ? `<div class="cube-quote-item"><span class="cube-quote-label">快照</span><span class="cube-quote-value small">${escapeHtml(formatSnapshotTs(kol.quote_at))}</span></div>` : ""}
      </div>` : "";
    const rows = (holdings.holdings || []).map((h) => {
      const delta = h.prev != null && Math.abs(h.weight - h.prev) >= 0.01
        ? `${h.weight >= h.prev ? "+" : ""}${(h.weight - h.prev).toFixed(1)}`
        : "";
      return { ...h, delta };
    });
    if (holdings.cash != null) rows.push({ name: "现金", symbol: "CASH", weight: holdings.cash, delta: "" });
    const holdingsHtml = rows.length ? `
      <div class="cube-holdings">
        ${rows.map((h) => `
          <div class="cube-holding">
            <div class="cube-holding-head">
              <span class="cube-holding-name" title="${escapeHtml(h.symbol)}">${escapeHtml(h.name)}</span>
              <span class="cube-holding-weight">${h.weight}%${h.delta ? ` <em class="cube-holding-delta ${Number(h.delta) >= 0 ? "up" : "down"}">${h.delta}</em>` : ""}</span>
            </div>
            <div class="cube-weight-bar"><div class="cube-weight-fill" style="width:${Math.max(h.weight, 1)}%"></div></div>
          </div>`).join("")}
        ${holdings.updated_at ? `<p class="section-meta" style="margin-top:10px">持仓更新于 ${escapeHtml(formatSnapshotTs(holdings.updated_at))}</p>` : ""}
      </div>` : `<p class="section-meta">暂无持仓数据（订阅后自动抓取）</p>`;
    const navHtml = (nav.series || []).length >= 2 ? `
      <div class="cube-nav-head">
        <b>最新 ${nav.series[nav.series.length - 1].value}</b>
        <span class="section-meta">${escapeHtml(nav.series[nav.series.length - 1].date)}${(nav.benchmark || []).length >= 2 ? " · 对照沪深300" : ""}</span>
      </div>
      ${navChartSvg(nav.series, nav.benchmark)}` : `<p class="section-meta">暂无净值数据（订阅后自动抓取）</p>`;
    return `
      ${quoteHtml ? `<section class="section-panel"><h2 class="section-title">组合状态</h2>${quoteHtml}</section>` : ""}
      <section class="section-panel"><h2 class="section-title">当前持仓</h2>${holdingsHtml}</section>
      <section class="section-panel"><h2 class="section-title">净值走势</h2>${navHtml}</section>`;
  } catch (err) {
    return `<section class="section-panel"><p class="section-meta">组合数据加载失败：${escapeHtml(err.message)}</p></section>`;
  }
}

// 后端快照时间（UTC "YYYY-MM-DD HH:MM:SS"）转本地 "MM-DD HH:MM"
function formatSnapshotTs(ts) {
  const d = new Date(String(ts).replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return ts;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 净值曲线：手绘 SVG 折线（含渐变面积、网格、首/中/尾日期），A股红涨绿跌
function navChartSvg(series, benchmark) {
  const W = 640, H = 200, padL = 44, padR = 10, padT = 10, padB = 24;
  let cube = series;
  let bench = null;
  if (benchmark && benchmark.length >= 2) {
    const bm = Object.fromEntries(benchmark.map((p) => [p.date, p.value]));
    const aligned = series.filter((p) => bm[p.date] != null);
    if (aligned.length >= 2 && aligned[0].value && bm[aligned[0].date]) {
      const c0 = aligned[0].value;
      const b0 = bm[aligned[0].date];
      cube = aligned.map((p) => ({ date: p.date, value: p.value / c0 }));
      bench = aligned.map((p) => ({ date: p.date, value: bm[p.date] / b0 }));
    }
  }
  const vals = cube.map((p) => p.value).concat(bench ? bench.map((p) => p.value) : []);
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (max - min < 1e-9) { max += 0.005; min -= 0.005; }
  const span = max - min;
  min -= span * 0.05;
  max += span * 0.05;
  const X = (i) => padL + (i / (cube.length - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);
  const pts = cube.map((p, i) => `${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`).join(" ");
  const up = series[series.length - 1].value >= series[0].value;
  const cssVar = up ? "--color-data-positive" : "--color-data-negative";
  const color = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim() || (up ? "#b05b63" : "#23714a");
  const muted = getComputedStyle(document.documentElement).getPropertyValue("--color-text-muted").trim() || "#6e6e73";
  const base = (H - padB).toFixed(1);
  const area = `M${X(0).toFixed(1)},${base} L${pts.replace(/ /g, " L")} L${X(cube.length - 1).toFixed(1)},${base} Z`;
  const grid = [0, 1, 2, 3].map((i) => {
    const v = min + ((max - min) * i) / 3;
    return `<line x1="${padL}" y1="${Y(v).toFixed(1)}" x2="${W - padR}" y2="${Y(v).toFixed(1)}" class="cube-nav-grid"/>`
      + `<text x="4" y="${(Y(v) + 3).toFixed(1)}" class="cube-nav-tick">${v.toFixed(3)}</text>`;
  }).join("");
  const first = cube[0], mid = cube[Math.floor(cube.length / 2)], last = cube[cube.length - 1];
  const benchLine = bench
    ? `<polyline points="${bench.map((p, i) => `${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`).join(" ")}" fill="none" stroke="${muted}" stroke-width="1.5" stroke-dasharray="4 3" stroke-linejoin="round"/>`
    : "";
  return `<svg viewBox="0 0 ${W} ${H}" class="cube-nav-svg" role="img" aria-label="净值曲线">
    ${grid}
    <path d="${area}" fill="${up ? "var(--color-data-positive-soft)" : "var(--color-data-negative-soft)"}"/>
    ${benchLine}
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <text x="${padL}" y="${H - 6}" class="cube-nav-date">${escapeHtml(first.date)}</text>
    <text x="${(padL + W - padR) / 2}" y="${H - 6}" text-anchor="middle" class="cube-nav-date">${escapeHtml(mid.date)}</text>
    <text x="${W - padR}" y="${H - 6}" text-anchor="end" class="cube-nav-date">${escapeHtml(last.date)}</text>
    <text x="${W - padR}" y="${(Y(last.value) - 5).toFixed(1)}" text-anchor="end" class="cube-nav-last" fill="${color}">${series[series.length - 1].value}</text>
  </svg>`;
}


async function toggleKolPageSubscribe(kolId) {
  await toggleSubscribe(kolId, $("#kol-sub-btn"));
}


function statsTabsHtml(active = "config") {
  const labels = {
    config: "抓取设置",
    cookies: "Cookie 管理",
    imgbed: "图床设置",
    proxies: "代理",
    plaza: "广场显示",
    news: "财经资讯",
  };
  return `<div class="settings-tabs" role="tablist" aria-label="数据源管理">
    ${STATS_TABS.map((tab) => `<button type="button" class="settings-tab ${tab === active ? "active" : ""}" role="tab" id="tab-${tab}" aria-selected="${tab === active}" aria-controls="st-${tab}" data-tab="${tab}" onclick="switchStatsTab('${tab}')">${labels[tab]}</button>`).join("")}
  </div>`;
}

function statsTabFromHash() {
  const tab = routeQuery().get("tab") || "config";
  if (tab === "overview" || tab === "health") return "legacy-dashboard";
  return STATS_TABS.includes(tab) ? tab : "config";
}

function switchStatsTab(name) {
  // 数据源页分段导航：抓取设置 / Cookie 管理 / 图床设置 / 代理 / 广场显示 / 财经资讯
  if (name === "legacy-dashboard" || name === "overview" || name === "health") {
    replaceRoute("admin/dashboard");
    return;
  }
  if (!STATS_TABS.includes(name)) name = "config";
  if (name === "news") {
    stopStatsTimer();
    const next = "/admin/stats?tab=news";
    if (location.pathname + location.search !== next) history.replaceState(null, "", next);
    loadAdminNews(routeRenderSeq);
    return;
  }
  // 财经资讯页会整页替换 #admin-body，其余面板不在 DOM；切回时整页重载再按 hash 定位
  if (!document.getElementById("st-" + name)) {
    const next = name === "config" ? "/admin/stats" : `/admin/stats?tab=${name}`;
    if (location.pathname + location.search !== next) history.replaceState(null, "", next);
    loadAdminStats(routeRenderSeq);
    return;
  }
  document.querySelectorAll(".settings-tab[data-tab]").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    if (STATS_TABS.includes(b.dataset.tab)) b.setAttribute("aria-selected", String(on));
  });
  STATS_TABS.forEach((t) => {
    const el = document.getElementById("st-" + t);
    if (!el) return;
    const on = t === name;
    el.style.display = on ? "" : "none";
    el.hidden = !on;
  });
  const next = name === "config" ? "/admin/stats" : `/admin/stats?tab=${name}`;
  if (location.pathname + location.search !== next) history.replaceState(null, "", next);
  document.getElementById(`tab-${name}`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (name === "proxies") loadProxyAdmin();
  if (name === "config" && !imaMountState.discoveryEntered) {
    imaMountState.discoveryEntered = true;
    discoverImaGroups();
  }
}

function cookieRepairItems(s) {
  const items = [];
  const src = {};
  (s.sources || []).forEach((row) => { src[row.platform] = row; });
  const live = (s.kol_health || []).filter((k) => k.enabled);
  const hasXq = live.some((k) => k.platform === "xueqiu" || k.platform === "combination");
  const hasWb = live.some((k) => k.platform === "weibo");
  const hasTw = live.some((k) => k.platform === "twitter");
  const xq = s.xueqiu_cookie || {};
  const xqErr = `${src.xueqiu?.last_error || ""} ${src.combination?.last_error || ""}`;
  const xqSick = /cookie|waf|反爬|401|403|失效|登录/i.test(xqErr);
  if (hasXq && !xq.set) items.push({ key: "xq-missing", label: "雪球 Cookie 未写入" });
  else if (hasXq && src.xueqiu && !src.xueqiu.ok && xqSick) {
    items.push({ key: "xq-bad", label: "雪球 Cookie 可能失效" });
  }
  const wb = src.weibo;
  if (hasWb && wb && !wb.ok && /登录|login|cookie|会话/i.test(wb.last_error || "")) {
    items.push({ key: "wb-bad", label: "微博登录态失效，可扫码续期" });
  }
  const tw = s.twitter_cookie || {};
  const twCh = src.twitter?.channels || {};
  const twReason = src.twitter?.direct_fallback_reason || src.twitter?.last_error || "";
  // 双通道：错误消息带通道标识（X GraphQL(app) / X GraphQL(cookie)），据此分辨到底
  // 是哪条通道失效后再提示——两条通道的凭证与续期方式完全不同，混在一起会指错方向。
  //
  // 判据只能用 HTTP 状态码与鉴权关键词：通道标识本身含 "app"/"cookie" 字样，
  // 把它们当特征词会恒真（曾因此把 429 限流误报成「X Cookie 可能失效」）。
  // 429（限流）也不提示：通道级失败转移 + 平台退避会自愈，提示只会让人误以为要换 Cookie。
  const xCredBad =
    /HTTP (401|403)|unauthorized|forbidden|could not authenticate|code (89|32)\b/i.test(twReason);
  const xAppReady = !!(twCh.app_ready || twCh.mode === "app_only" || twCh.mode === "split");
  if (hasTw && src.twitter?.direct_mode === "fallback" && xCredBad) {
    if (/GraphQL\(app\)/i.test(twReason)) {
      items.push({ key: "x-app-bad", label: "X App 通道凭证可能失效" });
    } else if (/GraphQL\(cookie\)/i.test(twReason)) {
      items.push({ key: "x-bad", label: "X Cookie 可能失效" });
    } else {
      items.push({ key: "x-bad", label: "X 抓取异常，检查凭证" });
    }
  }
  if (hasTw && !tw.set && !xAppReady) {
    items.push({ key: "x-missing", label: "X 凭证未写入" });
  }
  const hasZq = live.some((k) => k.platform === "zsxq");
  const zqCookie = s.zsxq_cookie || {};
  const zqErr = src.zsxq?.last_error || "";
  if (hasZq && !zqCookie.set) items.push({ key: "zq-missing", label: "知识星球 Cookie 未写入" });
  else if (hasZq && src.zsxq && !src.zsxq.ok && /cookie|401|1059|登录|token/i.test(zqErr)) {
    items.push({ key: "zq-bad", label: "知识星球 Cookie 可能失效" });
  }
  return items;
}

function cookieRepairBanner(s) {
  const items = cookieRepairItems(s);
  if (!items.length) return "";
  return `<div class="notice notice-warn" role="status">
    <div class="notice-warn-body">
      <strong>Cookie 需要更新</strong>
      <p>${items.map((i) => escapeHtml(i.label)).join("；")}。保存后即时生效，不用改配置文件、不用重启。</p>
    </div>
    <button type="button" class="btn-normal" onclick="go('admin/stats?tab=cookies')">去更新</button>
  </div>`;
}

function cookieUpdatedLabel(info) {
  if (!info || !info.set) return "未写入";
  if (info.from_env) return "已从环境变量读取";
  return info.updated_at ? `已写入（${escapeHtml(fmtTs(info.updated_at))}）` : "已写入";
}

function imgbedStatusLabel(info) {
  if (!info || !info.enabled) return "未接入";
  if (info.token_from_env) return "已从环境变量读取";
  return info.updated_at ? `已接入（${escapeHtml(fmtTs(info.updated_at))}）` : "已接入";
}

function markTurnstileDirty() {
  const btn = $("#ts-save");
  if (!btn || btn.dataset.dirty === "1") return;
  btn.dataset.dirty = "1";
  btn.textContent = "保存登录验证（未保存）";
}

async function pasteTurnstileSecret() {
  await pasteCookieField("ts-secret");
  markTurnstileDirty();
}

function turnstileSettingsHtml(info) {
  const active = !!info.active;
  const enabled = !!info.enabled;
  const status = active
    ? "登录/注册会显示 Cloudflare 验证框"
    : (enabled ? "开关已开，但还缺站点密钥或密钥，验证框不会出现" : "验证已关闭，登录不再出框");
  const secretHint = info.secret_set
    ? (info.secret_from_env ? "已从环境变量读取，留空保持原密钥" : "已配置，留空保持原密钥")
    : "Cloudflare Turnstile 后台的 Secret";
  const warn = enabled && !active
    ? `<div class="notice notice-warn" role="alert">
        <div class="notice-warn-body">
          <strong>验证框不会出现</strong>
          <p>开关已开，还缺站点密钥或密钥。填好后点保存。</p>
        </div>
      </div>`
    : "";
  return `${warn}<section class="section-panel" id="ts-form" data-secret-set="${info.secret_set ? "1" : "0"}">
    <header class="section-head">
      <div>
        <h2 class="section-title">登录验证</h2>
        <p class="section-meta">${status}。站点密钥和密钥在 Cloudflare Turnstile 后台创建；保存后即时生效，无需重启。</p>
      </div>
    </header>
    <div class="cfg-stack">
      <label class="switch">
        <input id="ts-enabled" type="checkbox" ${enabled ? "checked" : ""} onchange="markTurnstileDirty()">
        <span class="track"></span>
        <span>开启登录页人机验证</span>
      </label>
      <label class="cfg-field" for="ts-sitekey">
        <span>站点密钥<span class="cfg-unit">TURNSTILE_SITE_KEY</span></span>
        <input id="ts-sitekey" class="form-control" autocomplete="off" spellcheck="false" value="${escapeHtml(info.sitekey || "")}" placeholder="0x4AAAAA..." oninput="markTurnstileDirty()">
      </label>
      <label class="cfg-field" for="ts-secret">
        <span>密钥<span class="cfg-unit">TURNSTILE_SECRET</span></span>
        <input id="ts-secret" class="form-control" type="password" autocomplete="new-password" placeholder="${escapeHtml(secretHint)}" oninput="markTurnstileDirty()">
      </label>
      <label class="cfg-field" for="ts-hostnames">
        <span>允许域名<span class="cfg-unit">TURNSTILE_HOSTNAMES</span></span>
        <input id="ts-hostnames" class="form-control" autocomplete="off" spellcheck="false" value="${escapeHtml(info.hostnames || "")}" placeholder="vpush.net" oninput="markTurnstileDirty()">
      </label>
    </div>
    <div class="toolbar" style="margin-top:12px">
      <button type="button" class="btn-normal" id="ts-save" onclick="saveTurnstileSettings()">保存登录验证</button>
      <button type="button" class="btn-ghost" onclick="pasteTurnstileSecret()">从剪贴板填入</button>
    </div>
  </section>`;
}

async function loadAdminTurnstile() {
  const seq = _adminRenderSeq;
  try {
    const info = await api("/api/admin/turnstile");
    if (!routeStillActive(seq)) return;
    $("#admin-body").innerHTML = turnstileSettingsHtml(info);
  } catch (err) {
    if (!routeStillActive(seq)) return;
    $("#admin-body").innerHTML = emptyState("加载失败: " + err.message);
  }
}

async function saveTurnstileSettings() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const enabled = !!$("#ts-enabled")?.checked;
  const sitekey = $("#ts-sitekey")?.value.trim() || "";
  const secret = $("#ts-secret")?.value.trim() || "";
  const hostnames = $("#ts-hostnames")?.value.trim() || "";
  const secretSet = $("#ts-form")?.dataset.secretSet === "1";
  if (enabled && !sitekey) {
    flash("请填写站点密钥", "error");
    $("#ts-sitekey")?.focus();
    return;
  }
  if (enabled && !secret && !secretSet) {
    flash("请填写密钥", "error");
    $("#ts-secret")?.focus();
    return;
  }
  if (enabled && !hostnames) {
    flash("请填写允许域名", "error");
    $("#ts-hostnames")?.focus();
    return;
  }
  try {
    const saved = await api("/api/admin/turnstile", {
      method: "PUT",
      body: JSON.stringify({ enabled, sitekey, secret, hostnames }),
    });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    if (saved?.active) flash("登录验证已开启");
    else if (saved?.enabled) flash("开关已开，但还缺密钥，验证框不会出现", "error");
    else flash("登录验证已关闭");
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    await loadAdminGroup("account");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  }
}

async function saveImgbedSettings() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const baseUrl = $("#imgbed-base-url")?.value.trim() || "";
  const apiKey = $("#imgbed-token")?.value.trim() || "";
  const channelName = $("#imgbed-channel-name")?.value.trim() || "";
  const folder = $("#imgbed-folder")?.value.trim() || "";
  const retentionDays = parseInt($("#imgbed-retention-days")?.value, 10);
  if (!baseUrl) {
    flash("请填写图床地址", "error");
    $("#imgbed-base-url")?.focus();
    return;
  }
  try {
    const saved = await api("/api/admin/imgbed", {
      method: "PUT",
      body: JSON.stringify({
        base_url: baseUrl,
        token: apiKey,
        channel_name: channelName,
        folder,
        retention_days: Number.isFinite(retentionDays) ? retentionDays : 30,
      }),
    });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    if (saved?.last_check_error) {
      flash(`已保存，但连通检查失败：${saved.last_check_error}`, "error");
    } else {
      flash("图床设置已保存，连通正常");
    }
    history.replaceState(null, "", "/admin/stats?tab=imgbed");
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    await loadAdminStats(routeSeq);
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  }
}

let _imgbedClearPending = false;

async function clearImgbedSettings() {
  if (_imgbedClearPending) return;
  if (!confirm("清除图床设置？清除后 X 配图退回服务端代理，直到重新接入。")) return;
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  _imgbedClearPending = true;
  try {
    await api("/api/admin/imgbed", { method: "DELETE" });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash("已清除图床设置，X 配图走服务端代理");
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    await loadAdminStats(routeSeq);
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  } finally {
    _imgbedClearPending = false;
  }
}


// ---------- 管理后台（导航统一走左侧边栏） ----------
let _adminRenderSeq = 0; // 当前管理后台渲染令牌：loader 写 #admin-body 前凭此丢弃过期响应
let _adminStatsLoadSeq = 0;
let _adminStatsTimerSeq = 0;
let _lastAdminStatsSnapshot = null;

// 后台条目页签化：侧边栏一个条目，页内页签切换子页（路由即页签，深链可用）
const ADMIN_TAB_GROUPS = {
  content: { label: "内容管理", tabs: [
    { id: "dashboard", label: "全景概览" },
    { id: "kols", label: "大V管理" },
    { id: "vocab", label: "标签分类" },
    { id: "requests", label: "添加审批" },
  ]},
  ops: { label: "帖子与日志", tabs: [
    { id: "posts", label: "帖子" },
    { id: "logs", label: "推送记录" },
    { id: "audit", label: "操作日志" },
    { id: "backup", label: "备份" },
  ]},
  account: { label: "用户与注册", tabs: [
    { id: "users", label: "用户" },
    { id: "codes", label: "注册码" },
    { id: "turnstile", label: "登录验证" },
  ]},
};

// 旧路由 → 容器页签：收藏/旧链接/代码内 go() 全部自动落到新地址
const ADMIN_ROUTE_REDIRECTS = {
  dashboard: "content?tab=dashboard",
  kols: "content?tab=kols",
  vocab: "content?tab=vocab",
  requests: "content?tab=requests",
  posts: "ops?tab=posts",
  logs: "ops?tab=logs",
  audit: "ops?tab=audit",
  backup: "ops?tab=backup",
  users: "account?tab=users",
  codes: "account?tab=codes",
  categories: "content?tab=vocab",
  tags: "content?tab=vocab",
};

function adminGroupTabsHtml(groupKey, active) {
  const group = ADMIN_TAB_GROUPS[groupKey];
  return `<div class="settings-tabs" role="tablist" aria-label="${group.label}">
    ${group.tabs.map((t) => {
      const count = t.id === "requests" ? Number(state.pendingKolRequests) || 0 : 0;
      return `<button type="button" class="settings-tab ${t.id === active ? "active" : ""}" role="tab" aria-selected="${t.id === active}" data-tab="${t.id}" onclick="go('admin/${groupKey}?tab=${t.id}')">${t.label}${count ? ` <span class="tab-count">${count}</span>` : ""}</button>`;
    }).join("")}
  </div>`;
}

function mountAdminGroupTabs(groupKey, active) {
  const body = $("#admin-body");
  if (body && !body.querySelector(".admin-group-tabs")) {
    body.insertAdjacentHTML("afterbegin", `<div class="admin-group-tabs">${adminGroupTabsHtml(groupKey, active)}</div>`);
  }
}

function syncRequestBadges() {
  const count = Number(state.pendingKolRequests) || 0;
  document.querySelectorAll("[data-requests-badge]").forEach((el) => {
    el.textContent = count ? String(count) : "";
    el.hidden = !count;
  });
}

function updateNewsBadge() {
  const count = Number(state.newsUnreadCount) || 0;
  const label = count > 99 ? "99+" : (count ? String(count) : "");
  document.querySelectorAll("[data-news-badge]").forEach((el) => {
    el.textContent = label;
    el.hidden = !label;
  });
}

// 各子页 loader 由对应视图工厂解构（运行时才解析，此处用箭头惰性引用避免 TDZ）
const ADMIN_GROUP_LOADERS = {
  content: { dashboard: () => loadAdminDashboard(), kols: () => loadAdminKols(), vocab: () => loadAdminVocab(), requests: () => loadAdminRequests() },
  ops: { posts: () => loadAdminPosts(), logs: () => loadAdminLogs(), audit: () => loadAdminAudit(), backup: () => loadAdminBackup() },
  account: { users: () => loadAdminUsers(), codes: () => loadAdminCodes(), turnstile: () => loadAdminTurnstile() },
};

async function loadAdminGroup(groupKey, seq) {
  seq = seq ?? _adminRenderSeq; // renderAdmin 调 loader 不带参，兜底当前渲染令牌
  const tabs = ADMIN_TAB_GROUPS[groupKey].tabs;
  const requested = routeQuery().get("tab");
  const active = tabs.some((t) => t.id === requested) ? requested : tabs[0].id;
  await ADMIN_GROUP_LOADERS[groupKey][active]();
  if (!routeStillActive(seq)) return false;
  mountAdminGroupTabs(groupKey, active);
  syncRequestBadges();
  return true;
}

async function loadAdminContent(seq) { return loadAdminGroup("content", seq); }
async function loadAdminOps(seq) { return loadAdminGroup("ops", seq); }
async function loadAdminAccount(seq) { return loadAdminGroup("account", seq); }

async function renderAdmin(tab, seq) {
  _adminRenderSeq = seq;
  setPageTitle("管理后台");
  $("#main").innerHTML = `
    <div id="admin-body"><div class="admin-skeleton" aria-hidden="true">${Array(3).fill(`
      <div class="admin-sk-card">
        <div class="admin-sk-line admin-sk-head"></div>
        <div class="admin-sk-table-row"><div class="admin-sk-line"></div><div class="admin-sk-line"></div><div class="admin-sk-line"></div></div>
        <div class="admin-sk-table-row"><div class="admin-sk-line"></div><div class="admin-sk-line"></div></div>
        <div class="admin-sk-table-row"><div class="admin-sk-line"></div><div class="admin-sk-line"></div><div class="admin-sk-line"></div></div>
      </div>`).join("")}
    </div>`;
  const loaders = { content: loadAdminContent, ops: loadAdminOps, account: loadAdminAccount, stats: loadAdminStats, knowledge: loadAdminKnowledge };
  try {
    await loaders[tab]();
  } catch (err) {
    // 只有当前路由仍是本次渲染目标时才写错误状态，避免旧路由的错误覆盖新 tab
    if (routeStillActive(seq)) $("#admin-body").innerHTML = emptyState("加载失败: " + err.message);
  }
}

let statsTimer = null;

function stopStatsTimer() {
  _adminStatsTimerSeq += 1;
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
}

function startDashboardLiveTimer() {
  stopStatsTimer();
  statsTimer = setInterval(async () => {
    const timerSeq = _adminRenderSeq;
    const timerRequestSeq = ++_adminStatsTimerSeq;
    try {
      const fresh = await api("/api/stats");
      if (!routeStillActive(timerSeq) || timerRequestSeq !== _adminStatsTimerSeq) return;
      _lastAdminStatsSnapshot = fresh;
      renderStatsData(fresh);
    } catch {
      /* 后台刷新失败不打扰 */
    }
  }, 30000);
}

async function refreshDashboardLive() {
  try {
    const st = await api("/api/stats");
    if (!routeStillActive(_adminRenderSeq)) return;
    _lastAdminStatsSnapshot = st;
    renderStatsData(st);
  } catch (err) {
    if (!routeStillActive(_adminRenderSeq)) return;
    flash("刷新失败: " + err.message, "error");
  }
}

function fmtTs(ts) {
  return ts ? new Date(Number(ts) * 1000).toLocaleString() : "-";
}

// 飞书来源的 ISO 时间串（2026-09-03T00:58:17+00:00），fmtTs 的秒数语义会得到 Invalid Date
function fmtFeishuTime(s) {
  if (!s) return "-";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleString();
}

// 数据库里 SQLite 生成的 created_at/fetched_at 是 UTC（datetime('now')），
// 展示时按 UTC 解析并转成浏览器本地时间（北京时间），避免慢 8 小时
function fmtDbTime(s) {
  if (!s) return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(String(s));
  if (!m) return s;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
  if (Number.isNaN(d.getTime())) return s;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function rateBar(rate) {
  if (rate === null || rate === undefined) return `<span class="muted">暂无数据</span>`;
  const tone = rate >= 95 ? "ok" : rate >= 70 ? "warn" : "fail";
  return `
    <div class="rate-row">
      <div class="rate-bar">
        <div class="rate-fill ${tone}" style="width:${Math.min(100, Math.max(0, rate))}%"></div>
      </div>
      <span class="rate-label">${rate}%</span>
    </div>`;
}

function isAdminSettingsPath() {
  const path = location.pathname;
  return path === "/admin/stats" || path === "/admin/knowledge";
}

async function reloadAdminSettingsPage(seq, authoritativeImaStatus = null) {
  if (location.pathname === "/admin/knowledge") {
    return loadAdminKnowledge(seq, authoritativeImaStatus);
  }
  return loadAdminStats(seq, authoritativeImaStatus);
}

function feishuSourceStatusLabel(source) {
  const labels = {
    pending: "等待同步",
    running: "同步中",
    succeeded: "同步正常",
    failed: "同步失败",
    authorization_required: "需要重新授权",
    disabled: "已停用",
  };
  return labels[source.sync_status] || source.sync_status || "未知";
}

function feishuSourceRowsHtml(data) {
  const sources = data.sources || [];
  if (!sources.length) return emptyState("还没有飞书文档来源");
  return `<div class="feishu-source-list">${sources.map((source) => {
    const detail = [
      source.source_type === "wiki" ? "Wiki" : "Docx",
      source.revision_id ? `revision ${source.revision_id}` : "尚无版本",
      source.entry_count ? `${source.entry_count} 条记录` : "尚无记录",
      source.last_success_at ? `最近成功 ${fmtFeishuTime(source.last_success_at)}` : "尚未同步成功",
      source.next_check_at && source.enabled ? `下次检查 ${fmtFeishuTime(source.next_check_at)}` : "",
    ].filter(Boolean).join(" · ");
    const error = source.last_error ? `<p class="feishu-source-error">${escapeHtml(typeof imaSafeError === "function" ? imaSafeError(source.last_error) : source.last_error)}</p>` : "";
    const displayMode = source.display_mode === "document" ? "document" : "timeline";
    const readable = source.sync_status === "succeeded" && Number(source.entry_count || 0) > 0;
    const openLine = readable
      ? `<p class="feishu-source-open">全员可读，无需单独授权</p>`
      : `<p class="feishu-source-open is-blocked">同步成功后全员可读，当前暂无可读内容</p>`;
    const shownTitle = source.display_name || source.title;
    return `<article class="feishu-source-row" data-source-id="${source.id}">
      <div class="feishu-source-copy"><div class="feishu-source-title"><strong>${escapeHtml(shownTitle)}</strong><button type="button" class="feishu-title-rename" onclick="renameFeishuDocumentSource(this.closest('[data-source-id]').dataset.sourceId,${jsString(shownTitle)})" aria-label="修改展示名">改名</button><span class="feishu-source-state" data-status="${escapeHtml(source.sync_status)}">${escapeHtml(feishuSourceStatusLabel(source))}</span></div><p>${escapeHtml(detail)}</p>${error}</div>
      <label class="feishu-source-toggle"><span>启用</span><input type="checkbox" ${source.enabled ? "checked" : ""} onchange="toggleFeishuDocumentSource(this.closest('[data-source-id]').dataset.sourceId,this.checked,this)"></label>
      <div class="feishu-source-actions">
        <span class="feishu-display-label">展示方式</span>
        <div class="feishu-display-segment" role="group" aria-label="展示方式 ${escapeHtml(source.title)}">
        <button type="button" class="feishu-display-option${displayMode === "timeline" ? " is-selected" : ""}" aria-pressed="${displayMode === "timeline"}" onclick="setFeishuSourceDisplay(this.closest('[data-source-id]').dataset.sourceId,'timeline',this)">时间线</button>
        <button type="button" class="feishu-display-option${displayMode === "document" ? " is-selected" : ""}" aria-pressed="${displayMode === "document"}" onclick="setFeishuSourceDisplay(this.closest('[data-source-id]').dataset.sourceId,'document',this)">文档</button>
        </div>
        <button type="button" class="btn-ghost feishu-action" data-source-id="${source.id}" onclick="syncFeishuDocumentSource(this.dataset.sourceId,this)" aria-label="立即同步 ${escapeHtml(source.title)}">${REFRESH_ICON}<span>立即同步</span></button>
        <a class="btn-ghost feishu-action" href="${escapeHtml(source.canonical_url)}" target="_blank" rel="noopener" aria-label="打开飞书原文 ${escapeHtml(source.title)}">${EXTERNAL_LINK_ICON}<span>打开原文</span></a>
        <button type="button" class="btn-ghost danger feishu-action" data-source-id="${source.id}" data-title="${escapeHtml(source.title)}" onclick="removeFeishuDocumentSource(this.dataset.sourceId,this.dataset.title,this)">移除</button>
      </div>
      ${openLine}
    </article>`;
  }).join("")}</div>`;
}

function feishuDocsConfigHtml(data) {
  const cfg = data.config || {};
  const secretPlaceholder = cfg.app_secret_set ? "已保存（留空保持不变）" : "飞书开放平台 → 凭证与基础信息";
  const defaultRedirect = location.protocol === "https:"
    ? `${location.origin}/api/admin/feishu-documents/oauth/callback`
    : "";
  const redirectValue = cfg.redirect_uri || defaultRedirect;
  const redirectWarn = cfg.redirect_uri && cfg.redirect_path_ok === false
    ? `<p class="form-error" role="alert">当前回调路径不是本站 OAuth 回调（…/api/admin/feishu-documents/oauth/callback），授权会失败。</p>`
    : "";
  const sourceLabel = { db: "设置页", env: "环境变量" }[cfg.config_source] || "";
  return `<div class="cfg-group feishu-config">
    <div class="feishu-config-head">
      <p class="cfg-group-title">应用与采集</p>
      <span class="muted">${sourceLabel ? `配置来源：${sourceLabel}` : "尚未配置"}</span>
    </div>
    <div class="cfg-fields">
      <label class="cfg-field"><span>App ID</span><input id="feishu-cfg-app-id" class="form-control" value="${escapeHtml(cfg.app_id || "")}" placeholder="cli_..." autocomplete="off"></label>
      <label class="cfg-field"><span>App Secret</span><input id="feishu-cfg-secret" class="form-control" type="password" autocomplete="new-password" placeholder="${escapeHtml(secretPlaceholder)}"></label>
      <label class="cfg-field feishu-field--wide"><span>回调地址（须与开放平台安全设置一致）</span><input id="feishu-cfg-redirect" class="form-control" type="url" value="${escapeHtml(redirectValue)}" placeholder="https://your.domain.com/api/admin/feishu-documents/oauth/callback" autocomplete="off"></label>
      <label class="cfg-field feishu-field--wide"><span>授权权限（空格分隔）</span><input id="feishu-cfg-scopes" class="form-control" value="${escapeHtml(cfg.scopes || "")}" placeholder="wiki:node:read docx:document:readonly docs:document.media:download offline_access" autocomplete="off"></label>
      <label class="cfg-field"><span>检查间隔（秒，≥15）</span><input id="feishu-cfg-interval" class="form-control" type="number" min="15" max="86400" step="1" value="${Number(cfg.interval_seconds) || 60}"></label>
    </div>
    ${redirectWarn}
    <p class="section-meta">修改 App ID / Secret 后需重新授权；检查间隔对所有来源生效，按文档 revision 增量同步。</p>
    <div class="toolbar feishu-config-actions">
      <button type="button" class="btn-normal" id="feishu-cfg-save" onclick="saveFeishuDocsConfig()">保存设置</button>
    </div>
  </div>`;
}

async function renameFeishuDocumentSource(id, current) {
  const name = prompt("新的展示名（留空则恢复飞书标题）：", current || "");
  if (name === null) return;
  const value = name.trim();
  if (value.length > 200) {
    flash("展示名过长（≤200 字）", "error");
    return;
  }
  try {
    await api(`/api/admin/feishu-documents/${id}`, { method: "PATCH", body: JSON.stringify({ display_name: value }) });
    flash(value ? "展示名已更新，知识库内容将随之刷新" : "已恢复飞书标题，知识库内容将随之刷新");
    await loadFeishuDocumentSources();
  } catch (err) {
    flash(err.message || "保存失败", "error");
  }
}

async function saveFeishuDocsConfig() {
  const button = $("#feishu-cfg-save");
  const interval = Number($("#feishu-cfg-interval")?.value || 0);
  if (!Number.isFinite(interval) || interval < 15 || interval > 86400) {
    flash("检查间隔需在 15–86400 秒之间", "error");
    return;
  }
  const payload = {
    app_id: $("#feishu-cfg-app-id")?.value.trim() || "",
    redirect_uri: $("#feishu-cfg-redirect")?.value.trim() || "",
    scopes: $("#feishu-cfg-scopes")?.value.trim() || "",
    interval_seconds: interval,
  };
  const secret = $("#feishu-cfg-secret")?.value?.trim();
  if (secret) payload.app_secret = secret;
  if (button) button.disabled = true;
  try {
    const r = await api("/api/admin/feishu-documents/config", { method: "PUT", body: JSON.stringify(payload) });
    flash(r.reauth_required ? "设置已保存；应用凭据已变更，请重新授权" : "飞书文档设置已保存");
    await loadFeishuDocumentSources();
  } catch (err) {
    flash(err.message || "保存失败", "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadFeishuDocumentSources() {
  const host = $("#feishu-documents-body");
  if (!host) return;
  const loadSeq = ++_feishuSourceLoadSeq;
  const routeSeq = routeRenderSeq;
  const active = () => loadSeq === _feishuSourceLoadSeq && routeStillActive(routeSeq) && $("#feishu-documents-body") === host;
  try {
    const data = await api("/api/admin/feishu-documents");
    if (!active()) return;
    const auth = data.authorized ? "已授权" : (data.configured ? "尚未授权" : "应用未配置");
    const authButton = data.configured
      ? `<button type="button" class="btn-ghost" onclick="authorizeFeishuDocuments()">${data.authorized ? "重新授权" : "授权飞书"}</button>`
      : "";
    host.innerHTML = `${feishuDocsConfigHtml(data)}<div class="feishu-source-summary"><span>${escapeHtml(auth)} · 每 ${Number(data.interval_seconds) || 60} 秒检查</span>${authButton}</div>${feishuSourceRowsHtml(data)}`;
  } catch (err) {
    if (!active()) return;
    host.innerHTML = emptyState(`飞书文档加载失败：${err.message}`, `<div><button type="button" class="btn-normal" onclick="loadFeishuDocumentSources()">重试</button></div>`);
  }
}

async function authorizeFeishuDocuments() {
  try {
    const data = await api("/api/admin/feishu-documents/oauth/start", { method: "POST" });
    location.assign(data.url);
  } catch (err) {
    flash(err.message || "无法开始飞书授权", "error");
  }
}

function feishuLocalUrlInfo(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    const host = (parsed.hostname || "").toLowerCase().replace(/\.$/, "");
    const parts = parsed.pathname.split("/").filter(Boolean);
    const trusted = ["feishu.cn", "larksuite.com"].some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
    if (parsed.protocol !== "https:" || !trusted || parts.length !== 2 || !["wiki", "docx"].includes(parts[0])) return { invalid: true };
    return { type: parts[0], label: parts[0] === "wiki" ? "Wiki" : "Docx" };
  } catch {
    return { invalid: true };
  }
}

function renderFeishuDocumentPreview(info = null, message = "") {
  const preview = $("#feishu-document-preview");
  if (!preview) return;
  if (!info && !message) { preview.innerHTML = ""; return; }
  if (info?.loading) {
    preview.innerHTML = `<span class="feishu-preview-status is-loading">正在读取 ${escapeHtml(info.label)} 文档信息…</span>`;
    return;
  }
  if (info?.data) {
    const data = info.data;
    preview.innerHTML = `<span class="feishu-preview-status is-ready"><strong>${escapeHtml(data.title || "飞书文档")}</strong><span>${escapeHtml(data.source_type === "wiki" ? "Wiki" : "Docx")} · revision ${escapeHtml(data.revision_id || "-")}</span></span>`;
    return;
  }
  preview.innerHTML = `<span class="feishu-preview-status${info?.invalid ? " is-invalid" : ""}">${escapeHtml(message || (info?.invalid ? "请输入有效的飞书 Wiki 或 Docx 链接" : "尚未读取文档信息"))}</span>`;
}

function queueFeishuDocumentPreview(value) {
  const url = String(value || "").trim();
  clearTimeout(_feishuPreviewTimer);
  _feishuPreview = { url, data: null };
  const local = feishuLocalUrlInfo(url);
  if (!url) { renderFeishuDocumentPreview(); return; }
  if (!local || local.invalid) { renderFeishuDocumentPreview({ invalid: true }); return; }
  renderFeishuDocumentPreview({ loading: true, label: local.label });
  const seq = ++_feishuPreviewSeq;
  _feishuPreviewTimer = setTimeout(() => previewFeishuDocument(url, seq), 360);
}

async function previewFeishuDocument(url, seq = _feishuPreviewSeq) {
  try {
    const data = await api("/api/admin/feishu-documents/preview", { method: "POST", body: JSON.stringify({ url }) });
    if (seq !== _feishuPreviewSeq || $("#feishu-document-url")?.value.trim() !== url) return;
    _feishuPreview = { url, data };
    renderFeishuDocumentPreview({ data });
  } catch (err) {
    if (seq !== _feishuPreviewSeq || $("#feishu-document-url")?.value.trim() !== url) return;
    _feishuPreview = { url, data: null };
    renderFeishuDocumentPreview(null, err.message || "暂时无法读取文档信息");
  }
}

async function addFeishuDocumentSource() {
  const input = $("#feishu-document-url");
  const button = $("#feishu-document-add");
  const url = input?.value?.trim() || "";
  if (!url) { flash("请填写飞书 Wiki 或 Docx 链接", "error"); return; }
  const local = feishuLocalUrlInfo(url);
  if (!local || local.invalid) { renderFeishuDocumentPreview({ invalid: true }); flash("请输入有效的飞书 Wiki 或 Docx 链接", "error"); return; }
  if (!confirm("飞书文档添加后全站用户可读，不支持单独授权。确认添加吗？")) return;
  if (button) button.disabled = true;
  try {
    if (_feishuPreview.url !== url || !_feishuPreview.data) {
      await previewFeishuDocument(url, ++_feishuPreviewSeq);
    }
    await api("/api/admin/feishu-documents", { method: "POST", body: JSON.stringify({ url }) });
    input.value = "";
    _feishuPreview = { url: "", data: null };
    renderFeishuDocumentPreview();
    flash("来源已添加，正在首次同步");
    await loadFeishuDocumentSources();
  } catch (err) {
    flash(err.message || "添加失败", "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function setFeishuSourceDisplay(sourceId, displayMode, button) {
  const row = button?.closest("[data-source-id]");
  const options = row?.querySelectorAll(".feishu-display-option") || [];
  options.forEach((option) => { option.disabled = true; });
  try {
    await api(`/api/admin/feishu-documents/${encodeURIComponent(sourceId)}`, { method: "PATCH", body: JSON.stringify({ display_mode: displayMode }) });
    options.forEach((option) => {
      const selected = option.textContent.trim() === (displayMode === "document" ? "文档" : "时间线");
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-pressed", String(selected));
    });
    flash(displayMode === "document" ? "已切换为文档视图，重新打开即生效" : "已切换为时间线视图");
  } catch (err) {
    const previous = displayMode === "document" ? "timeline" : "document";
    options.forEach((option) => {
      const selected = option.textContent.trim() === (previous === "document" ? "文档" : "时间线");
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-pressed", String(selected));
    });
    flash(err.message || "切换失败", "error");
  } finally {
    options.forEach((option) => { option.disabled = false; });
  }
}

async function toggleFeishuDocumentSource(sourceId, enabled, input) {
  input.disabled = true;
  try {
    await api(`/api/admin/feishu-documents/${encodeURIComponent(sourceId)}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
    flash(enabled ? "来源已启用" : "来源已停用");
    await loadFeishuDocumentSources();
  } catch (err) {
    input.checked = !enabled;
    flash(err.message || "更新失败", "error");
  } finally {
    input.disabled = false;
  }
}

async function syncFeishuDocumentSource(sourceId, button) {
  button.disabled = true;
  try {
    await api(`/api/admin/feishu-documents/${encodeURIComponent(sourceId)}/sync`, { method: "POST" });
    flash("已开始同步");
    setTimeout(loadFeishuDocumentSources, 1200);
  } catch (err) {
    flash(err.message || "同步失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function removeFeishuDocumentSource(sourceId, title, button) {
  if (!confirm(`移除「${title}」？历史归档会永久保留。`)) return;
  button.disabled = true;
  try {
    await api(`/api/admin/feishu-documents/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
    flash("来源已移除，历史归档仍保留");
    await loadFeishuDocumentSources();
  } catch (err) {
    flash(err.message || "移除失败", "error");
    button.disabled = false;
  }
}

function imaStoragePanelHtml() {
  return `<section class="section-panel ks-panel" data-panel="storage" id="ks-panel-storage" role="tabpanel" aria-labelledby="ks-tab-storage">
    <header class="section-head"><div><h2 class="section-title">存储</h2>
    <p class="section-meta">现网经 ARM /pull 取文件。冷备由 ARM 推到存储机，此页不触发旧的 NFS 备份或去重。</p></div></header>
    <p class="muted" id="ima-storage-status">正在检查 ARM…</p>
    <div class="toolbar ima-storage-toolbar">
      <button type="button" class="btn-ghost" id="ima-storage-refresh" onclick="refreshImaStorage()">刷新状态</button>
    </div>
    <div id="ima-storage-health"><p class="muted">加载中…</p></div>
  </section>`;
}


async function savePollingConfig() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const body = {
    interval_seconds: Number($("#pc-interval").value),
    priority_interval_seconds: Number($("#pc-priority").value),
    truth_interval_seconds: Number($("#pc-truth").value),
    digest_interval_seconds: Number($("#pc-digest").value),
    source_probe_interval_seconds: Number($("#pc-probe").value),
    cookie_keepalive_interval_seconds: Number($("#pc-keepalive").value),
    daily_report_hour: Number($("#pc-daily").value),
    translate_twitter_content: $("#pc-translate").checked,
    telegram_rich_messages: $("#pc-tg-rich") ? $("#pc-tg-rich").checked : true,
    combination_base_seconds: Number($("#pc-cb").value),
    combination_idle_cap_seconds: Number($("#pc-cc").value),
    normal_idle_cap_seconds: Number($("#pc-nc").value),
    priority_idle_cap_seconds: Number($("#pc-pc").value),
    x_fallback_cap_seconds: Number($("#pc-xc").value),
    secondary_interval_seconds: Number($("#pc-si").value),
    secondary_idle_cap_seconds: Number($("#pc-sc").value),
    secondary_digest_interval_seconds: Number($("#pc-sd").value),
    secondary_min_digest_count: Number($("#pc-sd-min").value),
  };
  const btn = $("#pc-save");
  if (btn) btn.disabled = true;
  try {
    await api("/api/admin/polling-config", { method: "PUT", body: JSON.stringify(body) });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    // 标准操作反馈 toast；不重建页面（loadAdminStats 会整页重建）
    flash("抓取设置已保存，即时生效");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  } finally {
    if (btn && document.body.contains(btn)) btn.disabled = false;
  }
}

async function saveZsxqPollingConfig() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const body = {
    zsxq_max_pages: Number($("#pc-zq-pages").value),
    zsxq_fetch_delay_seconds: Number($("#pc-zq-delay").value),
    zsxq_file_delay_seconds: Number($("#pc-zq-file-delay").value),
    zsxq_prefetch_files: $("#pc-zq-prefetch").checked,
    zsxq_fetch_comments: $("#pc-zq-comments").checked,
    zsxq_max_comment_pages: Number($("#pc-zq-comment-pages").value),
    zsxq_comment_budget: Number($("#pc-zq-comment-budget").value),
    zsxq_app_channel: $("#pc-zq-app").checked,
    zsxq_app_device: $("#pc-zq-app-device").value.trim(),
  };
  const btn = $("#pc-zq-save");
  if (btn) btn.disabled = true;
  try {
    await api("/api/admin/polling-config", { method: "PUT", body: JSON.stringify(body) });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash("星球设置已保存，即时生效");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  } finally {
    if (btn && document.body.contains(btn)) btn.disabled = false;
  }
}

let _localLibsLast = null;
let _scanInFlight = false; // 扫描进行中：15s 轮询重渲染时按钮保持禁用，不复活

// admin 视图懒加载：cicc 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let ciccView, loadCiccStatus, startCiccPoll, stopCiccPoll, saveCiccCategories, triggerCicc, saveCiccScheduleTime, toggleCiccSchedule;

function localLibraryCardHtml(lib) {
  const meta = lib.error ? `异常：${escapeHtml(lib.error)}` : `${lib.pdf_count ?? 0} 个 PDF`;
  const aclCount = (lib.acl_usernames || []).length;
  const tags = (lib.tags || []).length ? ` · 标签 ${escapeHtml(lib.tags.join("、"))}` : "";
  const ciccInner = ciccView.renderLibraryControls(lib.slug);
  return `<div class="ima-source-block" data-slug="${escapeHtml(lib.slug)}">
    <header class="ima-source-block-head"><div><h3 class="ima-source-title">${escapeHtml(lib.name)}</h3>
    <p class="section-meta"><code>${escapeHtml(lib.slug)}</code> · ${meta} · ${lib.enabled ? "已启用" : "未启用"}${tags} · ${aclCount ? `权限 ${aclCount} 人` : "仅管理员"}</p></div>
    <div class="toolbar">
      <button type="button" class="btn-ghost" data-ll-edit="${escapeHtml(lib.slug)}">编辑</button>
      <button type="button" class="btn-ghost" data-ll-toggle="${escapeHtml(lib.slug)}" data-ll-enabled="${lib.enabled ? "false" : "true"}">${lib.enabled ? "停用" : "启用"}</button>
    </div></header>
    ${ciccInner}
  </div>`;
}

function renderLocalTab() {
  const slot = $("#local-libs-body");
  if (!slot || !_localLibsLast) return;
  ciccView.rememberDetails(slot);
  const libs = _localLibsLast.libraries || [];
  const fallbackCicc = ciccView.renderFallback(libs);
  // scanned_at 是 ISO 串（fmtTs 只吃 epoch 秒）
  const scannedAt = _localLibsLast.scanned_at ? new Date(_localLibsLast.scanned_at) : null;
  const scannedLabel = scannedAt && !Number.isNaN(scannedAt.getTime())
    ? `上次扫描 ${scannedAt.toLocaleString()}`
    : "从未扫描";
  slot.innerHTML = `
    <div class="toolbar" style="margin:12px 0">
      <button type="button" class="btn-normal" id="local-scan-btn" ${_scanInFlight ? "disabled" : ""} onclick="scanLocalLibraries()">${_scanInFlight ? "扫描中…" : "扫描本地库"}</button>
      <button type="button" class="btn-ghost" onclick="openLocalLibraryCreateModal()">新建本地库</button>
      <span class="muted" aria-live="polite">${scannedLabel}</span>
    </div>
    ${fallbackCicc}
    ${libs.length
      ? libs.map(localLibraryCardHtml).join("")
      : '<p class="muted">尚未发现本地库。点「新建本地库」创建，或在存储机 local/&lt;slug&gt;/ 放入 .vpush-local-library.json 标记与 PDF 后点「扫描本地库」。</p>'}`;
}

async function loadLocalLibraries(quiet = false) {
  const routeSeq = routeRenderSeq;
  try {
    const data = await api("/api/admin/ima-local-libraries");
    if (!routeStillActive(routeSeq)) return;
    _localLibsLast = data;
    renderLocalTab();
  } catch (err) {
    if (!routeStillActive(routeSeq)) return;
    if (!quiet) flash(err.message, "error");
    const slot = $("#local-libs-body");
    if (slot) slot.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

async function scanLocalLibraries() {
  if (_scanInFlight) return;
  _scanInFlight = true;
  renderLocalTab();
  try {
    const data = await api("/api/admin/ima-local-libraries/scan", { method: "POST" });
    flash(data.status === "scan_failed" ? "扫描失败：存储归档不可读" : "扫描完成");
    _localLibsLast = data;
  } catch (err) {
    flash(err.message, "error");
  } finally {
    _scanInFlight = false;
    renderLocalTab();
  }
}

async function toggleLocalLibrary(slug, enabled) {
  try {
    const data = await api(`/api/admin/ima-local-libraries/${encodeURIComponent(slug)}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    });
    flash(enabled ? "本地库已启用" : "本地库已停用");
    _localLibsLast = data;
    renderLocalTab();
  } catch (err) {
    flash(err.message, "error");
  }
}

function splitListCsv(text) {
  return String(text || "").split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
}

function localLibraryModalShell(title, innerHtml) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal-card" role="dialog" aria-modal="true" aria-label="${title}">
      <h3 class="ima-source-title" style="margin-bottom:12px">${title}</h3>
      ${innerHtml}
      <div class="toolbar" style="margin-top:16px">
        <button class="btn-normal" id="ll-save">保存</button>
        <button type="button" class="btn-sm" data-close>取消</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  const snap = () => ({
    name: mask.querySelector("#ll-name")?.value || "",
    tags: mask.querySelector("#ll-tags")?.value || "",
  });
  const initial = snap();
  const close = () => {
    const now = snap();
    if ((now.name !== initial.name || now.tags !== initial.tags)
      && !confirm("有未保存的修改，确定关闭？")) return;
    mask.remove();
  };
  mask.addEventListener("click", (e) => {
    if (e.target === mask) close();
  });
  trapFocus(mask, close);
  mask.querySelector("[data-close]").addEventListener("click", close);
  const first = mask.querySelector("#ll-name, input, select, textarea, button");
  if (first) first.focus();
  return mask;
}

async function openLocalLibraryModal(slug) {
  const lib = ((_localLibsLast && _localLibsLast.libraries) || []).find((l) => l.slug === slug);
  if (!lib) return;
  try {
    await fetchAclCandidateUsers();
  } catch (err) {
    flash("加载用户失败: " + err.message, "error");
    return;
  }
  const aclHtml = aclPickerHtml(lib.acl_usernames || [], "ll-acl-list");
  const mask = localLibraryModalShell(`编辑本地库：${escapeHtml(lib.name)}`, `
    <label class="form-label">库名
      <input id="ll-name" class="form-control" maxlength="80" value="${escapeHtml(lib.name)}">
    </label>
    <label class="form-label">库级标签（逗号分隔）
      <input id="ll-tags" class="form-control" value="${escapeHtml((lib.tags || []).join(", "))}" placeholder="研报, 中金">
    </label>
    <p class="muted">改标签后保存可立刻扫描，写入库内文档。</p>
    <p class="form-label">权限控制（添加或移除即时生效）</p>
    ${aclHtml}`);
  const picker = mask.querySelector(".ima-acl-picker");
  if (picker) picker.dataset.groupId = `local-${slug}`;
  mask.querySelector("#ll-save").addEventListener("click", () => saveLocalLibraryModal(slug, mask, lib.tags || []));
}

async function saveLocalLibraryModal(slug, mask, previousTags) {
  const btn = mask.querySelector("#ll-save");
  const name = mask.querySelector("#ll-name").value.trim();
  const tags = splitListCsv(mask.querySelector("#ll-tags").value);
  if (!name) {
    flash("库名不能为空", "error");
    return;
  }
  const tagsChanged = tags.slice().sort().join("\0") !== [...previousTags].map(String).sort().join("\0");
  if (btn) btn.disabled = true;
  try {
    await api(`/api/admin/ima-local-libraries/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: JSON.stringify({ name, tags }),
    });
    mask.remove();
    flash("已保存本地库设置");
    await loadLocalLibraries(true);
    if (tagsChanged && confirm("库级标签已改，现在扫描以应用到库内文档？")) {
      await scanLocalLibraries();
    }
  } catch (err) {
    flash("保存失败: " + err.message, "error");
    if (btn) btn.disabled = false;
  }
}

function openLocalLibraryCreateModal() {
  const mask = localLibraryModalShell("新建本地库", `
    <label class="form-label">目录名 slug（小写字母/数字/短横线）
      <input id="ll-slug" class="form-control" maxlength="47" placeholder="my-papers">
    </label>
    <label class="form-label">库名
      <input id="ll-name" class="form-control" maxlength="80" placeholder="我的论文库">
    </label>
    <label class="form-label">库级标签（可选，逗号分隔）
      <input id="ll-tags" class="form-control" placeholder="研报">
    </label>
    <p class="muted" style="margin-top:8px">创建后在存储机 <code>local/&lt;slug&gt;/</code> 放入 PDF，回这里点「扫描本地库」。</p>`);
  mask.querySelector("#ll-save").addEventListener("click", () => saveLocalLibraryCreate(mask));
}

async function saveLocalLibraryCreate(mask) {
  const btn = mask.querySelector("#ll-save");
  const slug = mask.querySelector("#ll-slug").value.trim().toLowerCase();
  const name = mask.querySelector("#ll-name").value.trim();
  const tags = splitListCsv(mask.querySelector("#ll-tags").value);
  if (!slug || !name) {
    flash("slug 与库名必填", "error");
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/admin/ima-local-libraries", {
      method: "POST",
      body: JSON.stringify({ slug, name, tags }),
    });
    mask.remove();
    flash(data.status === "scan_failed" ? `已创建「${name}」，但扫描失败：存储归档不可读` : `已创建「${name}」`);
    _localLibsLast = data;
    renderLocalTab();
  } catch (err) {
    flash("创建失败: " + err.message, "error");
    if (btn) btn.disabled = false;
  }
}

function fmtCacheBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "0 MB";
  if (n < 1048576) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

async function purgeZsxqCache() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  try {
    const r = await api("/api/admin/zsxq-cache/purge", { method: "POST" });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    const el = $("#zq-cache-stat");
    if (el) {
      el.textContent = `附件缓存 ${fmtCacheBytes(r.bytes)} / ${r.files || 0} 个文件`;
    }
    flash(r.deleted ? `已清理 ${r.deleted} 个未引用附件` : "没有可清理的附件");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  }
}

async function pasteCookieField(inputId) {
  const el = $("#" + inputId);
  if (!el) return;
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) {
      flash("剪贴板是空的", "error");
      return;
    }
    el.value = text;
    el.focus();
    flash("已填入，确认后点保存");
  } catch {
    flash("无法读剪贴板，请直接粘贴到输入框", "error");
  }
}

function focusCookieField(kind) {
  const focusId = { xueqiu: "xq-cookie", weibo: "wb-qr-start", twitter: "tw-cookie", ima: "ima-cookie", zsxq: "zq-cookie" }[kind];
  if (focusId) $(`#${focusId}`)?.focus();
}

let _cookieClearPending = false;

async function clearSavedCookie(kind, label) {
  if (_cookieClearPending) return;
  if (!confirm(`清除「${label}」Cookie？清除后该数据源会停止抓取，直到重新保存。`)) return;
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  _cookieClearPending = true;
  try {
    await api(`/api/admin/cookies/${kind}`, { method: "DELETE" });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(`已清除「${label}」Cookie`);
    if (kind === "ima" || kind === "zsxq") {
      if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
      await reloadAdminSettingsPage(routeSeq);
    } else {
      history.replaceState(null, "", "/admin/stats?tab=cookies");
      if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
      await loadAdminStats(routeSeq);
    }
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    focusCookieField(kind);
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  } finally {
    _cookieClearPending = false;
  }
}

async function saveXueqiuCookie() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const cookie = $("#xq-cookie").value.trim();
  if (!cookie) {
    flash("请先粘贴雪球 Cookie", "error");
    return;
  }
  try {
    await api("/api/admin/xueqiu-cookie", {
      method: "POST",
      body: JSON.stringify({ cookie }),
    });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash("雪球 Cookie 已保存，即时生效");
    history.replaceState(null, "", "/admin/stats?tab=cookies");
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    await loadAdminStats(routeSeq);
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    focusCookieField("xueqiu");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  }
}

async function saveZsxqCookie() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const cookie = $("#zq-cookie").value.trim();
  if (!cookie) {
    flash("请先粘贴知识星球 Cookie", "error");
    return;
  }
  try {
    await api("/api/admin/zsxq-cookie", {
      method: "POST",
      body: JSON.stringify({ cookie }),
    });
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash("知识星球 Cookie 已保存，即时生效");
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    await reloadAdminSettingsPage(routeSeq);
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    focusCookieField("zsxq");
  } catch (err) {
    if (!sessionOwnerStillActive(routeSeq, token, sessionGeneration)) return;
    flash(err.message, "error");
  }
}

async function saveTwitterCookie() {
  const routeSeq = routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  const cookie = $("#tw-cookie").value.trim();
  if (!cookie) {
    flash("请先粘贴 X Cookie", "error");
    return;
  }
  try {
    await api("/api/admin/twitter-cookie", {
      method: "POST",
      body: JSON.stringify({ cookie }),
    });
    if (!routeStillActive(routeSeq) || token !== state.token
      || sessionGeneration !== imaMountState.sessionGeneration) return;
    flash("X Cookie 已保存，即时生效");
    history.replaceState(null, "", "/admin/stats?tab=cookies");
    await loadAdminStats(routeSeq);
    if (!routeStillActive(routeSeq) || token !== state.token
      || sessionGeneration !== imaMountState.sessionGeneration) return;
    focusCookieField("twitter");
  } catch (err) {
    if (!routeStillActive(routeSeq) || token !== state.token
      || sessionGeneration !== imaMountState.sessionGeneration) return;
    flash(err.message, "error");
  }
}

let wbQrTimer = null;
let wbQrSeq = 0;

function weiboQrOwnerActive(owner) {
  return !!owner && owner.seq === wbQrSeq
    && owner.token === state.token
    && owner.sessionGeneration === imaMountState.sessionGeneration
    && routeStillActive(owner.routeSeq);
}

async function startWeiboQr() {
  const owner = {
    routeSeq: routeRenderSeq,
    token: state.token,
    sessionGeneration: imaMountState.sessionGeneration,
    seq: ++wbQrSeq,
  };
  if (wbQrTimer) {
    clearTimeout(wbQrTimer);
    wbQrTimer = null;
  }
  try {
    const data = await api("/api/admin/weibo-qr/start", { method: "POST" });
    if (!weiboQrOwnerActive(owner)) return;
    $("#wb-qr-box").innerHTML = `
      <div class="qr-card">
        <img src="${escapeHtml(data.qrurl)}" alt="微博登录二维码" width="220" height="220">
      </div>
      <p class="muted qr-status" id="wb-qr-status">等待扫码…</p>`;
    let timer = null;
    const tick = async () => {
      if (!weiboQrOwnerActive(owner) || wbQrTimer !== timer) return;
      const cont = await pollWeiboQr(data.qrid, owner);
      if (!weiboQrOwnerActive(owner) || wbQrTimer !== timer || !cont) return;
      timer = setTimeout(tick, 2000);
      wbQrTimer = timer;
    };
    timer = setTimeout(tick, 2000);
    wbQrTimer = timer;
  } catch (err) {
    if (weiboQrOwnerActive(owner)) flash(err.message, "error");
  }
}

async function pollWeiboQr(qrid, owner) {
  owner = owner || {
    routeSeq: routeRenderSeq,
    token: state.token,
    sessionGeneration: imaMountState.sessionGeneration,
    seq: wbQrSeq,
  };
  if (!weiboQrOwnerActive(owner)) return false;
  try {
    const data = await api(`/api/admin/weibo-qr/status?qrid=${encodeURIComponent(qrid)}`);
    if (!weiboQrOwnerActive(owner)) return false;
    const statusEl = $("#wb-qr-status");
    if (!statusEl) return false;
    if (data.status === "pending") {
      statusEl.textContent = "等待扫码…";
      return true;
    }
    if (data.status === "scanned") {
      statusEl.textContent = "已扫描，请在手机上确认登录";
      return true;
    }
    if (data.status === "ok") {
      statusEl.textContent = "登录成功，微博 Cookie 已自动保存";
      flash("微博 Cookie 已保存");
    }
    return false;
  } catch (err) {
    if (!weiboQrOwnerActive(owner)) return false;
    const statusEl = $("#wb-qr-status");
    if (statusEl) statusEl.textContent = "登录失败：" + err.message;
    return false;
  }
}

function parseDbUtcMs(s) {
  if (!s) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(String(s));
  if (!m) return null;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

// ---------- 主题（深色模式）----------
const THEME_KEY = "theme"; // 值：light | dark | auto

function themeMode() {
  try {
    return localStorage.getItem(THEME_KEY) || "auto";
  } catch {
    return "auto";
  }
}

function systemPrefersDark() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyTheme() {
  const mode = themeMode();
  const dark = mode === "dark" || (mode === "auto" && systemPrefersDark());
  document.documentElement.classList.toggle("theme-dark", dark);
  // 同步顶部浏览器 UI（桌面无意义，PWA/移动端状态栏）。
  // 用页面顶部背景色而非品牌强调色：iOS 用 theme-color 填充状态栏/安全区，
  // 若填强调蓝会出现一条与页面不符的蓝色条（详见 PWA 顶部蓝条问题）。
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#0f1115" : "#f5f5f7");
  const statusBar = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
  if (statusBar) statusBar.setAttribute("content", dark ? "black-translucent" : "default");
  // 同步 manifest 链接：部分安卓 PWA 独立窗口只认 manifest 静态 theme_color
  const manifestLink = document.getElementById("manifest");
  if (manifestLink) manifestLink.setAttribute("href", dark ? "/manifest-dark.webmanifest?v=3" : "/manifest.webmanifest?v=3");
  // 品牌符号（登录页 + topbar + 侧边栏）用融合版，深浅各一
  const logo = document.querySelector(".topbar-logo");
  if (logo) logo.src = dark ? "/logo-mark-dark.svg" : "/logo-mark.svg";
  const sidebarLogo = document.querySelector("#sidebar-logo");
  if (sidebarLogo) sidebarLogo.src = dark ? "/logo-mark-dark.svg" : "/logo-mark.svg";
  const loginLogo = document.querySelector("#login-logo");
  if (loginLogo) loginLogo.src = dark ? "/logo-mark-dark.svg" : "/logo-mark.svg";
  const favicon = document.getElementById("favicon");
  if (favicon) favicon.setAttribute("href", dark ? "/logo-mark-dark.svg" : "/logo-mark.svg");
  updateThemeToggleIcon();
  return dark;
}

function setTheme(mode) {
  if (!["light", "dark", "auto"].includes(mode)) mode = "auto";
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch { /* localStorage 不可用则只影响当前页 */ }
  applyTheme();
  renderThemeSwitcher();
}

function themeIconFor(mode) {
  return { light: THEME_SUN_ICON, dark: THEME_MOON_ICON, auto: THEME_AUTO_ICON }[mode] || THEME_AUTO_ICON;
}

function themeLabelFor(mode) {
  return { light: "浅色", dark: "深色", auto: "跟随系统" }[mode] || "跟随系统";
}

function renderThemeSwitcher() {
  const el = $("#theme-switcher");
  if (!el) return;
  const mode = themeMode();
  el.innerHTML = ["light", "dark", "auto"].map((m) => `
    <button class="theme-mode ${mode === m ? "selected" : ""}" data-mode="${m}" title="${themeLabelFor(m)}" aria-label="${themeLabelFor(m)}" aria-pressed="${mode === m}" onclick="setTheme('${m}')">${themeIconFor(m)}</button>`).join("");
}

function updateThemeToggleIcon() {
  const icon = themeIconFor(themeMode());
  document.querySelectorAll(".theme-toggle-btn").forEach((btn) => {
    btn.innerHTML = icon;
  });
}

function cycleTheme() {
  // 移动端顶部按钮：light → dark → auto 循环切换
  const order = ["light", "dark", "auto"];
  const next = order[(order.indexOf(themeMode()) + 1) % order.length];
  setTheme(next);
  updateThemeToggleIcon();
}

// 系统主题变化时，auto 模式跟随；手动模式不打扰
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (themeMode() === "auto") {
      applyTheme();
      renderThemeSwitcher();
      updateThemeToggleIcon();
    }
  });
}

// ---------- 路由 ----------
let routeRenderSeq = 0; // 每次路由切换递增；异步渲染完成后凭此丢弃过期响应
const SPA_PREFIXES = new Set([
  "timeline", "home", "combinations", "mysubs", "settings", "news",
  "search", "kol", "more", "admin", "zsxq", "ima-documents", "knowledge", "ticker",
]);

function routeStillActive(seq) {
  // 令牌必须是整数且等于当前路由序号；局部刷新必须在发起请求前捕获 routeRenderSeq 并回传
  return Number.isInteger(seq) && seq === routeRenderSeq;
}

function sessionOwnerStillActive(routeSeq, token, sessionGeneration) {
  return routeStillActive(routeSeq) && token === state.token
    && sessionGeneration === imaMountState.sessionGeneration;
}

function normalizeRoute(path) {
  const raw = String(path || "").replace(/^#\/?/, "").replace(/^\/+/, "");
  const [pathname, query] = raw.split("?");
  const clean = pathname || "timeline";
  return query ? `/${clean}?${query}` : `/${clean}`;
}

function routePath() {
  return location.pathname.replace(/^\/+/, "") || "timeline";
}

function routeQuery() {
  return new URLSearchParams(location.search);
}

function isRoute(prefix) {
  const path = routePath();
  return prefix.endsWith("/") ? path.startsWith(prefix) : path === prefix || path.startsWith(prefix + "/");
}

function isSpaPath(pathname) {
  const first = String(pathname || "").replace(/^\/+/, "").split("/")[0] || "";
  return SPA_PREFIXES.has(first);
}

function go(path) {
  const url = normalizeRoute(path);
  if (location.pathname + location.search !== url) history.pushState(null, "", url);
  router();
}

function findExportablePost(id) {
  const nid = Number(id);
  return _tlPosts.find((p) => Number(p.id) === nid) || _kolPosts.find((p) => Number(p.id) === nid) || null;
}

const { exportPostCard } = createPostCardExport({
  findPost: findExportablePost,
  isShowSrc: (id) => _tlShowSrc.has(id),
  flash,
});

const {
  clearNewsFilters,
  clearNewsReaderState,
  loadFinancialNews,
  markAllNewsRead,
  markNewsItemRead,
  openNewsArticle,
  openNewsSourcePicker,
  queueNewsSearch,
  renderFinancialNewsArticle,
  renderFinancialNewsList,
  renderNewsCenter,
  saveNewsSources,
  selectNewsSource,
  selectNewsTopic,
  setNewsFontSize,
  toggleNewsSearch,
  toggleNewsUnreadOnly,
  undoNewsReadAll,
} = createNewsView({
  $,
  state,
  api,
  apiBlob,
  routeStillActive,
  currentRouteSeq: () => routeRenderSeq,
  setPageTitle,
  emptyState,
  go,
  flash,
  escapeHtml,
  trapFocus,
  fmtPublished,
  externalLinkIcon: EXTERNAL_LINK_ICON,
  renderSidebar: () => renderSidebar(state.user),
  renderBottomNav: () => renderBottomNav(state.user),
  updateNewsBadge,
  SEARCH_ICON,
  GEAR_ICON,
  NEWS_ICON,
  EYE_ICON,
  CHEVRON_DOWN_ICON,
  CHECK_ICON,
  CHECK_CHECK_ICON,
});

const {
  clearImaPdfUrl,
  openImaDocument,
  prefetchKnowledge,
  renderTickerPage,
  renderKnowledge,
  renderImaDocuments,
  refreshKnowledge,
  subscribeKnowledge,
  unsubscribeKnowledge,
  backFromImaReader,
  openImaPdfNewTab,
  downloadImaPdf,
  loadImaDocumentsMore,
  clearImaDocumentsFilter,
  clearImaDocumentsFilters,
  queueImaDocumentsSearch,
  refreshImaDocuments,
  selectImaDocumentGroup,
  selectImaDocumentsTag,
  submitImaDocumentsSearch,
  toggleImaAbstract,
  toggleImaDayPicker,
  toggleImaTagMenu,
  restoreImaListSnapshot,
  stopImaDocumentsAutoLoad,
  fmtImaDay,
  pickImaDay,
  pickImaTag,
  imaDocumentReaderRoute,
  replaceImaDocumentsRoute,
} = createImaView({
  $,
  state,
  api,
  apiBlob,
  flash,
  escapeHtml,
  emptyState,
  go,
  isRoute,
  isStandalonePwa,
  normalizeRoute,
  routePath,
  routeQuery,
  routeStillActive,
  sessionOwnerStillActive,
  currentRouteSeq: () => routeRenderSeq,
  bumpRouteSeq: () => ++routeRenderSeq,
  currentImaReaderSeq: () => _imaReaderSeq,
  bumpImaReaderSeq: () => ++_imaReaderSeq,
  setPageTitle,
  imaMountState,
  fmtCacheBytes,
  loadFeishuTimeline,
  feishuSourceDisplay,
  feishuSourcePillsHtml,
  resetFeishuTimelineMedia,
  userKeywordSet,
  isReportWatchableTag,
  SEARCH_ICON,
  REFRESH_ICON,
  X_ICON,
  COPY_ICON,
  EXTERNAL_LINK_ICON,
  CHEVRON_LEFT_ICON,
  CHEVRON_RIGHT_ICON,
  CHEVRON_DOWN_ICON,
});


let pushSettingsView;
const feishuPersonalView = createFeishuPersonalView({
  $,
  state,
  api,
  currentRouteSeq: () => routeRenderSeq,
  routeStillActive,
  sessionOwnerStillActive,
  flash,
  escapeHtml,
  imaMountState,
  feishuChannelBound: (user) => pushSettingsView.feishuChannelBound(user),
  startSettingsPoll: () => pushSettingsView.startSettingsPoll(),
  reloadSettings: (routeSeq) => pushSettingsView.reloadSettings(routeSeq),
  switchSettingsTab: (name) => pushSettingsView.switchSettingsTab(name),
  userKeywordSet,
  isReportWatchableTag,
  KEYWORDS_MAX_COUNT,
  CHANNEL_ICONS,
});
const {
  fsPersonalState,
  stopFeishuPersonalPoll,
  feishuPersonalHtml,
  pushChannelsHtml,
  webPushSupported,
  renderBindResult,
  startFeishuPersonal,
  cancelFeishuPersonal,
  refreshFeishuBindCode,
  openBindGuide,
  savePushChannels,
  saveNotify,
  saveDailyReport,
  saveTranslateTwitter,
  toggleDnd,
  saveDnd,
  saveCustomTgBot,
  unbindChannel,
  saveWecomWebhook,
  saveBarkKey,
  enableWebPush,
  disableWebPush,
  saveKeywords,
  saveKeywordsMatchNews,
  saveKeywordsMatchReports,
  toggleReportKeyword,
  saveLlm,
  testLlm,
  loadLlmModels,
  savePassword,
  genBindCode,
} = feishuPersonalView;

pushSettingsView = createPushSettingsView({
  $,
  state,
  api,
  currentRouteSeq: () => routeRenderSeq,
  routeStillActive,
  imaMountState,
  flash,
  escapeHtml,
  emptyState,
  go,
  isRoute,
  setPageTitle,
  CHANNEL_ICONS,
  PLATFORM_LABELS,
  SEARCH_ICON,
  avatarHtml,
  feishuPersonalView,
  feishuPersonalHtml,
  pushChannelsHtml,
  webPushSupported,
  toggleDnd,
  CHEVRON_RIGHT_ICON,
});
const {
  stopSettingsPoll,
  startSettingsPoll,
  reloadSettings,
  feishuChannelBound,
  renderSettings,
  switchSettingsTab,
  filterKolImageSettings,
  toggleKolImages,
  loadKolImageSettings,
} = pushSettingsView;

// admin 视图懒加载：codes 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let codesView, loadAdminCodes, adminCodesBatch, adminCodesClearSelect, adminCodesCopySelected, adminCodesNoteInput, adminCodesPreset, adminCodesToggle,
  adminCodesToggleBatch, adminCodesTogglePage, adminGenerateCodes, adminRevokeBatch, adminRevokeCode, searchAdminCodes, selectAdminCodeFilter,
  clearAdminCodesResult, copyText;

// admin 视图懒加载：news 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let newsView, loadAdminNews, loadAdminPosts, selectAdminNewsSource, saveAdminNewsSettings, refreshAllAdminNews, refreshAdminNewsFeed, toggleAdminNewsSource,
  toggleAdminNewsFeed, archiveAdminNewsSource, restoreAdminNewsSource, deleteAdminNewsSource, archiveAdminNewsFeed, restoreAdminNewsFeed, deleteAdminNewsFeed, loadAdminNewsArticles, deleteAdminNewsArticle, openNewsSourceModal, openNewsFeedModal,
  updateAdminNewsQuery, updateAdminNewsStatus, updateAdminNewsArchived, adminFilterPosts, adminPostsLoadMore, adminTogglePost;

// admin 视图懒加载：users 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let usersView, loadAdminUsers, loadAdminRequests, adminApproveRequest, adminRejectRequest, adminUsersApplyFilter, adminUsersBatch, adminUserToggleSelect,
  adminUserTogglePage, adminUserClearSelect, adminOpenUser, adminSaveUserKnowledge, adminSaveUsername, adminSavePassword, adminSendTestPush, adminDeleteUser,
  adminToggleAdmin, adminSaveInactivePolicy, adminInactivePolicySyncSave, adminInactivePolicyKeydown, closeAdminModal;

// admin 视图懒加载：kol 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let kolView, loadAdminKols, loadAdminVocab, switchAdminKolsPlatform, adminKolsApplyFilter, adminKolsClearFilter, adminKolsPage, adminKolToggleSelect,
  adminKolTogglePage, adminKolClearSelect, adminKolBatch, adminKolBatchCategory, adminBatchAddKols, adminBatchLinesHint, adminToggleKol, adminTogglePriority,
  adminToggleSecondary, adminDeleteKol, adminEditKol, saveKolEdit, adminAddCategory, adminRenameCategory, adminDeleteCategory, adminSaveStockNames,
  adminSaveTags, adminMaintainTags, adminBackfillTags;

// admin 视图懒加载：infra 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let infraView, loadStorageHealth, refreshImaStorage, loadProxyAdmin,
  syncProxyRouteInputs, syncProxyPoolForm, saveProxyRoutes, createProxyPool, importProxyPool, extractProxyPool, deleteProxyPool, deleteProxyNode, testProxyNode,
  loadAdminBackup, saveBackupWebDAV, testBackupWebDAV, backupDownload, backupRestoreWebDAV, backupRestoreUpload;

// admin 视图懒加载：ima 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let imaCollectorView, initImaMountState, imaCollectorHasUnsaved, renderImaMountGroups, renderImaSelectedGroup, renderImaFolderTree, renderImaGroupAcl,
  renderImaCollectorDirtyState, discardImaCollectorChanges, selectImaMountGroup, setImaGroupInterval, toggleImaFolderPanel, toggleImaFolderExpand,
  toggleImaFolder, retryImaFolderLoad, discoverImaGroups, filterAclSuggest, onAclSearchKey, toggleImaAclExpanded, retryImaGroupAcl, saveImaCollector,
  triggerImaCollector, saveImaCredentials, stopImaProgressPoll, applyImaCollectorProgress, startImaProgressPoll, imaCollectorStatusText,
  imaGroupDiscoveryStatusText, imaCollectorProgressHtml, imaMountGroup, imaGroupIntervalSeconds, fetchAclCandidateUsers, aclPickerHtml, addAclUser,
  removeAclUser, imaCollectorFormSnapshot, imaCollectorFormRevision, rememberImaCollectorDraft, clearImaCollectorDraft, restoreImaCollectorOwnerToken,
  imaSafeError;


// admin 视图懒加载：dashboard 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let dashboardView, loadAdminStats, loadAdminDashboard, loadAdminAudit, loadAdminLogs, loadAdminErrorLogs, loadAdminSysLogsPanel, adminFilterLogs,
  stopSysLogsTimer, renderStatsData, openAdminKolFromHealth, setPlazaSourceMode;

// admin 视图懒加载：knowledge 由 ensureAdminViews() 赋值，求值期读到的是 undefined
let knowledgeView, loadAdminKnowledge, switchKnowledgeSettingsTab, onKnowledgeTabsKey;

// ---------- admin 视图懒加载 ----------
// 9 个 admin 视图工厂不再静态 import，只在真正进入 admin 路由时 import()，
// 首屏因此少下载约 73KB gz（研报中心等页面不再付这份钱）。
// 上面各视图名与 options / 解构块整块搬进本函数，保持原缩进不改动，
// 以便 diff 只剩真正新增的行；同步调用点需自己守卫（clearSessionCaches /
// router 序章 / feishuSourceRowsHtml）。
let adminViewsLoaded = false;
let adminViewsPromise = null;
let adminViewsRetry = 0;

async function ensureAdminViews() {
  if (adminViewsLoaded) return; // 并发去重：多个 await 同时进来只跑一次
  if (adminViewsPromise) return adminViewsPromise;
  const retry = adminViewsRetry ? `?retry=${adminViewsRetry}` : "";
  adminViewsPromise = (async () => {
    const [modCicc, modCodes, modNews, modUsers, modKol, modInfra, modIma, modDashboard, modKnowledge] = await Promise.all([
      import(`./views/admin/cicc.js${retry}`),
      import(`./views/admin/codes.js${retry}`),
      import(`./views/admin/news.js${retry}`),
      import(`./views/admin/users.js${retry}`),
      import(`./views/admin/kol.js${retry}`),
      import(`./views/admin/infra.js${retry}`),
      import(`./views/admin/ima-collector.js${retry}`),
      import(`./views/admin/dashboard.js${retry}`),
      import(`./views/admin/knowledge.js${retry}`),
    ]);

    ciccView = modCicc.createCiccView({
  api, flash, fmtTs, currentRouteSeq: () => routeRenderSeq, routeStillActive, renderLocalTab,
    });
    ({
  loadCiccStatus, startCiccPoll, stopCiccPoll,
  saveCiccCategories, triggerCicc, saveCiccScheduleTime, toggleCiccSchedule,
    } = ciccView);

    ({
  loadAdminCodes,
  adminCodesBatch,
  adminCodesClearSelect,
  adminCodesCopySelected,
  adminCodesNoteInput,
  adminCodesPreset,
  adminCodesToggle,
  adminCodesToggleBatch,
  adminCodesTogglePage,
  adminGenerateCodes,
  adminRevokeBatch,
  adminRevokeCode,
  searchAdminCodes,
  selectAdminCodeFilter,
  clearAdminCodesResult,
  copyText,
    } = (codesView = modCodes.createAdminCodesView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  jsString,
  routeStillActive,
  SEARCH_ICON,
  fmtDbTime,
  parseDbUtcMs,
  currentAdminSeq: () => _adminRenderSeq,
    })));

    ({
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
  deleteAdminNewsFeed,
  loadAdminNewsArticles,
  deleteAdminNewsArticle,
  openNewsSourceModal,
  openNewsFeedModal,
  updateAdminNewsQuery,
  updateAdminNewsStatus,
  updateAdminNewsArchived,
  adminFilterPosts,
  adminPostsLoadMore,
  adminTogglePost,
    } = (newsView = modNews.createAdminNewsView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  routeStillActive,
  currentRouteSeq: () => routeRenderSeq,
  currentAdminSeq: () => _adminRenderSeq,
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
    })));

    ({
  loadAdminUsers,
  loadAdminRequests,
  adminApproveRequest,
  adminRejectRequest,
  adminUsersApplyFilter,
  adminUsersBatch,
  adminUserToggleSelect,
  adminUserTogglePage,
  adminUserClearSelect,
  adminOpenUser,
  adminSaveUserKnowledge,
  adminSaveUsername,
  adminSavePassword,
  adminSendTestPush,
  adminDeleteUser,
  adminToggleAdmin,
  adminSaveInactivePolicy,
  adminInactivePolicySyncSave,
  adminInactivePolicyKeydown,
  closeAdminModal,
    } = (usersView = modUsers.createAdminUsersView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  routeStillActive,
  currentAdminSeq: () => _adminRenderSeq,
  emptyState,
  SEARCH_ICON,
  fmtDbTime,
  trapFocus,
  renderSidebar,
  renderTopbar,
  USER_CHANNEL_KEYS,
  CHANNEL_LABELS,
  CHANNEL_ICONS,
  PLATFORM_LABELS,
  usernameRuleError,
  CHEVRON_RIGHT_ICON,
    })));

    ({
  loadAdminKols,
  loadAdminVocab,
  switchAdminKolsPlatform,
  adminKolsApplyFilter,
  adminKolsClearFilter,
  adminKolsPage,
  adminKolToggleSelect,
  adminKolTogglePage,
  adminKolClearSelect,
  adminKolBatch,
  adminKolBatchCategory,
  adminBatchAddKols,
  adminBatchLinesHint,
  adminToggleKol,
  adminTogglePriority,
  adminToggleSecondary,
  adminDeleteKol,
  adminEditKol,
  saveKolEdit,
  adminAddCategory,
  adminRenameCategory,
  adminDeleteCategory,
  adminSaveStockNames,
  adminSaveTags,
  adminMaintainTags,
  adminBackfillTags,
    } = (kolView = modKol.createAdminKolsView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  routeStillActive,
  currentAdminSeq: () => _adminRenderSeq,
  emptyState,
  trapFocus,
  PLATFORM_LABELS,
  PLATFORM_TABS,
  platformTabHTML,
  routeQuery,
  CHEVRON_LEFT_ICON,
  CHEVRON_RIGHT_ICON,
    })));

    ({
  loadStorageHealth,
  refreshImaStorage,
  loadProxyAdmin,
  syncProxyRouteInputs,
  syncProxyPoolForm,
  saveProxyRoutes,
  createProxyPool,
  importProxyPool,
  extractProxyPool,
  deleteProxyPool,
  deleteProxyNode,
  testProxyNode,
  loadAdminBackup,
  saveBackupWebDAV,
  testBackupWebDAV,
  backupDownload,
  backupRestoreWebDAV,
  backupRestoreUpload,
    } = (infraView = modInfra.createAdminInfraView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  routeStillActive,
  currentRouteSeq: () => routeRenderSeq,
  currentAdminSeq: () => _adminRenderSeq,
  sessionOwnerStillActive,
  imaMountState,
  fmtTs,
  imaStoragePanelHtml,
  logout,
  PLATFORM_LABELS,
    })));

    ({
  initImaMountState,
  imaCollectorHasUnsaved,
  renderImaMountGroups,
  renderImaSelectedGroup,
  renderImaFolderTree,
  renderImaGroupAcl,
  renderImaCollectorDirtyState,
  discardImaCollectorChanges,
  selectImaMountGroup,
  setImaGroupInterval,
  toggleImaFolderPanel,
  toggleImaFolderExpand,
  toggleImaFolder,
  retryImaFolderLoad,
  discoverImaGroups,
  filterAclSuggest,
  onAclSearchKey,
  toggleImaAclExpanded,
  retryImaGroupAcl,
  saveImaCollector,
  triggerImaCollector,
  saveImaCredentials,
  stopImaProgressPoll,
  applyImaCollectorProgress,
  startImaProgressPoll,
  imaCollectorStatusText,
  imaGroupDiscoveryStatusText,
  imaCollectorProgressHtml,
  imaMountGroup,
  imaGroupIntervalSeconds,
  fetchAclCandidateUsers,
  aclPickerHtml,
  addAclUser,
  removeAclUser,
  imaCollectorFormSnapshot,
  imaCollectorFormRevision,
  rememberImaCollectorDraft,
  clearImaCollectorDraft,
  restoreImaCollectorOwnerToken,
  imaSafeError,
    } = (imaCollectorView = modIma.createAdminImaCollectorView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  emptyState,
  routeStillActive,
  sessionOwnerStillActive,
  currentRouteSeq: () => routeRenderSeq,
  bumpRouteSeq: () => ++routeRenderSeq,
  currentAdminSeq: () => _adminRenderSeq,
  FOLDER_ICON,
  CHEVRON_RIGHT_ICON,
  CHEVRON_DOWN_ICON,
  X_ICON,
  imaMountState,
  imaCollectorPureCache,
  reloadAdminSettingsPage,
  isAdminSettingsPath,
  currentLocalLibraries: () => _localLibsLast,
  focusCookieField,
    })));

    ({
  loadAdminStats,
  loadAdminDashboard,
  loadAdminAudit,
  loadAdminLogs,
  loadAdminErrorLogs,
  loadAdminSysLogsPanel,
  adminFilterLogs,
  stopSysLogsTimer,
  renderStatsData,
  openAdminKolFromHealth,
  setPlazaSourceMode,
    } = (dashboardView = modDashboard.createAdminDashboardView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  routeStillActive,
  currentAdminSeq: () => _adminRenderSeq,
  emptyState,
  statsTabsHtml,
  switchStatsTab,
  statsTabFromHash,
  startDashboardLiveTimer,
  stopStatsTimer,
  fmtTs,
  fmtDbTime,
  parseDbUtcMs,
  PLATFORM_LABELS,
  CHANNEL_LABELS,
  imaMountState,
  sessionOwnerStillActive,
  imaCollectorStatusText,
  imaGroupDiscoveryStatusText,
  go,
  nextStatsLoadSeq: () => ++_adminStatsLoadSeq,
  currentStatsLoadSeq: () => _adminStatsLoadSeq,
  getStatsSnapshot: () => _lastAdminStatsSnapshot,
  setStatsSnapshot: (s) => { _lastAdminStatsSnapshot = s; },
  cookieRepairItems,
  cookieRepairBanner,
  cookieUpdatedLabel,
  imgbedStatusLabel,
  setPageTitle,
  STALE_KOL_HOURS,
  STALE_KOL_LIMIT,
  PLATFORM_ICONS,
  fmtCacheBytes,
  loadAdminNews,
  replaceRoute,
  imaCollectorFormSnapshot,
  rememberImaCollectorDraft,
  imaCollectorFormRevision,
  rateBar,
  currentRouteSeq: () => routeRenderSeq,
    })));

    ({
  loadAdminKnowledge,
  switchKnowledgeSettingsTab,
  onKnowledgeTabsKey,
    } = (knowledgeView = modKnowledge.createAdminKnowledgeView({
  $,
  state,
  api,
  flash,
  escapeHtml,
  emptyState,
  routeStillActive,
  currentRouteSeq: () => routeRenderSeq,
  currentAdminSeq: () => _adminRenderSeq,
  routeQuery,
  REFRESH_ICON,
  CHEVRON_RIGHT_ICON,
  setPageTitle,
  imaMountState,
  imaCollectorPureCache,
  imaStoragePanelHtml,
  fmtCacheBytes,
  imaCollectorHasUnsaved,
  imaCollectorFormSnapshot,
  rememberImaCollectorDraft,
  initImaMountState,
  renderImaMountGroups,
  renderImaGroupAcl,
  imaGroupIntervalSeconds,
  imaMountGroup,
  restoreImaCollectorOwnerToken,
  clearImaCollectorDraft,
  startImaProgressPoll,
  stopImaProgressPoll,
  applyImaCollectorProgress,
  imaCollectorFormRevision,
  imaCollectorStatusText,
  imaGroupDiscoveryStatusText,
  imaCollectorProgressHtml,
  cookieUpdatedLabel,
  stopStatsTimer,
  renderStatsData,
  startDashboardLiveTimer,
  loadLocalLibraries,
  loadCiccStatus,
  startCiccPoll,
  stopCiccPoll,
  loadStorageHealth,
  loadFeishuDocumentSources,
  nextStatsLoadSeq: () => ++_adminStatsLoadSeq,
  currentStatsLoadSeq: () => _adminStatsLoadSeq,
  getStatsSnapshot: () => _lastAdminStatsSnapshot,
  setStatsSnapshot: (s) => { _lastAdminStatsSnapshot = s; },
    })));

    // 工厂返回值含未解构的内部方法（如 ciccView.reset），不能整体 spread 进
    // INLINE_HANDLERS，否则会给 window 多挂一批名字；按 INLINE_HANDLERS 已有的键回填。
    const lazyViews = { ...ciccView, ...codesView, ...newsView, ...usersView, ...kolView, ...infraView, ...imaCollectorView, ...dashboardView, ...knowledgeView };
    for (const name of Object.keys(INLINE_HANDLERS)) {
      if (typeof lazyViews[name] === "function") INLINE_HANDLERS[name] = lazyViews[name];
    }
    adminViewsLoaded = true;
  })();
  try {
    await adminViewsPromise;
  } catch (error) {
    adminViewsPromise = null;
    adminViewsRetry += 1;
    throw error;
  }
}



function replaceRoute(path) {
  const url = normalizeRoute(path);
  if (location.pathname + location.search !== url) history.replaceState(null, "", url);
  router();
}

function migrateHashRoute() {
  const hash = location.hash;
  if (!hash || hash === "#" || hash === "#main") return;
  if (!hash.startsWith("#/")) return;
  history.replaceState(null, "", normalizeRoute(hash));
}

async function router() {
  stopMarketQuotes();
  // admin 视图懒加载：这三个 stop 来自 admin 模块，未加载时没有在跑的轮询要停
  if (adminViewsLoaded) {
    stopCiccPoll();
    stopSysLogsTimer();
    stopImaProgressPoll();
  }
  const renderSeq = ++routeRenderSeq;
  const token = state.token;
  const sessionGeneration = imaMountState.sessionGeneration;
  stopSettingsPoll();
  stopStatsTimer();
  stopTimelinePoll();
  // 离开动态页前记录滚动位置，切回时恢复阅读位置
  if (document.querySelector("#feed")) {
    if (isLiveTimeline()) _liveSavedScrollY = window.scrollY;
    else _tlSavedScrollY = window.scrollY;
  }
  const path = routePath();
  const [page, rawParam] = path.split("/");
  if (page !== "news") clearNewsReaderState();
  if (page !== "settings") state.settingsTab = "push";
  // 管理后台默认内容管理：/admin 与 /admin/content 等价，侧边栏高亮才能对上
  const param = page === "admin" && !rawParam ? "content" : rawParam;
  if (!state.token) {
    $("#app-view").classList.add("hidden");
    $("#auth-view").classList.remove("hidden");
    initTurnstile();
    return;
  }
  const knowledgePrefetch = page === "knowledge" || page === "ima-documents"
    ? prefetchKnowledge(rawParam)
    : null;
  let user;
  try {
    user = await api("/api/me");
  } catch {
    if (!sessionOwnerStillActive(renderSeq, token, sessionGeneration)) return;
    return;
  }
  if (!sessionOwnerStillActive(renderSeq, token, sessionGeneration)) return;
  $("#app-view").classList.remove("hidden");
  $("#auth-view").classList.add("hidden");
  state.user = user;
  state.newsVisible = user.news_visible !== false;
  if (page === "news" && !state.newsVisible) {
    replaceRoute("timeline");
    return;
  }
  renderSidebar(state.user);
  renderTopbar(state.user);
  renderBottomNav(state.user);
  if (!knowledgePrefetch) prefetchLiveFeed();
  const navPage = page === "ima-documents" || page === "ticker" ? "knowledge" : page;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.route === navPage || b.dataset.route === `${navPage}/${param}`)
  );
  // 底部栏高亮：管理员进后台页时高亮「更多」
  const activeBottom = navPage === "admin" ? "more" : navPage;
  document.querySelectorAll(".bnav-item").forEach((b) => {
    const active = b.dataset.route === activeBottom;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  try {
    if (page === "home") await renderHome(renderSeq);
    else if (page === "combinations") {
      state.platform = "combination";
      replaceRoute("home");
      return;
    }
    else if (page === "mysubs") {
      state.homeSubscribed = true;
      replaceRoute("home");
      return;
    }
    else if (page === "news") await renderNewsCenter(renderSeq, rawParam);
    else if (page === "zsxq") {
      state.timelinePlatform = timelineVisibleSet().has("zsxq") ? "zsxq" : "";
      replaceRoute("timeline");
      return;
    }
    else if (page === "timeline") await renderTimeline(renderSeq);
    else if (page === "settings") await renderSettings(renderSeq);
    else if (page === "more") await renderMore(renderSeq);
    else if (page === "search") await renderSearch(renderSeq);
    else if (page === "kol") await renderKolPage(Number(param), renderSeq);
    else if (page === "ima-documents") {
      const next = `${location.pathname.replace(/^\/ima-documents\b/, "/knowledge")}${location.search}`;
      if (location.pathname + location.search !== next) history.replaceState(null, "", next);
      await renderKnowledge(renderSeq, param, knowledgePrefetch);
    }
    else if (page === "knowledge") await renderKnowledge(renderSeq, param, knowledgePrefetch);
    else if (page === "ticker") await renderTickerPage(renderSeq, param);
    else if (page === "admin") {
      if (!state.user.is_admin) { replaceRoute("timeline"); return; }
      // 后台条目页签化：旧路由（含更早的 categories/tags）统一重定向到容器页签
      if (ADMIN_ROUTE_REDIRECTS[param]) {
        const q = routeQuery();
        const extra = new URLSearchParams();
        q.forEach((v, k) => { if (k !== "tab") extra.append(k, v); });
        if (param === "vocab" && q.get("tab") === "tags") extra.append("vtab", "tags");
        const qs = extra.toString();
        replaceRoute("admin/" + ADMIN_ROUTE_REDIRECTS[param] + (qs ? "&" + qs : ""));
        return;
      }
      await ensureAdminViews();
      await renderAdmin(param || "content", renderSeq);
    }
    else { replaceRoute("timeline"); return; }
  } catch (err) {
    // 只在当前路由仍是本次渲染目标时才写错误状态，避免旧路由的错误覆盖新页面
    if (routeStillActive(renderSeq)) $("#main").innerHTML = emptyState(err.message);
  } finally {
    if (routeStillActive(renderSeq)) resetBottomNavScroll();
  }
}

// ---------- 认证 ----------
function togglePassword(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.classList.toggle("visible", show);
  btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
  btn.setAttribute("aria-pressed", String(show));
}

let turnstileSitekey = "";
let turnstileScriptPromise = null;
const turnstileWidgets = {};

function renderTurnstile(elId, action) {
  if (!turnstileSitekey || !window.turnstile) return;
  const el = document.getElementById(elId);
  if (!el) return;
  if (turnstileWidgets[elId]) {
    try { window.turnstile.remove(turnstileWidgets[elId]); } catch { /* already gone */ }
  }
  turnstileWidgets[elId] = window.turnstile.render(el, {
    sitekey: turnstileSitekey,
    action,
    theme: "auto",
    size: "normal",
    appearance: "always",
  });
}

function resetTurnstile(elId) {
  const id = turnstileWidgets[elId];
  if (id && window.turnstile) {
    try { window.turnstile.reset(id); } catch { /* ignore */ }
  }
}

function turnstileToken(elId) {
  const id = turnstileWidgets[elId];
  return (id && window.turnstile && window.turnstile.getResponse(id)) || "";
}

async function ensureTurnstile() {
  if (!turnstileSitekey) {
    try {
      const data = await api("/api/auth/turnstile");
      turnstileSitekey = data.sitekey || "";
    } catch {
      return;
    }
  }
  if (!turnstileSitekey) return;
  if (!turnstileScriptPromise) {
    turnstileScriptPromise = new Promise((resolve, reject) => {
      if (window.turnstile) { resolve(); return; }
      const s = document.createElement("script");
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.async = true;
      s.defer = true;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  try {
    await turnstileScriptPromise;
  } catch {
    return;
  }
  renderTurnstile("login-turnstile", "login");
  renderTurnstile("register-turnstile", "register");
}

function initTurnstile() {
  void ensureTurnstile();
}

async function doLogin(e) {
  e.preventDefault();
  $("#auth-error").textContent = "";
  const btn = $("#login-form").querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = "登录中…";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#login-username").value.trim(),
        password: $("#login-password").value,
        "cf-turnstile-response": turnstileToken("login-turnstile"),
      }),
    });
    state.token = data.token;
    localStorage.setItem("dav_token", data.token);
    go("timeline");
  } catch (err) {
    $("#auth-error").textContent = err.message;
    btn.disabled = false;
    btn.textContent = "登 录";
    resetTurnstile("login-turnstile");
  }
}

async function doRegister(e) {
  e.preventDefault();
  $("#reg-error").textContent = "";
  const username = $("#reg-username").value.trim();
  const ruleErr = usernameRuleError(username);
  if (ruleErr) {
    $("#reg-error").textContent = ruleErr;
    return;
  }
  const btn = $("#register-form").querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = "创建中…";
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username,
        password: $("#reg-password").value,
        code: $("#reg-code").value.trim(),
        "cf-turnstile-response": turnstileToken("register-turnstile"),
      }),
    });
    state.token = data.token;
    localStorage.setItem("dav_token", data.token);
    go("timeline");
  } catch (err) {
    $("#reg-error").textContent = err.message;
    btn.disabled = false;
    btn.textContent = "创建账号";
    resetTurnstile("register-turnstile");
  }
}

function switchAuthMode(mode) {
  const isLogin = mode === "login";
  const loginForm = $("#login-form");
  const registerForm = $("#register-form");
  loginForm.classList.toggle("hidden", !isLogin);
  registerForm.classList.toggle("hidden", isLogin);
  loginForm.hidden = !isLogin;
  registerForm.hidden = isLogin;
  $("#auth-error").textContent = "";
  $("#reg-error").textContent = "";
  resetAuthButtons();
  document.querySelectorAll(".switch-btn").forEach((btn) => {
    const on = btn.dataset.mode === mode;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", String(on));
  });
}

// ---------- 事件 ----------
$("#login-form").addEventListener("submit", doLogin);
$("#register-form").addEventListener("submit", doRegister);
document.querySelectorAll(".switch-btn").forEach((btn) =>
  btn.addEventListener("click", () => switchAuthMode(btn.dataset.mode))
);
$("#btn-back").addEventListener("click", () => {
  if (state.pageBackRoute) go(state.pageBackRoute);
  else history.back();
});
// 本地库卡片按钮：slug 来自存储机目录名，用 data 属性委托而非内联 onclick（防 JS 注入）
document.addEventListener("click", (e) => {
  const day = e.target.closest("[data-ima-day]");
  if (day) {
    pickImaDay(day.getAttribute("data-ima-day") || "");
    return;
  }
  const tag = e.target.closest("[data-ima-tag-index]");
  if (tag) {
    pickImaTag(Number(tag.getAttribute("data-ima-tag-index")));
    return;
  }
  const back = e.target.closest("[data-ima-back]");
  if (back) {
    go(back.getAttribute("data-ima-back") || "knowledge");
    return;
  }
  const add = e.target.closest("[data-acl-add]");
  if (add) {
    addAclUser(add.getAttribute("data-acl-add"), add.closest(".ima-acl-picker"));
    return;
  }
  const remove = e.target.closest("[data-acl-remove]");
  if (remove) {
    removeAclUser(remove.getAttribute("data-acl-remove"), remove.closest(".ima-acl-picker"));
    return;
  }
  const edit = e.target.closest("[data-ll-edit]");
  if (edit) {
    openLocalLibraryModal(edit.dataset.llEdit);
    return;
  }
  const toggle = e.target.closest("[data-ll-toggle]");
  if (toggle) toggleLocalLibrary(toggle.dataset.llToggle, toggle.dataset.llEnabled === "true");
  const homePlatform = e.target.closest("#home-mobile-platforms [data-platform]");
  if (homePlatform) {
    homePickMobilePlatform(homePlatform.dataset.platform || "");
    return;
  }
  const timelinePlatform = e.target.closest("#tl-pills [data-platform]");
  if (timelinePlatform) {
    const platform = timelinePlatform.dataset.platform || "";
    if (platform === "live") tlPickSource("live");
    else tlPickPlatform(platform);
    return;
  }
  const removeFilter = e.target.closest("[data-tl-remove-filter]");
  if (removeFilter) {
    tlRemoveFilter(removeFilter.dataset.tlRemoveFilter || "");
    return;
  }
});
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank" || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const url = new URL(a.getAttribute("href"), location.href);
  if (url.origin !== location.origin) return;
  if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
  if (!isSpaPath(url.pathname)) return;
  e.preventDefault();
  go(url.pathname.replace(/^\/+/, "") + url.search);
});
migrateHashRoute();
window.addEventListener("popstate", router);
window.addEventListener("hashchange", () => {
  const hash = location.hash;
  if (!hash.startsWith("#/")) return; // 保留 #main
  migrateHashRoute();
  router();
});

// PWA：注册 Service Worker（HTTP 或私有模式下失败静默，不影响功能）
if ("serviceWorker" in navigator) {
  // 带 APP_VERSION 让注册 URL 随发版变化：CF 按默认规则边缘缓存 /sw.js，
  // 固定 URL 曾导致浏览器一直拿到旧 SW（skipWaiting 永不触发、shell 缓存不换代）。
  navigator.serviceWorker.register(`/sw.js?v=${APP_VERSION}`).catch(() => {});
}

function selectFeishuSource(button) {
  return button.dataset.sourceTarget === "knowledge"
    ? selectImaDocumentGroup(button.dataset.group)
    : selectFeishuTimelineSource(button.dataset.group);
}

function selectPlatformTab(button) {
  return button.dataset.platformTarget === "admin"
    ? switchAdminKolsPlatform(button.dataset.platform)
    : switchPlatform(button.dataset.platform);
}

function reloadTimelineRail() {
  return loadTimelineRail(routeRenderSeq);
}

function reloadTimeline() {
  return loadTimeline(true, routeRenderSeq);
}

function runSearch() {
  return doSearch(routeRenderSeq);
}

function reloadKolImageSettings() {
  return loadKolImageSettings(routeRenderSeq);
}


const INLINE_HANDLERS = {
  addFeishuDocumentSource,
  adminAddCategory,
  adminApproveRequest,
  adminBackfillTags,
  adminBatchAddKols,
  adminBatchLinesHint,
  adminCodesBatch,
  adminCodesClearSelect,
  adminCodesCopySelected,
  adminCodesNoteInput,
  adminCodesPreset,
  adminCodesToggle,
  adminCodesToggleBatch,
  adminCodesTogglePage,
  adminDeleteCategory,
  adminDeleteKol,
  adminDeleteKolFromHome,
  adminDeleteUser,
  adminEditKol,
  adminFilterLogs,
  adminFilterPosts,
  adminGenerateCodes,
  adminInactivePolicyKeydown,
  adminInactivePolicySyncSave,
  adminKolBatch,
  adminKolBatchCategory,
  adminKolClearSelect,
  adminKolTogglePage,
  adminKolToggleSelect,
  adminKolsApplyFilter,
  adminKolsClearFilter,
  adminKolsPage,
  adminMaintainTags,
  adminOpenUser,
  adminPostsLoadMore,
  adminRejectRequest,
  adminRenameCategory,
  adminRevokeBatch,
  adminRevokeCode,
  adminSaveInactivePolicy,
  adminSavePassword,
  adminSaveStockNames,
  adminSaveTags,
  adminSaveUserKnowledge,
  adminSaveUsername,
  adminSendTestPush,
  adminToggleAdmin,
  adminToggleKol,
  adminTogglePost,
  adminTogglePriority,
  adminToggleSecondary,
  adminUserClearSelect,
  adminUserTogglePage,
  adminUserToggleSelect,
  adminUsersApplyFilter,
  adminUsersBatch,
  applyFeishuTimelineUpdate,
  archiveAdminNewsFeed,
  archiveAdminNewsSource,
  authorizeFeishuDocuments,
  backFromImaReader,
  backupDownload,
  backupRestoreUpload,
  backupRestoreWebDAV,
  cancelFeishuPersonal,
  clearAdminCodesResult,
  clearImaDocumentsFilter,
  clearImaDocumentsFilters,
  clearNewsFilters,
  clearSavedCookie,
  closeAdminModal,
  closeLightbox,
  copyText,
  createProxyPool,
  cycleTheme,
  deleteAdminNewsArticle,
  deleteAdminNewsFeed,
  deleteAdminNewsSource,
  deleteProxyNode,
  deleteProxyPool,
  disableWebPush,
  discardImaCollectorChanges,
  discoverImaGroups,
  downloadFeishuTimelineAsset,
  downloadZsxqFile,
  enableWebPush,
  exportPostCard,
  extractProxyPool,
  filterAclSuggest,
  filterKolImageSettings,
  genBindCode,
  go,
  goFromBottomNav,
  homeResetFilters,
  homeSearch,
  homeToggleFilter,
  imgOnError,
  importProxyPool,
  jumpFeishuTimelineDay,
  jumpFeishuTimelineLatest,
  lightboxStep,
  loadAdminDashboard,
  loadAdminErrorLogs,
  loadAdminKnowledge,
  loadAdminNews,
  loadAdminStats,
  loadAdminSysLogsPanel,
  loadCiccStatus,
  loadFeishuDocumentSources,
  loadFinancialNews,
  loadImaDocumentsMore,
  loadLlmModels,
  loadMoreFeishuTimeline,
  loadProxyAdmin,
  logout,
  markAllNewsRead,
  markNewsItemRead,
  onAclSearchKey,
  onAskLinkInput,
  onKnowledgeTabsKey,
  openAdminKolFromHealth,
  openBindGuide,
  openImaDocument,
  openImaPdfNewTab,
  openLightbox,
  openLocalLibraryCreateModal,
  openNewsArticle,
  openNewsFeedModal,
  openNewsSourceModal,
  openNewsSourcePicker,
  pasteCookieField,
  pasteTurnstileSecret,
  pickHomeCategory,
  purgeZsxqCache,
  queueFeishuDocumentPreview,
  queueImaDocumentsSearch,
  queueNewsSearch,
  quickSubscribe,
  railToggleSubscribe,
  refreshAdminNewsFeed,
  refreshAllAdminNews,
  refreshDashboardLive,
  refreshFeishuBindCode,
  refreshImaDocuments,
  refreshImaStorage,
  refreshKnowledge,
  refreshTimeline,
  reloadKolImageSettings,
  reloadTimeline,
  reloadTimelineRail,
  removeFeishuDocumentSource,
  renameFeishuDocumentSource,
  renderFinancialNewsArticle,
  renderFinancialNewsList,
  renderTimeline,
  restoreAdminNewsFeed,
  restoreAdminNewsSource,
  retryImaFolderLoad,
  retryImaGroupAcl,
  runSearch,
  saveAdminNewsSettings,
  saveBackupWebDAV,
  saveBarkKey,
  saveCiccCategories,
  saveCiccScheduleTime,
  saveCustomTgBot,
  saveDailyReport,
  saveDnd,
  saveFeishuDocsConfig,
  saveImaCollector,
  saveImgbedSettings,
  saveTurnstileSettings,
  markTurnstileDirty,
  clearImgbedSettings,
  saveKeywords,
  saveKeywordsMatchNews,
  saveKeywordsMatchReports,
  saveKolEdit,
  saveLlm,
  testLlm,
  saveNewsSources,
  saveNotify,
  savePassword,
  savePollingConfig,
  saveProxyRoutes,
  savePushChannels,
  saveTranslateTwitter,
  saveTwitterCookie,
  saveWecomWebhook,
  saveXueqiuCookie,
  saveZsxqCookie,
  saveZsxqPollingConfig,
  scanLocalLibraries,
  searchAdminCodes,
  selectAdminCodeFilter,
  selectAdminNewsSource,
  selectFeishuSource,
  selectImaDocumentGroup,
  selectImaDocumentsTag,
  selectImaMountGroup,
  selectNewsSource,
  selectNewsTopic,
  selectPlatformTab,
  setFeishuSourceDisplay,
  setImaGroupInterval,
  setNewsFontSize,
  setPlazaSourceMode,
  setSubscribeType,
  setTheme,
  startFeishuPersonal,
  startWeiboQr,
  submitAsk,
  submitImaDocumentsSearch,
  subscribeKnowledge,
  switchKnowledgeSettingsTab,
  switchSettingsTab,
  switchStatsTab,
  syncFeishuDocumentSource,
  syncProxyPoolForm,
  syncProxyRouteInputs,
  testBackupWebDAV,
  testProxyNode,
  tlApplyFilter,
  toggleNewsSearch,
  toggleNewsUnreadOnly,
  tlApplyRailSearch,
  tlFilterPanel,
  tlOnSearchInput,
  tlPickSource,
  tlPickTag,
  tlResetFilters,
  tlToggleOrigin,
  tlTogglePost,
  toggleAdminNewsFeed,
  toggleAdminNewsSource,
  toggleCiccSchedule,
  toggleDnd,
  toggleFavorite,
  toggleFeishuDocumentSource,
  toggleHomeFavorite,
  toggleHomeSubscribed,
  toggleImaAbstract,
  toggleImaAclExpanded,
  toggleImaDayPicker,
  toggleImaFolder,
  toggleImaFolderExpand,
  toggleImaFolderPanel,
  toggleImaTagMenu,
  toggleKolImages,
  toggleKolPageSubscribe,
  toggleLiveImportant,
  togglePassword,
  toggleReportKeyword,
  toggleSecondary,
  toggleSidebarSlim,
  toggleSubscribe,
  toggleTimelineFav,
  toggleTimelineSecondary,
  triggerCicc,
  triggerImaCollector,
  unbindChannel,
  undoNewsReadAll,
  unsubscribeKnowledge,
  updateAdminNewsArchived,
  updateAdminNewsQuery,
  updateAdminNewsStatus,
};
Object.assign(window, INLINE_HANDLERS);
// admin 视图懒加载：INLINE_HANDLERS 里来自 admin 工厂的名字此刻还是 undefined，
// 用 window getter 让内联 onclick 始终读到加载后的真实函数（admin 路由渲染前
// 已经 await ensureAdminViews()，正常流程取到的一直是已就绪的值）。
// 若在视图就绪前就被调用（例如非 admin 页面里被注入的 admin 内联按钮），
// 先补齐视图再执行，而不是拿 undefined 静默炸掉。
for (const name of Object.keys(INLINE_HANDLERS)) {
  Object.defineProperty(window, name, {
    configurable: true,
    get() {
      const handler = INLINE_HANDLERS[name];
      if (typeof handler === "function") return handler;
      return (...args) =>
        ensureAdminViews().then(() => {
          const ready = INLINE_HANDLERS[name];
          return ready(...args);
        });
    },
    // 运行时可覆盖：浏览器测试会把视图方法 Object.assign 回 window
    set(value) { INLINE_HANDLERS[name] = value; },
  });
}

const { startMarketQuotes, stopMarketQuotes } = createMarketView({ api, escapeHtml });

applyTheme(); // 与 index.html 防闪脚本同一逻辑，兜底 + 同步 meta theme-color
router();
