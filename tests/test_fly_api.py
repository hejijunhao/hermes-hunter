"""Tests for hunter.backends.fly_api — Fly Machines API client."""

import pytest
from unittest.mock import MagicMock, call, patch

from hunter.backends.fly_api import FlyAPIError, FlyMachinesClient


@pytest.fixture
def mock_httpx_client():
    """Patch httpx.Client so no real HTTP calls are made."""
    with patch("hunter.backends.fly_api.httpx.Client") as MockClient:
        instance = MagicMock()
        MockClient.return_value = instance
        yield instance


@pytest.fixture
def client(mock_httpx_client):
    """A FlyMachinesClient with a mocked httpx.Client."""
    return FlyMachinesClient(app_name="test-app", api_token="test-token")


class TestFlyMachinesClientInit:

    def test_creates_httpx_client_with_correct_headers(self):
        with patch("hunter.backends.fly_api.httpx.Client") as MockClient:
            FlyMachinesClient(app_name="my-app", api_token="tok123")
            MockClient.assert_called_once()
            call_kwargs = MockClient.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer tok123"
            assert call_kwargs["headers"]["User-Agent"] == "hermes-prime/1.0"
            assert call_kwargs["base_url"] == FlyMachinesClient.BASE_URL

    def test_context_manager(self):
        with patch("hunter.backends.fly_api.httpx.Client") as MockClient:
            instance = MagicMock()
            MockClient.return_value = instance

            with FlyMachinesClient(app_name="app", api_token="tok") as c:
                assert c is not None
            instance.close.assert_called_once()

    def test_close_delegates(self, client, mock_httpx_client):
        client.close()
        mock_httpx_client.close.assert_called_once()


