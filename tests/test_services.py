from intercom_exporter.services import DEFAULT_SERVICE_ID, SERVICES, get_service, iter_services


def test_default_service_is_vpn():
    assert DEFAULT_SERVICE_ID == "vpn"
    assert get_service().id == "vpn"


def test_get_service_by_id():
    assert get_service("vpn").label == "VPNНаоборот"
    assert get_service("hypertube").label == "HyperTube"


def test_unknown_service_raises():
    try:
        get_service("unknown")
    except ValueError as exc:
        assert "Неизвестный сервис" in str(exc)
    else:
        raise AssertionError("Ожидалось ValueError")


def test_iter_services_order():
    assert [s.id for s in iter_services()] == ["vpn", "hypertube"]


def test_services_have_token_env():
    assert SERVICES["vpn"].token_env == "INTERCOM_ACCESS_TOKEN"
    assert SERVICES["hypertube"].token_env == "HYPERTUBE_INTERCOM_ACCESS_TOKEN"
