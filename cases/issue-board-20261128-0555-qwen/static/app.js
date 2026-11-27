"use strict";

/* 事项管理前端（原生 JavaScript，无任何外部依赖）。
 *
 * 安全约定（硬性）：所有用户提供的文本（尤其事项标题）一律通过
 * textContent / createTextNode 渲染，绝不以 HTML 注入方式写入 DOM，
 * 保证 <script>alert(1)</script>、<img src=x onerror=alert(1)> 等内容
 * 按字面文字显示且不执行。
 */

(function () {
  var API_BASE = "api/items";
  var PAGE_SIZE = 10;
  var STATUS_LABELS = { todo: "待办", doing: "进行中", done: "完成" };
  var PRIORITY_LABELS = { low: "低", medium: "中", high: "高" };

  var state = {
    page: 1,
    totalPages: 0,
    total: 0,
    q: "",
    status: "",
    editingId: null,
    loading: false
  };

  function $(id) {
    return document.getElementById(id);
  }

  var els = {
    form: $("item-form"),
    formTitle: $("form-title"),
    titleInput: $("title-input"),
    statusInput: $("status-input"),
    priorityInput: $("priority-input"),
    submitBtn: $("submit-btn"),
    cancelEditBtn: $("cancel-edit-btn"),
    feedback: $("feedback"),
    searchInput: $("search-input"),
    searchBtn: $("search-btn"),
    filterStatus: $("filter-status"),
    list: $("item-list"),
    listStatus: $("list-status"),
    emptyState: $("empty-state"),
    prevBtn: $("prev-btn"),
    nextBtn: $("next-btn"),
    pageInfo: $("page-info"),
    totalInfo: $("total-info")
  };

  function setFeedback(message, kind) {
    els.feedback.textContent = message || "";
    els.feedback.className = kind ? "feedback " + kind : "feedback";
  }

  /* 统一 fetch 封装：非 2xx 抛出服务端 error.message（或网络错误说明）。 */
  function apiRequest(method, url, body) {
    var options = { method: method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json; charset=utf-8";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (resp) {
      return resp.text().then(function (text) {
        var payload = null;
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (parseError) {
            payload = null;
          }
        }
        if (!resp.ok) {
          var serverMessage = payload && payload.error && payload.error.message;
          throw new Error(serverMessage || "请求失败（HTTP " + resp.status + "）");
        }
        return payload;
      });
    }, function (networkError) {
      throw new Error("网络请求失败：" + networkError.message);
    });
  }

  function buildListUrl() {
    var params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("page_size", String(PAGE_SIZE));
    if (state.q) {
      params.set("q", state.q);
    }
    if (state.status) {
      params.set("status", state.status);
    }
    return API_BASE + "?" + params.toString();
  }

  function loadList() {
    if (state.loading) {
      return Promise.resolve();
    }
    state.loading = true;
    els.listStatus.textContent = "加载中…";
    return apiRequest("GET", buildListUrl())
      .then(function (data) {
        if (data.items.length === 0 && state.page > 1 && data.total_pages >= 1) {
          // 当前页被删空时回退到最后一页再取一次
          state.page = data.total_pages;
          return apiRequest("GET", buildListUrl());
        }
        return data;
      })
      .then(function (data) {
        state.total = data.total;
        state.totalPages = data.total_pages;
        if (state.totalPages === 0) {
          state.page = 1;
        }
        renderList(data.items);
        renderPagination();
        els.listStatus.textContent = "";
      })
      .catch(function (err) {
        els.listStatus.textContent = "";
        renderList([]);
        renderPagination();
        setFeedback("列表加载失败：" + err.message, "error");
      })
      .then(function () {
        state.loading = false;
      });
  }

  function renderList(items) {
    while (els.list.firstChild) {
      els.list.removeChild(els.list.firstChild);
    }
    els.emptyState.hidden = items.length !== 0;
    for (var i = 0; i < items.length; i += 1) {
      els.list.appendChild(renderItem(items[i]));
    }
  }

  function renderItem(item) {
    var li = document.createElement("li");
    li.className = "item";
    li.setAttribute("data-id", String(item.id));

    var main = document.createElement("div");
    main.className = "item-main";

    var title = document.createElement("span");
    title.className = "item-title";
    title.textContent = item.title; // 用户文本：仅 textContent，绝不拼 HTML

    var badges = document.createElement("span");
    badges.className = "badges";
    badges.appendChild(
      makeBadge("badge status-" + item.status, STATUS_LABELS[item.status] || item.status)
    );
    badges.appendChild(
      makeBadge(
        "badge priority-" + item.priority,
        "优先级：" + (PRIORITY_LABELS[item.priority] || item.priority)
      )
    );

    var time = document.createElement("span");
    time.className = "item-time";
    time.textContent = "创建于 " + item.created_at;

    main.appendChild(title);
    main.appendChild(badges);
    main.appendChild(time);

    var ops = document.createElement("div");
    ops.className = "item-ops";

    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "secondary";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", function () {
      startEdit(item);
    });

    var deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", function () {
      removeItem(item);
    });

    ops.appendChild(editBtn);
    ops.appendChild(deleteBtn);

    li.appendChild(main);
    li.appendChild(ops);
    return li;
  }

  function makeBadge(className, text) {
    var span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  function renderPagination() {
    els.pageInfo.textContent = "第 " + state.page + " / " + state.totalPages + " 页";
    els.totalInfo.textContent = "共 " + state.total + " 条";
    els.prevBtn.disabled = state.page <= 1;
    els.nextBtn.disabled = state.page >= state.totalPages;
  }

  function startEdit(item) {
    state.editingId = item.id;
    els.formTitle.textContent = "编辑事项 #" + item.id;
    els.titleInput.value = item.title;
    els.statusInput.value = item.status;
    els.priorityInput.value = item.priority;
    els.submitBtn.textContent = "保存";
    els.cancelEditBtn.hidden = false;
    setFeedback("正在编辑事项 #" + item.id + "，修改后点击「保存」。", "info");
    els.titleInput.focus();
    els.titleInput.scrollIntoView({ block: "nearest" });
  }

  function resetForm() {
    state.editingId = null;
    els.form.reset();
    els.formTitle.textContent = "新增事项";
    els.submitBtn.textContent = "创建";
    els.cancelEditBtn.hidden = true;
  }

  function handleSubmit(event) {
    event.preventDefault();
    var body = {
      title: els.titleInput.value,
      status: els.statusInput.value,
      priority: els.priorityInput.value
    };
    var wasEditing = state.editingId;
    els.submitBtn.disabled = true;
    setFeedback(wasEditing === null ? "创建中…" : "保存中…", "info");
    var request =
      wasEditing === null
        ? apiRequest("POST", API_BASE, body)
        : apiRequest("PUT", API_BASE + "/" + wasEditing, body);
    request
      .then(function (item) {
        if (wasEditing === null) {
          setFeedback("创建成功：「" + item.title + "」（#" + item.id + "）", "success");
          state.page = 1; // 新事项排在最前，回到第一页可见
        } else {
          setFeedback("保存成功：「" + item.title + "」（#" + item.id + "）", "success");
        }
        resetForm();
        return loadList();
      })
      .catch(function (err) {
        setFeedback(
          (wasEditing === null ? "创建失败：" : "保存失败：") + err.message,
          "error"
        );
      })
      .then(function () {
        els.submitBtn.disabled = false;
      });
  }

  function removeItem(item) {
    var ok = window.confirm(
      "确认删除事项「" + item.title + "」（#" + item.id + "）？此操作不可恢复。"
    );
    if (!ok) {
      return;
    }
    apiRequest("DELETE", API_BASE + "/" + item.id)
      .then(function () {
        setFeedback("删除成功：「" + item.title + "」（#" + item.id + "）", "success");
        return loadList();
      })
      .catch(function (err) {
        setFeedback("删除失败：" + err.message, "error");
      });
  }

  function applySearch() {
    state.q = els.searchInput.value.trim();
    state.page = 1;
    loadList();
  }

  els.form.addEventListener("submit", handleSubmit);
  els.cancelEditBtn.addEventListener("click", function () {
    resetForm();
    setFeedback("已取消编辑。", "info");
  });
  els.searchBtn.addEventListener("click", applySearch);
  els.searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      applySearch();
    }
  });
  els.filterStatus.addEventListener("change", function () {
    state.status = els.filterStatus.value;
    state.page = 1;
    loadList();
  });
  els.prevBtn.addEventListener("click", function () {
    if (state.page > 1) {
      state.page -= 1;
      loadList();
    }
  });
  els.nextBtn.addEventListener("click", function () {
    if (state.page < state.totalPages) {
      state.page += 1;
      loadList();
    }
  });

  loadList();
})();
