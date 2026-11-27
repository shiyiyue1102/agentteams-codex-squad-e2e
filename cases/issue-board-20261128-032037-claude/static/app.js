/* 事项管理前端逻辑（原生 JS，无外部依赖）。
 *
 * 安全约定：所有用户可控文本（标题、服务端 error.message）一律通过
 * textContent / createTextNode 渲染；本文件不使用 innerHTML、
 * insertAdjacentHTML 或 document.write。
 *
 * 网络约定：同源相对路径调用 /api/...，不硬编码 host:port。
 */
(function () {
  'use strict';

  var STATUS_LABEL = { todo: '待办', in_progress: '进行中', done: '完成' };
  var PRIORITY_LABEL = { low: '低', medium: '中', high: '高' };

  var state = {
    page: 1,
    pageSize: 20,
    totalPages: 1,
    total: 0,
    q: '',
    status: '',
    loading: false,
    editingId: null,
    pendingDeleteId: null, // 两步删除确认：当前待确认的 id
    lastListResult: null
  };

  // ---- DOM 引用 ----
  var elForm = document.getElementById('item-form');
  var elFormTitle = document.getElementById('form-title');
  var elEditId = document.getElementById('edit-id');
  var elTitleInput = document.getElementById('title-input');
  var elStatusSelect = document.getElementById('status-select');
  var elPrioritySelect = document.getElementById('priority-select');
  var elSubmitBtn = document.getElementById('submit-btn');
  var elCancelEditBtn = document.getElementById('cancel-edit-btn');
  var elMessage = document.getElementById('message');
  var elSearchInput = document.getElementById('search-input');
  var elFilterStatus = document.getElementById('filter-status');
  var elLoading = document.getElementById('loading');
  var elList = document.getElementById('item-list');
  var elEmpty = document.getElementById('empty-state');
  var elPrevBtn = document.getElementById('prev-btn');
  var elNextBtn = document.getElementById('next-btn');
  var elPageInfo = document.getElementById('page-info');

  var messageTimer = null;

  // ---- 基础工具 ----

  function showMessage(text, kind) {
    elMessage.textContent = text; // 纯文字渲染，防 XSS
    elMessage.className = 'message ' + kind;
    elMessage.hidden = false;
    if (messageTimer) { clearTimeout(messageTimer); }
    messageTimer = setTimeout(function () {
      elMessage.hidden = true;
      messageTimer = null;
    }, 6000);
  }

  function api(path, options) {
    options = options || {};
    return fetch(path, options).then(function (resp) {
      if (resp.status === 204) {
        if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
        return { status: resp.status, data: null };
      }
      return resp.text().then(function (raw) {
        var data = null;
        if (raw) {
          try { data = JSON.parse(raw); } catch (e) { data = null; }
        }
        if (!resp.ok) {
          var message = (data && data.error && data.error.message)
            ? data.error.message
            : ('请求失败（HTTP ' + resp.status + '）');
          var err = new Error(message);
          err.status = resp.status;
          err.data = data;
          throw err;
        }
        return { status: resp.status, data: data };
      });
    });
  }

  function setBusy(busy) {
    state.loading = busy;
    elLoading.hidden = !busy;
    elSubmitBtn.disabled = busy;
    elPrevBtn.disabled = busy || state.page <= 1;
    elNextBtn.disabled = busy || state.page >= state.totalPages;
    var actionButtons = elList.querySelectorAll('button');
    for (var i = 0; i < actionButtons.length; i++) {
      actionButtons[i].disabled = busy;
    }
  }

  // ---- 列表渲染 ----

  function formatTime(iso) {
    // 服务端固定返回 UTC "YYYY-MM-DDTHH:MM:SSZ"，转为本地时间展示
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    function pad(n) { return n < 10 ? '0' + n : String(n); }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function buildItemCard(item) {
    var card = document.createElement('li');
    card.className = 'item-card';
    card.setAttribute('data-id', String(item.id));

    var main = document.createElement('div');
    main.className = 'item-main';

    var title = document.createElement('div');
    title.className = 'item-title';
    title.textContent = item.title; // 纯文字渲染，防 XSS

    var meta = document.createElement('div');
    meta.className = 'item-meta';

    var statusBadge = document.createElement('span');
    statusBadge.className = 'badge status-' + String(item.status).replace(/[^a-z_]/g, '');
    statusBadge.textContent = STATUS_LABEL[item.status] || item.status;

    var prioBadge = document.createElement('span');
    prioBadge.className = 'badge prio-' + String(item.priority).replace(/[^a-z]/g, '');
    prioBadge.textContent = '优先级：' + (PRIORITY_LABEL[item.priority] || item.priority);

    var time = document.createElement('span');
    time.className = 'item-time';
    time.textContent = '创建于 ' + formatTime(item.created_at) +
      ' · 更新于 ' + formatTime(item.updated_at);

    meta.appendChild(statusBadge);
    meta.appendChild(prioBadge);
    meta.appendChild(time);
    main.appendChild(title);
    main.appendChild(meta);

    var actions = document.createElement('div');
    actions.className = 'item-actions';

    var editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'secondary small';
    editBtn.textContent = '编辑';
    editBtn.addEventListener('click', function () { startEdit(item); });

    var delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'danger small';
    delBtn.textContent = '删除';
    // 删除走 elList 上的统一事件委托（两步确认），此处不单独绑定

    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    card.appendChild(main);
    card.appendChild(actions);
    return card;
  }

  function renderList(result) {
    state.lastListResult = result;
    elList.textContent = ''; // 清空（安全 API）

    var items = result.items || [];
    for (var i = 0; i < items.length; i++) {
      elList.appendChild(buildItemCard(items[i]));
    }

    var filtering = state.q !== '' || state.status !== '';
    if (items.length === 0) {
      elEmpty.hidden = false;
      if (filtering) {
        elEmpty.textContent = '无匹配结果：没有符合当前搜索/筛选条件的事项，请调整条件。';
      } else {
        elEmpty.textContent = '暂无事项：使用上方表单创建第一条事项。';
      }
    } else {
      elEmpty.hidden = true;
      elEmpty.textContent = '';
    }

    elPageInfo.textContent =
      '第 ' + result.page + ' / ' + result.total_pages + ' 页 · 共 ' + result.total + ' 条';
    elPrevBtn.disabled = state.loading || result.page <= 1;
    elNextBtn.disabled = state.loading || result.page >= result.total_pages;
  }

  function loadList() {
    var params = [];
    if (state.q !== '') { params.push('q=' + encodeURIComponent(state.q)); }
    if (state.status !== '') { params.push('status=' + encodeURIComponent(state.status)); }
    params.push('page=' + encodeURIComponent(String(state.page)));
    params.push('page_size=' + encodeURIComponent(String(state.pageSize)));
    var url = '/api/items?' + params.join('&');

    setBusy(true);
    api(url).then(function (result) {
      state.total = result.data.total;
      state.totalPages = result.data.total_pages;
      renderList(result.data);
      setBusy(false);
    }).catch(function (err) {
      setBusy(false);
      showMessage('加载列表失败：' + err.message, 'err');
    });
  }

  // ---- 新增 / 编辑 ----

  function resetForm() {
    state.editingId = null;
    elEditId.value = '';
    elFormTitle.textContent = '新增事项';
    elSubmitBtn.textContent = '创建';
    elCancelEditBtn.hidden = true;
    elForm.reset();
    elStatusSelect.value = 'todo';
    elPrioritySelect.value = 'medium';
  }

  function startEdit(item) {
    state.editingId = item.id;
    elEditId.value = String(item.id);
    elFormTitle.textContent = '编辑事项 #' + item.id;
    elSubmitBtn.textContent = '保存';
    elCancelEditBtn.hidden = false;
    elTitleInput.value = item.title;
    elStatusSelect.value = item.status;
    elPrioritySelect.value = item.priority;
    elTitleInput.focus();
    elForm.scrollIntoView({ block: 'nearest' });
  }

  function onSubmit(event) {
    event.preventDefault();
    if (state.loading) { return; }

    var payload = {
      title: elTitleInput.value,
      status: elStatusSelect.value,
      priority: elPrioritySelect.value
    };
    var editing = state.editingId !== null;
    var url = editing ? ('/api/items/' + state.editingId) : '/api/items';
    var method = editing ? 'PUT' : 'POST';

    setBusy(true);
    api(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function () {
      setBusy(false);
      if (editing) {
        showMessage('已更新：事项 #' + state.editingId + ' 保存成功。', 'ok');
      } else {
        showMessage('已创建：' + payload.title.trim(), 'ok');
      }
      resetForm();
      loadList();
    }).catch(function (err) {
      setBusy(false);
      showMessage((editing ? '更新失败：' : '创建失败：') + err.message, 'err');
    });
  }

  // ---- 删除（两步确认，防误删） ----

  function clearPendingDelete() {
    if (state.pendingDeleteId === null) { return; }
    state.pendingDeleteId = null;
    var cards = elList.querySelectorAll('.item-card.confirming');
    for (var i = 0; i < cards.length; i++) {
      cards[i].classList.remove('confirming');
      var btn = cards[i].querySelector('button.danger');
      if (btn) { btn.textContent = '删除'; }
    }
  }

  elList.addEventListener('click', function (event) {
    var btn = event.target.closest ? event.target.closest('button.danger') : null;
    if (!btn || state.loading) { return; }
    var card = btn.closest('.item-card');
    if (!card) { return; }
    var id = Number(card.getAttribute('data-id'));

    if (state.pendingDeleteId !== id) {
      // 第一次点击：进入待确认状态（按钮变为「确认删除？」）
      clearPendingDelete();
      state.pendingDeleteId = id;
      btn.textContent = '确认删除？';
      card.classList.add('confirming');
      return;
    }

    // 第二次点击：执行删除
    setBusy(true);
    api('/api/items/' + id, { method: 'DELETE' }).then(function () {
      setBusy(false);
      state.pendingDeleteId = null;
      showMessage('已删除：事项 #' + id + ' 已删除。', 'ok');
      if (state.editingId === id) { resetForm(); }
      // 删除后若当前页将越界，回退到新的最后一页
      var newTotal = Math.max(0, state.total - 1);
      var newTotalPages = Math.max(1, Math.ceil(newTotal / state.pageSize));
      if (state.page > newTotalPages) { state.page = newTotalPages; }
      loadList();
    }).catch(function (err) {
      setBusy(false);
      state.pendingDeleteId = null;
      clearPendingDelete();
      showMessage('删除失败：' + err.message, 'err');
    });
  });

  // ---- 搜索 / 筛选 / 分页 ----

  var searchTimer = null;
  elSearchInput.addEventListener('input', function () {
    if (searchTimer) { clearTimeout(searchTimer); }
    searchTimer = setTimeout(function () {
      state.q = elSearchInput.value.trim();
      state.page = 1;
      loadList();
    }, 250);
  });

  elFilterStatus.addEventListener('change', function () {
    state.status = elFilterStatus.value;
    state.page = 1;
    loadList();
  });

  elPrevBtn.addEventListener('click', function () {
    if (state.page > 1) { state.page--; loadList(); }
  });

  elNextBtn.addEventListener('click', function () {
    if (state.page < state.totalPages) { state.page++; loadList(); }
  });

  elCancelEditBtn.addEventListener('click', function () {
    resetForm();
    showMessage('已取消编辑。', 'ok');
  });

  elForm.addEventListener('submit', onSubmit);

  // 点击列表空白处取消待确认删除
  document.addEventListener('click', function (event) {
    if (state.pendingDeleteId === null) { return; }
    if (event.target.closest && event.target.closest('.item-card')) { return; }
    clearPendingDelete();
  });

  // 初始化
  resetForm();
  loadList();
})();
