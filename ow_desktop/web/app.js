// 前端逻辑：通过 window.pywebview.api 调用 Python，不走网络请求
const $ = (id) => document.getElementById(id);

// 错误码 → 界面提示。封禁与限流要醒目，不能被当成「查无此人」
const ERROR_HINTS = {
  banned: "查询号被限制访问（封禁）。换 token 无效，需要换一个未被封的账号。",
  rate_limited: "请求太频繁被限流了，请过几分钟再查，不要连续点击。",
  auth_expired: "登录凭据已过期。",
  need_login: "需要先扫码登录。",
  not_found: "没找到该玩家，或对方未在大神公开战绩。",
  network: "网络请求失败，请检查网络或代理。",
};
const ROLE_ICONS = { tank: "shield", dps: "crosshair", healer: "heart", open: "grid" }; // 职责图标
const MODE_CLASS = { 排位: "sport", 开放排位: "sport", 竞技: "sport", 快速: "quick", "6v6快速": "quick" };  // 模式标签配色
// 段位名 → 配色 class
const RANK_CLASS = { 青铜: "r-bronze", 白银: "r-silver", 黄金: "r-gold", 铂金: "r-plat", 翡翠: "r-emerald",
  钻石: "r-diamond", 大师: "r-master", 宗师: "r-gm", 英杰: "r-champ", 冠军: "r-champ" };
const ACCOUNT_NAMES = { query: "查询号", main: "主号" };
const ACCOUNT_KEYS = ["query", "main"];  // 与 bridge.accounts() 返回顺序一致
const FIRST_PAGES = 2;                   // 首次查询连读的对局页数
const MORE_TEXT = "加载更多对局";  // 加载更多按钮文字
const BOTTOM_GAP = 4;                    // 离底部不到这么多像素算「已到底」
const FIND_AUTO_CHECK = 20;              // 找对局：要发请求的候选不超过这个数就自动比对，超过先让用户确认

// 当前查询状态
const cur = {
  tag: "",        // 当前玩家 BattleTag
  token: "",      // 当前玩家 customerToken（翻页、切模式、看详情都用它，免得重复搜索）
  mode: "all",    // 对局模式：all / sport / quick
  season: "",     // 赛季号，空为当前赛季
  page: 0,        // 已读到的最后一页
  hasMore: false, // 是否还能加载更多
};
let loginTimer = null;      // 扫码轮询定时器
let loginAccount = "query"; // 当前扫码的账号
let reportTimer = null;     // 举报提醒心跳定时器
let heartbeatSec = 60;      // 心跳间隔（秒），从设置读取
let toastTimer = null;      // 轻提示自动消失定时器
let library = { followed: [], all: [], hasMore: false };  // 玩家库数据（all 按页累加）
let libraryQuery = "";                   // 玩家库当前列表对应的检索词
let libraryLoading = false;              // 正在加载下一页，防止滚动时重复请求
let librarySeq = 0;                      // 检索请求序号：输入很快时丢弃过期的返回
let libraryTab = "followed";             // 玩家库当前标签页
let libraryTimer = null;                 // 玩家库检索防抖定时器
let moreFailed = false;                  // 上次加载更多失败：滚轮不再自动重试，要手动点按钮
let findRun = 0;                         // 找对局批次号：取消、关窗或重新查找时让旧的比对循环停下
let findOptionsLoaded = false;           // 英雄 / 地图输入提示是否已填好
const openAccounts = new Set();          // 展开着的账号卡片（query / main），重绘后保持
let audioCtx = null;                     // 提示音用的 AudioContext，需在用户点击时创建才能出声
let queryLoading = false;                // 资料或列表查询期间不叠加新请求
let querySeq = 0;                        // 换列表后丢弃旧的分页响应
let loginSeq = 0;                        // 返回或重新扫码后忽略上一轮登录响应
let detailSeq = 0;                       // 关闭或切换对局后不再渲染旧详情
let pendingReportTag = "";              // 查询中收到的新举报，成功结束当前查询后顺序处理
const MODALS = ["find-modal", "modal", "library-modal", "settings-modal", "accounts-modal", "reports-modal"]; // 全部弹窗
const modalStack = [];                   // 按打开顺序管理叠层与键盘焦点
const modalFocus = new Map();            // 关闭面板后恢复到原来的操作位置
const MODAL_NAV = { "library-modal": "btn-library", "settings-modal": "btn-settings", "accounts-modal": "btn-accounts", "reports-modal": "btn-reports" }; // 面板对应导航

