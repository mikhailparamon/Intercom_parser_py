"""Versioned, closed dictionaries shared by prompts, validation and exports."""

import re

SCHEMA_VERSION = 2
BILLING = "Оплата и подписка"
AUTH = "Авторизация и аккаунт"
SETUP = "Установка и настройка"
COMPAT = "Совместимость и ограничения"
SERVICE = "Тарифы и вопросы о сервисе"
OTHER = "Прочие обращения"
VPN_CONNECTION = "Проблемы подключения VPN"
HT_TECH = ("Доступ к YouTube", "Приложение", "Расширение браузера")

_COMMON_BILLING = (
    ("subscription_activation", "Подписка не активировалась", "Оплата прошла, но подписка не появилась или не распознаётся.", (BILLING,)),
    ("subscription_cancel", "Отмена подписки", "Отключение подписки или автопродления.", (BILLING,)),
    ("refund", "Возврат денег", "Клиент просит вернуть списанную оплату; отмену не приписывать без запроса.", (BILLING,)),
    ("payment_error", "Ошибка оплаты или списания", "Платёж не проходит, не найден, повторное или неверное списание.", (BILLING,)),
    ("subscription_change", "Продление или изменение подписки", "Продление, смена тарифа или способа оплаты.", (BILLING,)),
)
_ENDING = (
    ("compatibility", "Совместимость и способы подключения", "Поддержка устройств, ОС, протоколов; выбор способа подключения.", (COMPAT, SETUP)),
    ("service_question", "Вопрос о тарифах или сервисе", "Стоимость, условия, возможности, пробный период.", (SERVICE,)),
    ("technical_unspecified", "Техническая проблема не уточнена", "Техническая жалоба есть, но наблюдаемый симптом не установлен.", (VPN_CONNECTION, "Доступ к сайтам и сервисам", "Скорость и качество соединения", "Ключи и конфигурации", SETUP, COMPAT, AUTH)),
    ("no_request", "Нет содержательного запроса", "Приветствие, благодарность, вложение без пояснения или невозможно установить предмет запроса; объяснить в описании.", (OTHER,)),
    ("non_target", "Автоматическое, нецелевое сообщение или спам", "Служебное, рекламное или не относящееся к поддержке сообщение.", (OTHER,)),
)
_VPN = _COMMON_BILLING + (
    ("key_problem", "Проблема с ключом", "Ключ не пришёл, не работает, не скачивается или не импортируется.", ("Ключи и конфигурации",)),
    ("key_reissue", "Повторная выдача ключа", "Просьба прислать ключ повторно или заменить его.", ("Ключи и конфигурации",)),
    ("vpn_connection", "VPN не подключается", "Соединение явно не устанавливается; общий текст «не работает» этого не доказывает.", (VPN_CONNECTION,)),
    ("connection_drops", "Соединение обрывается", "Связь устанавливается, затем прерывается или переподключается.", (VPN_CONNECTION, "Скорость и качество соединения")),
    ("slow_speed", "Низкая скорость", "Медленная работа и задержки.", ("Скорость и качество соединения",)),
    ("site_unavailable", "Сайт или сервис недоступен", "VPN подключён, но конкретный ресурс не работает.", ("Доступ к сайтам и сервисам",)),
    ("login_code", "Код не приходит", "Не получен код авторизации.", (AUTH,)),
    ("login_error", "Ошибка входа или аккаунта", "Остальные ошибки входа или доступа к аккаунту.", (AUTH,)),
    ("installation_error", "Ошибка установки, запуска или обновления", "Приложение не устанавливается, не запускается, зависает или закрывается.", (SETUP,)),
    ("documents_plan", "Ограничение тарифа «Для документов»", "Подтверждено ограничение приложения этим тарифом; одного упоминания тарифа недостаточно.", (COMPAT,)),
) + _ENDING
_HT = _COMMON_BILLING + (
    ("login_code", "Код не приходит", "Не получен код входа.", (AUTH,)),
    ("login_error", "Ошибка входа или аккаунта", "Код отклоняется или истёк, аккаунт недоступен, проблема привязки.", (AUTH,)),
    ("no_internet", "Нет подключения к интернету", "Клиент сообщает именно такую ошибку.", HT_TECH),
    ("youtube_unavailable", "YouTube недоступен", "Не открывается сам YouTube, не установлен более точный симптом.", HT_TECH),
    ("video_not_loading", "Видео не загружается", "YouTube открылся, воспроизведение не начинается.", HT_TECH),
    ("video_buffering", "Буферизация или низкое качество видео", "Видео запускается, но тормозит, зависает или имеет низкое качество.", ("Скорость и качество видео", "Приложение", "Расширение браузера")),
    ("connection_drops", "Соединение обрывается", "Работа начинается, затем связь прерывается.", HT_TECH + ("Скорость и качество видео",)),
    ("installation_error", "Ошибка установки, запуска или обновления", "Не устанавливается, не запускается, зависает или закрывается приложение/расширение.", (SETUP, "Приложение", "Расширение браузера")),
    ("connection_setup", "Настройка подключения", "Выбор сервера, включение, настройка приложения или расширения.", (SETUP,)),
    ("compatibility", "Совместимость и ограничения", "Поддержка устройств, ОС, браузеров и регионов.", (COMPAT,)),
    _ENDING[1],
    ("technical_unspecified", "Техническая проблема не уточнена", "Жалоба есть, но нельзя установить конкретный наблюдаемый симптом.", HT_TECH + (SETUP, COMPAT, AUTH, "Скорость и качество видео")),
    _ENDING[3], _ENDING[4],
)


