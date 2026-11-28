/* 事项管理应用前端逻辑。
 * 安全约束（分析报告 §7.5）：用户可控文本只通过 textContent / value 写入 DOM，
 * 不使用任何 HTML 注入型接口，因此标题中的脚本片段只会作为文字显示。
 */
(function () {
  "use strict";

  var DEFAULT_PAGE_SIZE = 10;

  var STATUS_FALLBACK = [
    { value: "todo", label: "待办" },
    { value: "in_progress", label: "进行中" },
    { value: "done", label: "完成" }
  ];

  var PRIORITY_FALLBACK = [
    { value: "low", label: "低" },
    { value: "medium", label: "中" },
    { value: "high", label: "高" }
  ];

  var state = {
    statuses: STATUS_FALLBACK,
    priorities: PRIORITY_FALLBACK,
    statusLabels: {},
    priorityLabels: {},
    q: "",
    status: "all",
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    total: 0,
    totalPages: 1,
    hasPrev: false,
    hasNext: false,
    items: [],
    pending: 0,
    loadFailed: false
  };

  var dom = {};
  var loadingNode = null;
  var emptyNode = null;

  function cacheDom() {
    dom.listPanel = document.getElementById("list-panel");
    dom.itemList = document.getElementById("item-list");
    dom.messageArea = document.getElementById("message-area");
    dom.paginationBar = dom.listPanel.querySelector("nav.pagination");
    dom.pageInfo = document.getElementById("page-info");
    dom.prevButton = document.getElementById("page-prev");
    dom.nextButton = document.getElementById("page-next");
    dom.createForm = document.getElementById("create-form");
    dom.titleInput = document.getElementById("title-input");
    dom.statusSelect = document.getElementById("status-select");
    dom.prioritySelect = document.getElementById("priority-select");
    dom.createSubmit = document.getElementById("create-submit");
    dom.filterForm = document.getElementById("filter-form");
    dom.searchInput = document.getElementById("search-input");
    dom.searchSubmit = document.getElementById("search-submit");
    dom.statusFilter = document.getElementById("status-filter");
    dom.filterReset = document.getElementById("filter-reset");
  }

  function clearNode(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function makeElement(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function makeTestId(tag, testId, className, text) {
    var node = makeElement(tag, className, text);
    node.setAttribute("data-testid", testId);
    return node;
  }

  function makeButton(testId, label, className, onClick) {
    var node = makeTestId("button", testId, className, label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function buildLabelMaps() {
    state.statusLabels = {};
    state.statuses.forEach(function (entry) {
      state.statusLabels[entry.value] = entry.label;
    });
    state.priorityLabels = {};
    state.priorities.forEach(function (entry) {
      state.priorityLabels[entry.value] = entry.label;
    });
  }

  function statusLabel(value) {
    return state.statusLabels[value] || value;
  }

  function priorityLabel(value) {
    return state.priorityLabels[value] || value;
  }

  function apiRequest(method, url, payload) {
    var options = { method: method, headers: { Accept: "application/json" } };
    if (payload !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (parseError) {
            data = null;
          }
        }
        if (response.ok) {
          return data;
        }
        var detail = data && data.error ? data.error : {};
        var failure = new Error(detail.message || "请求失败（HTTP " + response.status + "）");
        failure.code = detail.code || "HTTP_" + response.status;
        failure.status = response.status;
        throw failure;
      });
    }, function () {
      var failure = new Error("网络请求失败，请确认服务是否仍在运行");
      failure.code = "NETWORK_ERROR";
      throw failure;
    });
  }

  function formatError(error) {
    var message = error && error.message ? error.message : "请求失败，请稍后重试";
    var code = error && error.code ? error.code : "";
    return code ? message + "（" + code + "）" : message;
  }

  function showMessage(text, kind) {
    dom.messageArea.textContent = text;
    dom.messageArea.setAttribute("data-message-kind", kind);
    dom.messageArea.setAttribute("role", kind === "error" ? "alert" : "status");
    dom.messageArea.classList.remove("message-hidden");
  }

  function beginRequest() {
    state.pending += 1;
    syncBusy();
  }

  function endRequest() {
    state.pending = state.pending > 0 ? state.pending - 1 : 0;
    syncBusy();
  }

  function setRowButtonsDisabled(disabled, testId) {
    var nodes = dom.itemList.querySelectorAll('[data-testid="' + testId + '"]');
    Array.prototype.forEach.call(nodes, function (node) {
      node.disabled = disabled;
    });
  }

  function syncBusy() {
    var busy = state.pending > 0;
    dom.itemList.setAttribute("aria-busy", busy ? "true" : "false");
    if (busy && !loadingNode) {
      loadingNode = makeTestId("div", "list-loading", "loading", "加载中…");
      dom.listPanel.insertBefore(loadingNode, dom.itemList);
    } else if (!busy && loadingNode) {
      if (loadingNode.parentNode) {
        loadingNode.parentNode.removeChild(loadingNode);
      }
      loadingNode = null;
    }
    dom.createSubmit.disabled = busy;
    setRowButtonsDisabled(busy, "save-button");
    setRowButtonsDisabled(busy, "delete-confirm");
    dom.prevButton.disabled = busy || !state.hasPrev;
    dom.nextButton.disabled = busy || !state.hasNext;
  }

  function renderPagination() {
    dom.pageInfo.textContent =
      "第 " + state.page + " / " + state.totalPages + " 页，共 " + state.total + " 条";
    dom.prevButton.disabled = state.pending > 0 || !state.hasPrev;
    dom.nextButton.disabled = state.pending > 0 || !state.hasNext;
  }

  function removeEmptyState() {
    if (emptyNode && emptyNode.parentNode) {
      emptyNode.parentNode.removeChild(emptyNode);
    }
    emptyNode = null;
  }

  function showEmptyState() {
    var filtering = state.q !== "" || state.status !== "all";
    var text = filtering
      ? "没有匹配的事项，试试调整关键词或状态"
      : "暂无事项，先添加一条吧";
    emptyNode = makeTestId("div", "item-empty", "empty-state", text);
    dom.listPanel.insertBefore(emptyNode, dom.paginationBar);
  }

  function renderList() {
    clearNode(dom.itemList);
    removeEmptyState();
    if (!state.items.length) {
      if (!state.loadFailed) {
        showEmptyState();
      }
      return;
    }
    state.items.forEach(function (item) {
      dom.itemList.appendChild(createRow(item));
    });
  }

  function buildMain(item) {
    var main = makeElement("div", "item-main");
    main.appendChild(makeTestId("span", "item-title", "item-title", item.title));
    main.appendChild(
      makeTestId(
        "span",
        "item-status",
        "badge badge-status-" + item.status,
        statusLabel(item.status)
      )
    );
    main.appendChild(
      makeTestId(
        "span",
        "item-priority",
        "badge badge-priority-" + item.priority,
        priorityLabel(item.priority)
      )
    );
    main.appendChild(makeElement("span", "item-time", "更新于 " + item.updated_at));
    return main;
  }

  function buildActions(item, mode, rerender) {
    var actions = makeElement("div", "item-actions");
    actions.appendChild(
      makeButton("edit-button", "编辑", "secondary", function () {
        mode.editing = true;
        mode.confirming = false;
        rerender();
      })
    );
    if (mode.confirming) {
      actions.appendChild(
        makeButton("delete-confirm", "确认删除", "danger", function () {
          removeItem(item.id);
        })
      );
      actions.appendChild(
        makeButton("delete-button", "取消", "secondary", function () {
          mode.confirming = false;
          rerender();
        })
      );
      return actions;
    }
    actions.appendChild(
      makeButton("delete-button", "删除", "danger", function () {
        mode.confirming = true;
        rerender();
      })
    );
    return actions;
  }

  function buildEditor(item, mode, rerender) {
    var form = makeElement("div", "edit-form");

    var titleField = makeElement("label", "edit-field");
    titleField.appendChild(makeElement("span", null, "标题"));
    var titleInput = makeTestId("input", "edit-title-input", null);
    titleInput.type = "text";
    titleInput.value = item.title;
    titleField.appendChild(titleInput);

    var statusField = makeElement("label", "edit-field");
    statusField.appendChild(makeElement("span", null, "状态"));
    var statusSelect = makeTestId("select", "edit-status-select", null);
    state.statuses.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.value;
      option.textContent = entry.label;
      statusSelect.appendChild(option);
    });
    statusSelect.value = item.status;
    statusField.appendChild(statusSelect);

    var priorityField = makeElement("label", "edit-field");
    priorityField.appendChild(makeElement("span", null, "优先级"));
    var prioritySelect = makeTestId("select", "edit-priority-select", null);
    state.priorities.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.value;
      option.textContent = entry.label;
      prioritySelect.appendChild(option);
    });
    prioritySelect.value = item.priority;
    priorityField.appendChild(prioritySelect);

    var actions = makeElement("div", "edit-actions");
    actions.appendChild(
      makeButton("save-button", "保存", null, function () {
        saveItem(item.id, {
          title: titleInput.value,
          status: statusSelect.value,
          priority: prioritySelect.value
        });
      })
    );
    actions.appendChild(
      makeButton("cancel-button", "取消", "secondary", function () {
        mode.editing = false;
        rerender();
      })
    );

    form.appendChild(titleField);
    form.appendChild(statusField);
    form.appendChild(priorityField);
    form.appendChild(actions);
    return form;
  }

  function createRow(item) {
    var row = makeTestId("li", "item-row", "item-row");
    row.setAttribute("data-item-id", String(item.id));
    var mode = { editing: false, confirming: false };

    function rerender() {
      clearNode(row);
      if (mode.editing) {
        row.appendChild(buildEditor(item, mode, rerender));
      } else {
        row.appendChild(buildMain(item));
        row.appendChild(buildActions(item, mode, rerender));
      }
      syncBusy();
    }

    rerender();
    return row;
  }

  function buildListUrl() {
    var parts = [];
    if (state.q !== "") {
      parts.push("q=" + encodeURIComponent(state.q));
    }
    parts.push("status=" + encodeURIComponent(state.status));
    parts.push("page=" + encodeURIComponent(String(state.page)));
    parts.push("page_size=" + encodeURIComponent(String(state.pageSize)));
    return "/api/items?" + parts.join("&");
  }

  function loadItems() {
    beginRequest();
    return apiRequest("GET", buildListUrl())
      .then(function (data) {
        state.loadFailed = false;
        state.items = data && data.items ? data.items : [];
        state.total = data ? data.total : 0;
        state.totalPages = data ? data.total_pages : 1;
        state.hasPrev = !!(data && data.has_prev);
        state.hasNext = !!(data && data.has_next);
        state.page = data ? data.page : 1;
        if (data && data.filters) {
          if (typeof data.filters.q === "string") {
            state.q = data.filters.q;
          }
          if (data.filters.status) {
            state.status = data.filters.status;
          }
        }
        endRequest();
        renderList();
        renderPagination();
      })
      .catch(function (error) {
        state.loadFailed = true;
        endRequest();
        renderList();
        renderPagination();
        showMessage(formatError(error), "error");
      });
  }

  function handleCreate(event) {
    event.preventDefault();
    if (state.pending > 0) {
      return;
    }
    var payload = {
      title: dom.titleInput.value,
      status: dom.statusSelect.value,
      priority: dom.prioritySelect.value
    };
    beginRequest();
    apiRequest("POST", "/api/items", payload)
      .then(function (item) {
        endRequest();
        dom.titleInput.value = "";
        showMessage("已新增事项「" + item.title + "」", "success");
        return loadItems();
      })
      .catch(function (error) {
        endRequest();
        showMessage(formatError(error), "error");
        dom.titleInput.focus();
      });
  }

  function saveItem(itemId, payload) {
    if (state.pending > 0) {
      return;
    }
    beginRequest();
    apiRequest("PUT", "/api/items/" + itemId, payload)
      .then(function (item) {
        endRequest();
        showMessage("已更新事项「" + item.title + "」", "success");
        return loadItems();
      })
      .catch(function (error) {
        endRequest();
        showMessage(formatError(error), "error");
      });
  }

  function removeItem(itemId) {
    if (state.pending > 0) {
      return;
    }
    beginRequest();
    apiRequest("DELETE", "/api/items/" + itemId)
      .then(function () {
        endRequest();
        showMessage("已删除事项", "success");
        if (state.items.length <= 1 && state.page > 1) {
          state.page -= 1;
        }
        return loadItems();
      })
      .catch(function (error) {
        endRequest();
        showMessage(formatError(error), "error");
        return loadItems();
      });
  }

  function handleSearch(event) {
    event.preventDefault();
    state.q = dom.searchInput.value;
    state.page = 1;
    return loadItems();
  }

  function handleStatusFilter() {
    state.status = dom.statusFilter.value;
    state.page = 1;
    return loadItems();
  }

  function handleReset() {
    dom.searchInput.value = "";
    dom.statusFilter.value = "all";
    state.q = "";
    state.status = "all";
    state.page = 1;
    return loadItems();
  }

  function gotoPage(delta) {
    if (state.pending > 0) {
      return;
    }
    if (delta > 0 && !state.hasNext) {
      return;
    }
    if (delta < 0 && !state.hasPrev) {
      return;
    }
    state.page += delta;
    return loadItems();
  }

  function fillSelect(select, entries, selectedValue) {
    clearNode(select);
    entries.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.value;
      option.textContent = entry.label;
      select.appendChild(option);
    });
    if (selectedValue) {
      select.value = selectedValue;
    }
  }

  function syncSelectOptions() {
    var currentStatus = dom.statusSelect.value;
    var currentPriority = dom.prioritySelect.value;
    var currentFilter = dom.statusFilter.value;
    fillSelect(dom.statusSelect, state.statuses, currentStatus);
    fillSelect(dom.prioritySelect, state.priorities, currentPriority);
    fillSelect(
      dom.statusFilter,
      [{ value: "all", label: "全部" }].concat(state.statuses),
      currentFilter
    );
  }

  function loadMeta() {
    return apiRequest("GET", "/api/meta")
      .then(function (meta) {
        if (meta && Array.isArray(meta.statuses) && meta.statuses.length) {
          state.statuses = meta.statuses.map(function (entry) {
            return { value: String(entry.value), label: String(entry.label) };
          });
        }
        if (meta && Array.isArray(meta.priorities) && meta.priorities.length) {
          state.priorities = meta.priorities.map(function (entry) {
            return { value: String(entry.value), label: String(entry.label) };
          });
        }
        if (meta && meta.page_size && meta.page_size.default) {
          state.pageSize = meta.page_size.default;
        }
        if (meta && meta.defaults) {
          if (meta.defaults.status) {
            dom.statusSelect.value = meta.defaults.status;
          }
          if (meta.defaults.priority) {
            dom.prioritySelect.value = meta.defaults.priority;
          }
        }
        buildLabelMaps();
        syncSelectOptions();
      })
      .catch(function (error) {
        buildLabelMaps();
        showMessage(formatError(error), "error");
      });
  }

  function init() {
    cacheDom();
    buildLabelMaps();
    dom.createForm.addEventListener("submit", handleCreate);
    dom.filterForm.addEventListener("submit", handleSearch);
    dom.statusFilter.addEventListener("change", handleStatusFilter);
    dom.filterReset.addEventListener("click", handleReset);
    dom.prevButton.addEventListener("click", function () {
      gotoPage(-1);
    });
    dom.nextButton.addEventListener("click", function () {
      gotoPage(1);
    });
    renderPagination();
    loadMeta().then(loadItems);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
