/* Удаление категории с выбором, куда перенести записи.
 *
 * Сервер (DELETE /api/categories/{id}) при использованной категории отвечает 409
 * и присылает состав использования и кандидатов. Здесь показываем диалог,
 * повторяем запрос с ?move_to=<id> и только потом перезагружаем страницу.
 * Стили — инлайновые: CSS-бандл Планера статический, готовых классов под
 * такой диалог в нём нет.
 */
(function () {
    'use strict';

    var HOST_ID = 'catdel-modal';

    function ensureModal() {
        var host = document.getElementById(HOST_ID);
        if (host) return host;

        host = document.createElement('div');
        host.id = HOST_ID;
        host.hidden = true;
        host.setAttribute('role', 'dialog');
        host.setAttribute('aria-modal', 'true');
        host.style.cssText = 'position:fixed;inset:0;z-index:95;display:flex;align-items:flex-start;justify-content:center;';

        var scrim = document.createElement('div');
        scrim.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,.72)';
        scrim.addEventListener('click', close);

        var panel = document.createElement('div');
        panel.style.cssText = [
            'position:relative', 'margin-top:10vh', 'width:min(460px,calc(100% - 2rem))',
            'background:#1e1e27', 'border:1px solid rgba(54,54,69,.9)', 'border-radius:14px',
            'box-shadow:0 24px 64px rgba(0,0,0,.55)', 'padding:16px', 'font-family:inherit'
        ].join(';');

        panel.innerHTML =
            '<div data-role="title" style="font-size:13px;font-weight:600;color:#f3f4f6;margin:0 0 4px"></div>' +
            '<div data-role="usage" style="font-size:11px;color:#9ca3af;margin:0 0 12px"></div>' +
            '<label for="catdel-target" style="display:block;font-size:11px;color:#8b8fa3;margin:0 0 4px">Куда перенести</label>' +
            '<select id="catdel-target" data-role="select" style="width:100%;font-size:11px;color:#e5e7eb;background:#16161d;' +
            'border:1px solid rgba(54,54,69,.9);border-radius:8px;padding:7px 8px;margin:0 0 14px"></select>' +
            '<div style="display:flex;gap:8px;justify-content:flex-end">' +
            '<button type="button" data-role="cancel" style="font-size:11px;color:#9ca3af;background:transparent;' +
            'border:1px solid rgba(54,54,69,.9);border-radius:8px;padding:6px 12px;cursor:pointer">Отмена</button>' +
            '<button type="button" data-role="ok" style="font-size:11px;color:#fff;background:#b45309;border:0;' +
            'border-radius:8px;padding:6px 12px;cursor:pointer">Перенести и удалить</button>' +
            '</div>' +
            '<div data-role="error" role="alert" aria-live="polite" style="font-size:11px;color:#f87171;margin-top:10px"></div>';

        host.appendChild(scrim);
        host.appendChild(panel);
        document.body.appendChild(host);

        panel.querySelector('[data-role="cancel"]').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !host.hidden) close();
        });
        return host;
    }

    var opener = null;

    function close() {
        var host = document.getElementById(HOST_ID);
        if (!host) return;
        host.hidden = true;
        host.style.display = 'none';
        document.body.style.overflow = '';
        if (opener && typeof opener.focus === 'function') opener.focus();
        opener = null;
    }

    function plural(n, one, few, many) {
        var n10 = n % 10, n100 = n % 100;
        if (n10 === 1 && n100 !== 11) return one;
        if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
        return many;
    }

    function askWhereToMove(payload, onDone) {
        var host = ensureModal();
        if (!opener) opener = document.activeElement;
        var usage = payload.usage || {};
        var parts = [];
        if (usage.transactions) {
            parts.push(usage.transactions + ' ' + plural(usage.transactions, 'транзакция', 'транзакции', 'транзакций'));
        }
        if (usage.tasks) {
            parts.push(usage.tasks + ' ' + plural(usage.tasks, 'задача', 'задачи', 'задач'));
        }
        if (usage.children) {
            parts.push(usage.children + ' ' + plural(usage.children, 'подкатегория', 'подкатегории', 'подкатегорий'));
        }

        host.querySelector('[data-role="title"]').textContent = 'Удалить «' + payload.category.name + '»?';
        host.querySelector('[data-role="usage"]').textContent =
            (parts.length ? parts.join(', ') + ' — ' : '') + 'сначала выбери, куда это перенести.';

        var select = host.querySelector('[data-role="select"]');
        select.style.display = '';
        select.innerHTML = '';
        (payload.candidates || []).forEach(function (c) {
            var opt = document.createElement('option');
            opt.value = c.id;
            var prefix = c.is_global ? '' : (c.parent_name ? c.parent_name + ' → ' : '') ;
            opt.textContent = prefix + c.name;
            if (payload.default_move_to && Number(c.id) === Number(payload.default_move_to)) opt.selected = true;
            select.appendChild(opt);
        });
        if (payload.allow_null) {
            var none = document.createElement('option');
            none.value = '0';
            none.textContent = 'Без категории (записи останутся без группы)';
            select.appendChild(none);
        }
        if (!select.options.length) {
            host.querySelector('[data-role="error"]').textContent = 'Переносить некуда — оставь без категории.';
        } else if (payload.reason === 'invalid_target') {
            host.querySelector('[data-role="error"]').textContent = 'Так переносить нельзя: выбери другую категорию.';
        } else {
            host.querySelector('[data-role="error"]').textContent = '';
        }

        var okBtn = host.querySelector('[data-role="ok"]');
        // Кнопку могли спрятать в ветке ошибки — возвращаем её на место.
        okBtn.style.display = '';
        okBtn.disabled = !select.options.length;
        okBtn.style.opacity = okBtn.disabled ? '0.5' : '1';
        okBtn.onclick = function () {
            if (!select.value) return;
            okBtn.disabled = true;
            send(payload.category.id, select.value, onDone, function (msg) {
                okBtn.disabled = false;
                host.querySelector('[data-role="error"]').textContent = msg;
            });
        };

        host.hidden = false;
        host.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        select.focus();
    }

    function send(id, moveTo, onDone, onError) {
        var url = '/api/categories/' + id + (moveTo ? '?move_to=' + encodeURIComponent(moveTo) : '');
        fetch(url, { method: 'DELETE', credentials: 'same-origin' })
            .then(function (r) {
                if (r.ok) return { ok: true };
                return r.json().then(function (body) { return { ok: false, status: r.status, body: body }; })
                    .catch(function () { return { ok: false, status: r.status, body: {} }; });
            })
            .then(function (res) {
                if (res.ok) {
                    close();
                    if (typeof onDone === 'function') onDone();
                    return;
                }
                if (res.status === 409) {
                    askWhereToMove(res.body, onDone);
                    return;
                }
                if (onError) onError('Не получилось удалить: ' + (res.body.detail || res.status));
            })
            .catch(function () { if (onError) onError('Сеть недоступна, попробуй ещё раз'); });
    }

    /* Публичная точка: клик по «удл.» в списках категорий. */
    window.deleteCategoryFlow = function (id, onDone) {
        send(id, null, onDone || function () { window.location.reload(); }, function (msg) {
            var host = ensureModal();
            host.querySelector('[data-role="title"]').textContent = 'Не удалось удалить';
            host.querySelector('[data-role="usage"]').textContent = '';
            host.querySelector('[data-role="select"]').innerHTML = '';
            host.querySelector('[data-role="ok"]').style.display = 'none';
            host.querySelector('[data-role="select"]').style.display = 'none';
            host.querySelector('[data-role="error"]').textContent = msg;
            host.hidden = false;
            host.style.display = 'flex';
        });
    };
})();
