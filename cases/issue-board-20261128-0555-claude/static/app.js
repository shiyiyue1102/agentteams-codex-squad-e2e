/* 事项管理应用前端逻辑（原生 JS，无外部依赖）。
 *
 * XSS 防护契约：一切用户文本（标题、API 错误消息）只通过 textContent /
 * createTextNode 渲染，全文件不使用 innerHTML；不使用 alert/confirm/prompt，
 * 删除确认为行内页内控件。
 */
"use strict";

(function () {
  var STATUS_LABELS = { todo: "待办", in_progress: "进行中", done: "完成" };
  var PRIORITY_LABELS = { high: "高", medium: "中", low: "低" };

  var state = {
    page: 1,
    pageSize: 10,
    q: "",
    status: "",
    total: 0,
    totalPages: 0,
    editingId: null,
    loading: false
  };

  var els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function initElements() {
    [
      "item-form", "form-heading", "title-input", "status-input",
      "priority-input", "submit-btn", "cancel-edit-btn", "message",
      "search-input", "search-btn", "filter-status", "loading",
      "empty-state", "item-list", "prev-btn", "page-info", "next-btn"
    ].forEach(function (id) {
      els[id] = $(id);
    });
  }

  /* ---------- 消息区 ---------- */

  function showMessage(text, kind) {
    els["message"].textContent = text; // textContent：用户文本按文字显示
    els["message"].className = kind === "error" ? "msg-error" : "msg-success";
    els["message"].hidden = false;
  }

  /* ---------- 加载态 ---------- */

  function setLoading(on) {
    state.loading = on;
    els["loading"].hidden = !on;
    ["submit-btn", "search-btn", "prev-btn", "next-btn"].forEach(function (id) {
      els[id].disabled = on;
    });
  }

  /* ---------- API 封装 ---------- */

  function api(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      if (resp.status === 204) {
        return null;
      }
      return resp.json().catch(function () { return null; }).then(function (data) {
        if (!resp.ok) {
          var msg =
            data && data.error && data.error.message
              ? data.error.message
              : "请求失败（HTTP " + resp.status + "）";
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  /* ---------- 列表渲染 ---------- */

  function makeBadge(className, text) {
    var span = document.createElement("span");
    span.className = "badge " + className;
    span.textContent = text;
    return span;
  }

  function makeActionButton(className, text) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = className;
    btn.textContent = text;
    return btn;
  }

  function buildItemRow(item) {
    var li = document.createElement("li");
    li.className = "item" + (state.editingId === item.id ? " editing" : "");
    li.setAttribute("data-id", String(item.id));

    var main = document.createElement("div");
    main.className = "item-main";

    var title = document.createElement("span");
    title.className = "item-title";
    title.textContent = item.title; // XSS 安全：文字显示

    var badges = document.createElement("div");
    badges.className = "item-badges";
    badges.appendChild(
      makeBadge("status-" + item.status, STATUS_LABELS[item.status] || item.status)
    );
    badges.appendChild(
      makeBadge("priority-" + item.priority, PRIORITY_LABELS[item.priority] || item.priority)
    );

    main.appendChild(title);
    main.appendChild(badges);

    var actions = document.createElement("div");
    actions.className = "item-actions";
    actions.appendChild(makeActionButton("btn-edit", "编辑"));
    actions.appendChild(makeActionButton("btn-delete", "删除"));

    li.appendChild(main);
    li.appendChild(actions);
    return li;
  }

  function renderList(items) {
    var list = els["item-list"];
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    if (!items.length) {
      var filtered = Boolean(state.q || state.status);
      els["empty-state"].textContent = filtered
        ? "没有符合当前搜索/筛选条件的事项"
        : "暂无事项，使用上方表单添加第一个事项吧";
      els["empty-state"].hidden = false;
      return;
    }
    els["empty-state"].hidden = true;
    items.forEach(function (item) {
      list.appendChild(buildItemRow(item));
    });
  }

  function renderPagination() {
    var shownPages = Math.max(state.totalPages, 1);
    els["page-info"].textContent =
      "第 " + state.page + " / " + shownPages + " 页 · 共 " + state.total + " 条";
    els["prev-btn"].disabled = state.loading || state.page <= 1;
    els["next-btn"].disabled =
      state.loading || state.page >= state.totalPages;
  }

  /* ---------- 数据加载 ---------- */

  function refreshList() {
    setLoading(true);
    var params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("page_size", String(state.pageSize));
    if (state.q) params.set("q", state.q);
    if (state.status) params.set("status", state.status);

    return api("GET", "/api/items?" + params.toString())
      .then(function (data) {
        state.total = data.total;
        state.totalPages = data.total_pages;
        // 删除末页最后一条等场景：当前页越界且有数据时回退到最后一页
        if (!data.items.length && data.total > 0 && state.page > data.total_pages) {
          state.page = Math.max(1, data.total_pages);
          setLoading(false);
          return refreshList();
        }
        renderList(data.items);
        renderPagination();
        setLoading(false);
      })
      .catch(function (err) {
        renderList([]);
        renderPagination();
        showMessage("加载列表失败：" + err.message, "error");
        setLoading(false);
      });
  }

  /* ---------- 表单模式 ---------- */

  function resetFormMode() {
    state.editingId = null;
    els["form-heading"].textContent = "新增事项";
    els["submit-btn"].textContent = "添加事项";
    els["cancel-edit-btn"].hidden = true;
    els["item-form"].reset();
    els["status-input"].value = "todo";
    els["priority-input"].value = "medium";
  }

  function enterEditMode(item, li) {
    state.editingId = item.id;
    els["form-heading"].textContent = "编辑事项 #" + item.id;
    els["submit-btn"].textContent = "保存修改";
    els["cancel-edit-btn"].hidden = false;
    els["title-input"].value = item.title; // value 赋值：非 HTML 注入
    els["status-input"].value = item.status;
    els["priority-input"].value = item.priority;
    var rows = els["item-list"].querySelectorAll(".item");
    Array.prototype.forEach.call(rows, function (row) {
      row.classList.toggle("editing", row.getAttribute("data-id") === String(item.id));
    });
    els["title-input"].focus();
    if (li && li.scrollIntoView) {
      li.scrollIntoView({ block: "nearest" });
    }
  }

  /* ---------- 事件绑定 ---------- */

  function onSubmit(e) {
    e.preventDefault();
    if (state.loading) return;
    var payload = {
      title: els["title-input"].value,
      status: els["status-input"].value,
      priority: els["priority-input"].value
    };
    var editing = state.editingId;
    setLoading(true);
    var request =
      editing === null
        ? api("POST", "/api/items", payload)
        : api("PUT", "/api/items/" + editing, payload);

    request
      .then(function (data) {
        showMessage(
          (editing === null ? "已添加事项「" : "已保存事项「") + data.title + "」",
          "success"
        );
        resetFormMode();
        if (editing === null) {
          state.page = 1; // 新事项排最前，回到第 1 页可见
        }
        return refreshList();
      })
      .catch(function (err) {
        showMessage("操作失败：" + err.message, "error");
        setLoading(false);
      });
  }

  function onListClick(e) {
    var target = e.target;
    if (!(target instanceof Element)) return;
    var li = target.closest(".item");
    if (!li) return;
    var id = Number(li.getAttribute("data-id"));

    if (target.classList.contains("btn-edit")) {
      // 直接由行内 DOM 构造编辑对象（textContent 均为文字，安全）
      var titleEl = li.querySelector(".item-title");
      var statusEl = li.querySelector(".badge[class*='status-']");
      var prioEl = li.querySelector(".badge[class*='priority-']");
      var statusKey = statusEl
        ? statusEl.className.replace(/^.*status-([a-z_]+).*$/, "$1")
        : "todo";
      var prioKey = prioEl
        ? prioEl.className.replace(/^.*priority-([a-z]+).*$/, "$1")
        : "medium";
      enterEditMode(
        { id: id, title: titleEl ? titleEl.textContent : "", status: statusKey, priority: prioKey },
        li
      );
      return;
    }

    if (target.classList.contains("btn-delete")) {
      // 行内删除确认（不使用原生 confirm）
      var actions = li.querySelector(".item-actions");
      while (actions.firstChild) actions.removeChild(actions.firstChild);
      var hint = document.createElement("span");
      hint.className = "confirm-hint";
      hint.textContent = "确认删除？";
      var confirmBtn = makeActionButton("btn-confirm-delete", "确认删除");
      var cancelBtn = makeActionButton("btn-cancel-delete", "取消");
      actions.appendChild(hint);
      actions.appendChild(confirmBtn);
      actions.appendChild(cancelBtn);
      return;
    }

    if (target.classList.contains("btn-cancel-delete")) {
      refreshList(); // 还原该行按钮
      return;
    }

    if (target.classList.contains("btn-confirm-delete")) {
      if (state.loading) return;
      setLoading(true);
      api("DELETE", "/api/items/" + id)
        .then(function () {
          showMessage("已删除事项 #" + id, "success");
          if (state.editingId === id) resetFormMode();
          return refreshList();
        })
        .catch(function (err) {
          showMessage("删除失败：" + err.message, "error");
          setLoading(false);
        });
      return;
    }
  }

  function applyFilters() {
    state.q = els["search-input"].value.trim();
    state.status = els["filter-status"].value;
    state.page = 1;
    refreshList();
  }

  function bindEvents() {
    els["item-form"].addEventListener("submit", onSubmit);
    els["cancel-edit-btn"].addEventListener("click", function () {
      resetFormMode();
      refreshList();
    });
    els["item-list"].addEventListener("click", onListClick);
    els["search-btn"].addEventListener("click", applyFilters);
    els["search-input"].addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        applyFilters();
      }
    });
    els["filter-status"].addEventListener("change", applyFilters);
    els["prev-btn"].addEventListener("click", function () {
      if (state.page > 1) {
        state.page -= 1;
        refreshList();
      }
    });
    els["next-btn"].addEventListener("click", function () {
      if (state.page < state.totalPages) {
        state.page += 1;
        refreshList();
      }
    });
  }

  function init() {
    initElements();
    bindEvents();
    renderPagination();
    refreshList();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
