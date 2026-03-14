"""Thin, typed wrapper around the Fly.io Machines REST API.

Uses raw ``httpx`` calls — no external SDK. All methods are synchronous
(the Overseer loop is synchronous).

API docs: https://docs.machines.dev

Only the subset needed for Hunter machine lifecycle is implemented:
create, start, stop, destroy, wait, get, list, and logs.
"""

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Status codes worth retrying (transient server errors).
_RETRYABLE_STATUS_CODES = {502, 503, 504, 429}


class FlyAPIError(Exception):
    """Raised when the Fly Machines API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Fly API error {status_code}: {message}")


class FlyMachinesClient:
    """Thin client for the Fly.io Machines REST API.

    Base URL: https://api.machines.dev/v1
    Auth: Bearer token via Authorization header.
    """

    BASE_URL = "https://api.machines.dev/v1"

    def __init__(self, app_name: str, api_token: str):
        self.app_name = app_name
        self.api_token = api_token
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-prime/1.0",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "FlyMachinesClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Machine lifecycle ------------------------------------------------

    def create_machine(self, config: dict) -> dict:
        """Create and start a machine.

        Args:
            config: Fly machine config dict (image, env, guest, etc.).

        Returns:
            Machine dict with ``id``, ``state``, ``config``, etc.
        """
        return self._request("POST", f"/apps/{self.app_name}/machines", json=config)

    def start_machine(self, machine_id: str) -> None:
        """Start an existing stopped machine."""
        self._request("POST", f"/apps/{self.app_name}/machines/{machine_id}/start")

    def stop_machine(self, machine_id: str, timeout: int = 30) -> None:
        """Stop a running machine.

        Args:
            machine_id: The machine ID.
            timeout: Seconds to wait for graceful stop before force-killing.
        """
        self._request(
            "POST",
            f"/apps/{self.app_name}/machines/{machine_id}/stop",
            json={"timeout": timeout},
        )

    def destroy_machine(self, machine_id: str, force: bool = False) -> None:
        """Permanently remove a machine.

        Args:
            machine_id: The machine ID.
            force: If True, force destroy even if running.
        """
        params = {"force": "true"} if force else {}
        self._request(
            "DELETE",
            f"/apps/{self.app_name}/machines/{machine_id}",
            params=params,
        )

    def wait_for_state(
        self, machine_id: str, state: str, timeout: int = 60
    ) -> dict:
        """Block until a machine reaches the target state.

        Args:
            machine_id: The machine ID.
            state: Target state (e.g. ``"started"``, ``"stopped"``).
            timeout: Maximum seconds to wait.

        Returns:
            Machine dict at the target state.
        """
        return self._request(
            "GET",
            f"/apps/{self.app_name}/machines/{machine_id}/wait",
            params={"state": state, "timeout": str(timeout)},
            request_timeout=timeout + 10,  # HTTP timeout > API timeout
        )

    # -- Status -----------------------------------------------------------

    def get_machine(self, machine_id: str) -> dict:
        """Get full machine state including status, config, events."""
        return self._request(
            "GET", f"/apps/{self.app_name}/machines/{machine_id}",
            request_timeout=10.0,
        )

    def list_machines(self) -> list[dict]:
        """List all machines for the app."""
        return self._request(
            "GET", f"/apps/{self.app_name}/machines",
            request_timeout=10.0,
        )

    # -- Logs -------------------------------------------------------------

    def get_logs(
        self, machine_id: str, tail: int = 100, nats_url: Optional[str] = None,
    ) -> list[dict]:
        """Fetch recent log entries for a machine.

        Uses the Fly Logs API (``/apps/{app}/machines/{id}/logs``).
        Each entry has ``message`` and ``timestamp`` keys.

        Args:
            machine_id: The machine ID.
            tail: Number of recent entries to return.
            nats_url: Optional override for the Fly Nats log endpoint.

        Returns:
            List of log entry dicts.
        """
        # Fly's log endpoint path — may vary; this matches the documented pattern.
        try:
            result = self._request(
                "GET",
                f"/apps/{self.app_name}/machines/{machine_id}/logs",
                params={"tail": str(tail)},
                request_timeout=10.0,
            )
            # Fly returns either a list or an object with a "data" key.
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "data" in result:
                return result["data"]
            return []
        except FlyAPIError:
            # Log endpoint may not be available for all machine states.
            logger.debug("Could not fetch logs for machine %s", machine_id)
            return []

    # -- Internal ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json: dict = None,
        params: dict = None,
        request_timeout: float = None,
        retries: int = 3,
    ) -> dict | list:
        """Execute an HTTP request against the Fly Machines API.

        Retries up to *retries* times on transient errors (5xx, 429, timeouts)
        with exponential backoff (1s, 2s, 4s).

        Args:
            method: HTTP method (GET, POST, DELETE).
            path: URL path (appended to base URL).
            json: Request body (for POST).
            params: Query parameters.
            request_timeout: Override default timeout for this request.
            retries: Max retry attempts for transient errors. 0 to disable.

        Returns:
            Parsed JSON response.

        Raises:
            FlyAPIError: On non-2xx responses after exhausting retries.
        """
        logger.debug("Fly API: %s %s", method, path)

        kwargs: dict = {}
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = params
        if request_timeout is not None:
            kwargs["timeout"] = request_timeout

        last_error: Optional[FlyAPIError] = None
        for attempt in range(1 + retries):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                last_error = FlyAPIError(0, f"Request timed out: {exc}")
                if attempt < retries:
                    self._backoff(attempt, last_error)
                    continue
                raise last_error
            except httpx.HTTPError as exc:
                last_error = FlyAPIError(0, f"HTTP error: {exc}")
                if attempt < retries:
                    self._backoff(attempt, last_error)
                    continue
                raise last_error

            if response.status_code < 200 or response.status_code >= 300:
                body = response.text[:500]
                last_error = FlyAPIError(
                    status_code=response.status_code,
                    message=f"{method} {path} failed",
                    response_body=body,
                )
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < retries
                ):
                    self._backoff(attempt, last_error)
                    continue
                raise last_error

            # Some endpoints return 200 with no body (e.g. start, stop).
            if not response.content:
                return {}

            return response.json()

        # Should be unreachable, but satisfy the type checker.
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _backoff(attempt: int, error: FlyAPIError) -> None:
        """Sleep with exponential backoff: 1s, 2s, 4s, ..."""
        delay = 2**attempt
        logger.warning(
            "Fly API transient error (attempt %d), retrying in %ds: %s",
            attempt + 1, delay, error,
        )
        time.sleep(delay)

    def __repr__(self) -> str:
        return f"FlyMachinesClient(app={self.app_name!r})"
