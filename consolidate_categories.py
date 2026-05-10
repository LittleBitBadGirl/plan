import sqlite3

def consolidate(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Схлопываем дубликаты (Keep ID, Remove ID)
    dupes = [
        (40, 109), # Благотворительность
        (43, 111), # Вещи
        (53, 108), # Связь
        (59, 110), # Подарки
        (68, 113), # Красота
    ]
    
    for keep_id, remove_id in dupes:
        # Перепривязываем транзакции
        cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (keep_id, remove_id))
        # Перепривязываем подкатегории
        cursor.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (keep_id, remove_id))
        # Удаляем дубликат
        cursor.execute("DELETE FROM categories WHERE id = ?", (remove_id,))
        print(f"Consolidated ID {remove_id} into {keep_id}")

    # 2. Создаем глобальную категорию 'Прочее' для финансов
    cursor.execute("SELECT id FROM categories WHERE name = 'Прочее' AND type = 'finance' AND is_global = 1")
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO categories (name, is_global, type, created_at) VALUES ('Прочее', 1, 'finance', datetime('now'))")
        prochee_id = cursor.lastrowid
        print(f"Created global 'Прочее' finance category (ID {prochee_id})")
    else:
        prochee_id = row[0]

    # 3. Переносим 'Табак' в 'Прочее'
    # Находим ID Табака
    cursor.execute("SELECT id FROM categories WHERE name = 'Табак' AND type = 'finance'")
    tabak_row = cursor.fetchone()
    if tabak_row:
        cursor.execute("UPDATE categories SET parent_id = ? WHERE id = ?", (prochee_id, tabak_row[0]))
        print(f"Moved 'Табак' (ID {tabak_row[0]}) to 'Прочее'")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "planner.db"
    consolidate(db)
