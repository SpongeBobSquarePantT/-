const state = {
  categories: [],
  tools: [],
  selected: new Set(),
  currentCategory: "all",
  currentTab: "preset",
  platform: "",
  packageManager: "",
  socket: null,
  searchResults: [],
  searchPage: 1,
  searchTotal: 0,
  searchKeyword: "",
  searchSuggestions: [],
  currentTaskId: "",
};

const els = {
  categoryNav: document.getElementById("category-nav"),
  toolList: document.getElementById("tool-list"),
  categoryTitle: document.getElementById("category-title"),
  selectedCount: document.getElementById("selected-count"),
  installBtn: document.getElementById("install-btn"),
  logPanel: document.getElementById("log-panel"),
  logBody: document.getElementById("log-body"),
  closeLog: document.getElementById("close-log"),
  platformInfo: document.getElementById("platform-info"),
  selectAll: document.getElementById("select-all"),
  clearAll: document.getElementById("clear-all"),
  taskStatus: document.getElementById("task-status"),
  presetView: document.getElementById("preset-view"),
  searchView: document.getElementById("search-view"),
  searchInput: document.getElementById("search-input"),
  searchBtn: document.getElementById("search-btn"),
  searchResults: document.getElementById("search-results"),
  footer: document.querySelector(".footer"),
  cancelTask: document.getElementById("cancel-task"),
};

async function loadTools(refresh) {
  let firstAttempt = true;
  let failures = 0;
  for (;;) {
    let data;
    try {
      // refresh 只在首次请求时带上，避免轮询期间反复触发后台检测
      const url = "/api/tools" + (refresh && firstAttempt ? "?refresh=1" : "");
      firstAttempt = false;
      const res = await fetch(url);
      data = await res.json();
    } catch (err) {
      if (++failures > 40) {
        showLoadError();
        return;
      }
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    if (data.detection !== "ready") {
      // 首次加载时显示检测提示；后台刷新期间保留已有列表，避免页面闪烁
      if (!state.tools.length) showDetecting();
      if (++failures > 60) {
        showLoadError();
        return;
      }
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    state.categories = data.categories;
    state.tools = data.tools;
    state.platform = data.platform;
    state.packageManager = data.package_manager;
    renderPlatform();
    renderCategories();
    renderTools();
    return;
  }
}

function showDetecting() {
  els.categoryTitle.textContent = "检测中";
  els.categoryNav.innerHTML = `<div class="detecting">正在检测已安装软件...</div>`;
  els.toolList.innerHTML = `<div class="empty">正在检测本地已安装软件，请稍候...</div>`;
}

function showLoadError() {
  els.categoryTitle.textContent = "加载失败";
  els.toolList.innerHTML = `<div class="empty">
    无法连接后端或检测超时。
    <button class="btn btn-primary btn-sm" id="retry-load">重试</button>
  </div>`;
  document.getElementById("retry-load").addEventListener("click", () => loadTools(true));
}

function renderPlatform() {
  els.platformInfo.innerHTML = `
    <div><span class="label">系统平台 </span><span class="value">${state.platform}</span></div>
    <div><span class="label">包管理器 </span><span class="value">${state.packageManager || "未检测到"}</span></div>
  `;
}

function renderCategories() {
  const allCount = state.tools.length;
  const onPreset = state.currentTab !== "search";
  const html = [
    `<button class="category-item ${onPreset && state.currentCategory === "all" ? "active" : ""}" data-category="all">
       📦 全部工具 <span class="count">${allCount}</span>
     </button>`,
    ...state.categories.map((cat) => {
      const count = state.tools.filter((t) => t.category === cat.id).length;
      const active = onPreset && state.currentCategory === cat.id ? "active" : "";
      return `<button class="category-item ${active}" data-category="${cat.id}">
        ${cat.icon} ${cat.name} <span class="count">${count}</span>
      </button>`;
    }),
    `<button class="category-item search-entry ${!onPreset ? "active" : ""}" data-category="__search__">
       🔍 搜索安装
     </button>`,
  ].join("");
  els.categoryNav.innerHTML = html;
  els.categoryNav.querySelectorAll(".category-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.category === "__search__") {
        switchTab("search");
      } else {
        state.currentCategory = btn.dataset.category;
        switchTab("preset");
        renderTools();
      }
      renderCategories();
    });
  });
}

function filteredTools() {
  if (state.currentCategory === "all") return state.tools;
  return state.tools.filter((t) => t.category === state.currentCategory);
}