function icon(name) { // 引用本地图标，不依赖外部字体
  return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function syncNavigation() { // 反映当前打开的功能面板
  const active = [...modalStack].reverse().find((id) => MODAL_NAV[id]);
  document.querySelectorAll(".nav-item").forEach((button) => {
    const selected = button.id === (MODAL_NAV[active] || "nav-query");
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("view-main").inert = modalStack.length > 0;
}

function openModal(id, focusSelector = "button, input, select") { // 打开面板并移入焦点
  if (!modalStack.includes(id)) {
    modalFocus.set(id, document.activeElement);
    modalStack.push(id);
  }
  $(id).classList.remove("hidden");
  syncNavigation();
  requestAnimationFrame(() => {
    if (modalStack.at(-1) === id) $(id).querySelector(focusSelector)?.focus();
  });
}

function closeModal(id) { // 关闭最上层后回到之前的操作位置
  if ($(id).classList.contains("hidden")) return;
  $(id).classList.add("hidden");
  const index = modalStack.indexOf(id);
  if (index >= 0) modalStack.splice(index, 1);
  if (id === "find-modal") stopFind($("find-go").disabled ? "已取消" : "");
  if (id === "modal") detailSeq += 1;
  syncNavigation();
  const previous = modalFocus.get(id);
  if (previous?.isConnected && previous.getClientRects().length) previous.focus();
  modalFocus.delete(id);
}

function api() { return window.pywebview.api; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function num(v) { const n = Number(v); return Number.isFinite(n) ? n.toLocaleString("zh-CN") : esc(v); }

function showNotice(res) {
  const box = $("notice");
  box.textContent = ERROR_HINTS[res.code] ? `${ERROR_HINTS[res.code]}（${res.message || ""}）` : (res.message || "操作未完成");
  if (["auth_expired", "need_login"].includes(res.code) || (res.code === "network" && cur.tag)) {
    const login = res.code !== "network"; // 过期时重新登录，网络失败只提供手动重试
    box.insertAdjacentHTML("beforeend", `<div class="notice-actions"><button type="button" class="btn ghost mini" ${login ? "data-notice-login" : "data-notice-retry"}>${login ? "重新扫码登录" : "重试查询"}</button></div>`);
  }
  box.classList.toggle("error", res.code === "banned" || res.code === "rate_limited");
  box.classList.remove("hidden");
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

function show(view) {
  $("view-login").classList.toggle("hidden", view !== "login");
  $("view-main").classList.toggle("hidden", view !== "main");
}

// 胜率可能是 0~1 的小数，也可能已经是百分数，统一成百分数
function pct(v) {
  const n = parseFloat(v) || 0;
  return Math.max(0, Math.min(100, n <= 1 ? n * 100 : n));
}

function duration(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (!s) return "-";
  return `${Math.floor(s / 60)} 分 ${String(s % 60).padStart(2, "0")} 秒`;
}

// 中文拼音首字母（零依赖，按拼音排序边界字判断），玩家库检索用
function pinyinInitials(text) {
  const letters = "abcdefghjklmnopqrstwxyz";
  const bounds = "芭擦搭蛾发噶哈击喀垃妈拿哦啪期然撒塌挖昔压匝";
  let out = "";
  for (const ch of String(text || "")) {
    if (/[a-zA-Z0-9#]/.test(ch)) { out += ch.toLowerCase(); continue; }
    if (!/[一-龥]/.test(ch)) continue;
    let letter = "z";
    for (let i = 0; i < bounds.length; i++) {
      if (ch.localeCompare(bounds[i], "zh-CN-u-co-pinyin") < 0) { letter = letters[i]; break; }
    }
    out += letter;
  }
  return out;
}

// ---------- 登录 ----------
// 二维码框状态：loading 转圈 / ready 显示图片 / failed 获取失败
function setQrState(state) {
  $("qr-wrap").dataset.state = state;
  $("qr-hint-text").textContent = ({ failed: "获取失败，请重新获取", expired: "二维码已过期" })[state] || "正在获取二维码…";
}

// canBack：查询号已有凭据时允许返回；主号随时可返回（查询界面不依赖它）
async function startLogin(account = loginAccount, canBack = false) {
  MODALS.forEach(closeModal);
  clearInterval(loginTimer);
  const seq = ++loginSeq; // 每次扫码有自己的生命周期
  loginAccount = account;
  show("login");
  $("login-title").textContent = `扫码登录${ACCOUNT_NAMES[account]}`;
  $("btn-login-back").classList.toggle("hidden", !canBack);
  $("login-msg").textContent = "正在申请二维码…";
  setQrState("loading");
  $("qr").removeAttribute("src");  // 清掉上一张，重新获取时回到转圈
  const res = await api().start_login(account);
  if (seq !== loginSeq) return;
  if (!res.ok) { setQrState("failed"); $("login-msg").textContent = res.message; return; }
  $("qr").onload = () => setQrState("ready");    // 真正加载完才显示，避免裂图
  $("qr").onerror = () => setQrState("failed");
  $("qr").src = res.data;
  $("login-msg").textContent = "等待扫码…";
  loginTimer = setInterval(async () => {
    const r = await api().poll_login();
    if (seq !== loginSeq) return;
    if (!r.ok) { clearInterval(loginTimer); $("login-msg").textContent = r.message; return; }
    if (r.data === "done") { clearInterval(loginTimer); toast(`${ACCOUNT_NAMES[account]}已登录`); init(); }
    if (r.data === "expired") { clearInterval(loginTimer); setQrState("expired"); $("login-msg").textContent = "二维码已过期，请重新获取。"; }
  }, 1500);
}

// ---------- 账号 ----------
// 一个槽位的切换下拉框 + 已保存列表（查询号、主号各管各的）
function savedBlock(key, saved) {
  const others = saved.filter((s) => !s.active);
  const bar = others.length ? `<div class="switchbar">
      <select class="select" data-switch-select="${key}">${others.map((s) =>
        `<option value="${esc(s.roleId)}">${esc(s.nick || s.roleId)}</option>`).join("")}</select>
      <button type="button" class="btn mini" data-switch="${key}">切换</button></div>` : "";
  const list = saved.length ? `<details class="saved"><summary>已保存 ${saved.length} 个</summary>${saved.map((s) => `
      <div class="saved-item"><span title="${esc(s.roleId)}">${esc(s.nick || s.roleId)}${s.active ? " · 在用" : ""}</span>
        <button type="button" class="btn ghost mini danger" data-delete="${esc(s.roleId)}" data-account="${key}"
          data-name="${esc(s.nick || s.roleId)}" ${s.active ? "disabled title=\"正在使用中，先切换走再删除\"" : ""}>删除</button></div>`).join("")}
    </details>` : "";
  return bar + list;
}

async function refreshAccounts() {
  const res = await api().accounts();
  if (!res.ok) return;
  const sv = await api().saved_accounts();
  const saved = sv.ok ? sv.data : { query: [], main: [] };
  const query = res.data[0]; // 侧栏只显示凭据状态，详细信息留在账号面板内
  $("account-summary").textContent = !query?.loggedIn ? "查询号未登录" : query.stale ? "查询号凭据较旧" : "查询号已就绪";
  $("account-status").querySelector(".dot").className = `dot ${query?.loggedIn ? query.stale ? "stale" : "ok" : ""}`;
  // 每个账号默认收成一行（圆点 + 昵称 + 槽位），点开才显示详情与操作
  $("accounts").innerHTML = res.data.map((a, i) => {
    const key = ACCOUNT_KEYS[i];
    const extra = savedBlock(key, saved[key] || []);
    const open = openAccounts.has(key) ? "open" : "";
    if (!a.loggedIn) {
      return `<details class="account" data-key="${key}" ${open}><summary class="head"><span class="dot"></span>
        <span class="nick muted">未登录</span><span class="role-label">${esc(a.label)}</span></summary>
        <div class="actions"><button type="button" class="btn mini" data-login="${key}">扫码登录</button></div>${extra}</details>`;
    }
    return `<details class="account" data-key="${key}" ${open}><summary class="head"
        title="${a.stale ? "凭据较旧" : "凭据正常"}，点击展开或收起">
        <span class="dot ${a.stale ? "stale" : "ok"}"></span>
        <span class="nick">${esc(a.nick || "（无昵称）")}</span>
        <span class="role-label">${esc(a.label)}</span></summary>
      <div class="meta">${esc(a.userName)} · 角色 ID <code>${esc(a.roleId)}</code><br>
        凭据 ${esc(a.fetchedAt)}${a.stale ? "（较旧）" : ""}</div>
      <div class="actions"><button type="button" class="btn ghost mini" data-login="${key}">扫码登录新号</button></div>${extra}</details>`;
  }).join("");
}

async function switchAccount(key, button) {
  const roleId = document.querySelector(`[data-switch-select="${key}"]`).value;
  button.disabled = true;
  button.textContent = "切换中…";
  const res = await api().switch_account(key, roleId);
  if (res.ok) {
    toast(`${ACCOUNT_NAMES[key]}已切换`);
    if (key === "main" && reportTimer) startReport();  // 换了主号，提醒立刻按新号查一次
  } else {
    toast(res.message);
  }
  refreshAccounts();
}

async function deleteSaved(button) {
  const { delete: roleId, account, name } = button.dataset;
  if (!window.confirm(`删除${ACCOUNT_NAMES[account]}「${name}」的已保存凭据？之后要用这个号得重新扫码。`)) return;
  const res = await api().delete_saved(account, roleId);
  toast(res.ok ? "已删除" : res.message);
  refreshAccounts();
}

// ---------- 查询 ----------
function skeleton() {
  $("empty").classList.add("hidden");
  $("result").classList.remove("hidden");
  $("profile").innerHTML = '<div class="skeleton" style="width:52px;height:52px;border-radius:18px;flex:none"></div><div class="info"><div class="skeleton" style="width:45%;height:24px"></div><div class="skeleton" style="width:70%;height:14px;margin-top:10px"></div></div>';
  $("ranks").innerHTML = '<div class="skeleton" style="height:130px"></div>'.repeat(3);
  $("avg").innerHTML = '<div class="skeleton" style="height:110px"></div>';
  matchesSkeleton();
}

function matchesSkeleton() {
  $("matches").innerHTML = '<div class="skeleton" style="height:38px;margin:4px 0"></div>'.repeat(6);
  $("btn-more").classList.add("hidden");
  moreFailed = false;  // 换了列表，之前的失败不再算数
}

function setQueryBusy(busy) { // 禁用会发起新查询的入口，避免操作叠加消耗请求
  queryLoading = busy;
  $("result").setAttribute("aria-busy", String(busy));
  $("btn-search").disabled = $("season").disabled = $("btn-find").disabled = busy;
  $("btn-search").textContent = busy ? "查询中…" : "查询";
  document.querySelectorAll("#mode-filter button, #stat-mode button").forEach((button) => { button.disabled = busy; });
}

// 拉一次数据。full=true 时重画资料卡和段位（新查人、换赛季），否则只换对局列表（换模式）
async function load({ full }) {
  if (queryLoading) return;
  const seq = ++querySeq;
  let succeeded = false; // 当前查询失败时不继续自动查询，避免封禁或限流后追加请求
  $("notice").classList.add("hidden");
  setQueryBusy(true);
  if (full) skeleton(); else matchesSkeleton();
  try {
    const res = await api().get_player({
      battleTag: cur.tag, mode: cur.mode, season: cur.season, page: 1, limit: FIRST_PAGES, customerToken: cur.token,
      stats: full,  // 只换对局列表时不要资料卡与统计，省请求
    });
    if (seq !== querySeq) return;
    if (!res.ok) {
      if (res.code === "need_login") return startLogin("query");
      if (full) { $("result").classList.add("hidden"); $("empty").classList.remove("hidden"); }
      else $("matches").innerHTML = "";
      return showNotice(res);
    }
    const d = res.data;
    cur.token = d.customerToken;
    cur.page = FIRST_PAGES;
    cur.hasMore = d.pagination.hasMore;
    if (full) renderPlayer(d);
    renderMatches(d.recentMatches, false);
    succeeded = true;
  } catch (error) {
    if (full) { $("result").classList.add("hidden"); $("empty").classList.remove("hidden"); }
    else $("matches").innerHTML = "";
    showNotice({ code: "network", message: error.message || String(error) });
  } finally {
    setQueryBusy(false);
    const pending = pendingReportTag;
    pendingReportTag = "";
    if (succeeded && pending && pending !== cur.tag) search(pending);
  }
}

function search(tag) {
  tag = String(tag || "").trim();
  if (!tag) return;
  if (queryLoading) return toast("正在查询，请稍候");
  $("tag").value = tag;
  Object.assign(cur, { tag, token: "", page: 0, hasMore: false });
  load({ full: true });
}

async function loadMore() {
  const btn = $("btn-more");
  if (btn.disabled || queryLoading || !cur.hasMore) return;  // 加载中再滚轮也不重复发请求
  const seq = querySeq; // 新查询开始后不把旧分页结果接到新玩家后面
  btn.disabled = true;
  btn.textContent = "加载中…";
  let res; // 把桥接异常转换为与业务错误相同的提示，并恢复重试入口
  try {
    res = await api().get_player({
      battleTag: cur.tag, mode: cur.mode, season: cur.season, page: cur.page + 1, limit: 1, customerToken: cur.token,
      stats: false,
    });
  } catch (error) {
    res = { ok: false, code: "network", message: error.message || String(error) };
  }
  if (seq !== querySeq) { btn.disabled = false; btn.textContent = MORE_TEXT; return; }
  btn.disabled = false;
  btn.textContent = MORE_TEXT;
  moreFailed = !res.ok;
  if (!res.ok) return showNotice(res);
  cur.page += 1;
  cur.hasMore = res.data.pagination.hasMore;
  renderMatches(res.data.recentMatches, true);
}

function isFollowed(tag) { return library.followed.some((p) => p.battle_tag === tag); }

function renderFavButton() {
  const btn = document.querySelector("#profile .fav-btn");
  if (!btn) return;
  const on = isFollowed(cur.tag);
  btn.classList.toggle("on", on);
  btn.textContent = on ? "★ 已收藏" : "☆ 收藏";
  btn.setAttribute("aria-pressed", String(on));
}

const STAT_LABEL = { sport: "竞技", quick: "快速" };  // 统计切换的两种模式
let statMode = "sport";  // 玩家面板当前显示竞技还是快速统计，换人后保留
let lastPlayer = null;   // 最近一次渲染的玩家数据，切换统计模式时直接重画，不发请求

// 一格大数字：上面数字、下面小字标签，可带一行更淡的补充说明
function statBox(value, label, extra = "", cls = "") {
  return `<div class="stat"><b class="${cls}">${value}</b><span>${label}</span>${extra ? `<small>${extra}</small>` : ""}</div>`;
}

// 场均表现：赛季战绩 + 官方「近 12 场最常用职责」场均，底部一行常用英雄（没有数据的项不显示）
function avgBlock(d) {
  const m = (d.overview || {})[statMode] || {};
  const label = STAT_LABEL[statMode];
  const boxes = [];
  if (m.summary) {
    const wr = pct(m.summary.winRate);
    boxes.push(statBox(`${wr.toFixed(1)}%`, "赛季胜率",
      `${num(m.summary.winSum)} 胜 ${num(m.summary.lossSum)} 负`, wr >= 50 ? "win-text" : "loss-text"));
    boxes.push(statBox(num(m.summary.matchSum), "赛季场次", esc(d.season.name)));
  }
  if (m.recent) {
    const r = m.recent;
    // 「近 12 场 · 主玩职责」只在近期胜率下标一次，后面三格同口径
    boxes.push(statBox(`${pct(r.winRate).toFixed(1)}%`, "近期胜率", `近 12 场 · ${esc(r.roleName)}`));
    boxes.push(statBox(num(Math.round(r.aveKill || 0)), "场均击杀"));
    boxes.push(statBox(num(Math.round(r.aveDamage || 0)), "场均伤害"));
    boxes.push(statBox(num(Math.round(r.aveCure || 0)), "场均治疗"));
  }
  const grid = boxes.length ? `<div class="stat-grid">${boxes.join("")}</div>`
    : `<div class="muted small pad">${esc(d.season.name)}没有${label}记录</div>`;
  const heroes = m.heroes && m.heroes.length
    ? `<div class="avg-foot"><span class="muted">常用英雄</span>${m.heroes.map(esc).join(" / ")}</div>` : "";
  return grid + heroes;
}

// 一个位置的一栏：竞技显示段位 + 最高段位/连胜，快速没有段位，大字显示胜率
function rankCard(r) {
  const role = (tag = "") => `<div class="role">${icon(ROLE_ICONS[r.roleType] || "grid")}
    <span>${esc(r.roleName)}</span>${tag}</div>`;
  if (statMode === "quick") {
    const q = r.quick;
    if (!q) return `<div class="rank">${role()}<div class="tier">-</div><div class="extra">没有快速记录</div></div>`;
    return `<div class="rank">${role()}<div class="tier q-rate">${pct(q.winRate).toFixed(1)}%</div>
      <div class="line">${num(q.matchSum)} 场 · KDA ${esc(q.kda)}</div></div>`;
  }
  if (!r.matchSum && !r.rankName) {
    return `<div class="rank">${role()}<div class="tier">未定级</div><div class="extra">没有竞技记录</div></div>`;
  }
  const max = r.maxRank && r.maxRank.rankName ? `最高 ${esc(r.maxRank.rankName)} ${esc(r.maxRank.rankTier)}` : "";
  const extra = [max, r.maxWinStreak ? `最长 ${num(r.maxWinStreak)} 连胜` : ""].filter(Boolean).join(" · ");
  const tag = r.rankName ? `<span class="season-tag ${r.past ? "past" : ""}" title="${r.past ? "本赛季没有段位，往前找到的最近一次" : "该赛季的最后段位"}">${esc(r.season)}</span>` : "";
  return `<div class="rank ${RANK_CLASS[r.rankName] || "r-unranked"}">${role(tag)}
    <div class="tier">${esc(r.rankName || "未定级")}${r.rankName ? `<small>${esc(r.rankTier)}</small>` : ""}</div>
    <div class="line"><b>${pct(r.winRate).toFixed(1)}%</b> 胜率 · ${num(r.matchSum)} 场 · KDA ${esc(r.kda)}</div>
    <div class="extra" title="${num(r.rankScore)} 分">${extra || "&nbsp;"}</div></div>`;
}

// 只重画随统计模式变化的部分：小标签与段位卡
function renderStats() {
  const d = lastPlayer;
  if (!d) return;
  document.querySelectorAll("#stat-mode button").forEach((b) => {
    b.classList.toggle("active", b.dataset.stat === statMode);
    b.setAttribute("aria-pressed", String(b.dataset.stat === statMode));
  });
  const label = STAT_LABEL[statMode];
  $("ranks-title").textContent = statMode === "quick" ? "快速 · 各位置胜率" : "竞技段位";
  $("avg-title").textContent = `${label}表现`;
  $("avg").innerHTML = avgBlock(d);
  // 竞技中保留未定级职责，统一中性灰；快速仅展示有数据的位置，同样没有段位色。
  const ranks = statMode === "quick" ? d.ranks.filter((r) => r.quick) : d.ranks;
  $("ranks").innerHTML = ranks.length ? ranks.map(rankCard).join("")
    : `<div class="muted small pad">${statMode === "quick" ? "这个赛季没有快速记录"
      : cur.season ? "这个赛季没有竞技数据" : "最近 4 个赛季都没有竞技段位"}</div>`;
}

function renderPlayer(d) {
  const p = d.profile;
  lastPlayer = d;
  $("profile").innerHTML = `
    ${p.icon ? `<img src="${esc(p.icon)}" alt="">` : `<div class="avatar">${esc((p.name || "?").slice(0, 1))}</div>`}
    <div class="info">
      <div class="name-row"><span class="name" title="${esc(cur.tag)}">${esc(p.name)}</span>
        ${p.title ? `<span class="title-tag">${esc(p.title)}</span>` : ""}</div>
      <div class="meta"><span>${esc(cur.tag)}</span><span>等级 <b>${num(p.level)}</b></span><span>游戏时间 <b>${num(p.gameTime)}</b> 小时</span></div></div>
    <div class="profile-actions">
      <button type="button" class="fav-btn" id="btn-fav"></button></div>`;
  renderFavButton();
  renderStats();
  $("empty").classList.add("hidden");
  $("result").classList.remove("hidden");
}

// 数字单元格：本队最高的项高亮
function statCell(m, key, cls = "") {
  const best = (m.best || []).includes(key);
  return `<span class="num ${cls}${best ? " best" : ""}" ${best ? 'title="本队最高"' : ""}>${num(m[key])}</span>`;
}

function matchRow(m) {
  const result = ["win", "loss", "draw"].includes(m.result) ? m.result : "draw"; // 非胜负状态保持中性
  const label = { win: "胜利", loss: "失败", draw: "平局" }[result]; // 对局结果文字
  return `
    <button type="button" class="match ${result}" data-id="${esc(m.matchId)}" aria-label="${esc(m.mapName || "对局")} ${label}，查看记分板">
      <span class="strip"></span>
      <span class="ret">${label}</span>
      <span class="score">${num(m.teamScore)} : ${num(m.opponentScore)}</span>
      <span class="hero" title="${esc(m.roleName)}">${esc(m.heroName || "未知英雄")}</span>
      <span class="mode ${MODE_CLASS[m.gameModeName] || ""}" title="${esc(m.gameMode)}">${esc(m.gameModeName || "其他")}</span>
      <span class="map">${esc(m.mapName || "-")}</span>
      <span class="num">${num(m.kill)} / ${num(m.assist)} / ${num(m.death)}</span>
      <span class="num time" title="${esc(m.matchTime)}">${esc(m.matchTime)}</span>
    </button>`;
}

function renderMatches(list, append) {
  const html = list.map(matchRow).join("");
  if (append) $("matches").insertAdjacentHTML("beforeend", html);
  else $("matches").innerHTML = html || '<div class="muted">没有查到这个模式下的对局</div>';
  $("btn-more").classList.toggle("hidden", !cur.hasMore);
}

async function loadSeasons() {
  const res = await api().seasons();
  if (!res.ok) return;
  $("season").innerHTML = '<option value="">当前赛季</option>' +
    res.data.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
}

// ---------- 对局详情 ----------
// 点开一名玩家后显示的其余数据：[字段, 名称, 格式化]
const PLAYER_MORE = [
  ["damageTaken", "承受伤害", (v) => num(Math.round(v || 0))],
  ["healingTaken", "受到治疗", (v) => num(Math.round(v || 0))],
  ["finalHit", "最后一击", num],
  ["targetCompetingTime", "目标争夺时间", (v) => `${num(v)} 秒`],
];

// 一队的记分板：玩家/英雄 | 段位 | 击杀/助攻/阵亡 | 伤害 | 治疗 | 阻挡；点一行展开其余数据
function team(title, cls, list, bans) {
  const head = `<div class="sb-row sb-head"><span>${title} <span class="muted">${list.length} 人</span></span>
    <span>段位</span><span class="num">击杀/助攻/阵亡</span><span class="num">伤害</span><span class="num">治疗</span>
    <span class="num">阻挡</span><span></span></div>`;
  const rows = list.map((p) => {
    const name = p.name.includes("#")
      ? `<button type="button" class="link" data-query="${esc(p.name)}" title="查询 ${esc(p.name)}">${esc(p.name)}</button>`
      : `<span>${esc(p.name || "未知玩家")}</span>`;
    const rank = p.rankName ? `${esc(p.rankName)} ${esc(p.rankTier)}` : '<span class="muted">未定级</span>';
    const more = PLAYER_MORE.map(([k, label, fmt]) => `<div><span class="muted">${label}</span><b>${fmt(p[k])}</b></div>`).join("");
    return `<div class="sb-item ${p.partyIndex ? `p${Math.min(p.partyIndex, 4)}` : ""}"><div class="sb-row sb-player" role="button" tabindex="0" aria-expanded="false"
        title="点击展开更多数据${p.partyIndex ? "；同色竖条为一起组队" : ""}">
      <div class="who">${name}<span class="muted small">${esc(p.heroName || "-")}</span></div>
      <span class="small">${rank}</span>
      <span class="num">${statCell(p, "kill", "kad")} / ${num(p.assist)} / ${num(p.death)}</span>
      ${statCell(p, "heroDamage")}${statCell(p, "cure")}${statCell(p, "resistDamage")}
      <span class="caret">▸</span></div>
      <div class="sb-more hidden">${more}</div></div>`;
  }).join("");
  const ban = bans && bans.length ? `<div class="sb-bans muted small">禁用：<b>${bans.map(esc).join(" / ")}</b></div>` : "";
  return `<div class="team ${cls}">${head}${rows}${ban}</div>`;
}

// 英雄数据项按名称格式化：命中率/占比显示百分数，时间显示分秒，其余去掉多余小数
function heroStat(name, v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return esc(v);
  if (/(率|占比)$/.test(name) && n >= 0 && n <= 1) return `${(n * 100).toFixed(1)}%`;
  if (/时间$/.test(name)) return duration(n);
  if (Number.isInteger(n) || Math.abs(n) >= 10) return num(Math.round(n));
  return num(Math.round(n * 100) / 100);  // 小于 10 的小数（如伤害系数）保留两位
}

function heroes(list) {
  if (!list.length) return '<div class="muted small">没有英雄数据</div>';
  return `<div class="heroes">${list.map((h) => `
    <div class="hero-card"><div class="row-between"><b>${esc(h.heroName || "未知英雄")}</b>
      <span class="muted small">使用 ${duration(h.userTimeSec)}${h.useTimeRate ? `（${pct(h.useTimeRate).toFixed(0)}%）` : ""}</span></div>
      <div class="hero-stats">${Object.entries(h.statMap || {}).map(([k, v]) =>
        `<div><span title="${esc(k)}">${esc(k)}</span><b title="${esc(v)}">${heroStat(k, v)}</b></div>`).join("") || '<span class="muted small">无统计项</span>'}</div>
    </div>`).join("")}</div>`;
}

async function openDetail(matchId) {
  const seq = ++detailSeq; // 慢响应不能覆盖后来点开的另一场
  $("detail").innerHTML = '<div class="skeleton" style="height:240px"></div>';
  openModal("modal");
  const res = await api().get_match_detail(matchId, cur.token);
  if (seq !== detailSeq) return;
  if (!res.ok) {
    $("detail").innerHTML = `<div class="notice error" style="margin:0">${esc(res.message)}</div>`;
    return;
  }
  const d = res.data;
  $("detail").innerHTML = `
    <div class="overview">
      <div class="${esc(d.result)}"><span class="muted small">结果</span>
        <b class="ret-text">${d.result === "win" ? "胜利" : d.result === "loss" ? "失败" : "平局"} ${num(d.teamScore)} : ${num(d.opponentScore)}</b></div>
      <div><span class="muted small">地图</span><b>${esc(d.mapName || "未知地图")}</b></div>
      <div><span class="muted small">开始时间</span><b>${esc(d.startTime || "-")}</b></div>
      <div><span class="muted small">时长</span><b>${duration(d.gameTimeSec)}</b></div>
    </div>
    <div class="teams">${team("我方", "ours", d.teammates, d.teamBans)}${team("敌方", "theirs", d.enemies, d.enemyBans)}</div>
    <div class="detail-title">英雄数据</div>${heroes(d.heroes)}`;
  refreshLibrary();  // 记分板里的玩家已顺手入库
}

// ---------- 找对局 ----------
function localDate() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function openFind() {
  if (!cur.tag) return;
  $("find-who").textContent = cur.tag;
  $("find-date-to").max = $("find-date-from").max = localDate();
  openModal("find-modal", "input");
  if (findOptionsLoaded) return;
  const res = await api().find_options();
  if (!res.ok) return;
  findOptionsLoaded = true;
  const opts = (list) => list.map((v) => `<option value="${esc(v)}">`).join("");
  $("hero-options").innerHTML = opts(res.data.heroes);
  $("map-options").innerHTML = opts(res.data.maps);
}

// 状态行：文字 + 可选按钮（取消 / 继续比对）
function findStatus(text, { error = false, cancel = false, cont = "" } = {}) {
  const el = $("find-status");
  el.classList.toggle("error", error);
  el.innerHTML = `<span>${esc(text)}</span>` +
    (cont ? `<button type="button" class="btn mini" data-find="continue">${esc(cont)}</button>` : "") +
    (cancel ? '<button type="button" class="btn ghost mini" data-find="cancel">取消</button>' : "");
}

function stopFind(text) {
  findRun += 1;
  $("find-go").disabled = false;
  if (text) findStatus(text);
}

async function runFind() {
  const run = ++findRun;
  const names = $("find-names").value.trim();
  const heroes = $("find-heroes").value.trim();
  $("find-go").disabled = true;
  $("find-list").innerHTML = "";
  $("find-results").classList.add("hidden");
  findStatus("正在翻对局列表…", { cancel: true });
  const res = await api().find_candidates({
    battleTag: cur.tag, dateFrom: $("find-date-from").value, dateTo: $("find-date-to").value,
    timeFrom: $("find-from").value, timeTo: $("find-to").value,
    mode: $("find-mode").value, hero: $("find-hero").value, map: $("find-map").value, customerToken: cur.token,
  });
  if (run !== findRun) return;
  if (!res.ok) {
    $("find-go").disabled = false;
    return findStatus(ERROR_HINTS[res.code] || res.message, { error: true });
  }
  const d = res.data;
  if (!cur.token) cur.token = d.customerToken;
  let head = `${d.range.from} ~ ${d.range.to}（${d.seasons.join("、")}）· 翻了 ${d.pagesRead} 页`;
  if (d.clipped) head += "；Y2026S1 之前的数据网易已清理，已跳过";
  if (d.truncated.length) head += `；${d.truncated.join("、")} 翻到上限，更早的没翻到`;
  if (d.emptySeasons.length) head += `；${d.emptySeasons.join("、")} 这个模式下一场都没有（没打过，或已被网易清理）`;
  const cands = d.candidates;
  if (!cands.length) return stopFind(`${head}，没有符合条件的对局`);
  if (!names && !heroes) {  // 没填同局条件：候选就是结果，不看详情
    showFindRows(cands.map((m) => ({ m })));
    return stopFind(`${head}，符合 ${cands.length} 场`);
  }
  const cost = cands.filter((c) => !c.cached).length;  // 详情不在本地、需要发请求的场数
  const go = () => checkCandidates(run, cands, names, heroes, head);
  if (cost <= FIND_AUTO_CHECK) return go();
  if (cost > d.maxDetails) {  // 硬上限：确认也不放行，只能先缩小范围
    return stopFind(`${head}，候选 ${cands.length} 场，比对需要 ${cost} 次请求，超过单次上限 ${d.maxDetails}。` +
      "请缩小日期 / 时段，或填上他用的英雄、地图、模式");
  }
  findStatus(`${head}，候选 ${cands.length} 场，逐场比对需要 ${cost} 次请求`, { cancel: true, cont: "继续比对" });
  $("find-status").querySelector('[data-find="continue"]').onclick = go;
}

async function checkCandidates(run, cands, names, heroes, head) {
  const hits = [];
  for (let i = 0; i < cands.length; i++) {
    if (run !== findRun) return;
    findStatus(`${head}，比对详情 ${i + 1}/${cands.length}，命中 ${hits.length} 场`, { cancel: true });
    const res = await api().check_match(cands[i].matchId, cur.token, names, heroes);
    if (run !== findRun) return;
    if (!res.ok) {  // 封禁、限流、过期等都停下，已命中的保留
      stopFind();
      return findStatus(`比对到第 ${i + 1} 场时出错：${ERROR_HINTS[res.code] || res.message}`, { error: true });
    }
    if (res.data.hit) {
      hits.push({ m: cands[i], matched: res.data.matched });
      showFindRows(hits);
    }
  }
  stopFind(`${head}，比对了 ${cands.length} 场，命中 ${hits.length} 场`);
}

function showFindRows(rows) {
  $("find-list").innerHTML = rows.map(({ m, matched }) => matchRow(m) +
    (matched ? `<div class="find-hit">命中：${matched.map((x) => `<b>${esc(x)}</b>`).join("、")}</div>` : "")).join("");
  $("find-results").classList.remove("hidden");
}

// ---------- 玩家库 ----------
async function refreshLibrary(query = "") {
  const seq = ++librarySeq;
  const res = await api().get_cached_players(query, 0);
  if (!res.ok || seq !== librarySeq) return;
  library = res.data;
  libraryQuery = query;
  libraryLoading = false;
  $("library-list").scrollTop = 0;
  renderFavorites();
}

// 顶部收藏按钮、资料卡收藏状态、打开着的玩家库一起刷新
function renderFavorites() {
  $("fav-chips").innerHTML = library.followed.map((p) => `<button type="button" class="favorite-link" data-query="${esc(p.battle_tag)}" title="查询 ${esc(p.battle_tag)}"><span>${esc(p.battle_tag)}</span></button>`).join("")
    || '<span class="muted small">点玩家资料卡的 ☆ 即可收藏</span>';
  renderFavButton();
  if (!$("library-modal").classList.contains("hidden")) renderLibrary();
}

// 往下滚到底：按当前检索词加载下一页，接在列表后面
async function loadMoreLibrary() {
  if (libraryLoading || !library.hasMore || libraryTab !== "all") return;
  libraryLoading = true;
  const seq = librarySeq;
  const res = await api().get_cached_players(libraryQuery, library.all.length);
  libraryLoading = false;
  if (!res.ok || seq !== librarySeq) return;  // 期间换了检索词，这一页作废
  library.all = library.all.concat(res.data.all);
  library.hasMore = res.data.hasMore;
  renderLibrary();
}

function renderLibrary() {
  const kw = $("library-filter").value.trim().toLowerCase();
  const match = (p) => !kw || p.battle_tag.toLowerCase().includes(kw) || pinyinInitials(p.battle_tag).includes(kw);
  const followed = library.followed.filter(match);
  const all = library.all.filter(match);
  $("lib-fav-count").textContent = String(followed.length);
  $("lib-all-count").textContent = `${all.length}${library.hasMore ? "+" : ""}`;
  const list = libraryTab === "followed" ? followed : all;
  $("library-list").innerHTML = list.length ? list.map((p) => {
    const on = isFollowed(p.battle_tag);
    return `<div class="lib-item">
      <button type="button" class="lib-star ${on ? "on" : ""}" data-star="${esc(p.battle_tag)}" title="${on ? "取消收藏" : "收藏"}">${on ? "★" : "☆"}</button>
      <button type="button" class="tag" data-query="${esc(p.battle_tag)}" title="查询 ${esc(p.battle_tag)}">${esc(p.battle_tag)}</button>
      ${p.has_token ? '<span class="badge" title="已缓存查询信息">已缓存</span>' : ""}
      <span class="muted small">${esc((p.last_seen || "").slice(5, 16))}</span></div>`;
  }).join("") + (libraryTab === "all" && library.hasMore ? '<div class="muted small lib-more">往下滚动加载更多…</div>' : "")
    : `<div class="muted small">${kw ? "没有匹配的玩家" : libraryTab === "followed" ? "还没有收藏的玩家，点 ☆ 收藏" : "还没有查过的玩家"}</div>`;
}

function openLibrary() {
  renderLibrary();
  openModal("library-modal", "input");
}

async function toggleFollowed(tag) {
  const res = await api().toggle_followed(tag);
  if (!res.ok) return toast(res.message);
  toast(res.data.is_followed ? `已收藏 ${tag}` : `已取消收藏 ${tag}`);
  // 只更新收藏列表，不重载已滚动加载的玩家，避免列表跳回顶部
  const fresh = await api().get_cached_players("", 0);
  if (!fresh.ok) return;
  library.followed = fresh.data.followed;
  renderFavorites();
}

// ---------- 举报提醒 ----------
// WebView 不允许无交互自动出声：在用户打开提醒开关那一下创建并解锁 AudioContext
function unlockSound() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    audioCtx.resume();
  } catch { audioCtx = null; }
}

// 合成一声「叮咚」，不依赖音频文件
function ding() {
  if (!audioCtx) return;
  const t = audioCtx.currentTime;
  [[880, 0], [660, 0.18]].forEach(([freq, delay]) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t + delay);
    gain.gain.exponentialRampToValueAtTime(0.3, t + delay + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + delay + 0.4);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(t + delay);
    osc.stop(t + delay + 0.45);
  });
}

// 新的一批举报：响一声，并自动查这一批里最新的一人（分批与标记由后端 report_watch 决定）
function onNewBatch(alert) {
  ding();
  $("report-dot").classList.remove("hidden");
  const more = alert.batchSize > 1 ? `（同批共 ${alert.batchSize} 人，其余可在举报提醒中手动查）` : "";
  if (!alert.battleTag) return toast(`有新的一批举报${more}`);
  MODALS.forEach(closeModal);
  if (queryLoading) {
    pendingReportTag = alert.battleTag;
    return toast(`新举报：当前查询完成后将查询 ${alert.battleTag}${more}`);
  }
  toast(`新举报：正在查询 ${alert.battleTag}${more}`);
  search(alert.battleTag);
}

async function reportTick() {
  const res = await api().report_check();
  if (!res.ok) {
    // 登录失效、限流等都停掉心跳，不自动重试，避免持续打接口
    stopReport();
    $("report-status").textContent = "已停止";
    return showNotice(res);
  }
  const d = res.data;
  $("report-status").textContent = `${d.checkedAt} 已检查 · 每 ${heartbeatSec} 秒 · 近 ${d.windowMin} 分钟`;
  if (d.alert) onNewBatch(d.alert);
  const newest = d.alert && d.alert.bnetId;
  $("report-panel").innerHTML = d.players.length ? d.players.map((p) => {
    const tag = p.battleTag || p.bnetId;
    return `<div class="report-item${p.bnetId === newest ? " new" : ""}${p.queried ? " done" : ""}">
      <div class="row-between"><span class="tag">${esc(tag)}</span>
        <span class="state ${p.queried ? "ok" : ""}">${p.queried ? "✓ 已查" : "同批未查"}</span></div>
      <div class="row-between"><span class="muted small">${esc(p.minutesAgo)} 分钟前 · ${esc(p.kinds.join("/"))}</span>
        <span class="row-gap">
          ${p.battleTag ? `<button type="button" class="btn ghost mini" data-report-query="${esc(p.battleTag)}"
            data-bnet="${esc(p.bnetId)}" data-time="${esc(p.reportTime)}">查询</button>` : ""}
          <button type="button" class="btn mini" data-copy="${esc(tag)}">复制</button></span></div>
    </div>`;
  }).join("") : '<div class="muted small">窗口内没有举报记录</div>';
}

async function startReport() {
  const res = await api().get_settings();
  if (res.ok) heartbeatSec = res.data.values.report_heartbeat_sec;
  clearInterval(reportTimer);
  $("report-status").textContent = "检查中…";
  $("report-summary").textContent = "举报提醒已开启";
  reportTick();
  reportTimer = setInterval(reportTick, heartbeatSec * 1000);
}

function stopReport() {
  pendingReportTag = "";
  clearInterval(reportTimer);
  reportTimer = null;
  $("report-toggle").checked = false;
  $("report-summary").textContent = "举报提醒未开启";
}

// ---------- 设置 ----------
async function openSettings() {
  const res = await api().get_settings();
  if (!res.ok) return showNotice(res);
  const { values, limits } = res.data;
  const fill = (id, key) => Object.assign($(id), { value: values[key], min: limits[key].min, max: limits[key].max });
  fill("set-heartbeat", "report_heartbeat_sec");
  fill("set-window", "report_window_min");
  fill("set-batch", "report_batch_min");
  fill("set-cli-limit", "cli_daily_limit");
  $("cli-settings-hint").textContent = `0 表示不限，上限 ${limits.cli_daily_limit.max} 次。保存后，对之后启动的 CLI 命令生效。`;
  $("report-settings-hint").textContent = `检查间隔最短 ${limits.report_heartbeat_sec.min} 秒。`;
  $("settings-msg").textContent = "";
  openModal("settings-modal", "input");
  refreshUpdateInfo();
}

// ---------- 版本与更新 ----------
function renderUpdateInfo(d) {
  $("update-info").textContent = d.frozen
    ? `当前版本 ${d.version}${d.builtAt ? ` · 构建于 ${d.builtAt}` : ""}`
    : "当前运行的是源码版，更新后重新打开即可。";
  $("btn-update").classList.toggle("hidden", !d.frozen);
  const rb = $("btn-rollback");
  rb.classList.toggle("hidden", !d.rollback);
  rb.textContent = `回滚到 ${d.rollback || ""}`;
}

function updateMsg(text, error = false) {
  $("update-msg").textContent = text;
  $("update-msg").classList.toggle("error", error);
}

async function refreshUpdateInfo() {
  const res = await api().update_info();
  if (res.ok) renderUpdateInfo(res.data);
}

// 安装 / 回滚都是换掉 app/ 文件夹，当前进程还跑着旧代码，重启后才生效
async function runUpdate(btn, call, doneText) {
  btn.disabled = true;
  updateMsg("处理中…（安装更新包会先自检，需要几秒）");
  const res = await call();
  btn.disabled = false;
  if (!res.ok) return updateMsg(res.message, true);
  if (!res.data) return updateMsg("");  // 取消了选文件
  renderUpdateInfo(res.data);
  updateMsg(doneText(res.data));
  $("btn-restart").classList.remove("hidden");
}

async function saveSettings() {
  const res = await api().save_settings({
    report_heartbeat_sec: Number($("set-heartbeat").value),
    report_window_min: Number($("set-window").value),
    report_batch_min: Number($("set-batch").value),
    cli_daily_limit: Number($("set-cli-limit").value),
  });
  if (!res.ok) { $("settings-msg").textContent = res.message; return; }
  closeModal("settings-modal");
  toast("设置已保存");
  if (reportTimer) startReport();  // 提醒开着时按新间隔重启
}

// ---------- 初始化 ----------
async function init() {
  const res = await api().status();
  if (!res.ok) { show("main"); return showNotice(res); }
  if (!res.data.loggedIn) return startLogin("query");
  show("main");
  refreshAccounts();
  refreshLibrary();
  loadSeasons();
}

// ---------- 事件 ----------
$("search-form").addEventListener("submit", (e) => { e.preventDefault(); search($("tag").value); });
// 任何带 data-query 的元素（收藏标签、玩家库、记分板里的名字）点了都直接查这个人
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-query]");
  if (!el) return;
  MODALS.forEach(closeModal);
  search(el.dataset.query);
});
$("season").addEventListener("change", (e) => { cur.season = e.target.value; cur.page = 0; load({ full: true }); });
$("mode-filter").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-mode]");
  if (!btn || btn.dataset.mode === cur.mode) return;
  if (queryLoading) return;
  $("mode-filter").querySelectorAll("button").forEach((b) => { b.classList.toggle("active", b === btn); b.setAttribute("aria-pressed", String(b === btn)); });
  cur.mode = btn.dataset.mode;
  if (cur.tag) load({ full: false });
});
$("btn-more").addEventListener("click", loadMore);
$("btn-find").addEventListener("click", openFind);
$("find-form").addEventListener("submit", (e) => { e.preventDefault(); runFind(); });
$("find-status").addEventListener("click", (e) => { if (e.target.dataset.find === "cancel") stopFind("已取消"); });
$("find-list").addEventListener("click", (e) => {
  const row = e.target.closest(".match[data-id]");
  if (row) openDetail(row.dataset.id);
});
// 对局列表滚到底后再往下滚一下才加载：只是看到底部不发请求，确实还想往下看才花一次额度
document.querySelector(".content").addEventListener("wheel", (e) => {
  const el = e.currentTarget;
  if (e.deltaY <= 0 || moreFailed || $("btn-more").classList.contains("hidden")) return;
  const scroller = getComputedStyle(el).overflowY === "visible" ? document.scrollingElement : el; // 窄屏改为页面滚动时仍只在底部加载
  if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < BOTTOM_GAP) loadMore();
}, { passive: true });
// 记分板点一行展开/收起该玩家的其余数据；点名字仍是查人（由 data-query 处理）
$("detail").addEventListener("click", (e) => {
  const row = e.target.closest(".sb-player");
  if (!row || e.target.closest("[data-query]")) return;
  // 开关挂在外层 .sb-item 上：行 + 展开区一起加粗边框
  row.parentElement.classList.toggle("open");
  row.nextElementSibling.classList.toggle("hidden");
  row.setAttribute("aria-expanded", String(row.parentElement.classList.contains("open")));
});
$("matches").addEventListener("click", (e) => {
  const row = e.target.closest(".match[data-id]");
  if (row) openDetail(row.dataset.id);
});
$("profile").addEventListener("click", (e) => {
  if (e.target.closest("#btn-fav")) toggleFollowed(cur.tag);
});
$("stat-mode").addEventListener("click", (e) => {
  const stat = e.target.closest("[data-stat]")?.dataset.stat;
  if (stat && stat !== statMode) { statMode = stat; renderStats(); }
});

