// 事项管理前端：原生 JS，fetch 调用 REST API。
// 所有用户文本一律通过 textContent 写入，绝不使用 innerHTML，防止 XSS 执行 HTML。

const STATUS_LABEL = { todo: '待办', doing: '进行中', done: '完成' };
const PRIORITY_LABEL = { low: '低', medium: '中', high: '高' };
const PAGE_SIZE = 10;

const state = {
  page: 1,
  totalPages: 1,
  total: 0,
  q: '',
  status: '',
  editingId: null,
};

const $ = (id) => document.getElementById(id);

const messageEl = $('message');
const loadingEl = $('loading');
const emptyEl = $('empty');
const listEl = $('item-list');
const paginationEl = $('pagination');
const pageInfoEl = $('page-info');
const formEl = $('item-form');
const formTitleEl = $('form-title');
const editIdEl = $('edit-id');
const titleInput = $('title-input');
const statusInput = $('status-input');
const priorityInput = $('priority-input');
const submitBtn = $('submit-btn');
const cancelEditBtn = $('cancel-edit-btn');
const searchInput = $('search-input');
const filterStatus = $('filter-status');
const prevBtn = $('prev-btn');
const nextBtn = $('next-btn');

let messageTimer = null;

function showMessage(text, kind) {
  messageEl.textContent = text; // textContent：按文字显示，不执行 HTML
  messageEl.className = 'message ' + (kind || 'info');
  if (messageTimer) clearTimeout(messageTimer);
  messageTimer = setTimeout(() => { messageEl.className = 'message hidden'; }, 4000);
}

function setLoading(on) {
  loadingEl.classList.toggle('hidden', !on);
}

function apiErrorText(err) {
  if (err && err.error && err.error.message) return err.error.message;
  return '请求失败，请稍后重试';
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  }, options || {}));
  if (res.status === 204) return { ok: true, status: 204, data: null };
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!res.ok) {
    const error = new Error(apiErrorText(data));
    error.payload = data;
    error.status = res.status;
    throw error;
  }
  return { ok: true, status: res.status, data };
}

function buildListQuery() {
  const params = new URLSearchParams();
  params.set('page', String(state.page));
  params.set('page_size', String(PAGE_SIZE));
  if (state.q) params.set('q', state.q);
  if (state.status) params.set('status', state.status);
  return '/api/items?' + params.toString();
}

async function loadItems() {
  setLoading(true);
  try {
    const { data } = await api(buildListQuery());
    state.total = data.total;
    state.totalPages = data.total_pages;
    renderList(data.items);
    renderPagination();
  } catch (err) {
    listEl.replaceChildren();
    emptyEl.classList.add('hidden');
    paginationEl.classList.add('hidden');
    showMessage('加载失败：' + apiErrorText(err), 'error');
  } finally {
    setLoading(false);
  }
}

function renderList(items) {
  listEl.replaceChildren();
  if (!items.length) {
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'item';
    li.dataset.id = String(item.id);

    const main = document.createElement('div');
    main.className = 'item-main';

    const title = document.createElement('span');
    title.className = 'item-title';
    title.textContent = item.title; // textContent：XSS 文本原样显示

    const meta = document.createElement('div');
    meta.className = 'item-meta';

    const statusBadge = document.createElement('span');
    statusBadge.className = 'badge status-' + item.status;
    statusBadge.textContent = STATUS_LABEL[item.status] || item.status;

    const priorityBadge = document.createElement('span');
    priorityBadge.className = 'badge priority-' + item.priority;
    priorityBadge.textContent = '优先级：' + (PRIORITY_LABEL[item.priority] || item.priority);

    const time = document.createElement('span');
    time.className = 'item-time';
    time.textContent = item.created_at;

    meta.append(statusBadge, priorityBadge, time);
    main.append(title, meta);

    const btns = document.createElement('div');
    btns.className = 'item-actions';

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'secondary';
    editBtn.textContent = '编辑';
    editBtn.addEventListener('click', () => startEdit(item));

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'danger';
    delBtn.textContent = '删除';
    delBtn.addEventListener('click', () => deleteItem(item));

    btns.append(editBtn, delBtn);
    li.append(main, btns);
    listEl.appendChild(li);
  }
}

function renderPagination() {
  if (state.total === 0) {
    paginationEl.classList.add('hidden');
    return;
  }
  paginationEl.classList.remove('hidden');
  pageInfoEl.textContent = '第 ' + state.page + ' / ' + state.totalPages + ' 页（共 ' + state.total + ' 条）';
  prevBtn.disabled = state.page <= 1;
  nextBtn.disabled = state.page >= state.totalPages;
}

function startEdit(item) {
  state.editingId = item.id;
  editIdEl.value = String(item.id);
  titleInput.value = item.title;
  statusInput.value = item.status;
  priorityInput.value = item.priority;
  formTitleEl.textContent = '编辑事项 #' + item.id;
  submitBtn.textContent = '保存修改';
  cancelEditBtn.classList.remove('hidden');
  titleInput.focus();
}

function resetForm() {
  state.editingId = null;
  editIdEl.value = '';
  formEl.reset();
  formTitleEl.textContent = '新增事项';
  submitBtn.textContent = '新增';
  cancelEditBtn.classList.add('hidden');
}

async function handleSubmit(event) {
  event.preventDefault();
  const title = titleInput.value;
  const status = statusInput.value;
  const priority = priorityInput.value;
  const payload = { title, status, priority };
  submitBtn.disabled = true;
  try {
    if (state.editingId != null) {
      await api('/api/items/' + state.editingId, { method: 'PUT', body: JSON.stringify(payload) });
      showMessage('修改成功', 'success');
    } else {
      await api('/api/items', { method: 'POST', body: JSON.stringify(payload) });
      showMessage('新增成功', 'success');
      state.page = 1; // 新建后回到第一页（新事项在最前）
    }
    resetForm();
    await loadItems();
  } catch (err) {
    showMessage(apiErrorText(err), 'error');
  } finally {
    submitBtn.disabled = false;
  }
}

async function deleteItem(item) {
  if (!window.confirm('确认删除事项「' + item.title + '」？')) return;
  try {
    await api('/api/items/' + item.id, { method: 'DELETE' });
    showMessage('删除成功', 'success');
    // 如果删的是当前页最后一条且不是第一页，回退一页
    if (state.total - 1 <= (state.page - 1) * PAGE_SIZE && state.page > 1) {
      state.page -= 1;
    }
    await loadItems();
  } catch (err) {
    showMessage('删除失败：' + apiErrorText(err), 'error');
  }
}

let searchDebounce = null;
searchInput.addEventListener('input', () => {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.q = searchInput.value.trim();
    state.page = 1;
    loadItems();
  }, 300);
});

filterStatus.addEventListener('change', () => {
  state.status = filterStatus.value;
  state.page = 1;
  loadItems();
});

prevBtn.addEventListener('click', () => {
  if (state.page > 1) { state.page -= 1; loadItems(); }
});
nextBtn.addEventListener('click', () => {
  if (state.page < state.totalPages) { state.page += 1; loadItems(); }
});

cancelEditBtn.addEventListener('click', resetForm);
formEl.addEventListener('submit', handleSubmit);

loadItems();
