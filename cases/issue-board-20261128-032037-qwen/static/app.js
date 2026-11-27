/* 事项管理应用前端逻辑 — 原生 JS，无外部依赖。
 *
 * 安全约定（硬性）：所有用户输入文本一律通过 textContent / createTextNode
 * 渲染为纯文字，整个文件禁止以 HTML 字符串赋值方式拼接用户数据（防 XSS）。
 * 网络请求全部使用相对路径（/api/items），不硬编码主机名。
 */
(function () {
  "use strict";

  var STATUS_LABELS = { todo: "待办", in_progress: "进行中", done: "完成" };
  var PRIORITY_LABELS = { low: "低", medium: "中", high: "高" };
  var PAGE_SIZE = 10;

  var state = {
    keyword: "",
    status: "",
    page: 1,
    pageSize: PAGE_SIZE,
    total: 0,
    totalPages: 0,
    items: [],
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
    saveButton: $("save-button"),
    cancelEditButton: $("cancel-edit-button"),
    filterForm: $("filter-form"),
    keywordInput: $("keyword-input"),
    filterStatus: $("filter-status"),
    resetFiltersButton: $("reset-filters"),
    message: $("message"),
    loading: $("loading"),
    list: $("item-list"),
    emptyState: $("empty-state"),
    listCount: $("list-count"),
    prevPage: $("prev-page"),
    nextPage: $("next-page"),
    pageInfo: $("page-info")
  };

  /* ---------- 网络层 ---------- */

  function api(method, url, body) {
    var options = {
      method: method,
      headers: { Accept: "application/json" }
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json; charset=utf-8";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (parseError) {
            payload = null;
          }
        }
        if (!response.ok) {
          var detail =
            payload && payload.error && payload.error.message
              ? payload.error.message
              : "请求失败（HTTP " + response.status + "）";
          var err = new Error(detail);
          if (payload && payload.error && payload.error.code) {
            err.code = payload.error.code;
          }
          err.status = response.status;
          throw err;
        }
        return payload;
      });
    });
  }

  /* ---------- 消息 / 加载状态 ---------- */

  function showMessage(text, kind) {
    els.message.textContent = text; // 纯文字渲染，防止 HTML 注入
    els.message.className = "message " + (kind || "info");
  }

  function clearMessage() {
    els.message.textContent = "";
    els.message.className = "message hidden";
  }

  function setLoading(flag) {
    state.loading = flag;
    els.loading.className = flag ? "loading" : "loading hidden";
  }

  function formatTime(iso) {
    if (!iso) {
      return "";
    }
    var date = new Date(iso);
    if (isNaN(date.getTime())) {
      return iso;
    }
    function pad(n) {
      return String(n).padStart(2, "0");
    }
    return (
      date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) + ":" + pad(date.getMinutes())
    );
  }

  /* ---------- 列表加载与渲染 ---------- */

  function buildQueryString() {
    var parts = [];
    if (state.keyword) {
      parts.push("keyword=" + encodeURIComponent(state.keyword));
    }
    if (state.status) {
      parts.push("status=" + encodeURIComponent(state.status));
    }
    parts.push("page=" + encodeURIComponent(String(state.page)));
    parts.push("page_size=" + encodeURIComponent(String(state.pageSize)));
    return parts.join("&");
  }

  function loadItems() {
    setLoading(true);
    return api("GET", "/api/items?" + buildQueryString())
      .then(function (data) {
        state.items = data.items || [];
        state.total = data.total || 0;
        state.totalPages = data.total_pages || 0;
        state.page = data.page || 1;
        state.pageSize = data.page_size || PAGE_SIZE;
        renderList();
        renderPagination();
      })
      .catch(function (err) {
        showMessage("加载列表失败：" + err.message, "error");
      })
      .then(function () {
        setLoading(false);
      });
  }

  function renderList() {
    // 清空旧节点：逐个移除 DOM 节点，不做 HTML 字符串拼接
    while (els.list.firstChild) {
      els.list.removeChild(els.list.firstChild);
    }
    var filtersActive = Boolean(state.keyword || state.status);
    if (state.items.length === 0) {
      els.emptyState.className = "empty-state";
      els.emptyState.textContent = filtersActive
        ? "没有匹配的事项：试试更换关键词或状态筛选，或点击“重置”。"
        : "暂无事项：使用上方“新增事项”表单创建第一条吧。";
      els.listCount.textContent = "共 0 条";
      return;
    }
    els.emptyState.className = "empty-state hidden";
    els.listCount.textContent = "共 " + state.total + " 条";
    state.items.forEach(function (item) {
      els.list.appendChild(renderItem(item));
    });
  }

  function renderItem(item) {
    var li = document.createElement("li");
    li.className = "item";
    li.setAttribute("data-id", String(item.id));

    var main = document.createElement("div");
    main.className = "item-main";

    var title = document.createElement("span");
    title.className = "item-title";
    title.textContent = item.title; // 用户文本按纯文字显示（防 XSS）
    main.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "item-meta";

    var statusBadge = document.createElement("span");
    statusBadge.className = "badge status-" + item.status;
    statusBadge.textContent = STATUS_LABELS[item.status] || String(item.status);
    meta.appendChild(statusBadge);

    var priorityBadge = document.createElement("span");
    priorityBadge.className = "badge priority-" + item.priority;
    priorityBadge.textContent =
      "优先级：" + (PRIORITY_LABELS[item.priority] || String(item.priority));
    meta.appendChild(priorityBadge);

    var time = document.createElement("span");
    time.className = "item-time";
    time.textContent = "创建于 " + formatTime(item.created_at);
    meta.appendChild(time);

    main.appendChild(meta);
    li.appendChild(main);

    var actions = document.createElement("div");
    actions.className = "item-actions";

    var editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary edit-button";
    editButton.textContent = "编辑";
    editButton.setAttribute("aria-label", "编辑事项 " + item.title);
    editButton.addEventListener("click", function () {
      startEdit(item);
    });
    actions.appendChild(editButton);

    var deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger delete-button";
    deleteButton.textContent = "删除";
    deleteButton.setAttribute("aria-label", "删除事项 " + item.title);
    deleteButton.addEventListener("click", function () {
      removeItem(item);
    });
    actions.appendChild(deleteButton);

    li.appendChild(actions);
    return li;
  }

  function renderPagination() {
    var totalPages = Math.max(state.totalPages, 1);
    els.pageInfo.textContent = "第 " + state.page + " / " + totalPages + " 页";
    els.prevPage.disabled = state.page <= 1;
    els.nextPage.disabled = state.page >= state.totalPages;
  }

  /* ---------- 新增 / 编辑 ---------- */

  function startEdit(item) {
    state.editingId = item.id;
    els.formTitle.textContent = "编辑事项 #" + item.id;
    els.titleInput.value = item.title;
    els.statusInput.value = item.status;
    els.priorityInput.value = item.priority;
    els.saveButton.textContent = "保存修改";
    els.cancelEditButton.className = "secondary";
    clearMessage();
    els.titleInput.focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function resetForm() {
    state.editingId = null;
    els.form.reset();
    els.formTitle.textContent = "新增事项";
    els.saveButton.textContent = "保存";
    els.cancelEditButton.className = "secondary hidden";
  }

  function describeError(prefix, err) {
    return prefix + err.message + (err.code ? "（" + err.code + "）" : "");
  }

  els.form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (state.loading) {
      return;
    }
    var editingId = state.editingId;
    var payload = {
      title: els.titleInput.value,
      status: els.statusInput.value,
      priority: els.priorityInput.value
    };
    setLoading(true);
    var request = editingId
      ? api("PUT", "/api/items/" + encodeURIComponent(String(editingId)), payload)
      : api("POST", "/api/items", payload);
    request
      .then(function (item) {
        resetForm();
        if (editingId) {
          showMessage("已保存事项 #" + item.id + " 的修改：" + item.title, "success");
        } else {
          showMessage("已新增事项 #" + item.id + "：" + item.title, "success");
          state.page = 1; // 默认新建在前，回到第一页即可看到
        }
        return loadItems();
      })
      .catch(function (err) {
        showMessage(
          describeError(editingId ? "保存修改失败：" : "新增失败：", err),
          "error"
        );
      })
      .then(function () {
        setLoading(false);
      });
  });

  els.cancelEditButton.addEventListener("click", function () {
    resetForm();
    clearMessage();
    els.titleInput.focus();
  });

  /* ---------- 删除（带确认） ---------- */

  function removeItem(item) {
    if (state.loading) {
      return;
    }
    var confirmed = window.confirm("确定删除事项「" + item.title + "」吗？");
    if (!confirmed) {
      return;
    }
    setLoading(true);
    api("DELETE", "/api/items/" + encodeURIComponent(String(item.id)))
      .then(function () {
        showMessage("已删除事项 #" + item.id + "：" + item.title, "success");
        if (state.editingId === item.id) {
          resetForm();
        }
        if (state.items.length === 1 && state.page > 1) {
          state.page -= 1; // 删空本页时回退一页
        }
        return loadItems();
      })
      .catch(function (err) {
        showMessage(describeError("删除失败：", err), "error");
      })
      .then(function () {
        setLoading(false);
      });
  }

  /* ---------- 搜索 / 筛选 / 分页 ---------- */

  function applyFilters() {
    state.keyword = els.keywordInput.value.trim();
    state.status = els.filterStatus.value;
    state.page = 1;
    loadItems();
  }

  els.filterForm.addEventListener("submit", function (event) {
    event.preventDefault();
    applyFilters();
  });

  els.filterStatus.addEventListener("change", function () {
    applyFilters();
  });

  els.resetFiltersButton.addEventListener("click", function () {
    els.keywordInput.value = "";
    els.filterStatus.value = "";
    applyFilters();
  });

  els.prevPage.addEventListener("click", function () {
    if (state.page > 1 && !state.loading) {
      state.page -= 1;
      loadItems();
    }
  });

  els.nextPage.addEventListener("click", function () {
    if (state.page < state.totalPages && !state.loading) {
      state.page += 1;
      loadItems();
    }
  });

  /* ---------- 启动 ---------- */

  loadItems();
})();