$("accounts").addEventListener("click", async (e) => {
  const t = e.target;
  if (t.dataset.switch) return switchAccount(t.dataset.switch, t);
  if (t.dataset.delete) return deleteSaved(t);
  const account = t.dataset.login;
  if (!account) return;
  // 查询号已有凭据时才允许返回；主号随时可返回
  const st = await api().status();
  startLogin(account, account === "main" || (st.ok && st.data.loggedIn));
});
// toggle 事件不冒泡，用捕获阶段监听；只记账号卡片本身，不管里面「已保存」的折叠
$("accounts").addEventListener("toggle", (e) => {
  const card = e.target;
  if (!card.classList.contains("account")) return;
  if (card.open) openAccounts.add(card.dataset.key); else openAccounts.delete(card.dataset.key);
}, true);
$("btn-login-back").addEventListener("click", async () => {
  loginSeq += 1;
  clearInterval(loginTimer);
  await api().cancel_login();
  show("main");
});
$("btn-relogin").addEventListener("click", () => startLogin(loginAccount, !$("btn-login-back").classList.contains("hidden")));

$("btn-library").addEventListener("click", openLibrary);
$("library-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  libraryTab = btn.dataset.tab;
  $("library-tabs").querySelectorAll("button").forEach((b) => { b.classList.toggle("active", b === btn); b.setAttribute("aria-pressed", String(b === btn)); });
  renderLibrary();
});
$("library-filter").addEventListener("input", () => {
  renderLibrary();  // 先在已加载的列表里即时过滤（含拼音首字母）
  clearTimeout(libraryTimer);
  // 再去本地库全量检索（不限最近 200 人），250ms 防抖
  libraryTimer = setTimeout(() => refreshLibrary($("library-filter").value.trim()), 250);
});
$("library-list").addEventListener("click", (e) => { if (e.target.dataset.star) toggleFollowed(e.target.dataset.star); });
$("library-list").addEventListener("scroll", (e) => {
  const el = e.target;
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) loadMoreLibrary();  // 离底部不到 80px 就预加载
});

