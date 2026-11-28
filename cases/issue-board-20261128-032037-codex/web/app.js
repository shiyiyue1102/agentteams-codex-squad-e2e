/* 事项管理前端逻辑：原生 JS，无外部依赖。
   安全约定：所有用户可见文本一律通过 textContent / createTextNode 渲染，
   不使用任何 HTML 字符串拼接接口（契约 7.4）。 */
"use strict";

var API_BASE = "/api/v1";
var STATUS_LABELS = { todo: "待办", doing: "进行中", done: "完成" };
var PRIORITY_LABELS = { low: "低", medium: "中", high: "高" };
var EMPTY_TEXT = "暂无事项，添加第一条吧";
var NO_MATCH_TEXT = "没有符合条件的事项";
var NETWORK_ERROR_TEXT = "请求失败，请稍后重试";
var SUCCESS_TTL_MS = 3000;
var DEFAULT_STATUS = "todo";
var DEFAULT_PRIORITY = "medium";
var DEFAULT_PAGE_SIZE = 20;

var state = {
  q: "",
  status: "",
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  editingId: null,
  busy: false
};

var summary = { page: 1, pageSize: DEFAULT_PAGE_SIZE, total: 0, totalPages: 1 };
var pendingDelete = null;
var successTimer = null;

var els = {};

function ApiFailure(message) {
  var error = new Error(message);
  error.name = "ApiFailure";
  return error;
}

function byId(id) {
  return document.getElementById(id);
}

function cacheElements() {
  els.formHeading = byId("form-heading");
  els.form = byId("item-form");
  els.titleInput = byId("title-input");
  els.statusSelect = byId("status-select");
  els.prioritySelect = byId("priority-select");
  els.submitButton = byId("submit-item");
  els.cancelEditButton = byId("cancel-edit");
  els.searchInput = byId("search-input");
  els.searchButton = byId("search-button");
  els.filterStatus = byId("filter-status");
  els.pageSizeSelect = byId("page-size-select");
  els.list = byId("item-list");
  els.loading = byId("loading");
  els.message = byId("message");
  els.emptyState = byId("empty-state");
  els.pageInfo = byId("page-info");
  els.prevButton = byId("prev-page");
  els.nextButton = byId("next-page");
  els.confirmDialog = byId("confirm-dialog");
  els.confirmText = byId("confirm-text");
  els.confirmYes = byId("confirm-yes");
  els.confirmNo = byId("confirm-no");
}

function messageText(error) {
  if (error && typeof error.message === "string" && error.message !== "") {
    return error.message;
  }
  return NETWORK_ERROR_TEXT;
}

function showMessage(text, kind) {
  if (successTimer !== null) {
    clearTimeout(successTimer);
    successTimer = null;
  }
  els.message.textContent = text;
  els.message.className = "message " + kind;
  els.message.hidden = false;
  if (kind === "success") {
    successTimer = setTimeout(function () {
      successTimer = null;
      clearMessage();
    }, SUCCESS_TTL_MS);
  }
}

function clearMessage() {
  if (successTimer !== null) {
    clearTimeout(successTimer);
    successTimer = null;
  }
  els.message.textContent = "";
  els.message.className = "message";
  els.message.hidden = true;
}

function setBusy(busy) {
  state.busy = busy;
  els.loading.hidden = !busy;
  els.submitButton.disabled = busy;
  els.searchButton.disabled = busy;
  if (busy) {
    els.prevButton.disabled = true;
    els.nextButton.disabled = true;
  }
  var buttons = els.list.querySelectorAll("button");
  for (var index = 0; index < buttons.length; index += 1) {
    buttons[index].disabled = busy;
  }
  if (!busy) {
    els.cancelEditButton.disabled = state.editingId === null;
    updatePagination();
  }
}

function updatePagination() {
  els.prevButton.disabled = summary.page <= 1;
  els.nextButton.disabled = summary.page >= summary.totalPages;
}

