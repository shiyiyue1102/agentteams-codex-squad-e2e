/* 事项管理应用前端逻辑（原生 JS，无任何外部依赖）。
 *
 * 安全约定（硬性）：所有用户可控文本一律通过 textContent / createElement
 * 渲染，禁止 innerHTML / insertAdjacentHTML / document.write / eval，
 * 确保 <script>、<img onerror> 等载荷只按字面文字显示。
 */
"use strict";

var STATUS_LABELS = { todo: "待办", in_progress: "进行中", done: "完成" };
var PRIORITY_LABELS = { high: "高", medium: "中", low: "低" };

var state = {
  page: 1,
  pageSize: 10,
  q: "",
  status: "",
  editingId: null,
  busy: false,
  lastData: null
};

var els = {};

function el(id) {
  return document.getElementById(id);
}

function initElements() {
  var ids = [
    "message", "loading",
    "item-form", "form-heading", "title-input", "status-input", "priority-input",
    "submit-btn", "cancel-edit-btn",
    "search-form", "q-input", "filter-status", "search-btn", "reset-search-btn",
    "item-list", "empty-state",
    "pagination", "prev-btn", "next-btn", "page-info"
  ];
  ids.forEach(function (id) {
    els[id] = el(id);
  });
}

/* ---------- 消息与加载状态 ---------- */

function showMessage(kind, text) {
  els["message"].textContent = text;
  els["message"].className = "msg msg-" + kind;
  els["message"].hidden = false;
}

function setBusy(busy) {
  state.busy = busy;
  els["loading"].hidden = !busy;
  els["submit-btn"].disabled = busy;
  els["search-btn"].disabled = busy;
  els["reset-search-btn"].disabled = busy;
  els["cancel-edit-btn"].disabled = busy;
  updatePaginationButtons();
  var actionButtons = document.querySelectorAll(".item-actions button");
  for (var i = 0; i < actionButtons.length; i += 1) {
    actionButtons[i].disabled = busy;
  }
}

function updatePaginationButtons() {
  var data = state.lastData;
  var atFirst = !data || data.page <= 1;
  var atLast = !data || data.page >= data.total_pages;
  els["prev-btn"].disabled = state.busy || atFirst;
  els["next-btn"].disabled = state.busy || atLast;
}

/* ---------- API 封装 ---------- */

