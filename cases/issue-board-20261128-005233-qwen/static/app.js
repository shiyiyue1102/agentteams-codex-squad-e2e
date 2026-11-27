/* 事项管理应用前端逻辑（原生 JS，无任何外部依赖）。
 *
 * 契约要点：
 * - 所有用户可控文本（事项标题、错误 message 等）一律通过 textContent /
 *   createTextNode 写入 DOM；禁止 innerHTML / insertAdjacentHTML /
 *   document.write，禁止把用户数据拼入 href/src，防止 XSS。
 * - 不使用 localStorage/sessionStorage/cookie 存业务数据；
 *   持久化只依赖后端 SQLite。
 */
"use strict";

var STATUS_LABELS = { todo: "待办", doing: "进行中", done: "完成" };
var PRIORITY_LABELS = { high: "高", medium: "中", low: "低" };
var PAGE_SIZE = 10;

var state = {
  page: 1,
  pageSize: PAGE_SIZE,
  q: "",
  status: "",
  total: 0,
  totalPages: 1,
  items: [],
  loading: false,
  editingId: null
};

function $(id) {
  return document.getElementById(id);
}

/* ---------------- API 封装 ---------------- */

function makeApiError(message) {
  var error = new Error(message || "请求失败");
  error.isApiError = true;
  return error;
}

/**
 * 统一 fetch 封装：
 * - 2xx：返回解析后的 JSON（204 返回 null）
 * - 非 2xx：抛出带后端 error.message 的错误
 * - 网络失败：抛出「无法连接服务」错误
 */
function request(method, path, body) {
  var options = {
    method: method,
    headers: { Accept: "application/json" }
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json; charset=utf-8";
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(
    function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (parseError) {
            throw makeApiError(
              "服务响应不是合法 JSON（HTTP " + response.status + "）"
            );
          }
        }
        if (!response.ok) {
          var message =
            data && data.error && typeof data.error.message === "string" &&
            data.error.message
              ? data.error.message
              : "请求失败（HTTP " + response.status + "）";
          throw makeApiError(message);
        }
        return data;
      });
    },
    function () {
      throw makeApiError("无法连接服务，请确认后端正在运行");
    }
  );
}

function errorMessage(error) {
  return error && error.message ? error.message : "未知错误";
}

/* ---------------- 操作结果 / 失败原因提示 ---------------- */

function showMessage(text, kind) {
  var box = $("message");
  box.textContent = text; // 一律按文字写入，绝不注入 HTML
  box.className = "message " + (kind || "info");
  box.hidden = false;
  box.scrollIntoView({ block: "nearest" });
}

/* ---------------- 列表渲染 ---------------- */

function hasActiveFilter() {
  return state.q !== "" || state.status !== "";
}

function makeBadge(className, text) {
  var badge = document.createElement("span");
  badge.className = "badge " + className;
  badge.textContent = text;
  return badge;
}

function renderItem(item) {
  var li = document.createElement("li");
  li.className = "item";
  li.setAttribute("data-id", String(item.id));

  var main = document.createElement("div");
  main.className = "item-main";

  var title = document.createElement("span");
  title.className = "item-title";
  title.textContent = item.title; // XSS 安全：按文字渲染

  var badges = document.createElement("div");
  badges.className = "item-badges";
  badges.appendChild(
    makeBadge(
      "badge-status",
      "状态：" + (STATUS_LABELS[item.status] || item.status)
    )
  );
  badges.appendChild(
    makeBadge(
      "badge-priority",
      "优先级：" + (PRIORITY_LABELS[item.priority] || item.priority)
    )
  );

  main.appendChild(title);
  main.appendChild(badges);

  var actions = document.createElement("div");
  actions.className = "item-actions";

  var editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "edit-button";
  editButton.textContent = "编辑";
  editButton.addEventListener("click", function () {
    openEditor(item);
  });

  var deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "danger delete-button";
  deleteButton.textContent = "删除";
  deleteButton.addEventListener("click", function () {
    removeItem(item, deleteButton);
  });

  actions.appendChild(editButton);
  actions.appendChild(deleteButton);

  li.appendChild(main);
  li.appendChild(actions);
  return li;
}

function renderList() {
  var list = $("item-list");
  var empty = $("empty-state");
  while (list.firstChild) {
    list.removeChild(list.firstChild);
  }
  if (state.items.length === 0) {
    // 两种空态文案可区分：无任何数据 vs 搜索/筛选无结果
    list.hidden = true;
    empty.hidden = false;
    empty.textContent = hasActiveFilter()
      ? "无匹配结果，请调整搜索词或状态筛选"
      : "暂无事项，使用上方表单新增第一条吧";
  } else {
    empty.hidden = true;
    list.hidden = false;
    state.items.forEach(function (item) {
      list.appendChild(renderItem(item));
    });
  }
  renderPagination();
}