function callApi(method, path, body) {
  var options = { method: method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json; charset=utf-8";
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(function (response) {
    return response.json().catch(function () {
      return null;
    }).then(function (payload) {
      if (!response.ok || payload === null || payload.ok !== true) {
        var detail = payload && payload.error ? payload.error.message : "";
        throw ApiFailure(typeof detail === "string" && detail !== "" ? detail : NETWORK_ERROR_TEXT);
      }
      return payload.data;
    });
  }, function () {
    throw ApiFailure(NETWORK_ERROR_TEXT);
  });
}

function buildListPath() {
  var parts = [];
  if (state.q !== "") {
    parts.push("q=" + encodeURIComponent(state.q));
  }
  if (state.status !== "") {
    parts.push("status=" + encodeURIComponent(state.status));
  }
  parts.push("page=" + encodeURIComponent(String(state.page)));
  parts.push("page_size=" + encodeURIComponent(String(state.pageSize)));
  return API_BASE + "/items?" + parts.join("&");
}

function hasActiveFilter() {
  return state.q !== "" || state.status !== "";
}

function createRow(item) {
  var row = document.createElement("li");
  row.className = "item-row";
  row.setAttribute("data-item-id", String(item.id));
  row.setAttribute("data-status", String(item.status));
  row.setAttribute("data-priority", String(item.priority));

  var main = document.createElement("div");
  main.className = "item-main";

  var title = document.createElement("p");
  title.className = "item-title";
  title.textContent = String(item.title);

  var meta = document.createElement("div");
  meta.className = "item-meta";

  var statusTag = document.createElement("span");
  statusTag.className = "item-status";
  statusTag.textContent = STATUS_LABELS[item.status] || String(item.status);

  var priorityTag = document.createElement("span");
  priorityTag.className = "item-priority";
  priorityTag.textContent = PRIORITY_LABELS[item.priority] || String(item.priority);

  var timeTag = document.createElement("span");
  timeTag.className = "item-time";
  timeTag.textContent = "更新于 " + String(item.updated_at);

  meta.appendChild(statusTag);
  meta.appendChild(priorityTag);
  meta.appendChild(timeTag);
  main.appendChild(title);
  main.appendChild(meta);

  var actions = document.createElement("div");
  actions.className = "item-actions";

  var editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "btn item-action";
  editButton.setAttribute("data-action", "edit");
  editButton.textContent = "编辑";
  editButton.disabled = state.busy;
  editButton.addEventListener("click", function () {
    if (!state.busy) {
      enterEditMode(item);
    }
  });

  var deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "btn btn-danger item-action";
  deleteButton.setAttribute("data-action", "delete");
  deleteButton.textContent = "删除";
  deleteButton.disabled = state.busy;
  deleteButton.addEventListener("click", function () {
    if (!state.busy) {
      openConfirmDialog(item);
    }
  });

  actions.appendChild(editButton);
  actions.appendChild(deleteButton);
  row.appendChild(main);
  row.appendChild(actions);
  return row;
}

function renderList(data) {
  summary.page = data.page;
  summary.pageSize = data.page_size;
  summary.total = data.total;
  summary.totalPages = data.total_pages;

  var items = data.items || [];
  var fragment = document.createDocumentFragment();
  for (var index = 0; index < items.length; index += 1) {
    fragment.appendChild(createRow(items[index]));
  }
  els.list.replaceChildren(fragment);

  if (items.length === 0) {
    els.emptyState.hidden = false;
    els.emptyState.textContent = hasActiveFilter() ? NO_MATCH_TEXT : EMPTY_TEXT;
  } else {
    els.emptyState.hidden = true;
    els.emptyState.textContent = EMPTY_TEXT;
  }

  els.pageInfo.textContent = "第 " + summary.page + " / " + summary.totalPages +
    " 页，共 " + summary.total + " 条";
}

function loadItems() {
  setBusy(true);
  return callApi("GET", buildListPath()).then(function (data) {
    renderList(data);
  }).catch(function (error) {
    showMessage(messageText(error), "error");
  }).then(function () {
    setBusy(false);
  });
}

function resetFormToCreate() {
  state.editingId = null;
  els.form.setAttribute("data-mode", "create");
  els.form.removeAttribute("data-editing-id");
  els.titleInput.value = "";
  els.statusSelect.value = DEFAULT_STATUS;
  els.prioritySelect.value = DEFAULT_PRIORITY;
  els.submitButton.textContent = "新增事项";
  els.formHeading.textContent = "新增事项";
  els.cancelEditButton.hidden = true;
  els.cancelEditButton.disabled = true;
}

function enterEditMode(item) {
  state.editingId = item.id;
  els.form.setAttribute("data-mode", "edit");
  els.form.setAttribute("data-editing-id", String(item.id));
  els.titleInput.value = String(item.title);
  els.statusSelect.value = String(item.status);
  els.prioritySelect.value = String(item.priority);
  els.submitButton.textContent = "保存修改";
  els.formHeading.textContent = "编辑事项";
  els.cancelEditButton.hidden = false;
  els.cancelEditButton.disabled = false;
  els.form.scrollIntoView({ block: "start" });
  els.titleInput.focus();
}

function submitForm(event) {
  if (event) {
    event.preventDefault();
  }
  if (state.busy) {
    return undefined;
  }
  var payload = {
    title: els.titleInput.value,
    status: els.statusSelect.value,
    priority: els.prioritySelect.value
  };
  var editingId = state.editingId;
  var path = editingId === null
    ? API_BASE + "/items"
    : API_BASE + "/items/" + encodeURIComponent(String(editingId));
  var method = editingId === null ? "POST" : "PUT";
  var successPrefix = editingId === null ? "已新增：" : "已更新：";

  setBusy(true);
  return callApi(method, path, payload).then(function (item) {
    resetFormToCreate();
    showMessage(successPrefix + String(item.title), "success");
    return loadItems();
  }).catch(function (error) {
    // 失败时保留表单已填内容，便于修正后重试（契约 7.2）
    showMessage(messageText(error), "error");
    return undefined;
  }).then(function () {
    setBusy(false);
  });
}

function openConfirmDialog(item) {
  pendingDelete = item;
  els.confirmText.textContent = "确认删除「" + String(item.title) + "」吗？删除后不可恢复。";
  els.confirmDialog.hidden = false;
  els.confirmYes.focus();
}

function closeConfirmDialog() {
  pendingDelete = null;
  els.confirmDialog.hidden = true;
}

function confirmDelete() {
  var item = pendingDelete;
  if (item === null || state.busy) {
    closeConfirmDialog();
    return undefined;
  }
  closeConfirmDialog();
  setBusy(true);
  return callApi("DELETE", API_BASE + "/items/" + encodeURIComponent(String(item.id))).then(function () {
    showMessage("已删除：" + String(item.title), "success");
    if (state.editingId === item.id) {
      resetFormToCreate();
    }
    return loadItems();
  }).catch(function (error) {
    showMessage(messageText(error), "error");
    return undefined;
  }).then(function () {
    setBusy(false);
  });
}

function applySearch() {
  state.q = els.searchInput.value.trim();
  state.page = 1;
  return loadItems();
}

function applyStatusFilter() {
  state.status = els.filterStatus.value;
  state.page = 1;
  return loadItems();
}

function applyPageSize() {
  var parsed = parseInt(els.pageSizeSelect.value, 10);
  state.pageSize = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_PAGE_SIZE;
  state.page = 1;
  return loadItems();
}

function gotoPage(nextPage) {
  if (nextPage < 1 || nextPage > summary.totalPages || nextPage === summary.page) {
    return undefined;
  }
  state.page = nextPage;
  return loadItems();
}

function bindEvents() {
  els.form.addEventListener("submit", submitForm);
  els.cancelEditButton.addEventListener("click", function () {
    resetFormToCreate();
  });
  els.searchButton.addEventListener("click", function () {
    applySearch();
  });
  els.searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      applySearch();
    }
  });
  els.filterStatus.addEventListener("change", applyStatusFilter);
  els.pageSizeSelect.addEventListener("change", applyPageSize);
  els.prevButton.addEventListener("click", function () {
    gotoPage(summary.page - 1);
  });
  els.nextButton.addEventListener("click", function () {
    gotoPage(summary.page + 1);
  });
  els.confirmYes.addEventListener("click", confirmDelete);
  els.confirmNo.addEventListener("click", closeConfirmDialog);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && els.confirmDialog.hidden === false) {
      closeConfirmDialog();
    }
  });
}

function init() {
  cacheElements();
  els.pageSizeSelect.value = String(DEFAULT_PAGE_SIZE);
  els.filterStatus.value = "";
  resetFormToCreate();
  bindEvents();
  setBusy(true);
  loadItems();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
