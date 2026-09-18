/* Planner PWA — клиентский офлайн-слой.
 *
 * Что делает:
 *  1. Держит срез задач (последнее, что видел сервер) — база для сравнения правок.
 *  2. Копит правки задач без сети: создание, изменение полей, выполнение, перенос,
 *     в бэклог, подзадачи, архив. Правка хранит и новое значение, и то, каким
 *     поле было на экране, — иначе сервер не сможет отличить «моя правка» от
 *     «это кто-то уже поменял».
 *  3. Досылает накопленное на /api/offline/sync, когда появилась связь.
 *  4. Показывает состояние и спорные поля: конфликты не разрешаются молча,
 *     их разбирает человек.
 *
 * Границы (осознанно): офлайн правятся только задачи. Формы остальных разделов
 * без сети не гасятся молча — плашка честно говорит, что нужен интернет.
 *
 * Архитектура и обоснования: pwa/ARCHITECTURE.md
 */

(function () {
    'use strict';

    const DB_NAME = 'planner-offline';
    const DB_VERSION = 2;
    const OUTBOX = 'outbox';
    const STATE = 'state';
    const SYNC_INTERVAL_MS = 30000;

    // Поля задачи, которые разрешено менять офлайн (совпадает с сервером).
    const EDITABLE_FIELDS = [
        'title', 'description', 'category_id', 'priority',
        'due_date', 'deadline', 'status', 'size', 'impact_notes',
    ];
    const DATE_FIELDS = ['due_date', 'deadline'];

    /* Формы и кнопки раздела «Задачи», которые офлайн уходят в очередь.
     * Порядок важен: первое совпадение выигрывает. */
    const TASK_ROUTES = [
        { re: /^\/tasks\/create$/, kind: 'create_task' },
        { re: /^\/backlog\/create$/, kind: 'create_task' },
        // Формы страницы /tasks/new и модалки редактирования — обычный POST
        // (method="post" action="..."), без htmx. Офлайн их тоже надо уметь.
        { re: /^\/tasks\/web\/create$/, kind: 'create_task' },
        { re: /^\/tasks\/web\/(\d+)\/edit$/, kind: 'update_fields', idGroup: 1, diff: true },
        { re: /^\/tasks\/(\d+)\/complete$/, kind: 'complete', idGroup: 1 },
        { re: /^\/tasks\/(\d+)\/complete-subtask$/, kind: 'complete', idGroup: 1 },
        { re: /^\/backlog\/(\d+)\/plan-today$/, kind: 'plan', idGroup: 1, dateToToday: true },
        { re: /^\/tasks\/(\d+)\/plan$/, kind: 'plan', idGroup: 1, dateField: 'due_date' },
        { re: /^\/tasks\/(\d+)\/backlog$/, kind: 'to_backlog', idGroup: 1 },
        { re: /^\/tasks\/(\d+)\/subtasks$/, kind: 'create_subtask', idGroup: 1 },
        { re: /^\/archive\/(\d+)\/restore$/, kind: 'unarchive', idGroup: 1 },
        { re: /^\/tasks\/(\d+)\/subtask$/, kind: 'delete_subtask', idGroup: 1 },
        { re: /^\/tasks\/(\d+)$/, kind: 'archive', idGroup: 1 },
    ];

    // Действия, которые безопасно повторить при обрыве связи: применение того же
    // действия дважды не портит данные. Создание сюда не входит.
    const RETRY_SAFE_KINDS = [
        'update_fields', 'plan', 'complete', 'archive', 'unarchive',
        'to_backlog', 'delete_subtask',
    ];

    let syncing = false;
    let registration = null;
    let stateCache = {};
    let authProblem = false;

    // ------------------------------------------------------------------ IndexedDB

    function openDb() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(OUTBOX)) {
                    db.createObjectStore(OUTBOX, { keyPath: 'id', autoIncrement: true });
                }
                if (!db.objectStoreNames.contains(STATE)) {
                    db.createObjectStore(STATE, { keyPath: 'id' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    function requestDone(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    /** Выполнить операцию и дождаться завершения транзакции (иначе запись теряется). */
    async function withStore(storeName, mode, fn) {
        const db = await openDb();
        try {
            const result = await new Promise((resolve, reject) => {
                const tx = db.transaction(storeName, mode);
                let value = null;
                try {
                    tx.oncomplete = () => resolve(value);
                    tx.onerror = () => reject(tx.error);
                    tx.onabort = () => reject(tx.error);
                    value = fn(tx.objectStore(storeName), tx);
                } catch (error) {
                    reject(error);
                }
            });
            return result;
        } finally {
            db.close();
        }
    }

    function outboxAll() {
        return withStore(OUTBOX, 'readonly', (store) => requestDone(store.getAll()))
            .catch(() => []);
    }

    async function outboxAdd(item) {
        return withStore(OUTBOX, 'readwrite', (store) => requestDone(store.add(item)));
    }

    async function outboxDelete(id) {
        return withStore(OUTBOX, 'readwrite', (store) => requestDone(store.delete(id)));
    }

    async function outboxPatch(id, patch) {
        const db = await openDb();
        try {
            await new Promise((resolve, reject) => {
                const tx = db.transaction(OUTBOX, 'readwrite');
                const store = tx.objectStore(OUTBOX);
                const get = store.get(id);
                get.onsuccess = () => {
                    const current = get.result;
                    if (current) store.put(Object.assign(current, patch));
                };
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error);
            });
        } finally {
            db.close();
        }
    }

    async function loadStateFromDb() {
        const rows = await withStore(STATE, 'readonly', (store) => requestDone(store.getAll()))
            .catch(() => []);
        stateCache = {};
        rows.forEach((row) => { stateCache[row.id] = row; });
    }

    async function saveState(tasks) {
        if (!Array.isArray(tasks) || !tasks.length) return;
        const db = await openDb();
        try {
            await new Promise((resolve, reject) => {
                const tx = db.transaction(STATE, 'readwrite');
                const store = tx.objectStore(STATE);
                tasks.forEach((task) => {
                    if (task && task.id) {
                        store.put(Object.assign({}, stateCache[task.id] || {}, task));
                    }
                });
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error);
            });
        } finally {
            db.close();
            tasks.forEach((task) => {
                if (task && task.id) stateCache[task.id] = Object.assign({}, stateCache[task.id] || {}, task);
            });
        }
    }

    // --------------------------------------------------------------------- даты

    /** Привести значение к тому виду, в котором его понимает сервер (ISO или строка). */
    function toIso(value) {
        const text = String(value == null ? '' : value).trim();
        if (!text) return '';

        let match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (match) return `${match[1]}-${match[2]}-${match[3]}`;

        match = text.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
        if (match) {
            return `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}`;
        }

        // «0606» / «6.6» — как в поле переноса: день и месяц без года.
        match = text.match(/^(\d{2})(\d{2})$/);
        if (!match) match = text.match(/^(\d{1,2})\.(\d{1,2})$/);
        if (match) {
            const year = new Date().getFullYear();
            const month = match[2].padStart(2, '0');
            const day = match[1].padStart(2, '0');
            return `${year}-${month}-${day}`;
        }
        return text;
    }

    function todayIso() {
        const now = new Date();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${now.getFullYear()}-${month}-${day}`;
    }

    function humanDate(iso) {
        const match = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        return match ? `${match[3]}.${match[2]}` : String(iso || '');
    }

    function uuid() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'offline-' + Date.now() + '-' + Math.random().toString(16).slice(2);
    }

    function isOffline() {
        return navigator.onLine === false;
    }

    function isEditing() {
        const el = document.activeElement;
        if (!el) return false;
        return ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
    }

    // ------------------------------------------------- разбор формы в действие

    function routeFor(url) {
        for (const route of TASK_ROUTES) {
            const match = String(url || '').match(route.re);
            if (match) {
                return {
                    route,
                    taskId: route.idGroup ? Number(match[route.idGroup]) : null,
                };
            }
        }
        return null;
    }

    function formValues(form) {
        const values = {};
        if (!form || !form.elements) return values;
        for (const el of Array.from(form.elements)) {
            if (!el.name) continue;
            if (el.type === 'checkbox') {
                values[el.name] = el.checked ? '1' : '0';
            } else if (el.type !== 'submit' && el.type !== 'button' && el.type !== 'file') {
                values[el.name] = el.value == null ? '' : el.value;
            }
        }
        return values;
    }

    /** Адрес, по которому форма/кнопка отправит данные: htmx или обычный POST. */
    function formUrl(form) {
        if (!form || !form.getAttribute) return null;
        const hx = form.getAttribute('hx-post') || form.getAttribute('hx-delete');
        if (hx) return hx;
        const action = form.getAttribute('action');
        const method = String(form.getAttribute('method') || 'get').toLowerCase();
        if (!action || method !== 'post') return null;
        try {
            const parsed = new URL(action, window.location.origin);
            if (parsed.origin !== window.location.origin) return null;
            return parsed.pathname + parsed.search;
        } catch (error) {
            return null;
        }
    }

    function actionUrl(element) {
        if (!element || !element.getAttribute) return null;
        const hx = element.getAttribute('hx-post') || element.getAttribute('hx-delete');
        if (hx) return hx;
        const form = element.tagName === 'FORM' ? element : (element.closest ? element.closest('form') : null);
        return form ? formUrl(form) : null;
    }

    /** Есть ли запись о задаче в срезе: без неё сравнивать правку не с чем. */
    function hasBaseline(taskId) {
        return Boolean(taskId && stateCache[taskId]);
    }


    /**
     * Превратить отправку формы/кнопки в действие очереди.
     * Возвращает null, если это не действие над задачей: тогда молча гасить
     * событие нельзя — иначе действие пропадёт без следа.
     */
    function describeAction(element) {
        if (!element || !element.getAttribute) return null;

        let url = element.getAttribute('hx-post') || element.getAttribute('hx-delete');
        let form = element.tagName === 'FORM' ? element : (element.closest ? element.closest('form') : null);
        // Обычные формы (method="post" action="...") офлайн обрабатываются так же,
        // как htmx-формы: форма редактирования задачи и /tasks/new именно такие.
        if (!url && form) url = formUrl(form);
        if (!url) return null;

        const found = routeFor(url);
        if (!found) return null;

        const { route, taskId } = found;

        if (route.kind === 'create_task') {
            const values = formValues(form);
            const title = String(values.title || '').trim();
            if (!title) return null;
            const payload = {
                title,
                category_id: values.category_id || '',
                due_date: values.due_date ? toIso(values.due_date) : todayIso(),
            };
            if (values.deadline) payload.deadline = toIso(values.deadline);
            if (values.size) payload.size = values.size;
            if (values.description) payload.description = values.description;
            return {
                kind: 'create_task',
                taskId: null,
                payload,
                title,
                url,
            };
        }

        if (route.kind === 'create_subtask') {
            const values = formValues(form);
            const title = String(values.title || '').trim();
            if (!title) return null;
            const deadline = toIso(values.deadline || '');
            return {
                kind: 'create_subtask',
                taskId,
                payload: { title, deadline },
                title,
                url,
            };
        }

        if (route.diff) {
            const values = formValues(form);
            // Без записи в срезе сравнивать не с чем: все непустые поля ушли бы
            // с from='' и сервер справедливо счёл бы это спором по каждому полю.
            // Честнее сказать Вере, что задачу надо поправить при связи.
            if (!hasBaseline(taskId)) {
                return { unavailable: 'Эта задача не сохранена на устройстве — поправь её, когда появится связь.' };
            }
            const changes = [];
            EDITABLE_FIELDS.forEach((field) => {
                if (!(field in values)) return;
                const baseline = stateCache[taskId] || {};
                const before = DATE_FIELDS.includes(field)
                    ? toIso(baseline[field] || '')
                    : String(baseline[field] == null ? '' : baseline[field]);
                const after = DATE_FIELDS.includes(field)
                    ? toIso(values[field] || '')
                    : String(values[field] == null ? '' : values[field]);
                if (after !== before) changes.push({ field, from: before, to: after });
            });
            if (!changes.length) {
                return { unavailable: 'Изменений нет.' };
            }
            return { kind: 'update_fields', taskId, payload: { changes }, title: '', url };
        }

        if (route.dateField) {
            if (!hasBaseline(taskId)) {
                return { unavailable: 'Эта задача не сохранена на устройстве — поправь её, когда появится связь.' };
            }
            const baseline = stateCache[taskId] || {};
            const values = formValues(form);
            // Пустая дата в вебе означает «на сегодня» для переноса и «снять
            // дедлайн» для DL — повторяем это, а не отказываем.
            let raw;
            if (route.dateToToday) {
                raw = todayIso();
            } else if (route.kind === 'plan') {
                raw = toIso(values[route.dateField] || '') || todayIso();
            } else {
                raw = toIso(values[route.dateField] || '');
            }
            const from = toIso(baseline[route.dateField] || '');
            // Перенос — отдельный вид действия: сервер должен применить ту же
            // логику счётчика переносов, что и кнопка в вебе.
            if (route.kind === 'plan') {
                return { kind: 'plan', taskId, payload: { due_date: raw, from }, title: humanDate(raw), url };
            }
            return {
                kind: 'update_fields',
                taskId,
                payload: { changes: [{ field: route.dateField, from, to: raw }] },
                title: humanDate(raw),
                url,
            };
        }

        return {
            kind: route.kind,
            taskId,
            payload: {},
            title: (stateCache[taskId] && stateCache[taskId].title) || '',
            url,
        };
    }

    async function enqueue(action) {
        await outboxAdd({
            client_uuid: uuid(),
            kind: action.kind,
            task_id: action.taskId,
            payload: action.payload || {},
            url: action.url || '',
            title: action.title || '',
            created_at: Date.now(),
            attempts: 0,
            failed: false,
        });
        await optimisticallyApply(action);
        await renderBar();
    }

    /** Сразу отразить правку на странице: чтобы без сети было видно результат. */
    async function optimisticallyApply(action) {
        if (action.kind === 'create_task') {
            renderDraftCard(action.title);
            return;
        }
        const card = document.getElementById('task-' + action.taskId);
        if (card && !card.querySelector('.offline-pending')) {
            const badge = document.createElement('span');
            badge.className = 'offline-pending';
            badge.textContent = 'в очереди';
            card.appendChild(badge);
        }
        if (action.kind === 'update_fields' || action.kind === 'plan') {
            const patch = { id: action.taskId };
            if (action.kind === 'plan') {
                patch.due_date = action.payload.due_date;
                patch.status = 'новая';
            } else {
                action.payload.changes.forEach((change) => { patch[change.field] = change.to; });
            }
            await saveState([Object.assign({}, stateCache[action.taskId] || { id: action.taskId }, patch)]);
        } else if (action.kind === 'complete') {
            await saveState([Object.assign({}, stateCache[action.taskId] || { id: action.taskId }, {
                status: 'выполнена', is_archived: true,
            })]);
        } else if (action.kind === 'to_backlog') {
            await saveState([Object.assign({}, stateCache[action.taskId] || { id: action.taskId }, {
                due_date: '', status: 'новая',
            })]);
        }
    }

    // ------------------------------------------------------------------ плашка

    function ensureBar() {
        let bar = document.getElementById('offline-bar');
        if (bar) return bar;

        bar = document.createElement('div');
        bar.id = 'offline-bar';
        bar.className = 'offline-bar';
        bar.hidden = true;

        const text = document.createElement('span');
        text.className = 'offline-bar__text';

        const conflicts = document.createElement('button');
        conflicts.type = 'button';
        conflicts.className = 'offline-bar__btn';
        conflicts.textContent = 'Разобрать';
        conflicts.hidden = true;

        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'offline-bar__btn';
        retry.textContent = 'Повторить';
        retry.hidden = true;

        // Действие, которое сервер отверг навсегда (задача уже в архиве и т.п.),
        // иначе висело бы в очереди бесконечно и плашка не гасла бы.
        const discard = document.createElement('button');
        discard.type = 'button';
        discard.className = 'offline-bar__btn';
        discard.textContent = 'Убрать';
        discard.hidden = true;

        const update = document.createElement('button');
        update.type = 'button';
        update.className = 'offline-bar__btn';
        update.textContent = 'Обновить';
        update.hidden = true;

        conflicts.addEventListener('click', () => openConflicts());
        retry.addEventListener('click', async () => {
            const items = await outboxAll();
            for (const item of items) {
                if (item.failed) await outboxPatch(item.id, { failed: false, attempts: 0 });
            }
            authProblem = false;
            await renderBar();
            sync();
        });
        discard.addEventListener('click', async () => {
            const items = await outboxAll();
            const broken = items.filter((item) => item.failed);
            if (!broken.length) return;
            const confirmed = window.confirm(
                `Убрать из очереди ${broken.length} шт.? Эти правки на сервер не уйдут.`
            );
            if (!confirmed) return;
            for (const item of broken) await outboxDelete(item.id);
            await renderBar();
        });
        update.addEventListener('click', () => {
            if (registration && registration.waiting) {
                registration.waiting.postMessage({ type: 'SKIP_WAITING' });
            }
        });

        bar.append(text, conflicts, retry, discard, update);
        document.body.appendChild(bar);
        return bar;
    }

    let conflictsPending = 0;
    let notice = '';
    let noticeUntil = 0;

    async function renderBar() {
        const bar = ensureBar();
        const text = bar.querySelector('.offline-bar__text');
        const buttons = bar.querySelectorAll('.offline-bar__btn');
        const [conflictsBtn, retryBtn, discardBtn, updateBtn] = buttons;

        const items = await outboxAll();
        const pending = items.filter((item) => !item.failed).length;
        const failed = items.filter((item) => item.failed).length;
        const hasWaitingWorker = Boolean(registration && registration.waiting);

        let message = '';
        if (notice && Date.now() < noticeUntil) {
            message = notice;
        } else if (isOffline()) {
            message = pending > 0
                ? `Нет сети. Ждут отправки: ${pending}.`
                : 'Нет сети. Показаны последние загруженные данные.';
        } else if (authProblem) {
            message = 'Нужно снова войти в планер, потом нажать «Повторить».';
        } else if (syncing) {
            message = `Отправляю: ${pending}…`;
        } else if (failed > 0) {
            const broken = items.find((item) => item.failed && item.last_error);
            const reason = broken ? ` Причина: ${String(broken.last_error).slice(0, 140)}.` : '';
            message = `Не отправлено: ${failed}.${reason}`;
        } else if (pending > 0) {
            message = `В очереди: ${pending}.`;
        } else if (conflictsPending > 0) {
            message = `Не разобрано конфликтов: ${conflictsPending}.`;
        }

        text.textContent = message;
        conflictsBtn.hidden = !(conflictsPending > 0);
        conflictsBtn.textContent = `Разобрать (${conflictsPending})`;
        retryBtn.hidden = !((failed > 0 || authProblem) && !isOffline());
        discardBtn.hidden = !(failed > 0);
        updateBtn.hidden = !hasWaitingWorker;

        bar.hidden = !(message || hasWaitingWorker);
        bar.classList.toggle('offline-bar--offline', isOffline());
        bar.classList.toggle('offline-bar--alert', conflictsPending > 0);
    }

    async function refreshConflicts() {
        try {
            const resp = await fetch('/api/offline/conflicts', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const data = await resp.json();
            conflictsPending = (data.conflicts || []).length;
            await renderBar();
        } catch (error) {
            // Нет сети — счётчик останется прежним.
        }
    }

    // ---------------------------------------------------- черновик в списке задач

    function renderDraftCard(title) {
        // Список задач есть на дашборде и в бэклоге, id у них разные.
        const list = document.getElementById('tasks-list') || document.getElementById('backlog-list');
        if (!list) return;

        const row = document.createElement('div');
        row.className = 'offline-draft';

        const text = document.createElement('span');
        text.className = 'offline-draft__title';
        text.textContent = title;

        const badge = document.createElement('span');
        badge.className = 'offline-draft__badge';
        badge.textContent = 'в очереди';

        row.append(text, badge);
        list.insertBefore(row, list.firstChild);
    }

    // ------------------------------------------------------------- разбор конфликтов

    function closeConflicts() {
        const modal = document.getElementById('offline-conflicts');
        if (modal) modal.remove();
    }

    async function openConflicts() {
        let conflicts = [];
        try {
            const resp = await fetch('/api/offline/conflicts', { credentials: 'same-origin' });
            conflicts = (await resp.json()).conflicts || [];
        } catch (error) {
            return;
        }

        closeConflicts();

        const modal = document.createElement('div');
        modal.id = 'offline-conflicts';
        modal.className = 'offline-modal';

        const box = document.createElement('div');
        box.className = 'offline-modal__box';

        const head = document.createElement('div');
        head.className = 'offline-modal__head';
        const title = document.createElement('strong');
        title.textContent = 'Что оставить';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'offline-bar__btn';
        close.textContent = 'Закрыть';
        close.addEventListener('click', closeConflicts);
        head.append(title, close);
        box.appendChild(head);

        const hint = document.createElement('p');
        hint.className = 'offline-modal__hint';
        hint.textContent = 'Поле меняли и офлайн, и в планере, пока телефон был без сети. Ничего не перетёрто: выбери, что оставить.';
        box.appendChild(hint);

        if (!conflicts.length) {
            const empty = document.createElement('p');
            empty.className = 'offline-modal__hint';
            empty.textContent = 'Конфликтов нет.';
            box.appendChild(empty);
        }

        conflicts.forEach((conflict) => {
            const row = document.createElement('div');
            row.className = 'offline-modal__row';

            const caption = document.createElement('div');
            caption.className = 'offline-modal__caption';
            caption.textContent = `${conflict.task_title || 'задача'} — ${conflict.label}`;
            row.appendChild(caption);

            const options = [
                [conflict.server_display || conflict.server_value, 'keep_server', 'Оставить в планере'],
                [conflict.local_display || conflict.local_value, 'keep_local', 'Поставить моё'],
            ];
            options.forEach(([value, resolution, label]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'offline-modal__choice';
                const what = document.createElement('span');
                what.className = 'offline-modal__value';
                what.textContent = value === '' ? '(пусто)' : value;
                const how = document.createElement('span');
                how.className = 'offline-modal__label';
                how.textContent = label;
                button.append(what, how);
                button.addEventListener('click', async () => {
                    button.disabled = true;
                    let answer = {};
                    try {
                        const resp = await fetch(`/api/offline/conflicts/${conflict.id}/resolve`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            credentials: 'same-origin',
                            body: JSON.stringify({ resolution }),
                        });
                        answer = await resp.json();
                    } catch (error) {
                        answer = {};
                    }
                    if (answer && answer.ok === false) {
                        // Поле изменили ещё раз: решение не применили, чтобы не
                        // затереть более свежее значение.
                        showNotice(answer.message || 'Не удалось применить выбор.');
                    } else {
                        row.remove();
                    }
                    await refreshConflicts();
                    await loadState();
                });
                row.appendChild(button);
            });

            box.appendChild(row);
        });

        modal.appendChild(box);
        document.body.appendChild(modal);
    }

    // ------------------------------------------------------------------ отправка

    async function loadState() {
        try {
            const resp = await fetch('/api/offline/state', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const data = await resp.json();
            await saveState(data.tasks || []);
        } catch (error) {
            // Нет сети и нет кэша — работаем на том, что уже есть в IndexedDB.
        }
    }

    async function sync() {
        if (syncing || isOffline()) return;

        const items = await outboxAll();
        const pending = items.filter((item) => !item.failed);
        if (!pending.length) {
            await renderBar();
            await refreshConflicts();
            return;
        }

        syncing = true;
        await renderBar();

        let sent = 0;
        let touchedServer = false;

        try {
            const response = await fetch('/api/offline/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                // Редирект на /login нельзя принимать за успех: иначе запись
                // удалится из очереди, а задачи на сервере не будет.
                redirect: 'manual',
                body: JSON.stringify({
                    actions: pending.map((item) => ({
                        client_uuid: item.client_uuid,
                        kind: item.kind,
                        task_id: item.task_id,
                        payload: item.payload || {},
                    })),
                }),
            });

            if (response.status === 0 || response.type === 'opaqueredirect') {
                authProblem = true;
            } else if (response.status === 401) {
                authProblem = true;
            } else if (response.ok && !response.redirected) {
                authProblem = false;
                const data = await response.json();
                const byUuid = {};
                (data.results || []).forEach((result) => { byUuid[result.client_uuid] = result; });

                for (const item of pending) {
                    const result = byUuid[item.client_uuid];
                    if (!result) {
                        // Сервер не отчитался за действие — оставляем в очереди.
                        continue;
                    }
                    if (result.status === 'applied' || result.status === 'duplicate') {
                        await outboxDelete(item.id);
                        sent += 1;
                        touchedServer = true;
                    } else if (result.status === 'conflict') {
                        // Спор зафиксирован на сервере: повторять бессмысленно,
                        // решение принимает Вера в «Разобрать».
                        await outboxDelete(item.id);
                        sent += 1;
                        touchedServer = true;
                    } else {
                        await outboxPatch(item.id, {
                            failed: true,
                            attempts: (item.attempts || 0) + 1,
                            last_error: result.message || result.status,
                        });
                    }
                }

                if (data.tasks) await saveState(data.tasks);
                // 0 — тоже значение: «|| conflictsPending» оставил бы старый счётчик.
                if (typeof data.conflicts_total === 'number') {
                    conflictsPending = data.conflicts_total;
                }
            } else {
                // Ошибка данных или сервера: оставляем, попробуем позже.
                for (const item of pending) {
                    await outboxPatch(item.id, { attempts: (item.attempts || 0) + 1 });
                }
            }
        } catch (error) {
            // Сеть пропала на полпути — ничего не удаляем, попробуем позже.
        } finally {
            syncing = false;
            await renderBar();
        }

        if (sent > 0) {
            await refreshConflicts();
        }

        if (touchedServer && document.getElementById('tasks-list') && !isEditing()) {
            window.location.reload();
        }
    }

    // -------------------------------------------------------------- перехват

    // Молча ничего не гасим: если действие офлайн не поддерживается, плашка
    // честно об этом говорит. Иначе нажатие выглядело бы сработавшим, а его нет.
    function showNotice(text) {
        notice = text;
        noticeUntil = Date.now() + 6000;
        const bar = ensureBar();
        bar.hidden = false;
        bar.querySelector('.offline-bar__text').textContent = text;
    }

    /** Увести действие в очередь. false — если офлайн его выполнить нельзя. */
    async function handleOfflineAction(element) {
        const action = describeAction(element);
        if (!action || action.unavailable) {
            showNotice((action && action.unavailable) || 'Без интернета это действие недоступно.');
            return false;
        }
        await enqueue(action);
        return true;
    }

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!isOffline()) return;
        // Учитываются и htmx-формы, и обычные POST-формы: редактирование задачи,
        // /tasks/new, категории, трекеры.
        if (!actionUrl(form)) return;

        // Событие гасится только у форм, которые умеем обработать; для остальных
        // оно тоже гасится, но с явным сообщением — потерять действие нельзя.
        event.preventDefault();
        event.stopPropagation();
        handleOfflineAction(form).catch(() => {});
    }, true);

    // Страховка: htmx может отправить запрос не через submit (кнопка с hx-delete).
    document.body.addEventListener('htmx:beforeRequest', (event) => {
        if (!isOffline()) return;
        const elt = event.detail && event.detail.elt;
        if (!elt || !actionUrl(elt)) return;
        event.preventDefault();
        handleOfflineAction(elt).catch(() => {});
    });

    // Запрос ушёл, но не дошёл: сеть пропала между нажатием и отправкой. Флаг
    // navigator.onLine здесь врёт (Wi-Fi без интернета бывает «онлайн»), поэтому
    // ориентируемся на сам факт сетевой ошибки. Повторяем только то, что
    // безопасно применить дважды; создание вслепую не повторяем — запрос мог
    // дойти до сервера.
    document.body.addEventListener('htmx:sendError', (event) => {
        const elt = event.detail && event.detail.elt;
        if (!elt || !actionUrl(elt)) return;
        const action = describeAction(elt);
        if (!action || action.unavailable) return;
        if (isOffline() || RETRY_SAFE_KINDS.includes(action.kind)) {
            enqueue(action).catch(() => {});
            return;
        }
        showNotice('Связь пропала. Проверь, не создалась ли задача, и добавь заново.');
    });

    // ------------------------------------------------------------------ события

    window.addEventListener('online', () => {
        authProblem = false;
        renderBar();
        loadState().then(sync);
    });

    window.addEventListener('offline', renderBar);

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            renderBar();
            refreshConflicts();
            sync();
        }
    });

    setInterval(() => {
        renderBar();
        sync();
    }, SYNC_INTERVAL_MS);

    // ------------------------------------------------------- service worker

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            loadStateFromDb()
                .then(loadState)
                .then(refreshConflicts)
                .then(sync);

            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .then((reg) => {
                    registration = reg;
                    renderBar();

                    reg.addEventListener('updatefound', () => {
                        const installing = reg.installing;
                        if (!installing) return;
                        installing.addEventListener('statechange', () => {
                            if (installing.state === 'installed') renderBar();
                        });
                    });

                    navigator.serviceWorker.addEventListener('controllerchange', () => renderBar());
                })
                .catch(() => {
                    // http:// или нет HTTPS: офлайн-режим недоступен.
                });
        });
    } else {
        window.addEventListener('load', () => {
            loadStateFromDb().then(refreshConflicts);
        });
    }
})();