def get_subcategories(service_id: str = "vpn") -> list[dict]:
    if service_id not in {"vpn", "hypertube"}:
        raise ValueError(f"Неизвестный сервис: {service_id}")
    return [dict(code=code, name=name, description=description, categories=list(categories))
            for code, name, description, categories in (_HT if service_id == "hypertube" else _VPN)]


def canonical_classification(item: dict, service_id: str) -> dict:
    entry = next((entry for entry in get_subcategories(service_id)
                  if entry["code"] == item.get("subcategory_code")), None)
    if entry is None:
        raise ValueError(f"Неизвестный или отсутствующий код подкатегории: {item.get('subcategory_code')!r}")
    if item.get("category") not in entry["categories"]:
        raise ValueError(f"Подкатегория {entry['name']} недопустима в категории {item.get('category')!r}")
    return {**item, "subcategory_code": entry["code"], "subcategory": entry["name"]}


def _customer_text(conversation: dict) -> str:
    texts = [conversation.get("title") or ""]
    for message in conversation.get("messages", []):
        if message.get("role") != "customer":
            continue
        text = message.get("text", "")
        if ("Start welcome message" in text or "какой тип подключения" in text.lower()
                or re.match(r"(?i)^\s*(?:vpn\s*наоборот|hypertube)\s*[–—-]\s*поддержка:", text)):
            continue
        text = re.split(r"(?im)^\s*(?:--\s*$|>|Отправлено (?:из|с)|Sent from|С уважением|On .+wrote:|.*(?:написал\(а\)|пишет):)", text)[0]
        text = re.split(r"(?i)\b(?:пн|вт|ср|чт|пт|сб|вс),?\s+\d{1,2}\s+\S+\s+\d{4}.*(?::|>)", text)[0]
        texts.append(text)
    return "\n".join(texts)


