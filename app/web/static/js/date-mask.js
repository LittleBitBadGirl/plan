/** Маска ДД.ММ: ввод 0606 → 06.06 (делегирование — работает после HTMX swap). */
(function () {
    function formatDdMm(input) {
        const digits = input.value.replace(/\D/g, '').slice(0, 4);
        input.value = digits.length > 2
            ? digits.slice(0, 2) + '.' + digits.slice(2)
            : digits;

        // Авто-submit только для inline-форм (бэклог/дашборд), не для полной формы задачи
        if (
            digits.length === 4
            && input.form
            && !input.dataset.submitting
            && !input.form.hasAttribute('data-no-date-auto-submit')
        ) {
            input.dataset.submitting = '1';
            input.form.requestSubmit();
        }
    }

    document.addEventListener('input', function (e) {
        if (e.target.matches('.date-mask-ddmm')) {
            delete e.target.dataset.submitting;
            formatDdMm(e.target);
        }
    });

    document.addEventListener('keydown', function (e) {
        if (!e.target.matches('.date-mask-ddmm')) return;
        if (e.key === 'Enter') {
            e.preventDefault();
            e.target.form?.requestSubmit();
        }
    });

    document.addEventListener('focusin', function (e) {
        if (e.target.matches('.date-mask-ddmm')) {
            e.target.select();
        }
    });
})();