$("modal-close").addEventListener("click", () => closeModal("modal"));
$("find-close").addEventListener("click", () => closeModal("find-modal"));
$("library-close").addEventListener("click", () => closeModal("library-modal"));
$("settings-close").addEventListener("click", () => closeModal("settings-modal"));
$("accounts-close").addEventListener("click", () => closeModal("accounts-modal"));
$("reports-close").addEventListener("click", () => closeModal("reports-modal"));
$("nav-query").addEventListener("click", () => { MODALS.forEach(closeModal); $("tag").focus(); });
$("btn-accounts").addEventListener("click", () => { openModal("accounts-modal", "summary"); refreshAccounts(); });
$("account-status").addEventListener("click", () => { openModal("accounts-modal", "summary"); refreshAccounts(); });
function openReports() { // 只打开现有记录，不因打开面板额外检查接口
  $("report-dot").classList.add("hidden");
  openModal("reports-modal", "input");
}
$("btn-reports").addEventListener("click", openReports);
$("report-summary").addEventListener("click", openReports);
$("btn-update").addEventListener("click", (e) => runUpdate(e.target, () => api().install_update(),
  (d) => `已安装 ${d.version}，重启后生效。原来的版本留作回滚。`));
$("btn-rollback").addEventListener("click", (e) => runUpdate(e.target, () => api().rollback_update(),
  (d) => `已切换到 ${d.version}，重启后生效。`));