function renderPagination() {
  $("page-info").textContent =
    "第 " + state.page + " / " + state.totalPages + " 页 · 共 " +
    state.total + " 条";
  $("prev-page").disabled = state.page <= 1 || state.loading;
  $("next-page").disabled = state.page >= state.totalPages || state.loading;
}

/* ---------------- 数据加载（加载态） ---------------- */

function loadList() {
  if (state.loading) {
    return Promise.resolve();
  }
  state.loading = true;
  $("loading").hidden = false;
  renderPagination(); // 加载期间禁用翻页按钮

  var params = new URLSearchParams();
  if (state.q !== "") {
    params.set("q", state.q);
  }
  if (state.status !== "") {
    params.set("status", state.status);
  }
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));

  return request("GET", "/api/items?" + params.toString())
    .then(function (data) {
      state.total = data.total;
      state.totalPages = data.total_pages;
      state.page = data.page;
      state.pageSize = data.page_size;
      state.items = data.items || [];
      renderList();
    })
    .catch(function (error) {
      showMessage("加载列表失败：" + errorMessage(error), "error");
    })
    .then(function () {
      state.loading = false;
      $("loading").hidden = true;
      renderPagination();
    });
}

/* ---------------- 新增 ---------------- */

function onAddSubmit(event) {
  event.preventDefault();
  var titleInput = $("add-title");
  var submitButton = $("add-submit");
  submitButton.disabled = true; // 操作期间防重复提交
  request("POST", "/api/items", {
    title: titleInput.value,
    status: $("add-status").value,
    priority: $("add-priority").value
  })
    .then(function (item) {
      showMessage("已新增事项「" + item.title + "」", "success");
      titleInput.value = "";
      state.page = 1; // 新事项按 id DESC 排在最前，回到第 1 页
      return loadList();
    })
    .catch(function (error) {
      showMessage("新增失败：" + errorMessage(error), "error");
    })
    .then(function () {
      submitButton.disabled = false;
    });
}

/* ---------------- 编辑 ---------------- */

function openEditor(item) {
  state.editingId = item.id;
  $("edit-id").textContent = String(item.id);
  $("edit-title").value = item.title; // 预填当前值
  $("edit-status").value = item.status;
  $("edit-priority").value = item.priority;
  $("edit-panel").hidden = false;
  $("edit-title").focus();
}

function closeEditor() {
  state.editingId = null;
  $("edit-panel").hidden = true;
}

function onEditSubmit(event) {
  event.preventDefault();
  if (state.editingId === null) {
    return;
  }
  var saveButton = $("edit-submit");
  saveButton.disabled = true;
  request("PUT", "/api/items/" + state.editingId, {
    title: $("edit-title").value,
    status: $("edit-status").value,
    priority: $("edit-priority").value
  })
    .then(function (item) {
      showMessage("已更新事项「" + item.title + "」", "success");
      closeEditor();
      return loadList();
    })
    .catch(function (error) {
      showMessage("更新失败：" + errorMessage(error), "error");
    })
    .then(function () {
      saveButton.disabled = false;
    });
}

/* ---------------- 删除（二次确认） ---------------- */

function removeItem(item, button) {
  var confirmed = window.confirm("确定删除事项「" + item.title + "」吗？");
  if (!confirmed) {
    return;
  }
  button.disabled = true;
  request("DELETE", "/api/items/" + item.id)
    .then(function () {
      showMessage("已删除事项「" + item.title + "」", "success");
      if (state.items.length === 1 && state.page > 1) {
        state.page -= 1; // 本页删空时自动回退一页
      }
      return loadList();
    })
    .catch(function (error) {
      showMessage("删除失败：" + errorMessage(error), "error");
      return loadList();
    });
}

/* ---------------- 搜索 / 状态筛选 / 分页 ---------------- */

function applySearch() {
  state.q = $("search-input").value.trim();
  state.page = 1;
  return loadList();
}

function bindEvents() {
  $("add-form").addEventListener("submit", onAddSubmit);
  $("edit-form").addEventListener("submit", onEditSubmit);
  $("edit-cancel").addEventListener("click", function () {
    closeEditor();
  });
  $("search-button").addEventListener("click", function () {
    applySearch();
  });
  $("search-input").addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      applySearch();
    }
  });
  $("status-filter").addEventListener("change", function () {
    state.status = $("status-filter").value;
    state.page = 1;
    loadList();
  });
  $("prev-page").addEventListener("click", function () {
    if (state.page > 1 && !state.loading) {
      state.page -= 1;
      loadList();
    }
  });
  $("next-page").addEventListener("click", function () {
    if (state.page < state.totalPages && !state.loading) {
      state.page += 1;
      loadList();
    }
  });
}

document.addEventListener("DOMContentLoaded", function () {
  bindEvents();
  loadList();
});
