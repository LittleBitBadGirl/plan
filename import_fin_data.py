import sqlite3
import os
import sys
from datetime import datetime

# Путь к БД
DB_PATH = "/Users/vera/Desktop/личные_доки/СLI/plan/planner.db"
FIN_BUDGET_DIR = "/Users/vera/Desktop/личные_доки/СLI/fin_budget"

files = [
    "january_transactions.py",
    "february_transactions.py",
    "march_transactions.py",
    "april_transactions.py"
]

def get_or_create_category(cursor, name, parent_id=None):
    cursor.execute("SELECT id FROM categories WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    
    # Пытаемся найти подходящего родителя если это подкатегория
    # Для упрощения пока просто создаем или привязываем к 'Личное' (id=2)
    is_global = 1 if parent_id is None else 0
    cursor.execute(
        "INSERT INTO categories (name, is_global, parent_id, created_at) VALUES (?, ?, ?, ?)",
        (name, is_global, parent_id or 2, datetime.now())
    )
    return cursor.lastrowid

def import_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Очистим старые транзакции если нужно (или просто добавим новые)
    # cursor.execute("DELETE FROM transactions")
    
    total_imported = 0
    
    for filename in files:
        file_path = os.path.join(FIN_BUDGET_DIR, filename)
        if not os.path.exists(file_path):
            print(f"File {filename} not found, skipping...")
            continue
            
        print(f"Importing {filename}...")
        
        # Читаем файл как текст и исполняем (опасно, но в данном контексте допустимо)
        namespace = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            exec(f.read(), namespace)
        
        if 'transactions' in namespace:
            for t in namespace['transactions']:
                # (дата, описание, сумма, общая_категория, частная_категория)
                date_str, desc, amount, main_cat, sub_cat = t
                
                # Получаем/создаем категорию
                main_cat_id = get_or_create_category(cursor, main_cat)
                sub_cat_id = get_or_create_category(cursor, sub_cat, main_cat_id)
                
                cursor.execute(
                    "INSERT INTO transactions (date, amount, description, category_id, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (date_str, amount, desc, sub_cat_id, "import_fin_budget", datetime.now())
                )
                total_imported += 1
                
        if 'incomes' in namespace:
            # Доходы запишем со знаком минус (или просто как поступления)
            # В нашей модели Transaction amount положительный = расход.
            # Давайте доходы писать со знаком минус.
            for i in namespace['incomes']:
                # (дата, описание, сумма, категория)
                date_str, desc, amount, cat_name = i
                cat_id = get_or_create_category(cursor, cat_name)
                
                cursor.execute(
                    "INSERT INTO transactions (date, amount, description, category_id, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (date_str, -amount, desc, cat_id, "import_fin_budget", datetime.now())
                )
                total_imported += 1

    conn.commit()
    conn.close()
    print(f"Total imported: {total_imported} transactions")

if __name__ == "__main__":
    import_data()
