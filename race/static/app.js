"use strict";
const $ = (id) => document.getElementById(id);
const escapeHTML = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const copy = (value) => JSON.parse(JSON.stringify(value));
const state = {user: null, items: [], sources: [], book: null, tab: null, grid: null, expanded: new Set(),
  saves: new Map(), loading: false, generation: 0};
const icons = {folders: "▱", workbooks: "▦", sheets: "▤", charts: "▥"};
const path = (item) => `/api/resources/${item.kind}/${encodeURIComponent(item.id)}`;
const byId = (id) => state.items.find((x) => x.id === id);
const editable = (item) => item && ["editor", "owner"].includes(item.role);
const owner = (item) => item?.role === "owner";
const handle = (fn) => async (...args) => { try { await fn(...args); } catch (err) { toast(err.message, true); } };

async function api(url, options = {}) {
  const headers = {"Content-Type": "application/json"};
  const response = await fetch(url, {...options, headers: {...headers, ...options.headers}, cache: "no-store"});
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((e) => e.msg).join("; ") : data.detail;
    const error = new Error(detail || "Unable to complete this request");
    error.status = response.status;
    throw error;
  }
  return data;
}
function toast(message, error = false) {
  $("toast").textContent = message; $("toast").className = `toast${error ? " error" : ""}`; $("toast").hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { $("toast").hidden = true; }, 4800);
}
function showError(message) { $("content-error").textContent = message; $("content-error").hidden = !message; }
function updateItem(item) {
  const index = state.items.findIndex((x) => x.id === item.id);
  if (index < 0) state.items.push(item); else state.items[index] = item;
  if (state.book?.id === item.id) state.book = item;
  if (state.tab?.id === item.id) state.tab = item;
}
function saveStatus(text, error = false) { $("save-status").textContent = text; $("save-status").classList.toggle("error", error); }
function scheduleSave(item, config) {
  if (!editable(item)) return;
  let entry = state.saves.get(item.id);
  if (!entry) { entry = {item, pending: null, timer: null, running: null, failed: false}; state.saves.set(item.id, entry); }
  entry.pending = copy(config); entry.failed = false;
  item.config = copy(config); saveStatus("Unsaved changes");
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => { drainSave(entry).catch((err) => toast(err.message, true)); }, 700);
}
async function drainSave(entry) {
  clearTimeout(entry.timer);
  if (entry.running) { await entry.running; if (entry.pending && !entry.failed) return drainSave(entry); return; }
  if (!entry.pending) return;
  const config = entry.pending; entry.pending = null;
  const current = byId(entry.item.id) || entry.item;
  saveStatus("Saving…");
  entry.running = (async () => {
    try {
      const updated = await api(path(current), {method: "PATCH", body: JSON.stringify({version: current.version, config})});
      if (entry.pending) updated.config = copy(entry.pending);
      updateItem(updated); entry.item = updated; entry.failed = false; saveStatus("All changes saved");
    } catch (err) {
      entry.pending ||= config; entry.failed = true; saveStatus("Save failed · reload", true);
      showError(`${err.message} Your unsaved changes are still in this session. Use Refresh to reload the saved version.`);
      throw err;
    } finally { entry.running = null; }
  })();
  await entry.running;
  if (entry.pending) await drainSave(entry);
}
async function flushSaves() {
  for (const entry of state.saves.values()) {
    if (entry.failed) throw new Error("Resolve the failed save with Refresh before leaving this sheet.");
    await drainSave(entry);
  }
}
window.addEventListener("beforeunload", (event) => {
  if ([...state.saves.values()].some((s) => s.pending || s.running)) { event.preventDefault(); event.returnValue = ""; }
});

