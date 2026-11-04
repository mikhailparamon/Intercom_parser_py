# AGENTS.md

Инструкции для AI-агентов, работающих с этим репозиторием.

## О проекте

CLI-приложение на Python для выгрузки обращений Intercom с историей переписки в форматированный JSON и подготовки отчёта по поддержке вручную через ChatGPT или оркестрацией субагентов.

- Язык интерфейса и документации: русский.
- Платформа разработки: Windows (Git Bash / PowerShell). Пути в командах — Windows-стиль.

## Стек

- Python 3.11+
- `requests` — HTTP к Intercom API
- `python-dotenv` — конфигурация через `.env`
- `tzdata` — часовые пояса
- `pytest` — тесты
- Виртуальное окружение: `.venv/` (использовать `.venv/Scripts/python.exe` на Windows)

## Структура

```
main.py                      # точка входа, вызывает cli.main()
src/intercom_exporter/
  cli.py                     # CLI-аргументы и оркестрация
  client.py                  # клиент Intercom API
  config.py                  # загрузка конфигурации из .env
  dates.py                   # работа с датами/периодами
  exporter.py                # выгрузка обращений в JSON
  monitoring.py              # мониторинг
  notify_slack.py            # отправка дашборда в Slack
  storage.py                 # сохранение результатов
  support_analysis.py        # отчёт по поддержке
  support_analysis_prompt_template.md  # шаблон промпта отчёта
tests/                       # тесты pytest (test_*.py, по модулю на файл)
data/                        # результаты выгрузки (в .gitignore, не коммитить)
docs/                        # документация
```

## Команды

Все команды — из корня проекта, с активированным `.venv` (или явным `.venv/Scripts/python.exe`):

```bash
# Установка зависимостей
python -m pip install -r requirements.txt

# Запуск CLI
python main.py --help

# Все тесты
python -m pytest

# Один тестовый файл / один тест
python -m pytest tests/test_config_cli.py
python -m pytest tests/test_config_cli.py -k имя_теста
```

`pytest.ini` уже задаёт `pythonpath = src` — импорты вида `from intercom_exporter...` работают в тестах без установки пакета.

## Конфигурация и секреты

- Конфигурация — через `.env` (шаблон: `.env.example`). Переменные: `INTERCOM_ACCESS_TOKEN`, `INTERCOM_API_VERSION`, `AGENT_WORKERS`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`.
- **Никогда не читать и не выводить `.env`.** Только `.env.example`.
- `data/` содержит реальные выгрузки переписок — не коммитить, не отправлять наружу.

## Правила работы с кодом

- Поведенческие правила — в `CLAUDE.md` (принципы Karpathy: думай до кода, простота, хирургические правки, проверяемые цели). Соблюдай их.
- Минимальные точечные изменения: не рефактори соседний код, не меняй чужие комментарии, держи существующий стиль.
- Новая функциональность — с тестами в `tests/` по образцу существующих файлов.
- Перед завершением задачи прогони `python -m pytest` и убедись, что тесты зелёные.
- Не коммить без явной просьбы пользователя.

## Окружение агента (локально, в .gitignore)

- `.kimi/skills/` — скиллы Superpowers и Caveman для этого проекта.
- `.mcp.json` — MCP-сервер context7 (актуальная документация библиотек).
- Эти файлы локальные, в репозиторий не попадают.

## Оркестрация субагентов

- Число субагентов по умолчанию: переменная `AGENT_WORKERS` в `.env` (2–20; без неё — 10), переопределяется флагами `--agent-workers` (main.py) и `--workers` (prepare-monitoring, research).
- Если среда не умеет порождать субагентов, классифицируйте чанки по одному: прочитайте `agents_work/chunk_XX.json`, классифицируйте по `PROMPT.md`, запишите `agents_work/result_XX.json` (формат — `agents_work/agents_protocol.md`), забудьте контекст чанка и переходите к следующему; затем `merge-agents`. Не держите в контексте больше одного чанка.
