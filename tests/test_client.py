from datetime import date

import pytest
import requests

from intercom_exporter.client import IntercomAPIError, IntercomClient


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, invalid_json=False):
        self.status_code = status
        self.payload = payload or {}
        self.headers = headers or {}
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("bad json")
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_request_adds_auth_and_version_headers_without_logging_token():
    session = FakeSession([FakeResponse(payload={"ok": True})])
    client = IntercomClient("fake-secret", "2.17", session=session, sleeper=lambda _: None)
    assert client._request("GET", "/test") == {"ok": True}
    headers = session.calls[0][2]["headers"]
    assert headers["Authorization"] == "Bearer fake-secret"
    assert headers["Intercom-Version"] == "2.17"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_not_retried_or_leaked(status):
    session = FakeSession([FakeResponse(status=status, payload={"message": "fake-secret"})])
    client = IntercomClient("fake-secret", "2.17", session=session, sleeper=lambda _: None)
    with pytest.raises(IntercomAPIError) as error:
        client._request("GET", "/test")
    assert len(session.calls) == 1
    assert "fake-secret" not in str(error.value)


def test_rate_limit_retries_and_honors_retry_after():
    delays = []
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(payload={"ok": 1})])
    client = IntercomClient("t", "2.17", session=session, sleeper=delays.append)
    assert client._request("GET", "/test") == {"ok": 1}
    assert delays == [3]


def test_server_error_is_retried_a_limited_number_of_times():
    session = FakeSession([FakeResponse(500), FakeResponse(500), FakeResponse(500)])
    client = IntercomClient("t", "2.17", session=session, retries=2, sleeper=lambda _: None)
    with pytest.raises(IntercomAPIError):
        client._request("GET", "/test")
    assert len(session.calls) == 3


def test_network_timeout_is_retried():
    session = FakeSession([requests.Timeout(), FakeResponse(payload={"ok": True})])
    client = IntercomClient("t", "2.17", session=session, sleeper=lambda _: None)
    assert client._request("GET", "/test")["ok"] is True
    assert len(session.calls) == 2


def test_invalid_json_raises_safe_api_error():
    client = IntercomClient("t", "2.17", session=FakeSession([FakeResponse(invalid_json=True)]))
    with pytest.raises(IntercomAPIError, match="JSON"):
        client._request("GET", "/test")


def test_search_paginates_and_uses_creation_bounds():
    session = FakeSession([
        FakeResponse(payload={"conversations": [{"id": "1"}], "pages": {"next": {"starting_after": "cursor1"}}}),
        FakeResponse(payload={"conversations": [{"id": "2"}], "pages": {}}),
    ])
    client = IntercomClient("t", "2.17", session=session, sleeper=lambda _: None)
    items = list(client.search_conversations(date(2026, 7, 1), date(2026, 10, 1)))
    assert [item["id"] for item in items] == ["1", "2"]
    query = session.calls[0][2]["json"]["query"]
    assert query["operator"] == "OR"
    creation_filters = query["value"][0]["value"]
    assert creation_filters[0] == {"field": "created_at", "operator": ">", "value": str(1782864000)}
    assert creation_filters[1] == {"field": "created_at", "operator": "<", "value": str(1790812799)}
    updated_filters = query["value"][1]["value"]
    assert updated_filters[0] == {"field": "created_at", "operator": "<", "value": str(1790812799)}
    assert updated_filters[1] == {"field": "updated_at", "operator": ">", "value": str(1782864000)}
    assert session.calls[1][2]["json"]["pagination"]["starting_after"] == "cursor1"


def test_repeated_pagination_cursor_is_rejected():
    payload = {"conversations": [{"id": "1"}], "pages": {"next": {"starting_after": "same"}}}
    client = IntercomClient("t", "2.17", session=FakeSession([FakeResponse(payload=payload), FakeResponse(payload=payload)]))
    with pytest.raises(IntercomAPIError, match="пагинации"):
        list(client.search_conversations(date(2026, 1, 1), date(2026, 2, 1)))


def test_retrieve_conversation_returns_full_api_response():
    payload = {"id": "42", "conversation_parts": {"conversation_parts": [{"id": "part1"}]}}
    client = IntercomClient("t", "2.17", session=FakeSession([FakeResponse(payload=payload)]))
    assert client.get_conversation("42") == payload



def test_retrieve_requests_plaintext_message_bodies():
    payload = {"id": "42"}
    session = FakeSession([FakeResponse(payload=payload)])
    client = IntercomClient("t", "2.17", session=session)
    client.get_conversation("42")
    assert session.calls[0][2]["params"] == {"display_as": "plaintext"}
