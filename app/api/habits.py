from collections import defaultdict
import calendar

from fastapi import APIRouter, Depends, HTTPException, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, update, tuple_, func
from sqlalchemy.exc import IntegrityError
from app.db.database import async_session
from app.models.habit import CYCLE_MODE_DAYS, CYCLE_MODE_MONTHLY, Habit
from app.models.habit_log import HabitLog
from datetime import date, timedelta
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import RedirectResponse, HTMLResponse

from app.web.deps import templates

router = APIRouter(prefix="/api/habits", tags=["habits"])

SERVER_DEFAULT_DAYS = 30
MAX_TARGET_DAYS = 366


class HabitToggle(BaseModel):
    habit_id: int
    date: date


def is_monthly(habit: Habit) -> bool:
    """Непрерывный трекер: цикл — календарный месяц, идёт сам по календарю."""
    return (getattr(habit, "cycle_mode", None) or CYCLE_MODE_DAYS) == CYCLE_MODE_MONTHLY


def target_days_of(habit: Habit) -> int:
    """Длина цикла «на N дней». У месячных не читается — там длина = дней в месяце."""
    return habit.target_days or SERVER_DEFAULT_DAYS


def month_bounds(day: date) -> tuple[date, date]:
    """Первый и последний день месяца, в котором лежит день."""
    last_day = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last_day)


