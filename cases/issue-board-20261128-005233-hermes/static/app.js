/* Issue Board frontend — vanilla JS, no framework.
 *
 * SECURITY: all user-supplied text is rendered with textContent ONLY.
 * innerHTML is never used with user data (XSS defense, criterion D3).
 */
(function () {
  "use strict";

  var STATUS_LABEL = { todo: "待办", doing: "进行中", done: "完成" };
  var PRIORITY_LABEL = { low: "低", medium: "中", high: "高" };
  var MAX_TITLE_LEN = 80;
  var PAGE_SIZE = 10;

  var state = {
    page: 1,
    totalPages: 1,
    total: 0,
    keyword: "",
    status: "",
    editingId: null, // null = create mode; number = editing that issue
    loading: false,
  };

  // ---- DOM refs ----------------------------------------------------------
  var form = document.getElementById("issue-form");
  var titleInput = document.getElementById("f-title");
  var statusSelect = document.getElementById("f-status");
  var prioritySelect = document.getElementById("f-priority");
  var submitBtn = document.getElementById("submit-btn");
  var cancelEditBtn = document.getElementById("cancel-edit-btn");
  var formError = document.getElementById("form-error");
  var titleCount = document.getElementById("title-count");

  var keywordInput = document.getElementById("q-keyword");
  var statusFilter = document.getElementById("q-status");
  var resetBtn = document.getElementById("q-reset");

  var listEl = document.getElementById("issue-list");
  var emptyState = document.getElementById("empty-state");
  var listStatus = document.getElementById("list-status");
  var pageInfo = document.getElementById("page-info");
  var prevBtn = document.getElementById("prev-page");
  var nextBtn = document.getElementById("next-page");
  var toastArea = document.getElementById("toast-area");

  // ---- helpers -----------------------------------------------------------
  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json; charset=utf-8";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (resp) {
      return resp.json().then(function (payload) {
        return { status: resp.status, payload: payload };
      }, function () {
        throw new Error("服务器返回了非 JSON 响应 (HTTP " + resp.status + ")");
      });
    });
  }

  function toast(message, kind) {
    var el = document.createElement("div");
    el.className = "toast " + (kind || "success");
    el.textContent = message; // textContent only — never innerHTML
    toastArea.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 3500);
  }

  function showFormError(message) {
    formError.textContent = message;
    formError.classList.remove("hidden");
  }

  function clearFormError() {
    formError.textContent = "";
    formError.classList.add("hidden");
  }

  function setListStatus(message) {
    listStatus.textContent = message || "";
  }

  // ---- rendering ---------------------------------------------------------
  function renderIssue(issue) {
    var li = document.createElement("li");
    li.className = "issue-item" + (state.editingId === issue.id ? " editing" : "");
    li.dataset.id = String(issue.id);

    var main = document.createElement("div");
    main.className = "issue-main";

    var titleEl = document.createElement("div");
    titleEl.className = "issue-title";
    titleEl.textContent = issue.title; // literal text, XSS-safe

    var meta = document.createElement("div");
    meta.className = "issue-meta";

    var statusBadge = document.createElement("span");
    statusBadge.className = "badge status-" + issue.status;
    statusBadge.textContent = STATUS_LABEL[issue.status] || issue.status;

    var prioBadge = document.createElement("span");
    prioBadge.className = "badge priority-" + issue.priority;
    prioBadge.textContent = "优先级：" + (PRIORITY_LABEL[issue.priority] || issue.priority);

    var idSpan = document.createElement("span");
    idSpan.textContent = "#" + issue.id;

    var updatedSpan = document.createElement("span");
    updatedSpan.textContent = "更新于 " + issue.updated_at.replace("T", " ").replace(/\.\d+Z$/, "Z");

    meta.appendChild(statusBadge);
    meta.appendChild(prioBadge);
    meta.appendChild(idSpan);
    meta.appendChild(updatedSpan);

    main.appendChild(titleEl);
    main.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "issue-actions";

    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "secondary";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", function () { startEdit(issue); });

    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-danger";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", function () { removeIssue(issue); });

    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    li.appendChild(main);
    li.appendChild(actions);
    return li;
  }

  function renderList(items) {
    // Clear children safely
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

    var hasItems = items && items.length > 0;
    emptyState.classList.toggle("hidden", hasItems);
    items.forEach(function (issue) {
      listEl.appendChild(renderIssue(issue));
    });
  }

  function renderPagination() {
    var tp = Math.max(state.totalPages, 1);
    pageInfo.textContent = "第 " + state.page + " / " + tp + " 页 · 共 " + state.total + " 条";
    prevBtn.disabled = state.page <= 1;
    nextBtn.disabled = state.page >= state.totalPages;
  }

  // ---- data loading ------------------------------------------------------
  function loadList() {
    state.loading = true;
    setListStatus("加载中…");
    var qs = [];
    if (state.keyword) qs.push("keyword=" + encodeURIComponent(state.keyword));
    if (state.status) qs.push("status=" + encodeURIComponent(state.status));
    qs.push("page=" + state.page);
    qs.push("page_size=" + PAGE_SIZE);

    return api("GET", "/api/issues?" + qs.join("&")).then(function (res) {
      state.loading = false;
      if (res.status === 200 && res.payload.data) {
        var data = res.payload.data;
        state.total = data.total;
        state.totalPages = data.total_pages;
        if (state.page > data.total_pages && data.total_pages > 0) {
          // Page fell out of range after delete — clamp and reload.
          state.page = data.total_pages;
          return loadList();
        }
        renderList(data.items);
        renderPagination();
        var filterNote = (state.keyword || state.status) ? "（当前筛选条件下）" : "";
        setListStatus(data.total === 0 ? "没有匹配的事项" + filterNote
                                       : "共 " + data.total + " 条事项" + filterNote);
      } else {
        var msg = (res.payload && res.payload.error && res.payload.error.message)
                  || ("加载失败 (HTTP " + res.status + ")");
        renderList([]);
        setListStatus("加载失败：" + msg);
        toast("加载失败：" + msg, "error");
      }
    }).catch(function (e) {
      state.loading = false;
      renderList([]);
      setListStatus("加载失败：" + e.message);
      toast("无法连接服务器：" + e.message, "error");
    });
  }

  // ---- form / create / edit ---------------------------------------------
  function validateTitleClient(title) {
    var stripped = title.trim();
    if (!stripped) return "标题去除两端空白后必填";
    if (stripped.length > MAX_TITLE_LEN)
      return "标题不得超过 " + MAX_TITLE_LEN + " 字（当前 " + stripped.length + " 字）";
    return null;
  }

  function updateTitleCount() {
    titleCount.textContent = String(titleInput.value.trim().length);
  }

  function resetForm() {
    form.reset();
    state.editingId = null;
    submitBtn.textContent = "新增";
    cancelEditBtn.classList.add("hidden");
    clearFormError();
    updateTitleCount();
    // remove editing highlight
    var editing = listEl.querySelectorAll(".issue-item.editing");
    for (var i = 0; i < editing.length; i++) editing[i].classList.remove("editing");
  }

  function startEdit(issue) {
    state.editingId = issue.id;
    titleInput.value = issue.title;
    statusSelect.value = issue.status;
    prioritySelect.value = issue.priority;
    submitBtn.textContent = "保存修改";
    cancelEditBtn.classList.remove("hidden");
    clearFormError();
    updateTitleCount();
    titleInput.focus();
    // highlight item
    var items = listEl.querySelectorAll(".issue-item");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("editing", items[i].dataset.id === String(issue.id));
    }
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function handleSubmit(ev) {
    ev.preventDefault();
    clearFormError();

    var title = titleInput.value;
    var clientErr = validateTitleClient(title);
    if (clientErr) { showFormError(clientErr); return; }

    var body = {
      title: title,
      status: statusSelect.value,
      priority: prioritySelect.value,
    };

    submitBtn.disabled = true;
    var request;
    if (state.editingId === null) {
      request = api("POST", "/api/issues", body);
    } else {
      request = api("PUT", "/api/issues/" + state.editingId, body);
    }

    request.then(function (res) {
      submitBtn.disabled = false;
      if ((res.status === 201 || res.status === 200) && res.payload.data) {
        var isEdit = state.editingId !== null;
        toast(isEdit ? "修改已保存：" + res.payload.data.title
                     : "新增成功：" + res.payload.data.title, "success");
        resetForm();
        return loadList();
      }
      var errObj = res.payload && res.payload.error;
      var msg = errObj ? (errObj.code + "：" + errObj.message)
                       : ("操作失败 (HTTP " + res.status + ")");
      showFormError(msg);
      toast("操作失败：" + msg, "error");
    }).catch(function (e) {
      submitBtn.disabled = false;
      showFormError("无法连接服务器：" + e.message);
      toast("无法连接服务器：" + e.message, "error");
    });
  }

  function removeIssue(issue) {
    if (!window.confirm("确定删除事项「" + issue.title + "」吗？")) return;
    api("DELETE", "/api/issues/" + issue.id).then(function (res) {
      if (res.status === 200 && res.payload.data && res.payload.data.deleted) {
        toast("已删除：" + issue.title, "success");
        if (state.editingId === issue.id) resetForm();
        return loadList();
      }
      var errObj = res.payload && res.payload.error;
      var msg = errObj ? (errObj.code + "：" + errObj.message)
                       : ("删除失败 (HTTP " + res.status + ")");
      toast(msg, "error");
    }).catch(function (e) {
      toast("无法连接服务器：" + e.message, "error");
    });
  }

  // ---- search / filter / pagination events -------------------------------
  var searchTimer = null;
  keywordInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.keyword = keywordInput.value.trim();
      state.page = 1;
      loadList();
    }, 300);
  });

  statusFilter.addEventListener("change", function () {
    state.status = statusFilter.value;
    state.page = 1;
    loadList();
  });

  resetBtn.addEventListener("click", function () {
    keywordInput.value = "";
    statusFilter.value = "";
    state.keyword = "";
    state.status = "";
    state.page = 1;
    loadList();
  });

  prevBtn.addEventListener("click", function () {
    if (state.page > 1) { state.page--; loadList(); }
  });
  nextBtn.addEventListener("click", function () {
    if (state.page < state.totalPages) { state.page++; loadList(); }
  });

  form.addEventListener("submit", handleSubmit);
  cancelEditBtn.addEventListener("click", resetForm);
  titleInput.addEventListener("input", updateTitleCount);

  // ---- init --------------------------------------------------------------
  updateTitleCount();
  loadList();
})();
