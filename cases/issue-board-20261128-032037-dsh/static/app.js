'use strict';

// 事项管理前端逻辑（原生 JS，同源相对路径 /api/...，无外部依赖）。
// XSS 硬性规则：所有用户可控文本只经 textContent / createTextNode / 表单 .value
// 写入 DOM；本文件禁止使用 innerHTML / insertAdjacentHTML / document.write。

var PAGE_SIZE = 10;
var STATUS_LABELS = { todo: '待办', in_progress: '进行中', done: '完成' };
var PRIORITY_LABELS = { low: '低', medium: '中', high: '高' };

var state = {
  keyword: '',
  status: '',
  page: 1,
  total: 0,
  totalPages: 0,
  items: [],
  editingId: null
};

function $(id) {
  return document.getElementById(id);
}

function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text; // 用户文本一律以文字写入
  }
  return node;
}

function buildQuery() {
  var params = [];
  if (state.keyword) {
    params.push('keyword=' + encodeURIComponent(state.keyword));
  }
  if (state.status) {
    params.push('status=' + encodeURIComponent(state.status));
  }
  params.push('page=' + encodeURIComponent(String(state.page)));
  params.push('page_size=' + PAGE_SIZE);
  return params.join('&');
}

function showMessage(text, isError) {
  var box = $('message');
  box.textContent = text; // 失败原因（含服务端 message）按文字显示
  box.className = 'message ' + (isError ? 'message-error' : 'message-success');
  box.hidden = false;
}

function clearMessage() {
  var box = $('message');
  box.hidden = true;
  box.textContent = '';
}

function apiRequest(method, url, body) {
  var options = { method: method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  return fetch(url, options).then(function (res) {
    return res.text().then(function (text) {
      var data = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (parseError) {
          data = null;
        }
      }
      if (!res.ok) {
        var message = (data && data.error && data.error.message)
          ? data.error.message
          : ('请求失败（HTTP ' + res.status + '）');
        var err = new Error(message);
        err.code = data && data.error ? data.error.code : undefined;
        throw err;
      }
      return data;
    });
  }, function (networkError) {
    throw new Error('网络请求失败：' + (networkError && networkError.message
      ? networkError.message : '无法连接服务器'));
  });
}

function loadList() {
  $('loading').hidden = false;
  return apiRequest('GET', '/api/issues?' + buildQuery()).then(function (data) {
    state.total = data.total;
    state.totalPages = data.total_pages;
    state.items = data.items;
    renderList();
    renderPagination();
  }).catch(function (err) {
    showMessage('加载失败：' + err.message, true);
  }).then(function () {
    $('loading').hidden = true;
  });
}

function renderList() {
  var list = $('issue-list');
  while (list.firstChild) {
    list.removeChild(list.firstChild);
  }
  var emptyBox = $('empty-text');
  if (state.items.length === 0) {
    // 两种空态必须可区分：有搜索/筛选条件 -> 无匹配；否则 -> 暂无事项
    var filtered = Boolean(state.keyword || state.status);
    emptyBox.textContent = filtered
      ? '无匹配结果，试试调整关键词或状态筛选'
      : '暂无事项，点击右上角「新建事项」开始吧';
    emptyBox.hidden = false;
    return;
  }
  emptyBox.hidden = true;
  emptyBox.textContent = '';

  state.items.forEach(function (issue) {
    var card = el('li', 'issue-card');

    var main = el('div', 'issue-main');
    main.appendChild(el('span', 'issue-title', issue.title));
    var badges = el('div', 'issue-badges');
    var statusLabel = STATUS_LABELS[issue.status] || issue.status;
    var priorityLabel = PRIORITY_LABELS[issue.priority] || issue.priority;
    // 仅当值为白名单枚举键时才拼接修饰类名（值域固定，非自由文本）
    var statusClass = Object.prototype.hasOwnProperty.call(STATUS_LABELS, issue.status)
      ? ' status-' + issue.status : '';
    var priorityClass = Object.prototype.hasOwnProperty.call(PRIORITY_LABELS, issue.priority)
      ? ' priority-' + issue.priority : '';
    badges.appendChild(el('span', 'badge badge-status' + statusClass, statusLabel));
    badges.appendChild(el('span', 'badge badge-priority' + priorityClass, priorityLabel));
    main.appendChild(badges);
    card.appendChild(main);

    card.appendChild(el('div', 'issue-meta',
      '创建于 ' + issue.created_at + '，更新于 ' + issue.updated_at));

    var actions = el('div', 'issue-actions');
    var editBtn = el('button', 'btn-link', '编辑');
    editBtn.type = 'button';
    editBtn.addEventListener('click', function () {
      openEditForm(issue);
    });
    var deleteBtn = el('button', 'btn-link btn-danger', '删除');
    deleteBtn.type = 'button';
    deleteBtn.addEventListener('click', function () {
      removeIssue(issue);
    });
    actions.appendChild(editBtn);
    actions.appendChild(deleteBtn);
    card.appendChild(actions);

    list.appendChild(card);
  });
}