function dialog(title, html, onSubmit, button = "Save") {
  $("dialog-title").textContent = title; $("dialog-body").innerHTML = html;
  $("dialog-submit").textContent = button; $("dialog-submit").hidden = !onSubmit;
  $("dialog-error").hidden = true; $("dialog-submit").disabled = false;
  $("dialog-form").onsubmit = async (event) => {
    event.preventDefault(); if (!onSubmit) return;
    $("dialog-submit").disabled = true; $("dialog-error").hidden = true;
    try { if (await onSubmit(new FormData($("dialog-form"))) !== false) $("dialog").close(); }
    catch (err) { $("dialog-error").textContent = err.message; $("dialog-error").hidden = false; }
    finally { $("dialog-submit").disabled = false; }
  };
  if (!$("dialog").open) $("dialog").showModal();
}
function field(label, name, value = "", options = {}) {
  const {type = "text", required = true, hint = ""} = options;
  return `<div class="field"><label for="f-${name}">${escapeHTML(label)}</label><input id="f-${name}" name="${name}" type="${type}" value="${escapeHTML(value)}" ${required ? "required" : ""} maxlength="${name === "expression" ? 500 : 120}">${hint ? `<small>${escapeHTML(hint)}</small>` : ""}</div>`;
}
function selectField(label, name, options, selected = "") {
  return `<div class="field"><label for="f-${name}">${escapeHTML(label)}</label><select id="f-${name}" name="${name}">${options.map(([value, text]) => `<option value="${escapeHTML(value)}" ${value === selected ? "selected" : ""}>${escapeHTML(text)}</option>`).join("")}</select></div>`;
}
function folderOptions(item = null) {
  const descendants = new Set(item ? [item.id] : []);
  for (let i = 0; i < 32; i++) state.items.filter((x) => descendants.has(x.parent_id)).forEach((x) => descendants.add(x.id));
  return [["", "My Workspace (root)"], ...state.items.filter((x) => x.kind === "folders" && editable(x) && !descendants.has(x.id)
    && (!item || x.owner_id === item.owner_id)).map((x) => [x.id, x.name])];
}
async function loadTree() { state.items = await api("/api/tree"); renderTree(); renderCards(); }
function renderTree() {
  const containers = state.items.filter((x) => ["folders", "workbooks"].includes(x.kind));
  const roots = containers.filter((x) => !byId(x.parent_id));
  const shared = roots.filter((x) => x.owner_id !== state.user.id);
  $("shared-count").textContent = shared.length;
  for (const [id, nodes] of [["my-tree", roots.filter((x) => x.owner_id === state.user.id)], ["shared-tree", shared]]) {
    $(id).replaceChildren();
    if (!nodes.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = id === "my-tree" ? "Your next idea starts here." : "Shared workbooks will appear here."; $(id).append(p); }
    nodes.forEach((x) => $(id).append(treeNode(x)));
  }
}
function treeNode(item) {
  const wrapper = document.createElement("div");
  const row = document.createElement("div"); row.className = `tree-node${state.tab?.id === item.id || (!state.tab && state.book?.id === item.id) ? " active" : ""}`;
  const isContainer = ["folders", "workbooks"].includes(item.kind);
  const children = state.items.filter((x) => x.parent_id === item.id);
  const toggle = document.createElement("button"); toggle.className = "tree-toggle";
  toggle.textContent = isContainer ? (state.expanded.has(item.id) ? "⌄" : "›") : "";
  toggle.setAttribute("aria-label", `${state.expanded.has(item.id) ? "Collapse" : "Expand"} ${item.name}`);
  toggle.disabled = !isContainer; toggle.onclick = () => { state.expanded.has(item.id) ? state.expanded.delete(item.id) : state.expanded.add(item.id); renderTree(); };
  const button = document.createElement("button"); button.className = "tree-label"; button.title = item.name;
  button.innerHTML = `<span class="tree-icon">${icons[item.kind]}</span><span>${escapeHTML(item.name)}</span>`;
  button.onclick = handle(async () => {
    if (item.kind === "folders") { state.expanded.has(item.id) ? state.expanded.delete(item.id) : state.expanded.add(item.id); renderTree(); }
    else if (item.kind === "workbooks") await openBook(item.id);
    else await openBook(item.parent_id, item.id);
  });
  const more = document.createElement("button"); more.className = "tree-more"; more.textContent = "•••"; more.setAttribute("aria-label", `Actions for ${item.name}`);
  more.onclick = handle(() => resourceMenu(item)); row.append(toggle, button, more); wrapper.append(row);
  if (isContainer && state.expanded.has(item.id)) {
    const group = document.createElement("div"); group.className = "tree-child";
    children.forEach((child) => group.append(treeNode(child))); wrapper.append(group);
  }
  return wrapper;
}
function renderCards() {
  const books = state.items.filter((x) => x.kind === "workbooks");
  $("book-count").textContent = `${books.length} workbook${books.length === 1 ? "" : "s"}`;
  $("workbook-cards").innerHTML = books.length ? "" : '<p class="muted">Create a workbook to begin, or ask a teammate to share one.</p>';
  books.forEach((book) => {
    const card = document.createElement("button"); card.className = "workbook-card";
    const tabs = state.items.filter((x) => x.parent_id === book.id);
    card.innerHTML = `<span class="card-icon">▦</span><span class="card-arrow">↗</span><h3>${escapeHTML(book.name)}</h3><p>${tabs.filter((x) => x.kind === "sheets").length} sheets · ${tabs.filter((x) => x.kind === "charts").length} charts · ${escapeHTML(book.role)}</p>`;
    card.onclick = handle(() => openBook(book.id)); $("workbook-cards").append(card);
  });
}
async function home() {
  await flushSaves(); destroyGrid(); state.book = null; state.tab = null; state.generation++;
  $("home").hidden = false; $("workbook").hidden = true; $("breadcrumbs").textContent = "Workspace / Overview";
  renderTree(); renderCards();
}
async function openBook(id, tabId = null) {
  await flushSaves(); state.book = await api(`/api/resources/workbooks/${encodeURIComponent(id)}`); updateItem(state.book);
  state.expanded.add(id); if (state.book.parent_id) state.expanded.add(state.book.parent_id);
  $("home").hidden = true; $("workbook").hidden = false;
  renderBookHeader();
  const tabs = state.items.filter((x) => x.parent_id === id);
  await openTab(tabs.find((x) => x.id === tabId) || tabs[0] || null);
}
function renderBookHeader() {
  const book = state.book;
  $("book-title").textContent = book.name; $("role-pill").textContent = book.role;
  const tabs = state.items.filter((x) => x.parent_id === book.id);
  $("book-subtitle").textContent = `${tabs.filter((x) => x.kind === "sheets").length} sheets · ${tabs.filter((x) => x.kind === "charts").length} charts · A shared source of possibility`;
  const folder = byId(book.parent_id);
  $("breadcrumbs").innerHTML = `Workspace <span>/</span> ${escapeHTML(folder?.name || (book.owner_id === state.user.id ? "My Workspace" : "Shared with Me"))} <span>/</span> ${escapeHTML(book.name)}`;
  $("share-button").hidden = !owner(book);
  $("add-sheet").disabled = !editable(book); $("add-chart").disabled = !editable(book) || !tabs.some((x) => x.kind === "sheets");
  $("empty-add").hidden = !editable(book);
}
function renderTabs() {
  $("tabs").replaceChildren();
  state.items.filter((x) => x.parent_id === state.book.id).forEach((tab) => {
    const button = document.createElement("button"); button.className = `tab${tab.id === state.tab?.id ? " active" : ""}`;
    button.innerHTML = `<span>${icons[tab.kind]}</span>${escapeHTML(tab.name)}`;
    button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(tab.id === state.tab?.id));
    button.onclick = handle(() => openTab(tab)); $("tabs").append(button);
  });
}
function destroyGrid() { if (state.grid) { state.grid.destroy(); state.grid = null; } }
async function openTab(tab) {
  await flushSaves(); destroyGrid(); state.generation++; showError("");
  state.tab = tab ? (byId(tab.id) || tab) : null;
  renderTabs(); renderTree();
  $("empty-workbook").hidden = !!tab;
  $("sheet-view").hidden = !tab || tab.kind !== "sheets";
  $("chart-view").hidden = !tab || tab.kind !== "charts";
  for (const id of ["filter-button", "columns-button", "calculation-button", "tab-menu"]) $(id).disabled = !tab;
  $("columns-button").hidden = tab?.kind === "charts"; $("calculation-button").hidden = tab?.kind === "charts";
  $("calculation-button").disabled = !editable(tab); $("columns-button").disabled = !editable(tab);
  $("filter-button").disabled = !editable(tab); $("edit-chart").hidden = !editable(tab);
  $("filter-chips").hidden = true; $("row-status").textContent = "Ready"; $("column-status").textContent = "";
  saveStatus(editable(tab) ? "All changes saved" : "View access");
  if (!tab) return;
  renderFilterChips();
  if (tab.kind === "sheets") await renderSheet(); else await renderChart();
}
function renderFilterChips() {
  const filters = state.tab?.config.filters || [];
  $("filter-count").textContent = filters.length ? `(${filters.length})` : "";
  $("filter-chips").hidden = !filters.length;
  $("filter-chips").innerHTML = filters.map((f) => `<span class="chip">${escapeHTML(f.column)} ${escapeHTML(f.op.replaceAll("_", " "))} ${escapeHTML(f.value ?? "")}</span>`).join("");
}
function columnFormatter(name, config) {
  return (cell) => {
    const value = cell.getValue(); const el = document.createElement("span");
    if (value === null || value === undefined) { el.textContent = "—"; el.style.color = "#bdc5b5"; return el; }
    const format = config.formats?.[name];
    if (format && ["number", "currency", "percent"].includes(format.type) && !Number.isNaN(Number(value))) {
      el.textContent = new Intl.NumberFormat("en-US", {style: format.type === "number" ? "decimal" : format.type,
        ...(format.type === "currency" ? {currency: "USD"} : {}), minimumFractionDigits: format.decimals ?? 2, maximumFractionDigits: format.decimals ?? 2}).format(Number(value));
    } else if (format?.type === "date" && !Number.isNaN(Date.parse(value))) el.textContent = new Date(value).toLocaleDateString("en-US", {timeZone: "UTC"});
    else el.textContent = typeof value === "object" ? JSON.stringify(value) : String(value);
    return el;
  };
}
async function renderSheet() {
  const tab = state.tab, config = tab.config, generation = state.generation;
  const source = state.sources.find((s) => s.id === config.source_id);
  $("source-label").textContent = source?.name || "Shared dataset";
  const names = config.columns?.length ? config.columns : [...(source?.columns.map((c) => c.name) || []), ...(config.calculations || []).map((c) => c.name)];
  if (!names.length) {
    try { const result = await api(`/api/sheets/${tab.id}/query`, {method:"POST", body:JSON.stringify({page:1,page_size:100})}); names.push(...result.columns); }
    catch (err) { showError(err.message); return; }
  }
  if (generation !== state.generation) return;
  if (!window.Tabulator) { showError("The spreadsheet assets are missing. Run scripts/vendor_assets.py and reload."); return; }
  state.loading = true;
  let built = false;
  const grid = new Tabulator("#grid", {
    height: "100%", layout: "fitDataStretch", nestedFieldSeparator: false, placeholder: "No rows match your filters.",
    movableColumns: editable(tab), resizableColumnFit: false, selectableRange: 1, selectableRangeRows: true,
    selectableRangeColumns: false, selectableRangeClearCells: false, clipboard: "copy", clipboardCopyRowRange: "range",
    clipboardCopyConfig: {rowHeaders: false, columnHeaders: false},
    rowHeader: {formatter:"rownum", headerSort:false, hozAlign:"center", resizable:false, frozen:true, width:48},
    columns: names.map((name) => ({title: escapeHTML(name), field: name, headerSort: true,
      width: config.column_widths?.[name] || (name === "product" ? 155 : 135), minWidth: 65,
      visible: !(config.hidden_columns || []).includes(name), formatter: columnFormatter(name, config)})),
    pagination: true, paginationMode: "remote", paginationSize: 100, paginationSizeSelector: [25, 50, 100, 250],
    sortMode: "remote", initialSort: (config.sorts || []).map((s) => ({column:s.column,dir:s.direction})),
    ajaxURL: "/grid", ajaxRequestFunc: async (_url, _options, params) => {
      const sorts = (params.sorters || []).map((s) => ({column: s.field, direction:s.dir}));
      const liveTab = byId(tab.id) || tab;
      if (built && editable(liveTab) && JSON.stringify(sorts) !== JSON.stringify(liveTab.config.sorts || [])) scheduleSave(liveTab, {...liveTab.config, sorts});
      try {
        $("row-status").textContent = "Loading data…";
        const data = await api(`/api/sheets/${encodeURIComponent(tab.id)}/query`, {method:"POST", body:JSON.stringify({page:params.page || 1,page_size:params.size || 100,sorts,filters:liveTab.config.filters || []})});
        if (generation !== state.generation) return {data:[],last_page:1};
        const start = data.total ? ((params.page || 1)-1)*(params.size || 100)+1 : 0;
        $("row-status").textContent = `${start.toLocaleString()}–${(start ? start + data.data.length - 1 : 0).toLocaleString()} of ${data.total.toLocaleString()} rows`;
        $("column-status").textContent = `${names.length - (config.hidden_columns || []).filter((c) => names.includes(c)).length} columns`;
        showError(""); return data;
      } catch (err) { if (generation === state.generation) { showError(err.message); $("row-status").textContent = "Query failed"; } return {data:[],last_page:1}; }
    }
  });
  state.grid = grid;
  grid.on("tableBuilt", () => { built = true; state.loading = false; });
  grid.on("cellClick", (_e, cell) => {
    const value = cell.getValue(); $("cell-address").textContent = cell.getField();
    $("cell-value").textContent = value === null ? "NULL" : typeof value === "object" ? JSON.stringify(value) : String(value ?? "");
  });
  grid.on("columnMoved", () => {
    if (!built || !editable(state.tab)) return;
    const names = grid.getColumns().map((c) => c.getField()).filter(Boolean);
    scheduleSave(state.tab, {...state.tab.config, columns:names});
  });
  grid.on("columnResized", (column) => {
    if (!built || !editable(state.tab) || !column.getField()) return;
    scheduleSave(state.tab, {...state.tab.config, column_widths:{...state.tab.config.column_widths,[column.getField()]:Math.round(column.getWidth())}});
  });
}
async function renderChart() {
  const tab = state.tab, cfg = tab.config, generation = state.generation;
  const sheet = byId(cfg.sheet_id), source = state.sources.find((s) => s.id === sheet?.config.source_id);
  $("source-label").textContent = source?.name || "Shared dataset";
  $("chart-title").textContent = tab.name;
  $("chart-description").textContent = `${cfg.aggregation} of ${cfg.measure || "rows"} by ${cfg.dimension} · ${sheet?.name || "Source sheet"}`;
  $("plot").replaceChildren(); $("row-status").textContent = "Loading chart…";
  try {
    const result = await api(`/api/charts/${encodeURIComponent(tab.id)}/data`);
    if (generation !== state.generation) return;
    if (!window.Plotly) throw new Error("Chart assets are missing. Run scripts/vendor_assets.py and reload.");
    const labels = result.data.map((r) => r.dimension ?? "(empty)"), values = result.data.map((r) => r.value);
    const palette = ["#3c7252", "#7fa364", "#c3d794", "#a3bc7b", "#5d8565", "#d6e2bb"];
    const trace = cfg.type === "pie" ? {type:"pie",labels,values,hole:.5,textinfo:"label+percent",marker:{colors:palette}} :
      {type:cfg.type === "bar" ? "bar" : "scatter",x:labels,y:values,mode:cfg.type === "line" ? "lines+markers" : "markers",
       marker:{color:cfg.type === "bar" ? palette : palette[0],size:9,line:{width:0}},line:{color:palette[0],width:3},hovertemplate:"%{x}<br>%{y:,.2f}<extra></extra>"};
    // Plotly treats some text fields as HTML; encode user-controlled titles/labels.
    if (trace.x) trace.x = trace.x.map((v) => typeof v === "string" ? escapeHTML(v) : v);
    if (trace.labels) trace.labels = trace.labels.map(escapeHTML);
    await Plotly.newPlot("plot", [trace], {paper_bgcolor:"#fff",plot_bgcolor:"#fff",font:{family:"-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",color:"#89967c",size:10},
      margin:{l:70,r:25,t:25,b:70},bargap:.5,showlegend:false,
      xaxis:{showgrid:false,zeroline:false,tickfont:{size:11},title:{text:escapeHTML(cfg.dimension),font:{size:10}}},
      yaxis:{gridcolor:"#edf1e7",zeroline:false,tickformat:",.0f",title:{text:escapeHTML(cfg.measure || "Count"),font:{size:10}}}},
      {responsive:true,displaylogo:false,modeBarButtonsToRemove:["select2d","lasso2d"]});
    $("row-status").textContent = `${result.data.length} groups · Complete filtered dataset`; $("column-status").textContent = cfg.type;
    if (!result.data.length) showError("No data matches the sheet and chart filters.");
  } catch (err) { if (generation === state.generation) { showError(err.message); $("row-status").textContent = "Chart unavailable"; } }
}

