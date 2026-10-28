# Intercom Conversation Exporter

CLI-утилита для безопасной выгрузки обращений и доступной истории переписки из Intercom API. В первой версии поддерживается только экспорт данных.

## Требования

- Python 3.11 или новее
- Git

## Быстрый старт (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Затем заполните `INTERCOM_ACCESS_TOKEN` в `.env`. Не добавляйте этот файл в Git и не отправляйте его другим людям.

## Запуск

Инструкция будет дополнена после реализации CLI.
