"""Client for the ZeroMOUSE cloud backend (AWS Cognito, AppSync, API Gateway, S3)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any, TypeVar

import aiohttp
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import jwt
from pycognito import Cognito
from pycognito.exceptions import WarrantException

from .const import (
    APP_VERSION,
    EVENT_FETCH_LIMIT,
    GRAPHQL_URL,
    IDENTITY_POOL_ID,
    IMAGE_BUCKET,
    REGION,
    REST_BASE_URL,
    USER_POOL_CLIENT_ID,
    USER_POOL_ID,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
TOKEN_REFRESH_MARGIN = 120  # seconds
AUTH_ERROR_CODES = {
    "NotAuthorizedException",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "PasswordResetRequiredException",
}

FLAP_FIELDS = """
    deviceID
    name
    model
    networkName
    ownerID
    verHardware
    verMajor
    verMinor
    verRevision
    bootCount
    eventCount
    ai_score
    userFeedback_score
    catClusters {
      id
      name
      allowEntry
    }
    blockUnknownCats
    createdAt
    updatedAt
"""

LIST_FLAPS_QUERY = f"""
  query listFlapByOwnerID($ownerID: String!, $limit: Int, $nextToken: String) {{
    listFlapByOwnerID(ownerID: $ownerID, limit: $limit, nextToken: $nextToken) {{
      items {{
        {FLAP_FIELDS}
      }}
      nextToken
    }}
  }}
"""

LIST_EVENTS_QUERY = """
  query listEventbyDeviceChrono(
    $deviceID: String!
    $sortDirection: ModelSortDirection
    $limit: Int
    $nextToken: String
  ) {
    listEventbyDeviceChrono(
      deviceID: $deviceID
      sortDirection: $sortDirection
      limit: $limit
      nextToken: $nextToken
    ) {
      items {
        eventID
        eventTime
        type
        deviceID
        ownerID
        isSeen
        isFlagged
        titleImageIndex
        isHidden
        isDeleted
        isFavorite
        classification_byNet
        catClusterId
        catClusterId_byUser
        createdAt
        updatedAt
        images {
          classification_byNet
          imageIndex
        }
      }
      nextToken
    }
  }