def shift_month(day: date, months: int) -> date:
    """Первое число месяца, сдвинутого на `months` (можно в любую сторону)."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def monthly_cycle_number(habit: Habit, today: date) -> int:
    """Номер цикла непрерывного трекера — сколько месяцев прошло с месяца старта.

    Считается от start_date, а не от сохранённого current_cycle: трекер идёт сам,
    и если Веры месяц не было в планере, счётчик циклов не должен отставать.
    """
    start = habit.start_date or today
    months = (today.year - start.year) * 12 + (today.month - start.month)
    return max(months, 0) + 1


def monthly_cycle_window(habit: Habit, cycle_number: int, today: date) -> tuple[date, date]:
    """Окно месячного цикла: первый — от старта до конца месяца, дальше — месяц целиком."""
    start = habit.start_date or today
    if cycle_number <= 1:
        return start, month_bounds(start)[1]
    first = shift_month(start, cycle_number - 1)
    return first, month_bounds(first)[1]


def current_cycle_number(habit: Habit, today: date) -> int:
    """Номер текущего цикла: у непрерывного он считается по календарю."""
    if is_monthly(habit):
        return monthly_cycle_number(habit, today)
    return habit.current_cycle or 1


def habit_cycle_window(habit: Habit, cycle_number: int, today: date) -> tuple[date, date]:
    """Окно цикла: [начало, конец], оба дня включительно."""
    if is_monthly(habit):
        return monthly_cycle_window(habit, cycle_number, today)
    start = habit.start_date or today
    return start, start + timedelta(days=target_days_of(habit) - 1)


def sync_monthly_cycle(habit: Habit, today: date) -> Habit:
    """Держит сохранённый номер цикла непрерывного трекера в согласии с календарём.

    Трекер идёт сам: сменился месяц — сменился цикл. Пишем только при расхождении,
    поэтому на каждой загрузке дашборда UPDATE не случается. Сохранённое значение
    нужно остальным читателям current_cycle, чтобы база не рассказывала про
    «Цикл 1» у трекера, который живёт четвёртый месяц.
    """
    if not is_monthly(habit):
        return habit
    derived = monthly_cycle_number(habit, today)
    if (habit.current_cycle or 1) != derived:
        habit.current_cycle = derived
    return habit


def days_label(days: int) -> str:
    """«21 день», «22 дня», «30 дней» — подпись кнопки и подсказки."""
    tail10, tail100 = days % 10, days % 100
    if tail10 == 1 and tail100 != 11:
        word = "день"
    elif tail10 in (2, 3, 4) and tail100 not in (12, 13, 14):
        word = "дня"
    else:
        word = "дней"
    return f"{days} {word}"

def compute_next_cycle_start(habit: Habit, today: date) -> date:
    """Дата старта следующего цикла — строго на день после окна предыдущего.

    Окно цикла — это `target_days` дней от старта включительно:
    [start, start + target_days - 1]. Старт следующего = start + target_days,
    поэтому последний день одного цикла и первый день следующего никогда не
    совпадают, и ни один день не принадлежит двум циклам.

    Циклы идут подряд и не делят между собой ни одного дня: новый начинается
    ровно через `target_days` дней после начала текущего. Раньше, если нажали
    «След. 30 дней» до конца окна, возвращалось `today`, и нажатие в последний
    день начинало новый цикл в тот же день. Этот день попадал в оба цикла: в
    сетке нового цикла он выглядел неотмеченным (отметка принадлежит прошлому
    циклу), а повторная отметка на нём падала на старом ограничении
    уникальности (habit_id, date) — «день не отмечается».

    Нажатие в свой последний день даёт старт нового цикла со следующего дня;
    нажатие с опозданием оставляет пропущенные дни в начале новой сетки.
    Раньше конца окна функция посчитала бы старт в будущем — но эндпоинт
    `restart_habit_cycle` такие нажатия отклоняет (в середине окна продлевать
    нечего), так что «трекер до будущей даты не отмечается» не случается.
    """
    if habit.start_date is None:
        return today
    if is_monthly(habit):
        # Непрерывный трекер идёт сам: следующий цикл — следующий календарный
        # месяц. Кнопки у него нет, перевод случается в sync_monthly_cycle.
        return shift_month(today, 1)
    target_days = habit.target_days or SERVER_DEFAULT_DAYS
    return habit.start_date + timedelta(days=target_days)


async def load_habit_logs_map(
    db: AsyncSession, habits: list[Habit]
) -> dict[int, set[str]]:
    """Batch-load current-cycle log dates for dashboard habits (one query)."""
    if not habits:
        return {}
    today = date.today()
    pairs = [(h.id, current_cycle_number(h, today)) for h in habits]
    result = await db.execute(
        select(HabitLog.habit_id, HabitLog.date).where(
            tuple_(HabitLog.habit_id, HabitLog.cycle_number).in_(pairs)
        )
    )
    out: dict[int, set[str]] = defaultdict(set)
    for habit_id, log_date in result.all():
        out[habit_id].add(log_date.isoformat())
    return dict(out)


def build_habit_cycle_grid(habit: Habit, today: date) -> dict:
    """Сетка текущего цикла для дашборда и истории."""
    if is_monthly(habit):
        start, end = monthly_cycle_window(
            habit, monthly_cycle_number(habit, today), today
        )
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    else:
        start = habit.start_date or today
        dates = [start + timedelta(days=i) for i in range(target_days_of(habit))]
    return {
        "start": start,
        "end": dates[-1],
        "dates": dates,
        "start_weekday": start.weekday(),
        # Длина цикла — это длина сетки: у месячных разное число дней в месяце,
        # у «на N дней» — ровно N. Счётчик прогресса считает и то и другое верно.
        "target_days": len(dates),
    }


def cycle_window(habit: Habit) -> tuple[date, date]:
    """Окно текущего цикла: [начало, конец], оба дня включительно.

    Конец — за день до старта следующего цикла, поэтому окна соседних циклов
    не пересекаются ни одним днём.
    """
    today = date.today()
    return habit_cycle_window(habit, current_cycle_number(habit, today), today)


def compute_cycle_start_dates(
    habit: Habit,
    logs_by_cycle: dict[int, list[date]],
    today: date | None = None,
) -> dict[int, date]:
    """Вычисляет дату старта каждого цикла, идя назад от текущего."""
    today = today or date.today()
    if is_monthly(habit):
        # Месячные циклы привязаны к календарю: отмотки по отметкам не нужны,
        # старт цикла N — первое число его месяца (у первого — день заведения).
        current = monthly_cycle_number(habit, today)
        return {
            num: monthly_cycle_window(habit, num, today)[0]
            for num in range(current, 0, -1)
        }

    target_days = target_days_of(habit)
    starts: dict[int, date] = {}
    next_start = habit.start_date or date.today()
    current = current_cycle_number(habit, today)
    starts[current] = next_start

    for cycle_num in range(current - 1, 0, -1):
        candidate = next_start - timedelta(days=target_days)
        marked = logs_by_cycle.get(cycle_num, [])

        if marked:
            mark_min = min(marked)
            window_end = candidate + timedelta(days=target_days - 1)
            if mark_min < candidate or max(marked) > window_end:
                starts[cycle_num] = mark_min
            else:
                starts[cycle_num] = candidate
        else:
            starts[cycle_num] = candidate

        next_start = starts[cycle_num]

    return starts


def build_habit_history_cycles(habit: Habit, logs: List[HabitLog], today: date) -> List[dict]:
    """Собирает историю отметок по циклам (новые сверху)."""
    by_cycle: dict[int, list[date]] = defaultdict(list)
    for log in logs:
        by_cycle[log.cycle_number].append(log.date)

    monthly = is_monthly(habit)
    target_days = target_days_of(habit)
    current = current_cycle_number(habit, today)
    cycle_starts = compute_cycle_start_dates(habit, by_cycle, today)
    cycles: List[dict] = []

    for cycle_num in range(current, 0, -1):
        marked_dates = sorted(set(by_cycle.get(cycle_num, [])))
        is_current = cycle_num == current
        marked_iso = {d.isoformat() for d in marked_dates}

        if is_current:
            grid = build_habit_cycle_grid(habit, today)
            dates = grid["dates"]
            start_weekday = grid["start_weekday"]
        else:
            start = cycle_starts[cycle_num]
            # У месячных длина цикла своя в каждом месяце, у «на N дней» — N.
            length = (
                (monthly_cycle_window(habit, cycle_num, today)[1] - start).days + 1
                if monthly
                else target_days
            )
            dates = [start + timedelta(days=i) for i in range(length)]
            start_weekday = start.weekday()

        cycles.append({
            "cycle_number": cycle_num,
            "is_current": is_current,
            "empty": not marked_dates,
            "dates": dates,
            "logs": marked_iso,
            "progress": len(marked_iso),
            "start_weekday": start_weekday,
            "target_days": len(dates),
            # Подпись окна нужна месячным циклам: по одной цифре «Цикл 3» не
            # понять, какой это месяц, а сетка в свёрнутом виде не видна.
            "window_label": (
                f"{dates[0].strftime('%d.%m')} – {dates[-1].strftime('%d.%m')}"
                if monthly
                else None
            ),
        })

    return cycles


@router.get("/")
async def get_habits():
    async with async_session() as db:
        result = await db.execute(select(Habit).where(Habit.is_active == True, Habit.is_archived == False))
        return result.scalars().all()

@router.post("/create")
async def create_habit(
    title: str = Form(...),
    cycle_mode: str = Form(CYCLE_MODE_DAYS),
    target_days: str = Form(str(SERVER_DEFAULT_DAYS)),
    category_id: int = Form(22),
):
    """Создать трекер. Старт — всегда сегодня (дату с формы убрали).

    cycle_mode=monthly — «непрерывный»: циклы-календарные месяцы, номер цикла
    растёт сам, нажимать ничего не нужно.
    cycle_mode=days — «на N дней»: один цикл ровно на N дней; когда он кончился,
    Вера сама решает — «Продлить» тем же N или в архив.

    Дата старта не принимается вообще: поле с формой убрали, а «всегда сегодня»,
    оставленное только на совести формы, ломается первым же внешним вызовом —
    трекер со стартом в будущем не отмечается до этого дня (toggle отдаёт 409).
    Лишнее поле в запросе FastAPI просто игнорирует, старым вызовам не больно.

    target_days принимаем строкой: у месячного трекера поле в форме можно
    очистить, и 422 от FastAPI на пустое число — не то, что Вера должна видеть.
    """
    mode = CYCLE_MODE_MONTHLY if cycle_mode == CYCLE_MODE_MONTHLY else CYCLE_MODE_DAYS
    try:
        days = int(str(target_days).strip())
    except (TypeError, ValueError):
        days = 21
    # Границы на сервере, а не только в input: 0 дней или 10 000 рисуют сетку
    # без конца. У месячных длина цикла своя, поле держим как нейтральное.
    days = min(max(days, 1), MAX_TARGET_DAYS) if mode == CYCLE_MODE_DAYS else SERVER_DEFAULT_DAYS

    async with async_session() as db:
        new_habit = Habit(
            title=title,
            start_date=date.today(),
            category_id=category_id,
            target_days=days,
            cycle_mode=mode,
            current_cycle=1,
        )
        db.add(new_habit)
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@router.post("/toggle")
async def toggle_habit(data: HabitToggle):
    today = date.today()
    async with async_session() as db:
        # Получаем привычку, чтобы знать текущий цикл
        habit_res = await db.execute(select(Habit).where(Habit.id == data.habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        # Непрерывный трекер: номер цикла — по календарю. sync нужен, чтобы
        # отметка легла в тот же цикл, который рисует дашборд.
        sync_monthly_cycle(habit, today)
        cycle_number = current_cycle_number(habit, today)

        # День обязан лежать в окне текущего цикла. Страница, открытая до смены
        # цикла, иначе запишет отметку прошлого дня в новый цикл — и день
        # окажется отмеченным в двух циклах сразу.
        window_start, window_end = habit_cycle_window(habit, cycle_number, today)
        if not (window_start <= data.date <= window_end):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"День {data.date.isoformat()} не входит в текущий цикл "
                    f"({window_start.isoformat()} — {window_end.isoformat()})"
                ),
            )

        result = await db.execute(
            select(HabitLog).where(
                and_(
                    HabitLog.habit_id == data.habit_id,
                    HabitLog.date == data.date,
                    HabitLog.cycle_number == cycle_number
                )
            )
        )
        existing_log = result.scalar_one_or_none()

        if existing_log:
            await db.delete(existing_log)
            action = "removed"
        else:
            new_log = HabitLog(
                habit_id=data.habit_id,
                date=data.date,
                cycle_number=cycle_number
            )
            db.add(new_log)
            action = "added"
        
        try:
            await db.commit()
        except IntegrityError:
            # Старое ограничение (habit_id, date) на непересобранной базе: день
            # уже отмечен в другом цикле. Пользователь должен видеть отказ, а не
            # «нажал — и ничего не произошло».
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Этот день уже отмечен в другом цикле — обнови страницу",
            )
        return {"status": "success", "action": action}

@router.post("/{habit_id}/archive")
async def archive_habit(habit_id: int):
    async with async_session() as db:
        await db.execute(
            update(Habit).where(Habit.id == habit_id).values(is_archived=True)
        )
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@router.get("/{habit_id}/history", response_class=HTMLResponse)
async def habit_history(habit_id: int, request: Request):
    """HTML-фрагмент: полная история отметок трекера в попапе."""
    today = date.today()
    async with async_session() as db:
        habit_res = await db.execute(select(Habit).where(Habit.id == habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        logs_result = await db.execute(
            select(HabitLog)
            .where(HabitLog.habit_id == habit_id)
            .order_by(HabitLog.cycle_number, HabitLog.date)
        )
        logs = list(logs_result.scalars().all())
        cycles = build_habit_history_cycles(habit, logs, today)

    return templates.TemplateResponse(request, "partials/habit_history_modal.html", {
        "request": request,
        "habit": habit,
        "cycles": cycles,
        "total_marks": len(logs),
        "today": today,
    })


@router.post("/{habit_id}/next-cycle")
async def restart_habit_cycle(habit_id: int):
    """Завершить текущий цикл и начать новый (циклы идут подряд, без нахлёста).

    Только для трекеров «на N дней». У непрерывного перевод делать нечем:
    цикл сменяется сам вместе с календарным месяцем, и нажатие из старой
    вкладки не должно ломать этот порядок.
    """
    today = date.today()
    async with async_session() as db:
        habit_res = await db.execute(select(Habit).where(Habit.id == habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        if is_monthly(habit):
            return RedirectResponse(url="/", status_code=303)

        # Продлевать можно только с последнего дня окна. Раньше нажатие в
        # середине цикла уводило старт нового цикла в будущее: сетка рисовалась
        # будущими числами, сегодняшний день не отмечался, а второе нажатие
        # («почему сетка старая?») отодвигало старт ещё на целый цикл — трекер
        # выглядел сломанным до этого дня. В середине окна продлевать нечего:
        # цикл ещё идёт. Последний день — можно: новый цикл начнётся со
        # следующего (это и есть случай Веры «нажала в последний день»).
        _, window_end = habit_cycle_window(
            habit, current_cycle_number(habit, today), today
        )
        if today < window_end:
            return RedirectResponse(url="/", status_code=303)

        new_start = compute_next_cycle_start(habit, today)
        previous_cycle = habit.current_cycle or 1

        # Одним UPDATE с условием на прежний номер цикла: при двойном сабмите
        # второй запрос читает то же состояние до commit, и раньше это уводило
        # цикл на два вперёд. rowcount показывает, нашёл ли UPDATE своё состояние.
        moved = await db.execute(
            update(Habit)
            .where(
                Habit.id == habit_id,
                func.coalesce(Habit.current_cycle, 1) == previous_cycle,
            )
            .values(current_cycle=previous_cycle + 1, start_date=new_start)
        )
        if moved.rowcount != 1:
            await db.rollback()
            return RedirectResponse(url="/", status_code=303)

        await db.commit()
    return RedirectResponse(url="/", status_code=303)
