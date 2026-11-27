/* 事项管理前端逻辑：原生 JS + fetch，无外部依赖。
 * XSS 防护：所有用户文本（标题、错误消息）一律经 textContent / createTextNode
 * 渲染，绝不拼入 innerHTML。
 */
(function () {
  "use strict";

  var STATUS_LABELS = { todo: "待办", in_progress: "进行中", done: "完成" };
  var PRIORITY_LABELS = { low: "低", medium: "中", high: "高" };
  var PAGE_SIZE = 10;

  var state = { page: 1, totalPages: 0, total: 0, keyword: "", status: "" };

  function $(id) { return document.getElementById(id); }

  var els = {
    list: $("item-list"),
    empty: $("empty-state"),
    loading: $("loading"),
    successMsg: $("success-msg"),
    errorMsg: $("error-msg"),
    pageInfo: $("page-info"),
    prev: $("prev-page"),
    next: $("next-page"),
    createForm: $("create-form"),
    newTitle: $("new-title"),
    newPriority: $("new-priority"),
    searchKeyword: $("search-keyword"),
    filterStatus: $("filter-status"),
    overlay: $("edit-overlay"),
    editForm: $("edit-form"),
    editId: $("edit-id"),
    editTitle: $("edit-title"),
    editStatus: $("edit-status"),
    editPriority: $("edit-priority"),
    editCancel: $("edit-cancel-btn")
  };

  var editingId = null;
  var pending = 0;

  function setLoading(on) {
    pending += on ? 1 : -1;
    if (pending < 0) pending = 0;
    els.loading.classList.toggle("hidden", pending === 0);
  }

  var successTimer = null;

  function showSuccess(text) {
    els.successMsg.textContent = text; // textContent：按文字显示
    els.successMsg.classList.remove("hidden");
    els.errorMsg.classList.add("hidden");
    // 成功提示 3 秒后自动消失（修复 DEF-1：提示不再被 refresh 立即清除）
    if (successTimer !== null) clearTimeout(successTimer);
    successTimer = setTimeout(function () {
      successTimer = null;
      els.successMsg.classList.add("hidden");
    }, 3000);
  }

  function showError(text) {
    els.errorMsg.textContent = text; // textContent：按文字显示
    els.errorMsg.classList.remove("hidden");
    els.successMsg.classList.add("hidden");
    if (successTimer !== null) { clearTimeout(successTimer); successTimer = null; }
  }

  function clearMessages() {
    els.successMsg.classList.add("hidden");
    els.errorMsg.classList.add("hidden");
    if (successTimer !== null) { clearTimeout(successTimer); successTimer = null; }
  }

  function api(path, options) {
    setLoading(true);
    return fetch(path, options)
      .then(function (resp) {
        setLoading(false);
        if (resp.status === 204) return { ok: true, data: null };
        return resp.json().then(function (data) {
          if (!resp.ok) {
            var e = new Error(
              (data && data.error && data.error.message) || "请求失败（HTTP " + resp.status + "）"
            );
            e.code = data && data.error && data.error.code;
            throw e;
          }
          return { ok: true, data: data };
        });
      })
      .catch(function (err) {
        setLoading(false);
        throw err;
      });
  }

  function buildQuery() {
    var params = [];
    if (state.keyword) params.push("keyword=" + encodeURIComponent(state.keyword));
    if (state.status) params.push("status=" + encodeURIComponent(state.status));
    params.push("page=" + state.page);
    params.push("page_size=" + PAGE_SIZE);
    return "/api/items?" + params.join("&");
  }

  function makeBadge(cls, text) {
    var span = document.createElement("span");
    span.className = "badge " + cls;
    span.textContent = text; // 用户数据只走 textContent
    return span;
  }

  function renderItem(item) {
    var li = document.createElement("li");
    li.className = "item";
    li.dataset.id = String(item.id);

    var main = document.createElement("div");
    main.className = "item-main";

    var title = document.createElement("div");
    title.className = "item-title";
    title.textContent = item.title; // XSS 防护核心：文字原样显示
    main.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "item-meta";
    meta.appendChild(makeBadge("status-" + item.status, STATUS_LABELS[item.status] || item.status));
    meta.appendChild(makeBadge("priority-" + item.priority, PRIORITY_LABELS[item.priority] || item.priority));
    var time = document.createElement("span");
    time.textContent = "更新于 " + item.updated_at;
    meta.appendChild(time);
    main.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "item-actions";

    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", function () { openEdit(item); });

    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "danger";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", function () { removeItem(item); });

    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    li.appendChild(main);
    li.appendChild(actions);
    return li;
  }

  function refresh() {
    // DEF-1 修复：不再在 refresh 开头 clearMessages()。
    // 成功提示由 showSuccess 的 3 秒定时器自动消失；失败提示保留到下次操作；
    // 用户主动查询（搜索/重置）时才显式清除旧消息。
    return api(buildQuery(), { method: "GET" })
      .then(function (res) {
        var data = res.data;
        state.total = data.total;
        state.totalPages = data.total_pages;
        els.list.textContent = ""; // 清空（不引入 HTML）
        if (!data.items.length) {
          els.empty.classList.remove("hidden");
        } else {
          els.empty.classList.add("hidden");
          data.items.forEach(function (item) {
            els.list.appendChild(renderItem(item));
          });
        }
        els.pageInfo.textContent =
          "第 " + data.page + " / " + data.total_pages + " 页 · 共 " + data.total + " 条";
        els.prev.disabled = data.page <= 1;
        els.next.disabled = data.total_pages === 0 || data.page >= data.total_pages;
      })
      .catch(function (err) {
        showError("加载失败：" + err.message);
      });
  }

  function createItem(ev) {
    ev.preventDefault();
    var title = els.newTitle.value;
    if (!title.trim()) { showError("标题去除空白后不能为空"); return; }
    if (Array.from(title.trim()).length > 80) { showError("标题去除空白后不能超过 80 个字符"); return; }
    api("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title, priority: els.newPriority.value })
    })
      .then(function () {
        els.newTitle.value = "";
        els.newPriority.value = "medium";
        showSuccess("新增成功");
        state.page = 1;
        return refresh();
      })
      .catch(function (err) { showError("新增失败：" + err.message); });
  }

  function removeItem(item) {
    if (!window.confirm("确定删除事项「" + item.title + "」吗？")) return;
    api("/api/items/" + item.id, { method: "DELETE" })
      .then(function () {
        showSuccess("已删除");
        if (state.page > 1 && state.page > state.totalPages) state.page = Math.max(1, state.totalPages - 1);
        return refresh();
      })
      .catch(function (err) { showError("删除失败：" + err.message); });
  }

  function openEdit(item) {
    editingId = item.id;
    els.editId.textContent = "#" + item.id;
    els.editTitle.value = item.title;
    els.editStatus.value = item.status;
    els.editPriority.value = item.priority;
    els.overlay.classList.remove("hidden");
    els.editTitle.focus();
  }

  function closeEdit() {
    editingId = null;
    els.overlay.classList.add("hidden");
  }

  function saveEdit(ev) {
    ev.preventDefault();
    if (editingId === null) return;
    var title = els.editTitle.value;
    if (!title.trim()) { showError("标题去除空白后不能为空"); closeEdit(); return; }
    if (Array.from(title.trim()).length > 80) { showError("标题去除空白后不能超过 80 个字符"); closeEdit(); return; }
    api("/api/items/" + editingId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: title,
        status: els.editStatus.value,
        priority: els.editPriority.value
      })
    })
      .then(function () {
        closeEdit();
        showSuccess("保存成功");
        return refresh();
      })
      .catch(function (err) { closeEdit(); showError("保存失败：" + err.message); });
  }

  function doSearch() {
    clearMessages(); // 用户主动查询时清除旧的成功/失败提示
    state.keyword = els.searchKeyword.value.trim();
    state.status = els.filterStatus.value;
    state.page = 1;
    refresh();
  }

  function doReset() {
    clearMessages(); // 用户主动重置时清除旧的成功/失败提示
    els.searchKeyword.value = "";
    els.filterStatus.value = "";
    state.keyword = "";
    state.status = "";
    state.page = 1;
    refresh();
  }

  els.createForm.addEventListener("submit", createItem);
  els.editForm.addEventListener("submit", saveEdit);
  els.editCancel.addEventListener("click", closeEdit);
  els.overlay.addEventListener("click", function (ev) { if (ev.target === els.overlay) closeEdit(); });
  $("search-btn").addEventListener("click", doSearch);
  $("reset-btn").addEventListener("click", doReset);
  els.searchKeyword.addEventListener("keydown", function (ev) { if (ev.key === "Enter") doSearch(); });
  els.filterStatus.addEventListener("change", doSearch);
  els.prev.addEventListener("click", function () { if (state.page > 1) { state.page--; refresh(); } });
  els.next.addEventListener("click", function () {
    if (state.page < state.totalPages) { state.page++; refresh(); }
  });

  refresh();
})();