function renderTools() {
  const tools = filteredTools();
  const cat = state.categories.find((c) => c.id === state.currentCategory);
  els.categoryTitle.textContent = cat ? cat.name : "全部工具";

  if (!tools.length) {
    els.toolList.innerHTML = `<div class="empty">该分类暂无可用工具</div>`;
    return;
  }

  els.toolList.innerHTML = tools.map((tool) => {
    const checked = state.selected.has(tool.id) ? "checked" : "";
    const badge = tool.installed
      ? `<span class="badge installed">已安装</span>`
      : `<span class="badge missing">未安装</span>`;
    return `
      <label class="tool-card ${checked}" data-id="${tool.id}">
        <input type="checkbox" ${state.selected.has(tool.id) ? "checked" : ""} />
        <div class="tool-info">
          <div class="tool-name">${tool.name} ${badge}</div>
          <div class="tool-desc">${tool.description}</div>
        </div>
      </label>
    `;
  }).join("");

  // label 原生点击已会切换 checkbox 并触发 change，只监听 change，避免再手动切换一次被抵消
  els.toolList.querySelectorAll(".tool-card input").forEach((input) => {
    input.addEventListener("change", (e) => {
      const card = e.target.closest(".tool-card");
      card.classList.toggle("checked", e.target.checked);
      if (e.target.checked) state.selected.add(card.dataset.id);
      else state.selected.delete(card.dataset.id);
      updateFooter();
    });
  });

  updateFooter();
}

function updateFooter() {
  const count = state.selected.size;
  els.selectedCount.textContent = `已选 ${count} 项`;
  els.installBtn.textContent = `安装选中 (${count})`;
  els.installBtn.disabled = count === 0;
}

function toggleSelectAll() {
  const tools = filteredTools();
  const allSelected = tools.every((t) => state.selected.has(t.id));
  if (allSelected) {
    tools.forEach((t) => state.selected.delete(t.id));
  } else {
    tools.forEach((t) => state.selected.add(t.id));
  }
  renderTools();
}

