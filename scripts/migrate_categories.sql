-- Миграция категорий: иерархия + новые подкатегории
-- Запустить внутри контейнера: docker-compose exec app sqlite3 /app/planner.db < scripts/migrate_categories.sql

-- 1. Новые родительские категории
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (122, 'Сбережения', 'finance', NULL);
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (123, 'Связь', 'finance', NULL);
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (124, 'Красота', 'finance', NULL);
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (125, 'Доходы', 'finance', NULL);

-- 2. Новые подкатегории
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (126, 'Аренда', 'finance', 49);      -- Вертикаль
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (127, 'Самокаты', 'finance', 47);    -- Транспорт
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (128, 'Клиники', 'finance', 112);    -- Здоровье
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (129, 'Премии', 'finance', 125);     -- Доходы
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (130, 'Телефон', 'finance', 123);    -- Связь
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (131, 'Озон / ВБ', 'finance', 43);   -- Вещи
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (137, 'Магазины', 'finance', 43);     -- Вещи
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (132, 'Мотоцикл', 'finance', 47);    -- Транспорт
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (133, 'Вещи', 'finance', 107);       -- Даня
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (134, 'Развлечения', 'finance', 107);-- Даня
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (135, 'Кружки', 'finance', 107);     -- Даня
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (136, 'Общее', 'finance', 107);     -- Даня

-- 3. Перемещение существующих категорий под новых родителей
-- Доходы: Зарплата, Аванс, Возврат, Кэшбэк, Авито, Командировочные
UPDATE categories SET parent_id = 125 WHERE id IN (65, 85, 66, 86, 93, 94);

-- Связь: Подписки
UPDATE categories SET parent_id = 123 WHERE id = 55;

-- Сбережения: ИИС, Подушка
UPDATE categories SET parent_id = 122 WHERE id IN (37, 61);

-- Красота: Салоны, Косметолог
UPDATE categories SET parent_id = 124 WHERE id IN (97, 101);

-- 4. Исправить is_global для новых родительских категорий (если миграция применяется повторно)
UPDATE categories SET is_global = 1 WHERE id IN (122, 123, 124, 125);
UPDATE categories SET is_global = 1 WHERE type = 'finance' AND parent_id IS NULL AND is_global = 0;

-- 5. Метро → Общественный (это транспорт, не магазин)
INSERT OR IGNORE INTO categories (id, name, type, parent_id) VALUES (138, 'Общественный', 'finance', 47);  -- Транспорт
UPDATE transactions SET category_id = 138 WHERE category_id = 71;
DELETE FROM categories WHERE id = 71;

-- 6. Убрать Авито из Доходов и удалить (пустая категория)
UPDATE categories SET parent_id = NULL WHERE id = 93;
UPDATE transactions SET category_id = 137 WHERE category_id = 93;
DELETE FROM categories WHERE id = 93 AND NOT EXISTS (SELECT 1 FROM transactions WHERE category_id = 93);

-- 7. Исправить is_global=NULL → 0 для всех подкатегорий
UPDATE categories SET is_global = 0 WHERE type = 'finance' AND parent_id IS NOT NULL AND (is_global IS NULL OR is_global != 0);

-- Проверка
SELECT c.id, c.name, p.name as parent
FROM categories c
LEFT JOIN categories p ON c.parent_id = p.id
WHERE c.type = 'finance'
ORDER BY c.parent_id IS NULL DESC, c.parent_id, c.id;