class TestCreateMachine:

    def test_posts_to_correct_endpoint(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b'{"id": "m123"}'
        response.json.return_value = {"id": "m123"}
        mock_httpx_client.request.return_value = response

        config = {"config": {"image": "alpine:latest"}}
        result = client.create_machine(config)

        mock_httpx_client.request.assert_called_once_with(
            "POST", "/apps/test-app/machines", json=config,
        )
        assert result == {"id": "m123"}


class TestStopMachine:

    def test_posts_stop_with_timeout(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b""
        mock_httpx_client.request.return_value = response

        client.stop_machine("m123", timeout=15)

        mock_httpx_client.request.assert_called_once_with(
            "POST", "/apps/test-app/machines/m123/stop",
            json={"timeout": 15},
        )


class TestDestroyMachine:

    def test_deletes_machine(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b""
        mock_httpx_client.request.return_value = response

        client.destroy_machine("m123")

        mock_httpx_client.request.assert_called_once_with(
            "DELETE", "/apps/test-app/machines/m123", params={},
        )

    def test_force_destroy(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b""
        mock_httpx_client.request.return_value = response

        client.destroy_machine("m123", force=True)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["params"] == {"force": "true"}


class TestWaitForState:

    def test_waits_with_correct_params(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b'{"id": "m123", "state": "started"}'
        response.json.return_value = {"id": "m123", "state": "started"}
        mock_httpx_client.request.return_value = response

        result = client.wait_for_state("m123", "started", timeout=30)

        call_args = mock_httpx_client.request.call_args
        assert call_args[0] == ("GET", "/apps/test-app/machines/m123/wait")
        assert call_args[1]["params"]["state"] == "started"
        assert call_args[1]["params"]["timeout"] == "30"
        assert result["state"] == "started"


class TestGetMachine:

    def test_gets_machine_status(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b'{"id": "m123", "state": "started"}'
        response.json.return_value = {"id": "m123", "state": "started"}
        mock_httpx_client.request.return_value = response

        result = client.get_machine("m123")
        assert result["state"] == "started"


class TestListMachines:

    def test_lists_machines(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b'[{"id": "m1"}, {"id": "m2"}]'
        response.json.return_value = [{"id": "m1"}, {"id": "m2"}]
        mock_httpx_client.request.return_value = response

        result = client.list_machines()
        assert len(result) == 2


class TestErrorHandling:

    def test_raises_on_4xx(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 404
        response.text = "Not Found"
        mock_httpx_client.request.return_value = response

        with pytest.raises(FlyAPIError) as exc_info:
            client.get_machine("nonexistent")
        assert exc_info.value.status_code == 404

    def test_raises_on_5xx(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 500
        response.text = "Internal Server Error"
        mock_httpx_client.request.return_value = response

        with pytest.raises(FlyAPIError) as exc_info:
            client.list_machines()
        assert exc_info.value.status_code == 500

    def test_timeout_raises_fly_api_error(self, client, mock_httpx_client):
        import httpx
        mock_httpx_client.request.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(FlyAPIError) as exc_info:
            client.get_machine("m123")
        assert exc_info.value.status_code == 0
        assert "timed out" in str(exc_info.value)


class TestStartMachine:

    def test_starts_machine(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b""
        mock_httpx_client.request.return_value = response

        client.start_machine("m123")

        mock_httpx_client.request.assert_called_once_with(
            "POST", "/apps/test-app/machines/m123/start",
        )


class TestGetLogs:

    def test_returns_log_list(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 200
        response.content = b'[{"message": "hello"}]'
        response.json.return_value = [{"message": "hello"}]
        mock_httpx_client.request.return_value = response

        logs = client.get_logs("m123", tail=50)
        assert len(logs) == 1
        assert logs[0]["message"] == "hello"

    def test_returns_empty_on_error(self, client, mock_httpx_client):
        response = MagicMock()
        response.status_code = 404
        response.text = "not found"
        mock_httpx_client.request.return_value = response

        logs = client.get_logs("m123")
        assert logs == []


class TestRetryLogic:

    @patch("hunter.backends.fly_api.time.sleep")
    def test_retries_on_5xx(self, mock_sleep, client, mock_httpx_client):
        fail = MagicMock()
        fail.status_code = 503
        fail.text = "Service Unavailable"

        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'[{"id": "m1"}]'
        ok.json.return_value = [{"id": "m1"}]

        mock_httpx_client.request.side_effect = [fail, ok]
        result = client.list_machines()

        assert result == [{"id": "m1"}]
        assert mock_httpx_client.request.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^0 = 1s

    @patch("hunter.backends.fly_api.time.sleep")
    def test_retries_on_timeout(self, mock_sleep, client, mock_httpx_client):
        import httpx
        mock_httpx_client.request.side_effect = [
            httpx.TimeoutException("timed out"),
            httpx.TimeoutException("timed out again"),
            httpx.TimeoutException("timed out a third time"),
            httpx.TimeoutException("timed out final"),
        ]

        with pytest.raises(FlyAPIError) as exc_info:
            client.get_machine("m123")
        assert exc_info.value.status_code == 0
        # 1 original + 3 retries = 4 total attempts
        assert mock_httpx_client.request.call_count == 4
        assert mock_sleep.call_count == 3

    @patch("hunter.backends.fly_api.time.sleep")
    def test_no_retry_on_4xx(self, mock_sleep, client, mock_httpx_client):
        fail = MagicMock()
        fail.status_code = 404
        fail.text = "Not Found"
        mock_httpx_client.request.return_value = fail

        with pytest.raises(FlyAPIError) as exc_info:
            client.get_machine("m123")
        assert exc_info.value.status_code == 404
        assert mock_httpx_client.request.call_count == 1
        mock_sleep.assert_not_called()

    @patch("hunter.backends.fly_api.time.sleep")
    def test_retries_on_429(self, mock_sleep, client, mock_httpx_client):
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.text = "Too Many Requests"

        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{"id": "m1"}'
        ok.json.return_value = {"id": "m1"}

        mock_httpx_client.request.side_effect = [rate_limited, ok]
        result = client.get_machine("m1")
        assert result == {"id": "m1"}
        assert mock_httpx_client.request.call_count == 2

    @patch("hunter.backends.fly_api.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep, client, mock_httpx_client):
        import httpx
        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{}'
        ok.json.return_value = {}

        mock_httpx_client.request.side_effect = [
            httpx.TimeoutException("1"),
            httpx.TimeoutException("2"),
            ok,
        ]
        client.get_machine("m1")

        # Backoff: 2^0=1, 2^1=2
        assert mock_sleep.call_args_list == [call(1), call(2)]