function appendLog(record) {
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  line.className = `log-line ${record.level || "info"}`;
  line.innerHTML = `<span class="time">[${time}]</span>${escapeHtml(record.message)}`;
  els.logBody.appendChild(line);
  els.logBody.scrollTop = els.logBody.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function showLogPanel() {
  els.logBody.innerHTML = "";
  els.logPanel.classList.remove("hidden");
}

/* ---------------- 标签切换 ---------------- */

function switchTab(tab) {
  state.currentTab = tab;
  els.presetView.classList.toggle("hidden", tab !== "preset");
  els.searchView.classList.toggle("hidden", tab !== "search");
  els.installBtn.classList.toggle("hidden", tab !== "preset");
  els.footer.classList.toggle("hidden", tab !== "preset");
}

/* ---------------- 搜索安装 ---------------- */

async function doSearch() {
  const keyword = els.searchInput.value.trim();
  if (!keyword) return;
  state.searchKeyword = keyword;
  state.searchPage = 1;
  await fetchSearch();
}

async function fetchSearch() {
  const keyword = state.searchKeyword;
  if (!keyword) return;
  els.searchBtn.disabled = true;
  els.searchBtn.textContent = "搜索中...";
  els.searchResults.innerHTML = `<div class="empty">正在搜索 "${escapeHtml(keyword)}" ...</div>`;
  try {
    const url = `/api/search?q=${encodeURIComponent(keyword)}&page=${state.searchPage}&page_size=20`;
    const res = await fetch(url);
    const data = await res.json();
    state.searchResults = data.results || [];
    state.searchTotal = data.total || 0;
    state.searchSuggestions = data.suggestions || [];
    renderSearchResults();
  } catch (err) {
    els.searchResults.innerHTML = `<div class="empty">搜索失败: ${escapeHtml(String(err))}</div>`;
  } finally {
    els.searchBtn.disabled = false;
    els.searchBtn.textContent = "搜索";
  }
}

function renderSearchResults() {
  if (!state.searchResults.length) {
    const chips = (state.searchSuggestions || [])
      .map((s) => `<button class="suggestion-chip" data-kw="${escapeHtml(s)}">${escapeHtml(s)}</button>`)
      .join("");
    els.searchResults.innerHTML = `
      <div class="empty">未找到匹配的软件包，试试英文关键词</div>
      ${chips ? `<div class="suggestions">可以试试：${chips}</div>` : ""}
    `;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(state.searchTotal / 20));
  els.searchResults.innerHTML = `
    <div class="search-table">
      <div class="search-row search-head">
        <span class="col-name">名称</span>
        <span class="col-id">包 ID</span>
        <span class="col-ver">版本</span>
        <span class="col-path">安装路径</span>
        <span class="col-act">操作</span>
      </div>
      ${state.searchResults.map((pkg, idx) => renderSearchRow(pkg, idx)).join("")}
    </div>
    <div class="pager">
      <button class="btn btn-ghost btn-sm" data-page="prev" ${state.searchPage <= 1 ? "disabled" : ""}>上一页</button>
      <span class="pager-info">第 ${state.searchPage} / ${totalPages} 页 · 共 ${state.searchTotal} 条</span>
      <button class="btn btn-ghost btn-sm" data-page="next" ${state.searchPage >= totalPages ? "disabled" : ""}>下一页</button>
    </div>
  `;
}

function renderSearchRow(pkg, idx) {
  if (pkg.official) {
    return `
      <div class="search-row" data-idx="${idx}">
        <span class="col-name">
          <span>${escapeHtml(pkg.name)}</span>
          <small class="badge src">${escapeHtml(pkg.source || "")}</small>
          <small class="desc">${escapeHtml(pkg.description || "")}</small>
        </span>
        <span class="col-id">${escapeHtml(pkg.id)}</span>
        <span class="col-ver">?</span>
        <span class="col-path">?</span>
        <span class="col-act">
          <a class="btn btn-primary btn-sm" href="${escapeHtml(pkg.url)}" target="_blank" rel="noopener">打开官网</a>
        </span>
      </div>
    `;
  }
  const variants = pkg.variants || [];
  const verOptions = [`<option value="">最新版本</option>`];
  variants.forEach((v, vi) => {
    verOptions.push(`<option value="${vi}">${escapeHtml(v.version)} (${escapeHtml(v.source || "")})</option>`);
  });
  return `
    <div class="search-row" data-idx="${idx}">
      <span class="col-name">
        <span>${escapeHtml(pkg.name || pkg.id)}</span>
        <small class="badge src">${escapeHtml(pkg.source || "")}</small>
        ${pkg.description ? `<small class="desc">${escapeHtml(pkg.description)}</small>` : ""}
      </span>
      <span class="col-id">${escapeHtml(pkg.id)}</span>
      <span class="col-ver">
        <select class="ver-select" data-idx="${idx}">
          ${verOptions.join("")}
        </select>
        <button class="btn btn-ghost btn-sm load-ver" data-idx="${idx}">加载</button>
      </span>
      <span class="col-path">
        <input type="text" class="path-input" data-idx="${idx}" placeholder="默认安装路径" />
      </span>
      <span class="col-act">
        <button class="btn btn-primary btn-sm install-pkg" data-idx="${idx}">安装</button>
      </span>
    </div>
  `;
}

async function loadVersions(idx) {
  const pkg = state.searchResults[idx];
  if (!pkg) return;
  const select = document.querySelector(`.ver-select[data-idx="${idx}"]`);
  const btn = document.querySelector(`.load-ver[data-idx="${idx}"]`);
  if (!select || !btn) return;
  btn.disabled = true;
  btn.textContent = "...";
  try {
    const res = await fetch(`/api/versions?package=${encodeURIComponent(pkg.id)}`);
    const data = await res.json();
    select.innerHTML = `<option value="">最新版本</option>` +
      (data.versions || []).map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
    if (!(data.versions || []).length) {
      select.innerHTML = `<option value="">无可用版本信息</option>`;
    }
  } catch (err) {
    select.innerHTML = `<option value="">加载失败</option>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "加载";
  }
}

async function installPackage(idx) {
  const pkg = state.searchResults[idx];
  if (!pkg) return;
  const verSelect = document.querySelector(`.ver-select[data-idx="${idx}"]`);
  const pathInput = document.querySelector(`.path-input[data-idx="${idx}"]`);
  const installPath = pathInput ? pathInput.value.trim() : "";
  let packageId = pkg.id;
  let version = "";
  let source = pkg.source || "";
  const sel = verSelect ? verSelect.value : "";
  if (sel !== "") {
    const vi = parseInt(sel, 10);
    const variant = (pkg.variants || [])[vi];
    if (variant) {
      packageId = variant.id || pkg.id;
      version = variant.version || "";
      source = variant.source || source;
    }
  }

  showLogPanel();
  els.taskStatus.textContent = "正在安装...";

  try {
    const res = await fetch("/api/install/package", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        package_id: packageId,
        name: pkg.name || packageId,
        version,
        install_path: installPath,
        source,
      }),
    });
    const { task_id } = await res.json();
    connectLogs(task_id, () => {
      els.taskStatus.textContent = "安装完成";
      setTimeout(() => (els.taskStatus.textContent = ""), 6000);
    });
  } catch (err) {
    appendLog({ level: "error", message: "安装请求失败: " + String(err) });
    els.taskStatus.textContent = "";
  }
}

els.searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doSearch();
});
els.searchBtn.addEventListener("click", doSearch);

els.searchResults.addEventListener("click", (e) => {
  const loadBtn = e.target.closest(".load-ver");
  if (loadBtn) {
    loadVersions(Number(loadBtn.dataset.idx));
    return;
  }
  const installBtnEl = e.target.closest(".install-pkg");
  if (installBtnEl) {
    installPackage(Number(installBtnEl.dataset.idx));
    return;
  }
  const pageBtn = e.target.closest(".pager button");
  if (pageBtn) {
    const target = pageBtn.dataset.page;
    if (target === "prev" && state.searchPage > 1) state.searchPage -= 1;
    if (target === "next") state.searchPage += 1;
    fetchSearch();
    return;
  }
  const chip = e.target.closest(".suggestion-chip");
  if (chip) {
    els.searchInput.value = chip.dataset.kw;
    doSearch();
  }
});

/* ---------------- 预设安装 ---------------- */

async function startInstall() {
  if (!state.selected.size) return;
  const toolIds = [...state.selected];
  showLogPanel();
  els.taskStatus.textContent = "正在安装...";
  els.installBtn.disabled = true;

  try {
    const res = await fetch("/api/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_ids: toolIds }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const { task_id } = await res.json();
    connectLogs(task_id, () => {
      els.installBtn.disabled = false;
      els.taskStatus.textContent = "安装完成";
      setTimeout(() => (els.taskStatus.textContent = ""), 6000);
    });
  } catch (err) {
    appendLog({ level: "error", message: "安装请求失败: " + String(err) });
    els.installBtn.disabled = false;
    els.taskStatus.textContent = "";
  }
}

function connectLogs(taskId, onDone) {
  // 先断开上一次的日志连接；摘掉 onclose，避免旧任务的兜底轮询被误触发
  if (state.socket) {
    state.socket.onclose = null;
    state.socket.close();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${proto}://${location.host}/api/ws/logs?task_id=${encodeURIComponent(taskId)}`
  );
  state.socket = socket;
  state.currentTaskId = taskId;
  els.cancelTask.classList.remove("hidden");
  let finished = false;

  function finish() {
    if (finished) return;
    finished = true;
    state.currentTaskId = "";
    els.cancelTask.classList.add("hidden");
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close();
    }
    onDone();
    if (state.currentTab === "preset") loadTools(true);
  }

  socket.onopen = () => appendLog({ level: "info", message: "日志通道已连接，任务 ID: " + taskId });
  socket.onmessage = (event) => {
    const record = JSON.parse(event.data);
    if (record.task_id !== taskId) return;
    appendLog(record);
    if (record.event === "done") finish();
  };
  socket.onerror = () => appendLog({ level: "error", message: "日志通道连接失败" });
  socket.onclose = () => {
    if (finished) return;
    // 连接提前断开：轮询任务状态兜底，避免按钮一直卡在“安装中”
    let attempts = 0;
    const timer = setInterval(async () => {
      if (++attempts > 150) {
        clearInterval(timer);
        return;
      }
      try {
        const res = await fetch(`/api/status/${taskId}`);
        if (!res.ok) return;
        const task = await res.json();
        if (task.status === "done") {
          clearInterval(timer);
          finish();
        }
      } catch (err) {
        /* 后端暂时不可达，继续轮询 */
      }
    }, 2000);
  };
}

els.selectAll.addEventListener("click", toggleSelectAll);
els.clearAll.addEventListener("click", () => {
  state.selected.clear();
  renderTools();
});
els.installBtn.addEventListener("click", startInstall);
els.cancelTask.addEventListener("click", async () => {
  if (!state.currentTaskId) return;
  els.cancelTask.disabled = true;
  try {
    const res = await fetch(`/api/status/${state.currentTaskId}`, { method: "DELETE" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      appendLog({ level: "warn", message: data.detail || "无法取消当前任务" });
    }
  } catch (err) {
    appendLog({ level: "error", message: "取消失败: " + String(err) });
  } finally {
    els.cancelTask.disabled = false;
  }
});
els.closeLog.addEventListener("click", () => {
  els.logPanel.classList.add("hidden");
  if (state.socket) state.socket.close();
});

loadTools();