$("btn-restart").addEventListener("click", () => api().restart_app());
MODALS.forEach((id) => $(id).addEventListener("click", (e) => { if (e.target === $(id)) closeModal(id); }));
document.addEventListener("keydown", (e) => {
  const id = modalStack.at(-1); // 嵌套详情先关闭最上层，保留后面的查找结果
  if (!id) return;
  if (e.key === "Escape") { e.preventDefault(); closeModal(id); return; }
  if (e.key !== "Tab") return;
  const controls = [...$(id).querySelectorAll("button, input, select, summary, [tabindex='0']")]
    .filter((element) => !element.disabled && element.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (!first) return;
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

$("report-toggle").addEventListener("change", (e) => {
  if (e.target.checked) { unlockSound(); return startReport(); }
  stopReport();
  $("report-status").textContent = "提醒已关闭";
  $("report-panel").innerHTML = '<div class="panel-empty">开启提醒后，最近的举报记录会显示在这里。</div>';
});
$("report-panel").addEventListener("click", async (e) => {
  const q = e.target.dataset.reportQuery;
  if (q) {
    // 手动查同批的其他人：先标记已查，界面上立即改状态，不等下一次心跳
    await api().mark_report_queried(e.target.dataset.bnet, e.target.dataset.time);
    const item = e.target.closest(".report-item");
    item.classList.add("done");
    item.querySelector(".state").classList.add("ok");
    item.querySelector(".state").textContent = "✓ 已查";
    return search(q);
  }
  const text = e.target.dataset.copy;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => toast(`已复制 ${text}`), () => toast("复制失败，请手动选中"));
});
$("btn-settings").addEventListener("click", openSettings);
$("settings-form").addEventListener("submit", (e) => { e.preventDefault(); saveSettings(); });
$("notice").addEventListener("click", (e) => {
  if (e.target.closest("[data-notice-login]")) startLogin("query", true);
  if (e.target.closest("[data-notice-retry]") && cur.tag) load({ full: true });
});
$("detail").addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".sb-player")) { e.preventDefault(); e.target.click(); }
});
// pywebview 注入 api 后会触发该事件
window.addEventListener("pywebviewready", init);
