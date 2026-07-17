"""Run inside container: python3 /tmp/import_all.py"""
import sqlite3

conn = sqlite3.connect('/app/planner.db')
cur = conn.cursor()

# === CATEGORY: Косметолог ===
cur.execute("SELECT id FROM categories WHERE name='Косметолог' AND parent_id=22 AND type='task'")
kosmo = cur.fetchone()
if kosmo:
    kosmo_id = kosmo[0]
else:
    cur.execute("INSERT INTO categories (name, parent_id, type) VALUES ('Косметолог', 22, 'task')")
    kosmo_id = cur.lastrowid
    print(f"✓ Косметолог (id={kosmo_id})")

# === 2 JULY ===
cur.execute("UPDATE transactions SET amount=-5200 WHERE date='2026-07-02' AND description='Совкомбанк / ИИС' AND amount=-9000")
print(f"✓ Совкомбанк/ИИС -9000→-5200" if cur.rowcount else "⚠ ИИС не найден")

txns = [
    ('2026-07-02', 'АЗС (Совком)', -2330.0, 52),
    ('2026-07-02', 'Магазины (Совком)', -1470.0, 39),
    ('2026-07-07', 'Перевод себе / ИИС (Совком)', -9400.0, 37),
    ('2026-07-07', 'Ресторан (Совком)', -3300.0, 41),
    ('2026-07-07', 'АЗС (Совком)', -1500.0, 52),
    ('2026-07-07', 'Магазины (Совком)', -800.0, 39),
    # 12 July
    ('2026-07-12', 'Анастасия Х.', 330.0, 147),
    ('2026-07-12', 'IP IVANOVSKAYA', -200.0, 39),
    ('2026-07-12', 'IP IVANOVSKAYA', -326.0, 39),
    ('2026-07-12', 'IP IVANOVSKAYA', -144.0, 39),
    ('2026-07-12', 'IP FOMAKIN AN...', -1281.0, 42),
    ('2026-07-12', 'АЗС', -1425.0, 52),
    ('2026-07-12', 'ЛюдиЛюбят', -169.0, 42),
    ('2026-07-12', 'Красное&Белое', -109.99, 39),
    ('2026-07-12', 'Парковки СПб', -200.0, 56),
    ('2026-07-12', 'Перекресток', -299.98, 39),
    ('2026-07-12', 'Краски вкуса', -650.0, 42),
    ('2026-07-12', 'MOTOSTYLES P...', -2440.0, 136),
    ('2026-07-12', 'IP IVANOVSKAYA', -110.0, 39),
    ('2026-07-12', 'АЗС', -255.0, 34),
    ('2026-07-12', 'АЗС', -1436.0, 52),
    # 11 July
    ('2026-07-11', 'Хагакурэ', -1560.0, 41),
    ('2026-07-11', 'LAMPAPUL2', -345.0, 41),
    ('2026-07-11', 'TERM. PULKOVSKIJ', -500.0, 114),
    ('2026-07-11', 'Полина Ш.', -500.0, 40),
    ('2026-07-11', 'Teboil', -1404.60, 52),
    # 10 July
    ('2026-07-10', 'Магнит', -451.67, 39),
    ('2026-07-10', 'Газпромнефть', -255.0, 34),
    ('2026-07-10', 'Termoland', -5432.0, 114),
    ('2026-07-10', 'Полина Ш.', -300.0, 40),
    ('2026-07-10', 'Перевод себе / Озон', -4400.0, 137),
    ('2026-07-10', 'Магнит', -255.0, 34),
    # 9 July
    ('2026-07-09', 'Александра П.', -11000.0, kosmo_id),
    ('2026-07-09', 'IP IVANOVSKAYA', -110.0, 39),
    ('2026-07-09', 'Перевод себе / ИИС (СБП)', -6000.0, 37),
    # 8 July
    ('2026-07-08', 'ZELENYJ KVART...', -1071.72, 39),
    ('2026-07-08', 'Street Food', -600.0, 42),
    ('2026-07-08', 'Перевод себе / Озон', -8000.0, 43),
    ('2026-07-08', 'Красное&Белое', -510.0, 34),
    ('2026-07-08', 'Булки', -368.0, 39),
    ('2026-07-08', 'Перевод себе / Озон', -1000.0, 10),
]

added = 0
for date, desc, amt, cat in txns:
    cur.execute("SELECT id FROM transactions WHERE date=? AND description=? AND amount=?", (date, desc, amt))
    if cur.fetchone():
        continue
    cur.execute("INSERT INTO transactions (date, description, amount, category_id) VALUES (?,?,?,?)", (date, desc, amt, cat))
    added += 1
print(f"✓ Транзакций добавлено: {added}")

# === GOALS ===
cur.execute("SELECT current_amount FROM financial_goals WHERE id=3")
old = cur.fetchone()[0]
cur.execute("UPDATE financial_goals SET current_amount=500000 WHERE id=3")
cur.execute("INSERT INTO goal_history (goal_id, new_amount, delta, note, created_at) VALUES (3, 500000, ?, 'Добивка до 500к', datetime('now'))", (500000 - old,))
print(f"✓ Подушка: {old:,.0f} → 500 000")

cur.execute("UPDATE financial_goals SET name='Автомобиль' WHERE id=6")
print(f"✓ Брокерский 1 → Автомобиль")

cur.execute("DELETE FROM financial_goals WHERE id=4")
print(f"✓ Удалён: Квартира")

cur.execute("SELECT id FROM financial_goals WHERE name='Зимовка'")
if not cur.fetchone():
    cur.execute("INSERT INTO financial_goals (name, target_amount, current_amount, created_at) VALUES ('Зимовка', 200000, 0, datetime('now'))")
    print(f"✓ Зимовка (200k)")

conn.commit()

# Verify
print("\n=== ПРОВЕРКА ===")
for d in ['2026-07-02','2026-07-07','2026-07-08','2026-07-09','2026-07-10','2026-07-11','2026-07-12']:
    rows = cur.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE date=?", (d,)).fetchone()
    print(f"{d}: {rows[0]} тр, сумма {rows[1]:,.2f}" if rows[1] else f"{d}: {rows[0]} тр")

print()
for r in cur.execute("SELECT id, name, target_amount, current_amount FROM financial_goals ORDER BY id").fetchall():
    print(f"  {r[0]}: {r[1]:25s} {r[3]:>10,.0f} / {r[2]:>10,.0f}")

conn.close()
print("\n✓ ГОТОВО")
