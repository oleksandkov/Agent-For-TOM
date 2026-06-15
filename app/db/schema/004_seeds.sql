-- app/db/schema/004_seeds.sql
-- Idempotent initial data inserted the first time the DB is created.
-- Inserts are gated by `WHERE NOT EXISTS` so re-running the seed is
-- safe. The Python-level `seed.py` handles more complex seeding
-- (instructions and user_style content) because the SQL file should
-- stay declarative.

-- Built-in templates. The instruction content is added by seed.py.
INSERT OR IGNORE INTO templates (id, name, display_name, description, is_builtin, has_instructions, supports_images)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'lab1', 'Лабораторна робота №1',
   'Звіт про виконання лабораторної роботи з алгоритмів сортування',
   1, 1, 1),
  ('00000000-0000-0000-0000-000000000002', 'lab2', 'Лабораторна робота №2',
   'Звіт з бази даних — проектування, нормалізація, SQL',
   1, 1, 1);

-- Default app settings. Overridable by the user via SettingsScreen.
INSERT OR IGNORE INTO app_settings (key, value_json) VALUES
  ('ui.isDarkTheme',          'false'),
  ('ui.isSidebarCollapsed',   'false'),
  ('ui.isBigFont',            'false'),
  ('cache.llm_ttl_days',      '30'),
  ('cache.image_ttl_days',    '60'),
  ('cache.document_ttl_days', '365'),
  ('session.retention_days',  '90'),
  ('pipeline.cancellation_timeout_ms', '5000');