async function createContainer(kind, parentId = "") {
  await flushSaves();
  dialog(kind === "folders" ? "Create a folder" : "Create a workbook", field("Name", "name") + selectField("Location", "parent", folderOptions(), parentId), async (form) => {
    const result = await api(`/api/resources/${kind}`, {method:"POST",body:JSON.stringify({name:form.get("name"),parent_id:form.get("parent") || null})});
    await loadTree(); if (result.parent_id) state.expanded.add(result.parent_id);
    if (kind === "workbooks") await openBook(result.id); else { state.expanded.add(result.id); renderTree(); }
    toast(kind === "folders" ? "Folder created" : "Your workbook is ready");
  }, "Create");
}
async function createSheet() {
  await flushSaves();
  state.sources = await api("/api/sources");
  const html = field("Sheet name", "name") + selectField("Shared dataset", "source", state.sources.map((s) => [s.id, `${s.name} · ${s.table}`])) +
    '<p class="dialog-hint">Explore live, read-only data. Filters, calculations, and formatting are saved with your sheet.</p>' +
    '<button type="button" id="register-source" class="secondary" style="margin-top:15px">＋ Connect a Unity Catalog table</button>';
  dialog("Add a sheet", html, async (form) => {
    if (!form.get("source")) throw new Error("Connect a dataset first.");
    const result = await api("/api/resources/sheets",{method:"POST",body:JSON.stringify({name:form.get("name"),parent_id:state.book.id,config:{source_id:form.get("source")}})});
    await loadTree(); renderBookHeader(); await openTab(result); toast("Sheet created");
  }, "Create sheet");
  if ($("register-source")) $("register-source").onclick = () => {
    dialog("Connect a shared dataset", field("Display name","name") + field("Unity Catalog table or view","table","",{hint:"Enter catalog.schema.table. Your Databricks permissions determine access."}), async (form) => {
      await api("/api/sources",{method:"POST",body:JSON.stringify({name:form.get("name"),table:form.get("table")})});
      $("dialog").close(); await createSheet(); return false;
    }, "Connect dataset");
  };
}
function sheetColumns(sheet) {
  const source = state.sources.find((s) => s.id === sheet.config.source_id);
  return [...new Set([...(source?.columns || []).map((c) => c.name), ...(sheet.config.columns || []), ...(sheet.config.calculations || []).map((c) => c.name)])];
}
async function chartDialog(existing = null) {
  await flushSaves();
  const sheets = state.items.filter((x) => x.kind === "sheets" && x.parent_id === state.book.id);
  if (!sheets.length) throw new Error("Add a sheet before creating a chart.");
  const cfg = existing?.config || {sheet_id:state.tab?.kind === "sheets" ? state.tab.id : sheets[0].id,type:"bar",aggregation:"SUM"};
  dialog(existing ? "Edit chart" : "Create a chart", field("Chart title","name",existing?.name || "") +
    selectField("Source sheet","sheet",sheets.map((s) => [s.id,s.name]),cfg.sheet_id) +
    '<div class="field-row">' + selectField("Chart type","type",[["bar","Bar chart"],["line","Line chart"],["scatter","Scatter plot"],["pie","Donut / pie chart"]],cfg.type) +
    selectField("Aggregation","aggregation",["SUM","AVG","COUNT","MIN","MAX"].map((s) => [s,s]),cfg.aggregation) + '</div><div id="chart-fields"></div>' +
    '<p class="dialog-hint">Chart results include every matching row, including rows beyond the current sheet page.</p>', async (form) => {
      const config = {sheet_id:form.get("sheet"),type:form.get("type"),aggregation:form.get("aggregation"),dimension:form.get("dimension"),measure:form.get("measure") || null,filters:existing?.config.filters || []};
      const result = await api(existing ? path(existing) : "/api/resources/charts", {method:existing ? "PATCH" : "POST",body:JSON.stringify({name:form.get("name"),config,...(existing ? {version:existing.version} : {parent_id:state.book.id})})});
      await loadTree(); renderBookHeader(); await openTab(result); toast("Chart saved");
    }, existing ? "Save chart" : "Create chart");
  function fields() {
    const columns = sheetColumns(sheets.find((s) => s.id === $("f-sheet").value));
    $("chart-fields").innerHTML = '<div class="field-row">' + selectField("Dimension","dimension",columns.map((c) => [c,c]),cfg.dimension) +
      selectField("Measure","measure",[["","All rows (COUNT only)"],...columns.map((c) => [c,c])],cfg.measure ?? columns.find((c) => ["revenue","units","budget"].includes(c)) ?? "") + '</div>';
  }
  $("f-sheet").onchange = fields; fields();
}
async function filterDialog() {
  await flushSaves();
  const tab = state.tab, sheet = tab.kind === "sheets" ? tab : byId(tab.config.sheet_id), columns = sheetColumns(sheet);
  const filters = copy(tab.config.filters || []);
  dialog("Filter your data", '<p class="dialog-hint">All conditions must match. These filters are saved for everyone with access to this item.</p><div id="filter-rows" style="margin-top:17px"></div><button type="button" id="add-filter" class="secondary">＋ Add condition</button>', async () => {
    const rows = [...$("filter-rows").querySelectorAll(".filter-row")].map((el) => {
      let value = el.querySelector("input").value;
      // Exact numeric comparisons use numbers; text identifiers (including leading zeroes) remain strings.
      if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)) value = Number(value);
      return {column:el.querySelector(".filter-column").value,op:el.querySelector(".filter-op").value,value};
    });
    scheduleSave(tab,{...tab.config,filters:rows}); await flushSaves(); await openTab(byId(tab.id)); toast("Filters applied");
  }, "Apply filters");
  function add(f = {column:columns[0],op:"eq",value:""}) {
    const row = document.createElement("div"); row.className = "filter-row";
    row.innerHTML = `<select class="filter-column" aria-label="Filter column">${columns.map((c) => `<option ${c === f.column ? "selected" : ""}>${escapeHTML(c)}</option>`).join("")}</select><select class="filter-op" aria-label="Filter operator">${[["eq","equals"],["ne","not equal"],["contains","contains"],["gt",">"],["gte","≥"],["lt","<"],["lte","≤"],["is_null","is empty"],["not_null","not empty"]].map(([v,t]) => `<option value="${v}" ${v === f.op ? "selected" : ""}>${escapeHTML(t)}</option>`).join("")}</select><input aria-label="Filter value" value="${escapeHTML(f.value ?? "")}"><button type="button" aria-label="Remove condition">×</button>`;
    row.querySelector("button").onclick = () => row.remove(); $("filter-rows").append(row);
  }
  filters.forEach(add); $("add-filter").onclick = () => add();
}
async function columnsDialog() {
  await flushSaves();
  const tab = state.tab, all = sheetColumns(tab), ordered = [...new Set([...(tab.config.columns || []),...all])];
  dialog("Columns & formatting", '<p class="dialog-hint">Choose visible columns, set formats, and arrange their order. Drag column headers to reorder directly in the sheet.</p><div id="column-options"></div>', async () => {
    const rows = [...$("column-options").children], formats = {...tab.config.formats};
    rows.forEach((row) => { formats[row.dataset.column] = {type:row.querySelector("select").value,decimals:2}; });
    const hidden = rows.filter((r) => !r.querySelector("input").checked).map((r) => r.dataset.column);
    if (hidden.length === rows.length) throw new Error("Keep at least one column visible.");
    scheduleSave(tab,{...tab.config,columns:rows.map((r) => r.dataset.column),hidden_columns:hidden,formats});
    await flushSaves(); await openTab(byId(tab.id)); toast("Column layout saved");
  }, "Apply changes");
  ordered.forEach((name) => {
    const row = document.createElement("div"); row.className = "column-option"; row.dataset.column = name;
    row.innerHTML = `<input type="checkbox" aria-label="Show ${escapeHTML(name)}" ${(tab.config.hidden_columns || []).includes(name) ? "" : "checked"}><label>${escapeHTML(name)}</label><select aria-label="Format ${escapeHTML(name)}">${["text","number","currency","percent","date"].map((f) => `<option value="${f}" ${tab.config.formats?.[name]?.type === f ? "selected" : ""}>${f === "currency" ? "currency (USD)" : f}</option>`).join("")}</select><button type="button" title="Move up" aria-label="Move ${escapeHTML(name)} up">↑</button><button type="button" title="Move down" aria-label="Move ${escapeHTML(name)} down">↓</button>`;
    const [up,down] = row.querySelectorAll("button");
    up.onclick = () => { if (row.previousElementSibling) row.parentNode.insertBefore(row,row.previousElementSibling); };
    down.onclick = () => { if (row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling,row); };
    $("column-options").append(row);
  });
}
async function calculationDialog() {
  await flushSaves(); const tab = state.tab; let calculations = copy(tab.config.calculations || []);
  dialog("Calculated columns", '<p class="dialog-hint">Use column names and + − * /. Functions: abs(), round(), col("Column name"). Calculations reference source columns.</p><div id="calculations" class="calc-list"></div>' +
    field("New column name","name","",{required:false,hint:"Letters, numbers, and underscores. For example: margin"}) +
    field("Expression","expression","",{required:false,hint:"Example: round((revenue - cost) / revenue * 100, 2)"}), async (form) => {
      const name = String(form.get("name")).trim(), expression = String(form.get("expression")).trim();
      if (name || expression) { if (!name || !expression) throw new Error("Enter both a name and an expression."); calculations.push({name,expression}); }
      const available = new Set([...(state.sources.find((s) => s.id === tab.config.source_id)?.columns || []).map((c) => c.name), ...calculations.map((c) => c.name)]);
      const config = {...tab.config,calculations,columns:[...new Set([...(tab.config.columns || []).filter((c) => available.has(c)),...calculations.map((c) => c.name)])],
        hidden_columns:(tab.config.hidden_columns || []).filter((c) => available.has(c)),
        formats:Object.fromEntries(Object.entries(tab.config.formats || {}).filter(([k]) => available.has(k))),
        column_widths:Object.fromEntries(Object.entries(tab.config.column_widths || {}).filter(([k]) => available.has(k)))};
      if (!(tab.config.columns || []).length) config.columns = [...available];
      // Validate once synchronously so an invalid expression remains in the dialog for correction.
      const result = await api(path(tab),{method:"PATCH",body:JSON.stringify({version:tab.version,config})});
      updateItem(result); await openTab(result); toast("Calculations saved");
    }, "Save calculations");
  function render() {
    $("calculations").innerHTML = calculations.map((c,i) => `<div><strong>${escapeHTML(c.name)}</strong><code>${escapeHTML(c.expression)}</code><button type="button" data-index="${i}">Remove</button></div>`).join("");
    $("calculations").querySelectorAll("button").forEach((b) => { b.onclick = () => { calculations.splice(Number(b.dataset.index),1); render(); }; });
  }
  render();
}