function api(method, path, body) {
  var options = { method: method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  return fetch(path, options)
    .catch(function (networkError) {
      var reason = networkError && networkError.message ? networkError.message : String(networkError);
      throw new Error("网络请求失败：" + reason);
    })
    .then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (parseError) {
            data = null;
          }
        }
        if (!response.ok) {
          var message =
            data && data.error && data.error.message
              ? data.error.message
              : "请求失败（HTTP " + response.status + "）";
          var error = new Error(message);
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
}

/* ---------- 列表加载与渲染 ---------- */

function loadItems() {
  setBusy(true);
  var params = new URLSearchParams();
  if (state.q) {
    params.set("q", state.q);
  }
  if (state.status) {
    params.set("status", state.status);
  }
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return api("GET", "/api/items?" + params.toString())
    .then(function (data) {
      state.lastData = data;
      renderList(data.items);
      renderPagination(data);
    })
    .catch(function (error) {
      state.lastData = null;
      renderList([]);
      renderPagination(null);
      showMessage("error", "加载失败：" + error.message);
    })
    .then(function () {
      setBusy(false);
    });
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function makeBadge(className, text) {
  var badge = document.createElement("span");
  badge.className = "badge " + className;
  badge.textContent = text;
  return badge;
}

function renderList(items) {
  var list = els["item-list"];
  clearNode(list);
  var empty = !items || items.length === 0;
  els["empty-state"].hidden = !empty;
  if (empty) {
    return;
  }
  items.forEach(function (item) {
    list.appendChild(renderItem(item));
  });
}

function renderItem(item) {
  var li = document.createElement("li");
  li.className = "item";

  var title = document.createElement("span");
  title.className = "item-title";
  title.textContent = item.title; /* 用户文本只按文字显示 */

  var meta = document.createElement("div");
  meta.className = "item-meta";
  meta.appendChild(
    makeBadge("status-" + item.status, "状态：" + (STATUS_LABELS[item.status] || item.status))
  );
  meta.appendChild(
    makeBadge("priority-" + item.priority, "优先级：" + (PRIORITY_LABELS[item.priority] || item.priority))
  );

  var actions = document.createElement("div");
  actions.className = "item-actions";

  var editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "btn-edit";
  editButton.textContent = "编辑";
  editButton.addEventListener("click", function () {
    startEdit(item);
  });

  var deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "btn-delete";
  deleteButton.textContent = "删除";
  deleteButton.addEventListener("click", function () {
    deleteItem(item);
  });

  actions.appendChild(editButton);
  actions.appendChild(deleteButton);

  li.appendChild(title);
  li.appendChild(meta);
  li.appendChild(actions);
  return li;
}

function renderPagination(data) {
  if (!data) {
    els["page-info"].textContent = "";
    updatePaginationButtons();
    return;
  }
  var totalPages = Math.max(1, data.total_pages);
  els["page-info"].textContent =
    "第 " + data.page + " / " + totalPages + " 页 · 共 " + data.total + " 条";
  updatePaginationButtons();
}

/* ---------- 新增 / 编辑 ---------- */

function startEdit(item) {
  state.editingId = item.id;
  els["title-input"].value = item.title;
  els["status-input"].value = item.status;
  els["priority-input"].value = item.priority;
  els["form-heading"].textContent = "编辑事项 #" + item.id;
  els["submit-btn"].textContent = "保存修改";
  els["cancel-edit-btn"].hidden = false;
  showMessage("info", "正在编辑事项 #" + item.id + "，修改后点击「保存修改」。")
  els["item-form"].scrollIntoView({ block: "start" });
  els["title-input"].focus();
}

function cancelEdit() {
  state.editingId = null;
  els["item-form"].reset();
  els["form-heading"].textContent = "新增事项";
  els["submit-btn"].textContent = "新增事项";
  els["cancel-edit-btn"].hidden = true;
}

function onItemFormSubmit(event) {
  event.preventDefault();
  if (state.busy) {
    return;
  }
  var payload = {
    title: els["title-input"].value,
    status: els["status-input"].value,
    priority: els["priority-input"].value
  };
  var editing = state.editingId;
  setBusy(true);
  var request =
    editing !== null
      ? api("PUT", "/api/items/" + editing, payload)
      : api("POST", "/api/items", payload);
  request
    .then(function (result) {
      if (editing !== null) {
        showMessage("success", "事项 #" + result.id + " 已更新：" + result.title);
        cancelEdit();
      } else {
        showMessage("success", "事项 #" + result.id + " 已新增：" + result.title);
        els["item-form"].reset();
      }
      return reloadAfterChange();
    })
    .catch(function (error) {
      showMessage("error", (editing !== null ? "更新失败：" : "新增失败：") + error.message);
      setBusy(false);
    });
}

function deleteItem(item) {
  if (state.busy) {
    return;
  }
  setBusy(true);
  api("DELETE", "/api/items/" + item.id)
    .then(function () {
      showMessage("success", "事项 #" + item.id + " 已删除：" + item.title);
      if (state.editingId === item.id) {
        cancelEdit();
      }
      return reloadAfterChange();
    })
    .catch(function (error) {
      showMessage("error", "删除失败：" + error.message);
      return reloadAfterChange();
    });
}

/* 变更后重新拉取列表；若当前页已越界（如末页删光），自动回到最后一页。 */
function reloadAfterChange() {
  var params = new URLSearchParams();
  if (state.q) {
    params.set("q", state.q);
  }
  if (state.status) {
    params.set("status", state.status);
  }
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return api("GET", "/api/items?" + params.toString())
    .then(function (data) {
      if (data.items.length === 0 && data.total > 0 && data.page > data.total_pages) {
        state.page = Math.max(1, data.total_pages);
        return loadItems();
      }
      state.lastData = data;
      renderList(data.items);
      renderPagination(data);
      setBusy(false);
      return undefined;
    })
    .catch(function (error) {
      state.lastData = null;
      renderList([]);
      renderPagination(null);
      showMessage("error", "加载失败：" + error.message);
      setBusy(false);
    });
}

/* ---------- 搜索 / 筛选 / 分页 ---------- */

function onSearchSubmit(event) {
  event.preventDefault();
  if (state.busy) {
    return;
  }
  state.q = els["q-input"].value.trim();
  state.status = els["filter-status"].value;
  state.page = 1; /* 条件变更后回到第 1 页 */
  loadItems();
}

function onSearchReset() {
  if (state.busy) {
    return;
  }
  els["q-input"].value = "";
  els["filter-status"].value = "";
  state.q = "";
  state.status = "";
  state.page = 1;
  loadItems();
}

function goPage(delta) {
  if (state.busy) {
    return;
  }
  var next = state.page + delta;
  if (next < 1) {
    return;
  }
  if (state.lastData && next > state.lastData.total_pages) {
    return;
  }
  state.page = next;
  loadItems();
}

/* ---------- 初始化 ---------- */

function init() {
  initElements();
  els["item-form"].addEventListener("submit", onItemFormSubmit);
  els["cancel-edit-btn"].addEventListener("click", function () {
    cancelEdit();
    showMessage("info", "已取消编辑。");
  });
  els["search-form"].addEventListener("submit", onSearchSubmit);
  els["reset-search-btn"].addEventListener("click", onSearchReset);
  els["prev-btn"].addEventListener("click", function () {
    goPage(-1);
  });
  els["next-btn"].addEventListener("click", function () {
    goPage(1);
  });
  loadItems();
}

document.addEventListener("DOMContentLoaded", init);
