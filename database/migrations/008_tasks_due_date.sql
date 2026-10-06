-- День (локальная дата 'YYYY-MM-DD'), на который запланирована задача «на день».
-- Заполняется только для новых задач «Сегодня»/«Завтра». У старых задач NULL —
-- для них день по-прежнему вычисляется как «дата создания + 1».
ALTER TABLE tasks ADD COLUMN due_date TEXT;

INSERT OR IGNORE INTO schema_version (version) VALUES (8);
