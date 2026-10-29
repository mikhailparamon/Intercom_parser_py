import logging
import threading
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterator

import requests

logger = logging.getLogger(__name__)


class IntercomAPIError(RuntimeError):
    pass


class IntercomClient:
    BASE_URL = "https://api.intercom.io"

    def __init__(self, token: str, api_version: str, *, session=None, retries: int = 3,
                 timeout: float = 30, sleeper=time.sleep):
        self._token = token
        self.api_version = api_version
        self.session = session
        self._thread_local = threading.local()
        self.retries = retries
        self.timeout = timeout
        self._sleep = sleeper

    def _get_session(self):
        if self.session is not None:
            return self.session
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = requests.Session()
        return self._thread_local.session

    def _request(self, method: str, path: str, *, params=None, json=None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Intercom-Version": self.api_version,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        for attempt in range(self.retries + 1):
            try:
                response = self._get_session().request(
                    method, f"{self.BASE_URL}{path}", headers=headers,
                    params=params, json=json, timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt >= self.retries:
                    raise IntercomAPIError("Сетевая ошибка при обращении к Intercom API") from exc
                self._sleep(min(2 ** attempt, 60))
                continue

            status = response.status_code
            if status in (401, 403):
                raise IntercomAPIError(f"Intercom API отклонил авторизацию (HTTP {status})")
            if status == 429 or 500 <= status <= 599:
                if attempt >= self.retries:
                    raise IntercomAPIError(f"Временная ошибка Intercom API (HTTP {status}), повторы исчерпаны")
                self._sleep(self._retry_delay(response.headers.get("Retry-After"), attempt))
                continue
            if status < 200 or status >= 300:
                raise IntercomAPIError(f"Intercom API вернул HTTP {status}")
            try:
                payload = response.json()
            except (ValueError, requests.JSONDecodeError) as exc:
                raise IntercomAPIError("Intercom API вернул некорректный JSON") from exc
            if not isinstance(payload, dict):
                raise IntercomAPIError("Intercom API вернул неожиданный формат JSON")
            return payload
        raise IntercomAPIError("Не удалось выполнить запрос к Intercom API")

    @staticmethod
    def _retry_delay(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0), 120)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0), 120)
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(2 ** attempt, 60)

    def search_conversations(self, start: date, end: date) -> Iterator[dict[str, Any]]:
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        start_epoch = int(start_dt.timestamp())
        end_epoch = int(end_dt.timestamp())
        created_before_end = {"field": "created_at", "operator": "<", "value": str(end_epoch - 1)}
        payload: dict[str, Any] = {
            "query": {
                "operator": "OR",
                "value": [
                    {
                        "operator": "AND",
                        "value": [
                            {"field": "created_at", "operator": ">", "value": str(start_epoch)},
                            created_before_end,
                        ],
                    },
                    {
                        "operator": "AND",
                        "value": [
                            created_before_end,
                            {"field": "updated_at", "operator": ">", "value": str(start_epoch)},
                        ],
                    },
                ],
            },
            "pagination": {"per_page": 150},
        }
        seen_cursors: set[str] = set()
        while True:
            response = self._request("POST", "/conversations/search", json=payload)
            items = response.get("conversations", [])
            if not isinstance(items, list):
                raise IntercomAPIError("Intercom API вернул некорректный список обращений")
            logger.info("Получено объектов обращений: %d", len(items))
            yield from (item for item in items if isinstance(item, dict))
            pages = response.get("pages") or {}
            next_page = pages.get("next") if isinstance(pages, dict) else None
            cursor = next_page.get("starting_after") if isinstance(next_page, dict) else None
            if not cursor:
                break
            cursor = str(cursor)
            if cursor in seen_cursors:
                raise IntercomAPIError("Обнаружен цикл в курсорной пагинации Intercom API")
            seen_cursors.add(cursor)
            payload["pagination"]["starting_after"] = cursor

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/conversations/{conversation_id}", params={"display_as": "plaintext"})