function renderPagination() {
  $('page-info').textContent = '第 ' + state.page + ' / '
    + Math.max(state.totalPages, 1) + ' 页，共 ' + state.total + ' 条';
  $('prev-btn').disabled = state.page <= 1;
  $('next-btn').disabled = state.page >= state.totalPages;
}

function openCreateForm() {
  state.editingId = null;
  $('form-title').textContent = '新建事项';
  $('title-input').value = '';
  $('status-input').value = 'todo';
  $('priority-input').value = 'medium';
  $('form-section').hidden = false;
  $('title-input').focus();
}

function openEditForm(issue) {
  state.editingId = issue.id;
  $('form-title').textContent = '编辑事项 #' + issue.id;
  $('title-input').value = issue.title; // 表单 .value 回显，字面文本
  $('status-input').value = issue.status;
  $('priority-input').value = issue.priority;
  $('form-section').hidden = false;
  $('title-input').focus();
}

function closeForm() {
  $('form-section').hidden = true;
  state.editingId = null;
}

function handleSubmit(event) {
  event.preventDefault();
  clearMessage();
  var isEdit = state.editingId !== null;
  var title = $('title-input').value;
  var status = $('status-input').value;
  var priority = $('priority-input').value;

  // 客户端预校验（与服务端同规则；服务端为权威，其错误必须可见）
  var trimmed = title.trim();
  if (!trimmed) {
    showMessage('保存失败：标题去除两端空白后不能为空', true);
    return Promise.resolve();
  }
  if (trimmed.length > 80) {
    showMessage('保存失败：标题去除两端空白后不能超过 80 字', true);
    return Promise.resolve();
  }

  var payload = { title: title, status: status, priority: priority };
  var url = isEdit ? ('/api/issues/' + state.editingId) : '/api/issues';
  $('save-btn').disabled = true;
  return apiRequest(isEdit ? 'PUT' : 'POST', url, payload).then(function () {
    if (isEdit) {
      showMessage('已保存：' + trimmed, false);
      closeForm();
      return loadList(); // 原地刷新当前页
    }
    showMessage('已创建：' + trimmed, false);
    closeForm();
    state.page = 1; // 新建后回到第 1 页，保留当前搜索/筛选
    return loadList();
  }).catch(function (err) {
    showMessage((isEdit ? '保存失败：' : '创建失败：') + err.message, true);
  }).then(function () {
    $('save-btn').disabled = false;
  });
}

function removeIssue(issue) {
  clearMessage();
  // 删除确认：原生 confirm() 对话框（README 已说明，浏览器测试需处理该对话框）
  if (!window.confirm('确定删除事项「' + issue.title + '」吗？')) {
    return Promise.resolve();
  }
  return apiRequest('DELETE', '/api/issues/' + issue.id).then(function () {
    showMessage('已删除：' + issue.title, false);
    // 当前页删空后回退一页，避免停留在越界空页
    if (state.items.length === 1 && state.page > 1) {
      state.page -= 1;
    }
    return loadList();
  }).catch(function (err) {
    showMessage('删除失败：' + err.message, true);
  });
}

function applySearch() {
  state.keyword = $('keyword-input').value.trim();
  state.page = 1;
  clearMessage();
  return loadList();
}

function applyStatusFilter() {
  state.status = $('filter-status').value;
  state.page = 1;
  clearMessage();
  return loadList();
}

function resetFilters() {
  $('keyword-input').value = '';
  $('filter-status').value = '';
  state.keyword = '';
  state.status = '';
  state.page = 1;
  clearMessage();
  return loadList();
}

function changePage(delta) {
  var target = state.page + delta;
  if (target < 1 || target > state.totalPages) {
    return Promise.resolve();
  }
  state.page = target;
  clearMessage();
  return loadList();
}

function init() {
  $('new-issue-btn').addEventListener('click', function () {
    clearMessage();
    openCreateForm();
  });
  $('cancel-btn').addEventListener('click', closeForm);
  $('issue-form').addEventListener('submit', handleSubmit);
  $('search-btn').addEventListener('click', applySearch);
  $('keyword-input').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      applySearch();
    }
  });
  $('filter-status').addEventListener('change', applyStatusFilter);
  $('reset-btn').addEventListener('click', resetFilters);
  $('prev-btn').addEventListener('click', function () { changePage(-1); });
  $('next-btn').addEventListener('click', function () { changePage(1); });
  loadList();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