async function shareDialog(item) {
  await flushSaves(); const grants = await api(`${path(item)}/permissions`); item = byId(item.id) || item;
  const html = '<p class="dialog-hint">Viewers can explore and clone. Editors can change sheets and charts. Dataset permissions still apply.</p><div id="share-list"></div>' +
    field("Databricks user ID","principal","",{hint:"Use the stable Databricks user ID shown by /api/me, not an email address."}) +
    selectField("Permission","role",[["viewer","Viewer — explore and clone"],["editor","Editor — edit sheets and charts"]]);
  dialog(`Share ${item.name}`, html, async (form) => {
    const updated = await api(`${path(item)}/permissions`, {method:"PUT",body:JSON.stringify({principal_id:form.get("principal"),role:form.get("role"),version:grants.version})});
    updateItem(updated); toast("Sharing updated");
  }, "Share");
  [...grants.direct.map((g) => ({...g,inherited:false})),...grants.inherited.map((g) => ({...g,inherited:true}))].forEach((g) => {
    const row = document.createElement("div"); row.className = "share-row";
    row.innerHTML = `<span>${escapeHTML(g.principal_id)}</span><small>${escapeHTML(g.role)}${g.inherited ? " · inherited" : ""}</small>`;
    if (!g.inherited) {
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove"; remove.className = "danger";
      remove.onclick = handle(async () => {
        const updated = await api(`${path(item)}/permissions/${encodeURIComponent(g.principal_id)}?version=${grants.version}`,{method:"DELETE"});
        updateItem(updated); await shareDialog(updated); toast("Direct sharing removed");
      }); row.append(remove);
    }
    $("share-list").append(row);
  });
}
async function cloneDialog(book) {
  await flushSaves(); const key = crypto.randomUUID();
  dialog("Make it your own", '<p class="dialog-hint">Create an independent copy of this workbook, including its sheets, charts, and settings. Sharing permissions are not copied. Source data stays connected.</p>' + field("Workbook name","name",`${book.name} (copy)`) + selectField("Destination folder","folder",folderOptions()), async (form) => {
    const result = await api(`/api/workbooks/${book.id}/clone`,{method:"POST",body:JSON.stringify({name:form.get("name"),folder_id:form.get("folder") || null,idempotency_key:key})});
    await loadTree(); await openBook(result.id); toast("Your independent copy is ready");
  }, "Clone workbook");
}
async function renameDialog(item) {
  await flushSaves(); item = byId(item.id) || item;
  dialog("Rename item", field("Name","name",item.name), async (form) => {
    const result = await api(path(item),{method:"PATCH",body:JSON.stringify({version:item.version,name:form.get("name")})});
    updateItem(result); renderTree(); renderCards();
    if (state.book) { renderBookHeader(); renderTabs(); if (state.tab?.id === item.id && item.kind === "charts") $("chart-title").textContent = result.name; }
    toast("Renamed");
  });
}
async function moveDialog(item) {
  await flushSaves();
  dialog("Move item", selectField("Destination","folder",folderOptions(item),item.parent_id || "") + '<p class="dialog-hint">Direct shares remain. Inherited access changes to match the destination folder.</p>', async (form) => {
    const result = await api(path(item),{method:"PATCH",body:JSON.stringify({version:item.version,parent_id:form.get("folder") || null})});
    updateItem(result); await loadTree(); if (state.book) { state.book = byId(state.book.id); if (state.book) renderBookHeader(); else await home(); } toast("Moved");
  }, "Move");
}
async function deleteDialog(item) {
  await flushSaves();
  dialog("Delete this item?", `<p class="dialog-hint">${escapeHTML(item.name)} will be removed from the workspace.${["folders","workbooks"].includes(item.kind) ? " All contents will also become inaccessible." : ""} The underlying source data is preserved.</p>`, async () => {
    await api(`${path(item)}?version=${item.version}`,{method:"DELETE"}); await loadTree();
    if (state.book) { const book = byId(state.book.id); if (!book) await home(); else await openBook(book.id,state.tab?.id); }
    toast("Item deleted");
  }, "Delete");
}
async function resourceMenu(item) {
  await flushSaves(); item = byId(item.id) || item;
  const actions = [];
  if (editable(item)) actions.push(["Rename",() => renameDialog(item)]);
  if (item.kind === "folders" && editable(item)) actions.push(["New subfolder",() => createContainer("folders",item.id)],["New workbook here",() => createContainer("workbooks",item.id)]);
  if (["folders","workbooks"].includes(item.kind) && owner(item)) actions.push(["Move to folder",() => moveDialog(item)],["Manage sharing",() => shareDialog(item)]);
  if (item.kind === "workbooks") actions.push(["Clone to my workspace",() => cloneDialog(item)]);
  if (item.kind === "charts" && editable(item)) actions.push(["Edit chart",() => chartDialog(item)]);
  if (owner(item) || (editable(item) && ["sheets","charts"].includes(item.kind))) actions.push(["Delete item",() => deleteDialog(item)]);
  dialog(item.name, '<div class="menu-actions" id="menu-actions"></div>', null);
  if (!actions.length) $("menu-actions").textContent = "You have view access to this item.";
  actions.forEach(([label,fn]) => { const button = document.createElement("button"); button.type = "button"; button.textContent = label; if (label === "Delete item") button.className = "danger"; button.onclick = handle(fn); $("menu-actions").append(button); });
}
async function refresh() {
  const tabId = state.tab?.id, bookId = state.book?.id;
  for (const entry of state.saves.values()) {
    if (entry.failed) {
      dialog("Reload saved changes?", '<p class="dialog-hint">This will discard the unsaved changes from the failed save and load the latest version.</p>', async () => {
        for (const s of state.saves.values()) clearTimeout(s.timer); state.saves.clear(); showError("");
        await loadTree(); if (bookId && byId(bookId)) await openBook(bookId,tabId); else await home();
      }, "Reload saved version"); return;
    }
  }
  await flushSaves(); await loadTree(); state.sources = await api("/api/sources");
  if (bookId && byId(bookId)) await openBook(bookId,tabId); else await home(); toast("Up to date");
}
async function boot() {
  try {
    state.user = await api("/api/me"); $("notice").hidden = true;
    $("profile-name").textContent = state.user.name;
    $("avatar").textContent = state.user.name.split(" ").map((s) => s[0]).slice(0,2).join("");
    await loadTree();
    try { state.sources = await api("/api/sources"); } catch (err) { state.sources = []; toast(err.message,true); }
    const first = state.items.find((x) => x.kind === "workbooks");
    if (first) await openBook(first.id); else await home();
  } catch (err) {
    $("notice").textContent = err.message; $("notice").hidden = false;
    $("my-tree").textContent = "Workspace unavailable";
    for (const id of ["new-workbook","new-folder"]) $(id).disabled = true;
  }
}
$("dialog-close").onclick = $("dialog-cancel").onclick = () => $("dialog").close();
$("home-button").onclick = handle(home);
$("new-workbook").onclick = $("home-new").onclick = handle(() => createContainer("workbooks"));
$("new-folder").onclick = handle(() => createContainer("folders"));
$("add-sheet").onclick = $("empty-add").onclick = handle(createSheet);
$("add-chart").onclick = handle(() => chartDialog());
$("edit-chart").onclick = handle(() => chartDialog(state.tab));
$("share-button").onclick = handle(() => shareDialog(state.book));
$("clone-button").onclick = handle(() => cloneDialog(state.book));
$("book-menu").onclick = handle(() => resourceMenu(state.book));
$("tab-menu").onclick = handle(() => resourceMenu(state.tab));
$("filter-button").onclick = handle(filterDialog);
$("columns-button").onclick = handle(columnsDialog);
$("calculation-button").onclick = handle(calculationDialog);
$("refresh-button").onclick = handle(refresh);
boot();