"""


class ZeroMouseError(Exception):
    """Base error for the ZeroMOUSE client."""


class ZeroMouseAuthError(ZeroMouseError):
    """Raised when the credentials are rejected."""


class ZeroMouseApiError(ZeroMouseError):
    """Raised when the backend cannot be reached or returns an error."""


def image_key(identity_id: str, device_id: str, event_id: str, index: int) -> str:
    """Return the S3 key for an event image.

    The app stores images with Amplify "private" access level, which prefixes
    the key with the Cognito identity id of the owner.
    """
    return f"private/{identity_id}/devices/{device_id}/events/{event_id}/images/{index}.jpg"


class ZeroMouseApi:
    """Async client for the ZeroMOUSE cloud."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._email = email
        self._password = password
        self._cognito: Cognito | None = None
        self._auth_lock = asyncio.Lock()
        self._aws_lock = asyncio.Lock()
        self._identity_client: Any = None
        self._s3_client: Any = None
        self._identity_id: str | None = None
        self._aws_expiration: datetime | None = None
        self.user_id: str | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _run(func: Callable[..., _T], *args: Any) -> _T:
        """Run blocking boto3/pycognito code in the executor."""
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)

    @staticmethod
    def _token_expires_soon(token: str | None) -> bool:
        if not token:
            return True
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return True
        return claims.get("exp", 0) - TOKEN_REFRESH_MARGIN < time.time()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self) -> None:
        """Log in with SRP (blocking)."""
        cognito = Cognito(USER_POOL_ID, USER_POOL_CLIENT_ID, username=self._email)
        try:
            cognito.authenticate(password=self._password)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in AUTH_ERROR_CODES:
                raise ZeroMouseAuthError(code) from err
            raise ZeroMouseApiError(code or str(err)) from err
        except WarrantException as err:
            # MFA or forced password change: not supported, user must fix in the app.
            raise ZeroMouseAuthError(type(err).__name__) from err
        except BotoCoreError as err:
            raise ZeroMouseApiError(str(err)) from err
        self._cognito = cognito
        claims = jwt.decode(cognito.id_token, options={"verify_signature": False})
        self.user_id = claims["sub"]

    def _refresh(self) -> None:
        """Refresh tokens, falling back to a full login (blocking)."""
        assert self._cognito is not None
        try:
            self._cognito.renew_access_token()
        except (ClientError, BotoCoreError, WarrantException) as err:
            _LOGGER.debug("Token refresh failed (%s), logging in again", err)
            self._login()

    async def async_login(self) -> str:
        """Log in and return the Cognito user id (sub)."""
        async with self._auth_lock:
            await self._run(self._login)
        assert self.user_id is not None
        return self.user_id

    async def _async_tokens(self, force: bool = False) -> tuple[str, str]:
        """Return a valid (access_token, id_token) pair."""
        async with self._auth_lock:
            if self._cognito is None:
                await self._run(self._login)
            elif force or self._token_expires_soon(self._cognito.access_token):
                await self._run(self._refresh)
            assert self._cognito is not None
            return self._cognito.access_token, self._cognito.id_token

    async def async_get_access_token(self, force_refresh: bool = False) -> str:
        """Return a valid access token (used by the live connection)."""
        access_token, _ = await self._async_tokens(force=force_refresh)
        return access_token

    # ------------------------------------------------------------------
    # AppSync GraphQL
    # ------------------------------------------------------------------

    async def _graphql(
        self, query: str, variables: dict[str, Any], *, _retry: bool = True
    ) -> dict[str, Any]:
        access_token, _ = await self._async_tokens()
        try:
            async with self._session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": access_token},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    if _retry:
                        await self._async_tokens(force=True)
                        return await self._graphql(query, variables, _retry=False)
                    raise ZeroMouseAuthError(f"GraphQL HTTP {resp.status}")
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ZeroMouseApiError(f"GraphQL request failed: {err}") from err

        if errors := payload.get("errors"):
            if any(e.get("errorType") == "UnauthorizedException" for e in errors):
                raise ZeroMouseAuthError(errors[0].get("message", "Unauthorized"))
            # Partial data is still useful; only fail if nothing came back.
            if not payload.get("data"):
                raise ZeroMouseApiError(errors[0].get("message", str(errors)))
            _LOGGER.debug("GraphQL returned partial errors: %s", errors)
        return payload.get("data") or {}

    async def async_list_flaps(self) -> list[dict[str, Any]]:
        """Return all ZeroMOUSE devices owned by the user."""
        if self.user_id is None:
            await self._async_tokens()
        flaps: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            data = await self._graphql(
                LIST_FLAPS_QUERY,
                {"ownerID": self.user_id, "limit": 50, "nextToken": next_token},
            )
            result = data.get("listFlapByOwnerID") or {}
            flaps.extend(item for item in result.get("items") or [] if item)
            next_token = result.get("nextToken")
            if not next_token:
                return flaps

    async def async_list_events(
        self, device_id: str, limit: int = EVENT_FETCH_LIMIT
    ) -> list[dict[str, Any]]:
        """Return the newest events for a device, newest first."""
        data = await self._graphql(
            LIST_EVENTS_QUERY,
            {"deviceID": device_id, "sortDirection": "DESC", "limit": limit},
        )
        result = data.get("listEventbyDeviceChrono") or {}
        return [item for item in result.get("items") or [] if item]

    # ------------------------------------------------------------------
    # REST API (API Gateway)
    # ------------------------------------------------------------------

    async def _rest(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        _retry: bool = True,
    ) -> Any:
        _, id_token = await self._async_tokens()
        headers = {
            "auth-token": id_token,
            "x-app-version": APP_VERSION,
            "x-platform": "android",
        }
        try:
            async with self._session.request(
                method,
                f"{REST_BASE_URL}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    if _retry:
                        await self._async_tokens(force=True)
                        return await self._rest(
                            method,
                            path,
                            params=params,
                            json_body=json_body,
                            _retry=False,
                        )
                    raise ZeroMouseAuthError(f"REST HTTP {resp.status}")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ZeroMouseApiError(f"REST request to {path} failed: {err}") from err

    async def async_get_shadow(self, device_id: str) -> dict[str, Any]:
        """Return the device shadow state ({"reported": ..., "desired": ...})."""
        body = await self._rest(
            "GET", "/device-shadow/", params={"deviceID": device_id}
        )
        if not isinstance(body, dict):
            return {}
        # The app reads response.data.state; tolerate an unwrapped body too.
        inner = body.get("data", body)
        if not isinstance(inner, dict):
            return {}
        state = inner.get("state", inner)
        return state if isinstance(state, dict) else {}

    # ------------------------------------------------------------------
    # Event images (S3 via Cognito Identity Pool credentials)
    # ------------------------------------------------------------------

    def _ensure_aws_clients(self, id_token: str) -> None:
        """Create/refresh temporary AWS credentials and the S3 client (blocking)."""
        now = datetime.now(UTC)
        if (
            self._s3_client is not None
            and self._aws_expiration is not None
            and self._aws_expiration - timedelta(minutes=5) > now
        ):
            return
        if self._identity_client is None:
            self._identity_client = boto3.client("cognito-identity", region_name=REGION)
        logins = {f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": id_token}
        try:
            if self._identity_id is None:
                self._identity_id = self._identity_client.get_id(
                    IdentityPoolId=IDENTITY_POOL_ID, Logins=logins
                )["IdentityId"]
            creds = self._identity_client.get_credentials_for_identity(
                IdentityId=self._identity_id, Logins=logins
            )["Credentials"]
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code == "NotAuthorizedException":
                raise ZeroMouseAuthError(code) from err
            raise ZeroMouseApiError(code or str(err)) from err
        except BotoCoreError as err:
            raise ZeroMouseApiError(str(err)) from err
        self._s3_client = boto3.client(
            "s3",
            region_name=REGION,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretKey"],
            aws_session_token=creds["SessionToken"],
        )
        self._aws_expiration = creds["Expiration"]

    def _fetch_image(
        self, id_token: str, key_parts: tuple[str, str, int]
    ) -> bytes | None:
        self._ensure_aws_clients(id_token)
        assert self._identity_id is not None
        key = image_key(self._identity_id, *key_parts)
        try:
            obj = self._s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
            return obj["Body"].read()
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                # AccessDenied is what S3 returns for a missing key when the
                # caller lacks ListBucket permission.
                _LOGGER.debug("Image %s not available (%s)", key, code)
                return None
            raise ZeroMouseApiError(f"S3 error {code}") from err
        except BotoCoreError as err:
            raise ZeroMouseApiError(str(err)) from err

    async def async_get_image(
        self, device_id: str, event_id: str, index: int
    ) -> bytes | None:
        """Download one event image (JPEG). Returns None if it doesn't exist."""
        _, id_token = await self._async_tokens()
        async with self._aws_lock:
            return await self._run(
                self._fetch_image, id_token, (device_id, event_id, index)
            )
