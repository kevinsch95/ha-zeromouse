"""Live event subscription via AWS AppSync real-time (GraphQL over websocket)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
import json
import logging
import time
from typing import Any
import uuid

import aiohttp

from .api import ZeroMouseError
from .const import GRAPHQL_URL

_LOGGER = logging.getLogger(__name__)

API_HOST = GRAPHQL_URL.split("/")[2]
REALTIME_URL = (
    "wss://" + API_HOST.replace("appsync-api", "appsync-realtime-api") + "/graphql"
)

# Same selection set as the app's OnCreateMbrPtfEventData subscription.
SUBSCRIPTION_QUERY = """
subscription OnCreateMbrPtfEventData(
  $filter: ModelSubscriptionMbrPtfEventDataFilterInput
) {
  onCreateMbrPtfEventData(filter: $filter) {
    eventID
    eventTime
    type
    deviceID
    classification_byNet
    ownerID
    isSeen
    isFlagged
    isHidden
    isDeleted
    isFavorite
    catClusterId
    titleImageIndex
    createdAt
    updatedAt
  }
}
"""

ACK_TIMEOUT = 15  # seconds
DEFAULT_KEEPALIVE_TIMEOUT = 300  # seconds, AppSync's default
# Reconnect well before the Cognito access token (1 h) expires.
MAX_CONNECTION_AGE = 45 * 60  # seconds
BACKOFF_MIN = 5
BACKOFF_MAX = 300


class RealtimeProtocolError(Exception):
    """Unexpected message or error from AppSync."""


def _encode(obj: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


class ZeroMouseRealtime:
    """Keeps an AppSync subscription open and forwards new events."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[[bool], Awaitable[str]],
        on_event: Callable[[dict[str, Any]], None],
        on_connection_change: Callable[[bool], None],
    ) -> None:
        """Initialize.

        token_provider(force_refresh) returns a Cognito access token.
        """
        self._session = session
        self._token_provider = token_provider
        self._on_event = on_event
        self._on_connection_change = on_connection_change
        self.connected = False

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            self._on_connection_change(connected)

    async def async_run(self) -> None:
        """Run forever (until cancelled), reconnecting as needed."""
        backoff = BACKOFF_MIN
        force_refresh = False
        while True:
            started = time.monotonic()
            try:
                token = await self._token_provider(force_refresh)
                force_refresh = False
                await self._async_session(token)
                backoff = BACKOFF_MIN
                continue  # planned renewal: reconnect right away
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except RealtimeProtocolError as err:
                _LOGGER.debug("Live connection error: %s", err)
                force_refresh = "nauthorized" in str(err)
            except (aiohttp.ClientError, TimeoutError, ZeroMouseError) as err:
                _LOGGER.debug("Live connection failed: %s", err)
            except Exception:
                _LOGGER.exception("Unexpected error in ZeroMOUSE live connection")
            self._set_connected(False)

            # A connection that lived a while was healthy: reconnect quickly.
            if time.monotonic() - started > 60:
                backoff = BACKOFF_MIN
            _LOGGER.debug("Reconnecting live connection in %s s", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _async_session(self, token: str) -> None:
        """Open one websocket connection and process messages until it ends."""
        auth = {"host": API_HOST, "Authorization": token}
        url = f"{REALTIME_URL}?header={_encode(auth)}&payload=e30="
        async with self._session.ws_connect(
            url, protocols=("graphql-ws",), heartbeat=None
        ) as ws:
            await ws.send_json({"type": "connection_init"})
            ack = await self._receive(ws, ACK_TIMEOUT)
            if ack.get("type") != "connection_ack":
                raise RealtimeProtocolError(f"No connection_ack: {ack}")
            keepalive = (ack.get("payload") or {}).get(
                "connectionTimeoutMs", 0
            ) / 1000 or DEFAULT_KEEPALIVE_TIMEOUT

            sub_id = str(uuid.uuid4())
            await ws.send_json(
                {
                    "id": sub_id,
                    "type": "start",
                    "payload": {
                        "data": json.dumps(
                            {"query": SUBSCRIPTION_QUERY, "variables": {}}
                        ),
                        "extensions": {"authorization": auth},
                    },
                }
            )

            deadline = time.monotonic() + MAX_CONNECTION_AGE
            while time.monotonic() < deadline:
                timeout = min(keepalive, max(deadline - time.monotonic(), 1))
                try:
                    msg = await self._receive(ws, timeout)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        break
                    raise
                self._handle_message(msg, sub_id)

            _LOGGER.debug("Renewing live connection before token expiry")
            await ws.send_json({"id": sub_id, "type": "stop"})
            # Stay "connected" across the renewal to avoid a catch-up poll.

    async def _receive(
        self, ws: aiohttp.ClientWebSocketResponse, timeout: float
    ) -> dict[str, Any]:
        msg = await ws.receive(timeout=timeout)
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        ):
            raise aiohttp.ClientError(f"Websocket closed ({ws.close_code})")
        if msg.type is aiohttp.WSMsgType.ERROR:
            raise aiohttp.ClientError(f"Websocket error: {ws.exception()}")
        if msg.type is not aiohttp.WSMsgType.TEXT:
            return {}
        try:
            data = json.loads(msg.data)
        except ValueError as err:
            raise RealtimeProtocolError(f"Invalid JSON: {msg.data!r}") from err
        return data if isinstance(data, dict) else {}

    def _handle_message(self, msg: dict[str, Any], sub_id: str) -> None:
        msg_type = msg.get("type")
        if msg_type == "ka" or not msg_type:
            return
        if msg_type == "start_ack":
            _LOGGER.debug("Live subscription active")
            self._set_connected(True)
            return
        if msg_type == "data" and msg.get("id") == sub_id:
            data = (msg.get("payload") or {}).get("data") or {}
            if item := data.get("onCreateMbrPtfEventData"):
                self._on_event(item)
            return
        if msg_type in ("error", "connection_error", "complete"):
            raise RealtimeProtocolError(json.dumps(msg.get("payload") or msg))
        _LOGGER.debug("Ignoring live message: %s", msg)