def confirmed_platforms(conversation: dict, service_id: str) -> list[str]:
    """Recover explicit customer platforms; never count operator menus or signatures."""
    text = _customer_text(conversation).casefold()
    rules = (
        (r"\bopen\s*vpn\b", "OpenVPN"),
        (r"\btunnelblick\b", "Tunnelblick"),
        (r"\b(?:wireguard|softether|clash)\b", "Другой клиент"),
        (r"приложени\w*.{0,35}(?:vpn\s*наоборот|vpnnaoborot)", "Приложение VPN Наоборот"),
        (r"приложени\w*.{0,35}(?:hypertube|гипертуб)", "Приложение HyperTube"),
        (r"расширени\w*|\bextension\b", "Расширение браузера"),
        (r"\b(?:windows|win\s*(?:10|11))\b|виндовс", "Windows"),
        (r"\b(?:macos|mac\s*os|macbook|imac)\b|макбук", "macOS"),
        (r"\blinux\b|линукс", "Linux"),
        (r"\bandroid\b|андроид", "Android"),
        (r"\b(?:ios|ipados|iphone|ipad)\b|айфон|айпад", "iOS/iPadOS"),
        (r"телевизор|приставк|\b(?:smart\s*tv|android\s*tv|google\s*tv|тв)\b", "TV/приставка"),
        (r"роутер|маршрутизатор|\brouter\b", "Роутер"),
        (r"\bchrome\b|хром", "Chrome"),
        (r"яндекс(?:[ .-]*браузер)|\byandex(?:[ .-]*browser)?\b", "Яндекс"),
        (r"\bedge\b", "Edge"), (r"\bopera\b|опер[аы]", "Opera"),
        (r"\bfirefox\b", "Firefox"), (r"\bsafari\b", "Safari"),
    )
    result = [name for pattern, name in rules if re.search(pattern, text)]
    if "Яндекс" not in result and re.search(r"яндекс", text) and re.search(r"расширени|браузер", text):
        result.append("Яндекс")
    if re.search(r"компьютер|ноутбук|\b(?:пк|pc|desktop|laptop)\b", text) and not set(result) & {"Windows", "macOS", "Linux"}:
        result.append("Компьютер (ОС не указана)")
    return result


def catalog_prompt(service_id: str = "vpn") -> str:
    lines = ["## Обязательный закрытый справочник подкатегорий (схема 2)",
             "Новые подкатегории запрещены. Для каждого ID верни subcategory_code из таблицы; subcategory — точное название. Подробности сохраняй в problem_summary, вторую проблему в additional_topics.",
             "При нескольких проблемах основная — актуальный запрос клиента. Не угадывай техническую причину.",
        "Содержательный title учитывай как текст клиента: «Как поменять карту для оплаты?» → subscription_change; «Не получается войти» → login_error.",
             "| Код | Подкатегория | Допустимые категории | Условие |", "|---|---|---|---|"]
    for item in get_subcategories(service_id):
        lines.append(f"| {item['code']} | {item['name']} | {', '.join(item['categories'])} | {item['description']} |")
    lines.extend([
        "Для итогового JSON обязательно classification_schema_version: 2 и service_id: " + service_id + ".",
        "platforms — единый список способов подключения, приложений, ОС, устройств и браузеров. Одно обращение может содержать несколько платформ; одинаковые значения не дублируй.",
        "Используй OpenVPN (включая OpenVPN Connect), Tunnelblick, Другой клиент, Приложение VPN Наоборот, Приложение HyperTube, Расширение браузера, Windows, macOS, Linux, Android, iOS/iPadOS, TV/приставка, Роутер, Компьютер (ОС не указана), Chrome, Яндекс, Edge, Opera, Firefox, Safari.",
        "Подтверждай платформы собственными словами клиента. Не используй типовой перечень оператора, цитаты старых писем, App Store/Google Play, почтовую подпись или страну. Не угадывай ОС/браузер. Если нет подтверждения, верни [].",
        "Не оптимизируй процент за счёт догадок. Если содержательного клиентского текста нет, выбирай no_request или non_target с объяснением; `evidence` = `""` допустимо при отсутствии цитаты.",
    ])
    return "\n".join(lines)
