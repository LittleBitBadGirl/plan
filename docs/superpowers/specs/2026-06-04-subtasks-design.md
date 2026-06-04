# Subtasks: UI fix + progress accounting

**Date:** 2026-06-04  
**Status:** Approved (brainstorming)  
**Approach:** 1 — behavior layer on existing `Task` model

---

## Problem

1. **UI bug:** Clicking "done" on a subtask shows `✅ выполнено` or breaks layout instead of inline strike-through.
2. **Model mismatch:** Subtasks are archived on completion like full tasks; big multi-day work needs a persistent checklist on the dashboard.

## User decisions

| Question | Choice |
|----------|--------|
| Workflow | Parent stays on dashboard for several days; subtasks = progress checklist (no daily re-planning) |
| Daily vs weekly stats | Hybrid: subtasks add partial daily progress; weekly stats count parent as one item |
| All subtasks done | Parent auto-closes and leaves dashboard |

---

## Root cause (UI bug)

After add/delete subtask, `partials/subtasks.html` replaces the subtasks panel. Its complete form uses:

- `hx-target="#subtask-{id}"` + `hx-swap="outerHTML"`

The server endpoint `POST /tasks/{id}/complete` returns either the full tasks list or `✅ выполнено` (when `HX-Target` starts with `task-`). Neither is a single subtask row → broken UI.

Initial render in `tasks_list.html` correctly targets `#tasks-list`, but adding a subtask switches to the broken partial.

---

## Design

### 1. Unified subtask row partial

Create `partials/subtask_row.html` — one checklist row:

- Checkbox / done button
- Title (strike-through when `status == 'выполнена'`)
- Delete button

Both `tasks_list.html` and `subtasks.html` include this partial (no duplicated HTMX targets).

### 2. New endpoint: `POST /tasks/{sub_id}/complete-subtask`

Web-only HTMX endpoint (API can stay aligned separately).

**Subtask completion:**

| Field | Value |
|-------|-------|
| `status` | `выполнена` |
| `completed_at` | `now()` |
| `is_archived` | **`False`** — stays visible, struck through |

**Response:**

- Default: render `subtask_row.html` for this subtask (swap outerHTML of row)
- OOB: update `#today-stats-counter` with new X/Y
- If all siblings done → auto-close parent (see below) and return full `#tasks-list` instead

**Parent auto-close (when last subtask completed):**

```
parent.status = 'выполнена'
parent.completed_at = now()
parent.is_archived = True
```

Return refreshed `partials/tasks_list.html` for `#tasks-list`. Do not double-count parent in daily stats (last subtask already counted).

**Parent manual complete (existing button):**

- Close all open subtasks (`status=выполнена`, `completed_at=now`, `is_archived=False`)
- Archive parent
- Daily stats: +N where N = subtask count (or +1 if no subtasks)

Align web route with existing API cascade logic in `app/api/tasks.py`.

### 3. Multi-day persistence

No schema changes. Existing rollover (`rollover_service.py`) moves overdue parent to today. Completed subtasks remain struck through; open ones stay active. Parent `postpones` counter unchanged.

### 4. Daily progress formula (`get_today_stats`)

For each root task on today's dashboard (`due_date=today`, `parent_task_id=None`, not archived):

| Case | Adds to **total (Y)** | Adds to **completed (X)** |
|------|----------------------|---------------------------|
| No subtasks | +1 | +1 if completed today |
| Has subtasks | +N (all subtasks) | +1 per subtask with `completed_at.date() == today` |

Notes:

- Subtasks completed on previous days show struck through but do not inflate today's X.
- Auto-close parent does not add extra +1 to X.
- Manual parent complete with N subtasks adds +N to X (all counted as done today).

Implement as dedicated helper e.g. `get_today_progress(db) -> (completed, total)` replacing inline logic in `get_today_stats`.

### 5. Weekly / stats page

**No change** to filters. `_completed_tasks_base_filter` already requires `parent_task_id == None`:

- Individual subtask completions do not appear in weekly KPIs
- Parent appears once when fully closed (`parent.completed_at` in range)

### 6. Subtask list queries

When loading subtasks for display, include completed ones (`is_archived == False` OR `status == 'выполнена'` with parent still open). Exclude subtasks whose parent is archived.

Delete subtask behavior unchanged (hard delete, refresh panel).

---

## Files to touch

| File | Change |
|------|--------|
| `app/web/templates/partials/subtask_row.html` | **New** — single row partial |
| `app/web/templates/partials/subtasks.html` | Use `subtask_row.html`; fix targets |
| `app/web/templates/partials/tasks_list.html` | Use `subtask_row.html`; remove inline duplicate |
| `app/web/routes/tasks.py` | Add `complete_subtask`; align parent cascade |
| `app/web/deps.py` | New `get_today_progress()` |
| `app/api/tasks.py` | Subtask complete: don't archive subtask; archive parent when all done |
| `tests/test_web_pages.py` | HTMX subtask complete → strike-through, not `✅ выполнено` |
| `tests/test_api_tasks.py` | Subtask not archived; parent auto-close |
| New `tests/test_subtask_progress.py` | Daily progress formula cases |

---

## Out of scope

- Separate `SubtaskItem` table or JSON checklist
- Per-subtask due dates
- Undo / reopen subtask (future)
- Telegram bot subtask UX

---

## Verification

1. Add subtask → complete → title strike-through immediately, panel stays open
2. No `✅ выполнено` text anywhere on dashboard subtask flow
3. Complete last subtask → parent disappears from dashboard
4. Dashboard header: partial progress updates via OOB
5. Parent with 2/5 done rolls over next day → still on dashboard, 2 struck through
6. Weekly stats: +1 only when parent fully closed, not per subtask
