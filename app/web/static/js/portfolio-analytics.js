/**
 * Portfolio Analyzer UI — extracted from finance.html analytics modal + extensions.
 * Loads /api/portfolios/{id}/analytics, composition, payments drill-down.
 */
(function () {
    'use strict';

    var MONTHS = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
    var FLOW_LABELS = {
        deposit: 'Пополнения',
        withdrawal: 'Выводы',
        coupon: 'Купоны',
        dividend: 'Дивиденды',
        tax: 'Налоги',
        commission: 'Комиссии',
        redemption: 'Погашения',
        pif_accrual: 'ПИФ начисления',
        sale: 'Продажа',
    };
    var INCOME_GROUP_ORDER = { coupon: 0, dividend: 1, pif_accrual: 2, redemption: 3 };
    var INCOME_GROUP_LABELS = {
        coupon: 'Купоны',
        dividend: 'Дивиденды',
        pif_accrual: 'ПИФ',
        redemption: 'Погашения / выкупы',
        other: 'Прочее',
    };
    var COMPOSITION_GROUP_ORDER = { bond: 0, stock: 1, pif: 2 };
    var COMPOSITION_GROUP_LABELS = {
        bond: 'Облигации',
        stock: 'Акции',
        pif: 'ПИФ',
        other: 'Прочее',
    };

    function inferIncomeType(instr) {
        if (instr && instr.type) return instr.type;
        var n = String((instr && instr.name) || '').toLowerCase();
        if (n.indexOf('купон') === 0) return 'coupon';
        if (n.indexOf('дивиденд') === 0) return 'dividend';
        if (n.indexOf('пиф') === 0) return 'pif_accrual';
        if (n.indexOf('выкуп') === 0 || n.indexOf('погашен') !== -1) return 'redemption';
        return 'other';
    }

    function incomeGroupRank(instr) {
        var t = inferIncomeType(instr);
        return INCOME_GROUP_ORDER.hasOwnProperty(t) ? INCOME_GROUP_ORDER[t] : 9;
    }

    function displayInstrumentName(name) {
        var raw = String(name || '').trim();
        var cleaned = raw.replace(/^(дивиденды|дивиденд|купоны|купон)\s*[:—–-]\s*/i, '');
        if (cleaned === raw) {
            cleaned = raw.replace(/^(дивиденды|дивиденд|купоны|купон)\s+/i, '');
        }
        return cleaned || raw;
    }

    function fmtMaturityMonthYear(iso) {
        if (!iso) return '';
        var raw = String(iso).substring(0, 10);
        var d = parseDate(raw);
        if (isNaN(d.getTime())) return '';
        return MONTHS[d.getMonth()] + ' ' + d.getFullYear();
    }

    function shortenName(name, max) {
        max = max || 24;
        if (name.length <= max) return name;
        return name.substring(0, max - 3) + '...';
    }

    function fmtRub(v) {
        if (v == null || isNaN(v)) return '—';
        return (v >= 0 ? '+' : '') + Math.abs(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽';
    }

    function fmtPlain(v) {
        if (!v) return '';
        return Math.abs(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 });
    }

    function clr(v) {
        return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-600';
    }

    function esc(s) {
        return String(s || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function parseDate(s) {
        return new Date(s + (s.length === 10 ? 'T00:00:00' : ''));
    }

    window.PortfolioAnalytics = {
        portfolioId: null,
        data: null,
        composition: null,
        cfYear: null,
        cfSearch: '',
        period: 'prevMonth',
        yoy: false,
        selectedInstrument: null,

        init: function (portfolioId) {
            this.portfolioId = portfolioId;
            this.cfYear = new Date().getFullYear();
            this.bindEvents();
            this.load();
        },

        bindEvents: function () {
            var self = this;
            var search = document.getElementById('paSearch');
            if (search) {
                search.addEventListener('input', function () {
                    self.cfSearch = search.value.trim().toLowerCase();
                    self.renderCashflowTable();
                });
            }
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') self.closeDrilldown();
            });
        },

        load: async function () {
            var root = document.getElementById('portfolioRoot');
            if (root) root.classList.add('opacity-60');
            try {
                var pid = this.portfolioId;
                var r1 = await fetch('/api/portfolios/' + pid + '/analytics', { credentials: 'same-origin' });
                if (!r1.ok) throw new Error('analytics HTTP ' + r1.status);
                this.data = await r1.json();

                var r2 = await fetch('/api/portfolios/' + pid + '/composition', { credentials: 'same-origin' });
                if (r2.ok) this.composition = await r2.json();

                this.initYearTabs();
                this.buildDropdowns();
                this.renderAll();
            } catch (e) {
                var err = document.getElementById('paError');
                if (err) {
                    err.textContent = 'Ошибка загрузки: ' + e.message;
                    err.classList.remove('hidden');
                }
            } finally {
                if (root) root.classList.remove('opacity-60');
            }
        },

        initYearTabs: function () {
            var summary = (this.data && this.data.monthly_cashflow && this.data.monthly_cashflow.summary) || {};
            var years = Object.keys(summary).map(function (k) { return k.substring(0, 4); });
            years = years.filter(function (y, i, a) { return a.indexOf(y) === i; }).sort();
            if (years.length && years.indexOf(String(this.cfYear)) === -1) {
                this.cfYear = parseInt(years[years.length - 1], 10);
            }
            this.availableYears = years;
        },

        buildDropdowns: function () {
            var snaps = (this.data && this.data.snapshots) || [];
            var months = snaps.map(function (s) { return s.date.substring(0, 7); });
            months = months.filter(function (m, i, a) { return a.indexOf(m) === i; }).sort();
            var years = snaps.map(function (s) { return s.date.substring(0, 4); });
            years = years.filter(function (y, i, a) { return a.indexOf(y) === i; }).sort();
            var now = new Date();
            var curYear = now.getFullYear();

            var yearSel = document.getElementById('paYear');
            if (yearSel) {
                yearSel.innerHTML = years.map(function (y) {
                    return '<option value="' + y + '"' + (y == curYear ? ' selected' : '') + '>' + y + '</option>';
                }).join('');
            }

            var monthSel = document.getElementById('paMonth');
            if (monthSel) {
                var prevMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
                var defMonth = prevMonth.getFullYear() + '-' + String(prevMonth.getMonth() + 1).padStart(2, '0');
                monthSel.innerHTML = '<option value="">—</option>' +
                    months.map(function (m) {
                        return '<option value="' + m + '"' + (m === defMonth ? ' selected' : '') + '>' + m + '</option>';
                    }).join('');
            }
        },

        getCutoff: function () {
            var now = new Date();
            var yearSel = document.getElementById('paYear');
            var monthSel = document.getElementById('paMonth');
            var yearVal = yearSel ? yearSel.value : '';
            var monthVal = monthSel ? monthSel.value : '';

            if (this.yoy) {
                var prevDef = new Date(now.getFullYear(), now.getMonth() - 1, 1);
                var anchorYear, anchorMonth;
                if (monthVal && yearVal) {
                    var parts = monthVal.split('-').map(Number);
                    anchorYear = parts[0];
                    anchorMonth = parts[1];
                } else if (yearVal && !monthVal) {
                    anchorYear = parseInt(yearVal, 10);
                    anchorMonth = prevDef.getMonth() + 1;
                } else {
                    anchorYear = prevDef.getFullYear();
                    anchorMonth = prevDef.getMonth() + 1;
                }
                return {
                    start: new Date(anchorYear - 1, anchorMonth - 1, 1),
                    end: new Date(anchorYear, anchorMonth, 0, 23, 59, 59),
                    compareStart: new Date(anchorYear - 2, anchorMonth - 1, 1),
                    compareEnd: new Date(anchorYear - 1, anchorMonth, 0, 23, 59, 59),
                };
            }

            if (monthVal && yearVal) {
                var mp = monthVal.split('-').map(Number);
                return { start: new Date(mp[0], mp[1] - 1, 1), end: new Date(mp[0], mp[1], 0, 23, 59, 59) };
            }
            if (yearVal && !monthVal) {
                var y = parseInt(yearVal, 10);
                return { start: new Date(y, 0, 1), end: new Date(y, 11, 31, 23, 59, 59) };
            }
            if (monthVal && !yearVal) {
                var mp2 = monthVal.split('-').map(Number);
                return { start: new Date(mp2[0], mp2[1] - 1, 1), end: new Date(mp2[0], mp2[1], 0, 23, 59, 59) };
            }
            if (this.period === 'curYear') {
                var cy = now.getFullYear();
                return { start: new Date(cy, 0, 1), end: new Date(cy, 11, 31, 23, 59, 59) };
            }
            var prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            return {
                start: new Date(prev.getFullYear(), prev.getMonth(), 1),
                end: new Date(prev.getFullYear(), prev.getMonth() + 1, 0, 23, 59, 59),
            };
        },

        setPeriod: function (p) {
            this.period = p;
            this.yoy = false;
            var now = new Date();
            var monthSel = document.getElementById('paMonth');
            var yearSel = document.getElementById('paYear');
            if (p === 'prevMonth') {
                var prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
                if (monthSel) monthSel.value = prev.getFullYear() + '-' + String(prev.getMonth() + 1).padStart(2, '0');
                if (yearSel) yearSel.value = now.getFullYear();
            } else if (p === 'curYear') {
                if (monthSel) monthSel.value = '';
                if (yearSel) yearSel.value = now.getFullYear();
            }
            this.renderAll();
        },

        onFilterChange: function () {
            this.period = 'custom';
            this.yoy = false;
            this.renderAll();
        },

        toggleYoY: function () {
            this.yoy = !this.yoy;
            if (this.yoy) {
                this.period = 'custom';
                var monthSel = document.getElementById('paMonth');
                if (monthSel) monthSel.value = '';
            }
            this.renderAll();
        },

        switchCashflowYear: function (year) {
            this.cfYear = year === 'all' ? 'all' : parseInt(year, 10);
            this.renderYearTabButtons();
            this.renderCashflowTable();
        },

        renderYearTabButtons: function () {
            var container = document.getElementById('paYearTabs');
            if (!container) return;
            var years = this.availableYears || [];
            var self = this;
            var btnCls = function (active) {
                return active
                    ? 'px-2.5 py-1 text-[10px] rounded border transition bg-yellow-600/20 text-yellow-300 border-yellow-600/40'
                    : 'px-2.5 py-1 text-[10px] rounded border transition bg-dark-700 text-gray-400 border-dark-600 hover:border-dark-500';
            };
            var html = years.map(function (y) {
                return '<button type="button" data-year="' + y + '" class="' + btnCls(self.cfYear === parseInt(y, 10)) + '">' + y + '</button>';
            }).join('');
            html += '<button type="button" data-year="all" class="' + btnCls(self.cfYear === 'all') + '">Все</button>';
            container.innerHTML = html;
            container.querySelectorAll('button').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    self.switchCashflowYear(btn.getAttribute('data-year'));
                });
            });
        },

        renderAll: function () {
            this.renderKpi();
            this.renderFilterButtons();
            this.renderSparkline();
            this.renderYearTabButtons();
            this.renderCashflowTable();
            this.renderComposition();
            this.renderClosed();
        },

        renderFilterButtons: function () {
            var prevBtn = document.getElementById('paBtnPrev');
            var yearBtn = document.getElementById('paBtnYear');
            var yoyBtn = document.getElementById('paYoY');
            var activeCls = 'px-2.5 py-1 text-[10px] rounded-lg border transition font-medium bg-yellow-600/20 text-yellow-300 border-yellow-600/40';
            var idleCls = 'px-2.5 py-1 text-[10px] rounded-lg border transition font-medium bg-dark-700 text-gray-400 border-dark-600';
            if (prevBtn) prevBtn.className = (this.period === 'prevMonth' && !this.yoy) ? activeCls : idleCls;
            if (yearBtn) yearBtn.className = (this.period === 'curYear' && !this.yoy) ? activeCls : idleCls;
            if (yoyBtn) {
                yoyBtn.className = this.yoy
                    ? 'px-2.5 py-1 text-[10px] rounded-lg border transition font-medium border-yellow-600/40 bg-yellow-600/10 text-yellow-300'
                    : 'px-2.5 py-1 text-[10px] rounded-lg border transition font-medium border-dark-600 text-gray-500 hover:text-yellow-300';
            }
        },

        renderKpi: function () {
            var el = document.getElementById('paKpi');
            if (!el || !this.data) return;
            var snaps = this.data.snapshots || [];
            var flows = this.data.flows || [];
            var now = new Date();
            var curYear = now.getFullYear();
            var curMonth = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
            var prevMonthDate = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            var prevMonthKey = prevMonthDate.getFullYear() + '-' + String(prevMonthDate.getMonth() + 1).padStart(2, '0');

            var balance = snaps.length ? snaps[snaps.length - 1].balance : null;

            function snapAtOrBefore(target) {
                var best = null;
                for (var i = 0; i < snaps.length; i++) {
                    if (parseDate(snaps[i].date) <= target) best = snaps[i].balance;
                }
                return best;
            }

            var monthStart = snapAtOrBefore(new Date(now.getFullYear(), now.getMonth(), 1));
            var monthDelta = balance != null && monthStart != null ? balance - monthStart : null;

            var yearStart = snapAtOrBefore(new Date(curYear, 0, 1));
            var yearDelta = balance != null && yearStart != null ? balance - yearStart : null;

            var summary = (this.data.monthly_cashflow && this.data.monthly_cashflow.summary) || {};
            var couponsYtd = 0;
            var dividendsYtd = 0;
            Object.keys(summary).forEach(function (ym) {
                if (ym.substring(0, 4) === String(curYear)) {
                    couponsYtd += (summary[ym].coupons || 0);
                    dividendsYtd += (summary[ym].dividends || 0);
                }
            });

            function kpiCard(label, value, color) {
                return '<div class="bg-dark-800 rounded-xl border border-dark-600 px-4 py-3">' +
                    '<p class="text-[10px] text-gray-500 mb-1">' + label + '</p>' +
                    '<p class="text-lg font-bold ' + color + '">' + value + '</p></div>';
            }

            el.innerHTML =
                kpiCard('Баланс', balance != null ? fmtPlain(balance) + ' ₽' : '—', 'text-white') +
                kpiCard('Δ мес', monthDelta != null ? fmtRub(monthDelta) : '—', monthDelta >= 0 ? 'text-emerald-400' : 'text-red-400') +
                kpiCard('Δ год', yearDelta != null ? fmtRub(yearDelta) : '—', yearDelta >= 0 ? 'text-emerald-400' : 'text-red-400') +
                kpiCard('Купоны YTD', fmtPlain(couponsYtd) + ' ₽', 'text-emerald-400') +
                kpiCard('Дивиденды YTD', fmtPlain(dividendsYtd) + ' ₽', 'text-amber-400');
        },

        renderSparkline: function () {
            var sparkDiv = document.getElementById('paSparkline');
            if (!sparkDiv || !this.data) return;
            var d = this.data;
            var snaps = d.snapshots || [];
            var cutoff = this.getCutoff();

            if (snaps.length === 0) {
                sparkDiv.innerHTML = '<p class="text-sm text-gray-500">Нет данных по балансу</p>';
                return;
            }

            var chartSnaps = snaps;
            if (cutoff) {
                chartSnaps = snaps.filter(function (s) {
                    var sd = parseDate(s.date);
                    if (cutoff.end) return sd >= cutoff.start && sd <= cutoff.end;
                    return cutoff.start ? sd >= cutoff.start : true;
                });
                if (cutoff.start && chartSnaps.length > 0) {
                    var preSnap = null;
                    for (var i = snaps.length - 1; i >= 0; i--) {
                        if (parseDate(snaps[i].date) < cutoff.start) { preSnap = snaps[i]; break; }
                    }
                    if (preSnap) chartSnaps = [preSnap].concat(chartSnaps);
                }
            }

            var displaySnaps = chartSnaps;
            if (displaySnaps.length < 2 && cutoff && cutoff.end) {
                var padStart = new Date(cutoff.start);
                padStart.setMonth(padStart.getMonth() - 1);
                var padEnd = new Date(cutoff.end);
                padEnd.setMonth(padEnd.getMonth() + 1);
                displaySnaps = snaps.filter(function (s) {
                    var sd = parseDate(s.date);
                    return sd >= padStart && sd <= padEnd;
                });
            }

            if (displaySnaps.length === 0) {
                sparkDiv.innerHTML = '<p class="text-sm text-gray-500">Нет данных за период</p>';
                return;
            }
            if (displaySnaps.length === 1) {
                sparkDiv.innerHTML =
                    '<p class="text-[10px] text-gray-500 mb-1">Баланс</p>' +
                    '<div class="text-center py-2">' +
                    '<p class="text-[10px] text-gray-500">' + esc(displaySnaps[0].date) + '</p>' +
                    '<p class="text-xl font-bold text-white">' + fmtPlain(displaySnaps[0].balance) + ' ₽</p></div>';
                return;
            }

            var vals = displaySnaps.map(function (s) { return s.balance; });
            var max = Math.max.apply(null, vals);
            var min = Math.min.apply(null, vals);
            var range = max - min || 1;
            var yMin = Math.max(0, Math.floor((min - range * 0.15) / 1000) * 1000);
            var yMax = Math.ceil((max + range * 0.15) / 1000) * 1000;
            var yRange = yMax - yMin || 1;
            var W = 600, H = 100, padL = 55, padR = 15, padT = 10, padB = 20;
            var cW = W - padL - padR, cH = H - padT - padB;
            var toX = function (i) { return padL + (i / (displaySnaps.length - 1)) * cW; };
            var toY = function (v) { return padT + cH - ((v - yMin) / yRange) * cH; };
            var allPts = displaySnaps.map(function (s, i) { return toX(i) + ',' + toY(s.balance); }).join(' ');
            var gridHtml = '';
            for (var gi = 0; gi <= 4; gi++) {
                var y = yMin + yRange * gi / 4;
                var yp = toY(y);
                var label = y >= 1000000 ? (y / 1e6).toFixed(1) + 'M' : y >= 1000 ? (y / 1000).toFixed(0) + 'k' : y;
                gridHtml += '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + yp + '" y2="' + yp + '" stroke="#374151" stroke-width="0.5" stroke-dasharray="3,3"/>' +
                    '<text x="' + (padL - 4) + '" y="' + (yp + 3) + '" fill="#6b7280" font-size="8" text-anchor="end">' + label + '</text>';
            }
            sparkDiv.innerHTML =
                '<p class="text-[10px] text-gray-500 mb-2">Динамика баланса</p>' +
                '<svg class="w-full" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" style="max-height:120px">' +
                gridHtml +
                '<polyline fill="none" stroke="#ca8a04" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" points="' + allPts + '"/>' +
                '<text x="' + padL + '" y="' + (H - 2) + '" fill="#6b7280" font-size="8">' + esc(displaySnaps[0].date) + '</text>' +
                '<text x="' + (W - padR) + '" y="' + (H - 2) + '" fill="#6b7280" font-size="8" text-anchor="end">' +
                esc(displaySnaps[displaySnaps.length - 1].date) + '</text></svg>';
        },

        buildCashflowTable: function (year) {
            if (year !== undefined) this.cfYear = year;
            this.renderCashflowTable();
        },

        renderCashflowTable: function () {
            var container = document.getElementById('paCashflowTable');
            var section = document.getElementById('paCashflow');
            if (!container || !this.data) return;
            var cf = this.data.monthly_cashflow;
            if (!cf) return;

            var instruments = (cf.instruments || []).slice();
            if (this.cfSearch) {
                instruments = instruments.filter(function (i) {
                    var hay = (i.name + ' ' + displayInstrumentName(i.name)).toLowerCase();
                    return hay.indexOf(this.cfSearch) !== -1;
                }.bind(this));
            }

            if (instruments.length === 0 && !this.cfSearch) {
                if (section) section.classList.add('hidden');
                return;
            }
            if (section) section.classList.remove('hidden');

            var year = this.cfYear;
            var summary = cf.summary || {};
            var self = this;
            instruments = this._sortIncomeInstruments(instruments, year);

            if (year === 'all') {
                container.innerHTML = this._buildAllYearsTable(instruments, summary);
            } else {
                container.innerHTML = this._buildYearTable(instruments, summary, year);
            }

            container.querySelectorAll('[data-instrument]').forEach(function (row) {
                row.addEventListener('click', function () {
                    self.openDrilldown(row.getAttribute('data-instrument'));
                });
            });
        },

        _sortIncomeInstruments: function (instruments, year) {
            var self = this;
            var list = instruments.slice();
            if (year && year !== 'all') {
                list = list.filter(function (instr) {
                    return self._yearInstrumentTotal(instr, year) > 0;
                });
            }
            return list.sort(function (a, b) {
                var ga = incomeGroupRank(a);
                var gb = incomeGroupRank(b);
                if (ga !== gb) return ga - gb;
                var ta = (year && year !== 'all')
                    ? self._yearInstrumentTotal(a, year)
                    : (a.total || 0);
                var tb = (year && year !== 'all')
                    ? self._yearInstrumentTotal(b, year)
                    : (b.total || 0);
                if (tb !== ta) return tb - ta;
                return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
            });
        },

        _groupHeader: function (label, colCount) {
            return "<tr class='border-b border-dark-700/50'>" +
                "<td class='sticky left-0 bg-dark-800 px-2 py-1 text-[9px] uppercase tracking-wide text-gray-500 font-semibold'>" +
                esc(label) + '</td>' +
                "<td colspan='" + (colCount - 1) + "' class='bg-dark-800'></td></tr>";
        },

        _incomeGroupHeader: function (type, colCount) {
            return this._groupHeader(INCOME_GROUP_LABELS[type] || INCOME_GROUP_LABELS.other, colCount);
        },

        _buildYearTable: function (instruments, summary, year) {
            var yrSummary = { deposits: 0, withdrawals: 0, coupons: 0, dividends: 0, taxes: 0, redemptions: 0 };
            for (var m = 1; m <= 12; m++) {
                var ym = year + '-' + String(m).padStart(2, '0');
                if (summary[ym]) {
                    yrSummary.deposits += summary[ym].deposits || 0;
                    yrSummary.withdrawals += summary[ym].withdrawals || 0;
                    yrSummary.coupons += summary[ym].coupons || 0;
                    yrSummary.dividends += summary[ym].dividends || 0;
                    yrSummary.taxes += summary[ym].taxes || 0;
                    yrSummary.redemptions += summary[ym].redemptions || 0;
                }
            }

            var h = "<table class='w-full text-[10px] border-collapse min-w-[640px]'><thead><tr class='border-b border-dark-700'>";
            h += "<th class='sticky left-0 top-0 z-20 bg-dark-800 px-2 py-1.5 text-left text-gray-500'>Инструмент</th>";
            h += "<th class='sticky top-0 z-10 bg-dark-800 px-1.5 py-1.5 text-right text-gray-500 w-14'>∑</th>";
            for (var mi = 1; mi <= 12; mi++) {
                h += "<th class='sticky top-0 z-10 bg-dark-800 px-1.5 py-1.5 text-right text-gray-500'>" + MONTHS[mi - 1] + '</th>';
            }
            h += '</tr></thead><tbody>';

            var prevType = null;
            for (var i = 0; i < instruments.length; i++) {
                var instr = instruments[i];
                var instrType = inferIncomeType(instr);
                if (instrType !== prevType) {
                    h += this._incomeGroupHeader(instrType, 14);
                    prevType = instrType;
                }
                h += "<tr class='border-b border-dark-700/30 hover:bg-dark-700/30 cursor-pointer' data-instrument=\"" + esc(instr.name) + '">';
                var shown = displayInstrumentName(instr.name);
                var mat = (instrType === 'coupon' || instrType === 'redemption')
                    ? fmtMaturityMonthYear(instr.maturity_date)
                    : '';
                var title = mat ? shown + ' · ' + mat : shown;
                h += "<td class='sticky left-0 bg-dark-800 px-2 py-1.5 text-gray-300 whitespace-nowrap' title=\"" + esc(title) + '">';
                h += esc(shortenName(shown, 22));
                if (mat) {
                    h += " <span class='text-gray-500'>" + esc(mat) + '</span>';
                }
                h += '</td>';
                h += "<td class='px-1.5 py-1.5 text-right font-semibold text-emerald-400'>" + this._yearInstrumentTotal(instr, year).toLocaleString('ru-RU') + '</td>';
                for (var mj = 1; mj <= 12; mj++) {
                    var ym2 = year + '-' + String(mj).padStart(2, '0');
                    var val = (instr.months && instr.months[ym2]) || 0;
                    h += "<td class='px-1.5 py-1.5 text-right " + (val > 0 ? 'text-emerald-400' : 'text-gray-600') + "'>" +
                        (val > 0 ? val.toLocaleString('ru-RU') : '') + '</td>';
                }
                h += '</tr>';
            }

            var rows = [
                { key: 'deposits', label: 'Пополнения', cls: 'text-blue-400' },
                { key: 'coupons', label: 'Купоны', cls: 'text-emerald-400' },
                { key: 'dividends', label: 'Дивиденды', cls: 'text-amber-400' },
                { key: 'redemptions', label: 'Погашения', cls: 'text-purple-400' },
                { key: 'taxes', label: 'Налоги', cls: 'text-red-400' },
                { key: 'withdrawals', label: 'Выводы', cls: 'text-red-400' },
            ];
            for (var j = 0; j < rows.length; j++) {
                var sr = rows[j];
                if (!yrSummary[sr.key]) continue;
                h += "<tr class='border-t border-dark-600/50 bg-dark-700/30 font-semibold'>";
                h += "<td class='sticky left-0 bg-dark-700/30 px-2 py-1.5 " + sr.cls + '">' + sr.label + '</td>';
                h += "<td class='px-1.5 py-1.5 text-right " + sr.cls + '">' + Math.abs(yrSummary[sr.key]).toLocaleString('ru-RU') + '</td>';
                for (var mk = 1; mk <= 12; mk++) {
                    var ym3 = year + '-' + String(mk).padStart(2, '0');
                    var sv = summary[ym3] ? (summary[ym3][sr.key] || 0) : 0;
                    h += "<td class='px-1.5 py-1.5 text-right " + clr(sv) + '">' + (sv !== 0 ? fmtPlain(sv) : '') + '</td>';
                }
                h += '</tr>';
            }
            h += '</tbody></table>';
            return h;
        },

        _yearInstrumentTotal: function (instr, year) {
            var total = 0;
            if (!instr.months) return 0;
            Object.keys(instr.months).forEach(function (ym) {
                if (ym.substring(0, 4) === String(year)) total += instr.months[ym];
            });
            return total;
        },

        _buildAllYearsTable: function (instruments, summary) {
            var h = "<table class='w-full text-[10px] border-collapse'><thead><tr class='border-b border-dark-700'>";
            h += "<th class='px-2 py-1.5 text-left text-gray-500'>Инструмент</th>";
            h += "<th class='px-2 py-1.5 text-right text-gray-500'>∑ за всё время</th>";
            h += '</tr></thead><tbody>';
            var prevType = null;
            for (var i = 0; i < instruments.length; i++) {
                var instr = instruments[i];
                var instrType = inferIncomeType(instr);
                if (instrType !== prevType) {
                    h += this._incomeGroupHeader(instrType, 2);
                    prevType = instrType;
                }
                h += "<tr class='border-b border-dark-700/30 hover:bg-dark-700/30 cursor-pointer' data-instrument=\"" + esc(instr.name) + '">';
                var shownAll = displayInstrumentName(instr.name);
                var matAll = (instrType === 'coupon' || instrType === 'redemption')
                    ? fmtMaturityMonthYear(instr.maturity_date)
                    : '';
                h += "<td class='px-2 py-1.5 text-gray-300'>" + esc(shownAll);
                if (matAll) {
                    h += " <span class='text-gray-500'>" + esc(matAll) + '</span>';
                }
                h += '</td>';
                h += "<td class='px-2 py-1.5 text-right font-semibold text-emerald-400'>" +
                    (instr.total || 0).toLocaleString('ru-RU') + ' ₽</td></tr>';
            }
            h += '</tbody></table>';
            return h;
        },

        renderComposition: function () {
            var el = document.getElementById('paComposition');
            var meta = document.getElementById('paCompositionMeta');
            if (!el) return;
            var comp = this.composition;
            if (!comp || !comp.positions || comp.positions.length === 0) {
                el.innerHTML = '<p class="text-sm text-gray-500 py-4">Нет данных о составе. Импортируйте отчёт брокера.</p>';
                if (meta) meta.textContent = '';
                return;
            }
            if (meta) meta.textContent = 'на ' + (comp.snapshot_date || '—');

            var positions = (comp.positions || []).slice().sort(function (a, b) {
                var ga = COMPOSITION_GROUP_ORDER.hasOwnProperty(a.asset_type) ? COMPOSITION_GROUP_ORDER[a.asset_type] : 9;
                var gb = COMPOSITION_GROUP_ORDER.hasOwnProperty(b.asset_type) ? COMPOSITION_GROUP_ORDER[b.asset_type] : 9;
                if (ga !== gb) return ga - gb;
                var wa = a.weight_pct || 0;
                var wb = b.weight_pct || 0;
                if (wb !== wa) return wb - wa;
                return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
            });

            var byType = {};
            var groups = [];
            positions.forEach(function (p) {
                var group = p.asset_type || 'other';
                if (!byType[group]) {
                    byType[group] = [];
                    groups.push(group);
                }
                byType[group].push(p);
            });
            groups.sort(function (a, b) {
                var ga = COMPOSITION_GROUP_ORDER.hasOwnProperty(a) ? COMPOSITION_GROUP_ORDER[a] : 9;
                var gb = COMPOSITION_GROUP_ORDER.hasOwnProperty(b) ? COMPOSITION_GROUP_ORDER[b] : 9;
                return ga - gb;
            });

            var html = '';
            var self = this;
            groups.forEach(function (group, idx) {
                html += self._buildCompositionGroupTable(group, byType[group], idx > 0);
            });
            el.innerHTML = html;
        },

        _buildCompositionGroupTable: function (group, rows, spaced) {
            var showMaturity = group === 'bond';
            var label = COMPOSITION_GROUP_LABELS[group] || COMPOSITION_GROUP_LABELS.other;
            var h = "<p class='text-[9px] uppercase tracking-wide text-gray-500 font-semibold px-2 pb-1" +
                (spaced ? " pt-4" : "") + "'>" + esc(label) + '</p>';
            h += "<div class='overflow-x-auto'><table class='w-full text-[11px] border-collapse min-w-[480px]'>";
            h += "<thead><tr class='border-b border-dark-700 text-gray-500'>";
            h += '<th class="px-2 py-2 text-left">Ticker</th>';
            h += '<th class="px-2 py-2 text-left">Название</th>';
            h += '<th class="px-2 py-2 text-right">Доля</th>';
            h += '<th class="px-2 py-2 text-right">Кол-во</th>';
            h += '<th class="px-2 py-2 text-right">Стоимость</th>';
            if (showMaturity) {
                h += '<th class="px-2 py-2 text-left">Погашение</th>';
            }
            h += '</tr></thead><tbody>';
            rows.forEach(function (p) {
                h += "<tr class='border-b border-dark-700/30 hover:bg-dark-700/20'>";
                h += '<td class="px-2 py-1.5 text-yellow-300/80 font-mono">' + esc(p.ticker || '—') + '</td>';
                h += '<td class="px-2 py-1.5 text-gray-300">' + esc(p.name) + '</td>';
                h += '<td class="px-2 py-1.5 text-right text-gray-400">' +
                    (p.weight_pct != null ? p.weight_pct.toFixed(1) + '%' : '—') + '</td>';
                h += '<td class="px-2 py-1.5 text-right text-gray-300 tabular-nums">' +
                    (p.quantity != null ? p.quantity.toLocaleString('ru-RU') : '—') + '</td>';
                h += '<td class="px-2 py-1.5 text-right text-white tabular-nums">' +
                    (p.market_value != null ? fmtPlain(p.market_value) + ' ₽' : '—') + '</td>';
                if (showMaturity) {
                    h += '<td class="px-2 py-1.5 text-gray-500">' +
                        esc(fmtMaturityMonthYear(p.maturity_date) || '—') + '</td>';
                }
                h += '</tr>';
            });
            h += '</tbody></table></div>';
            return h;
        },

        renderClosed: function () {
            var el = document.getElementById('paClosedBody');
            var meta = document.getElementById('paClosedMeta');
            if (!el) return;
            var rows = (this.composition && this.composition.closed) || [];
            if (meta) {
                meta.textContent = rows.length ? rows.length + ' шт.' : '';
            }
            if (rows.length === 0) {
                el.innerHTML =
                    '<div class="rounded-lg border border-dashed border-dark-600 px-4 py-5 text-center">' +
                    '<p class="text-[11px] text-gray-400 tracking-wide">покупка → выплаты → выход = итог</p>' +
                    '<p class="text-[11px] text-gray-600 mt-2">Пока ни одна бумага не закрыта. Продажа акции или погашение облигации появится здесь.</p>' +
                    '</div>';
                return;
            }

            var kindLabel = { sale: 'продажа', redemption: 'погашение' };
            var typeLabel = { stock: 'акция', bond: 'облигация', pif: 'ПИФ', other: '' };
            var self = this;
            var html = '<div class="space-y-2">';
            rows.forEach(function (row) {
                var result = row.result;
                var resultCls = result == null ? 'text-gray-500' : result >= 0 ? 'text-emerald-400' : 'text-red-400';
                var resultText = result == null ? 'нет цены покупки' : fmtRub(result);
                var accent = row.asset_type === 'bond' ? 'border-l-yellow-600/70' : 'border-l-amber-500/70';
                html += '<button type="button" class="w-full text-left rounded-lg bg-dark-800 border border-dark-700 border-l-2 ' +
                    accent + ' px-4 py-3 hover:border-dark-500 transition" data-closed="' + esc(row.name) + '">';
                html += '<div class="flex items-baseline justify-between gap-2 mb-2">';
                html += '<p class="text-[13px] text-white font-medium truncate">' + esc(row.name) + '</p>';
                html += '<p class="text-[10px] text-gray-500 shrink-0">' +
                    esc(typeLabel[row.asset_type] || '') +
                    (row.exit_kind ? ' · ' + esc(kindLabel[row.exit_kind] || row.exit_kind) : '') +
                    (row.closed_on ? ' · ' + esc(row.closed_on.substring(0, 7)) : '') +
                    '</p></div>';
                html += '<div class="grid grid-cols-4 gap-2">';
                html += self._closedCell('покупка', row.cost, 'text-gray-300');
                html += self._closedCell('выплаты', row.income, 'text-amber-400');
                html += self._closedCell(row.exit_kind === 'redemption' ? 'погашение' : 'продажа', row.exit, 'text-gray-200');
                html += '<div><p class="text-[9px] uppercase tracking-wider text-gray-600 mb-0.5">итог</p>' +
                    '<p class="text-sm font-semibold tabular-nums ' + resultCls + '">' + resultText + '</p></div>';
                html += '</div></button>';
            });
            html += '</div>';
            el.innerHTML = html;
            el.querySelectorAll('[data-closed]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    self.openDrilldown(btn.getAttribute('data-closed'), true);
                });
            });
        },

        _closedCell: function (label, value, cls) {
            var text = value == null ? '—' : Math.abs(value).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽';
            return '<div><p class="text-[9px] uppercase tracking-wider text-gray-600 mb-0.5">' + esc(label) + '</p>' +
                '<p class="text-[12px] tabular-nums ' + cls + '">' + text + '</p></div>';
        },

        openDrilldown: async function (instrumentName, allYears) {
            this.selectedInstrument = instrumentName;
            var panel = document.getElementById('paDrilldown');
            var title = document.getElementById('paDrillTitle');
            var body = document.getElementById('paDrillBody');
            if (!panel || !body) return;

            if (title) title.textContent = displayInstrumentName(instrumentName);
            body.innerHTML = '<p class="text-gray-500 text-sm">Загрузка...</p>';
            panel.classList.remove('hidden');

            var yearParam = this.cfYear === 'all' ? '' : '&year=' + this.cfYear;
            var ref = encodeURIComponent(instrumentName);
            try {
                var url = '/api/portfolios/' + this.portfolioId + '/payments?instrument=' + ref;
                if (this.cfYear !== 'all' && !allYears) url += '&year=' + this.cfYear;
                var r = await fetch(url, { credentials: 'same-origin' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                var data = await r.json();
                var payments = data.payments || [];
                if (payments.length === 0) {
                    body.innerHTML = '<p class="text-gray-500 text-sm">Нет выплат за выбранный период</p>';
                    return;
                }
                var html = "<table class='w-full text-[11px]'><thead><tr class='text-gray-500 border-b border-dark-700'>";
                html += '<th class="py-2 text-left">Дата</th><th class="py-2 text-left">Тип</th>';
                html += '<th class="py-2 text-right">Сумма</th><th class="py-2 text-left">Описание</th></tr></thead><tbody>';
                payments.forEach(function (p) {
                    html += "<tr class='border-b border-dark-700/30'>";
                    html += '<td class="py-1.5 text-gray-400 font-mono">' + esc(p.date) + '</td>';
                    html += '<td class="py-1.5 text-gray-300">' + esc(FLOW_LABELS[p.type] || p.type) + '</td>';
                    html += '<td class="py-1.5 text-right ' + clr(p.amount) + '">' + fmtRub(p.amount) + '</td>';
                    html += '<td class="py-1.5 text-gray-400">' + esc(p.description || '') + '</td></tr>';
                });
                html += '</tbody></table>';
                body.innerHTML = html;
            } catch (e) {
                body.innerHTML = '<p class="text-red-400 text-sm">' + esc(e.message) + '</p>';
            }
        },

        closeDrilldown: function () {
            var panel = document.getElementById('paDrilldown');
            if (panel) panel.classList.add('hidden');
            this.selectedInstrument = null;
        },
    };

    document.addEventListener('DOMContentLoaded', function () {
        var root = document.getElementById('portfolioRoot');
        if (!root) return;
        var pid = root.getAttribute('data-portfolio-id');
        if (pid) window.PortfolioAnalytics.init(parseInt(pid, 10));
    });
})();
