"""QuickBooks Online OAuth and query transport.

Lifted from a working production integration and tightened in three places.

**Read-only is architectural, not granted.** Intuit publishes one accounting
scope, ``com.intuit.quickbooks.accounting``, and it confers both read and write.
There is no read-only accounting scope to ask for. So the boundary is enforced
here instead: :class:`QboReadClient` exposes ``query`` and ``report`` and has no
method that issues a POST. Write capability, when it is eventually earned,
arrives as a separate class with separate stored credentials, so "read-only in
the alpha" is a property of the type rather than a promise in a document.

**Query values are escaped.** QBO's query language is SQL-like and takes string
literals. A vendor named ``O'Brien`` breaks an unescaped query, and a vendor
named something worse does more than break it.

**Token refresh is single-flight and persisted.** Intuit rotates the refresh
token on every use. Losing the rotated token means the client has to
re-authorise, so the store is written before the new access token is returned.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "FileTokenStore",
    "QboConfig",
    "QboTokens",
    "TokenStore",
    "MemoryTokenStore",
    "QboAuth",
    "QboReadClient",
    "QboError",
    "QboAuthError",
    "escape_query_value",
    "ACCOUNTING_SCOPE",
]

ACCOUNTING_SCOPE = "com.intuit.quickbooks.accounting"
AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
MINOR_VERSION = "65"


class QboError(RuntimeError):
    """A QuickBooks API call failed."""


class QboAuthError(QboError):
    """Authorisation is missing, expired or was revoked."""


def escape_query_value(value: str) -> str:
    """Escape a string literal for a QBO query.

    Single quotes are doubled, which is what the QBO query language expects, and
    backslashes are rejected outright rather than escaped: no legitimate account
    or vendor name contains one, so its presence means the input is not what the
    caller thinks it is.
    """
    if "\\" in value:
        raise ValueError("backslash is not valid in a QuickBooks query literal")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("control characters are not valid in a QuickBooks query literal")
    return value.replace("'", "''")


@dataclass(frozen=True)
class QboConfig:
    """Application credentials and environment."""

    client_id: str
    client_secret: str
    redirect_uri: str
    sandbox: bool = True

    @property
    def api_base(self) -> str:
        return SANDBOX_BASE if self.sandbox else PRODUCTION_BASE

    @classmethod
    def from_environment(cls) -> QboConfig:
        """Read QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI and QBO_SANDBOX.

        Fails with the names of the missing variables rather than a KeyError,
        because the person hitting this is setting up their first connection.
        """
        import os

        missing = [k for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET") if not os.environ.get(k)]
        if missing:
            raise QboAuthError(f"set {' and '.join(missing)} (from Intuit developer portal, Keys & credentials)")
        return cls(
            client_id=os.environ["QBO_CLIENT_ID"],
            client_secret=os.environ["QBO_CLIENT_SECRET"],
            redirect_uri=os.environ.get("QBO_REDIRECT_URI", "http://localhost:8765/callback"),
            sandbox=os.environ.get("QBO_SANDBOX", "true").strip().lower() not in ("0", "false", "no", "production"),
        )

    @property
    def basic_auth(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return base64.b64encode(raw).decode()


@dataclass
class QboTokens:
    """One company's tokens. ``refresh_token`` rotates on every refresh."""

    realm_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime | None = None
    obtained_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_expired(self, *, skew_seconds: int = 120) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(seconds=skew_seconds)

    @property
    def refresh_expired(self) -> bool:
        if self.refresh_expires_at is None:
            return False
        return datetime.now(UTC) >= self.refresh_expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "realm_id": self.realm_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "refresh_expires_at": (
                self.refresh_expires_at.isoformat() if self.refresh_expires_at else None
            ),
            "obtained_at": self.obtained_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QboTokens:
        return cls(
            realm_id=data["realm_id"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            refresh_expires_at=(
                datetime.fromisoformat(data["refresh_expires_at"])
                if data.get("refresh_expires_at")
                else None
            ),
            obtained_at=datetime.fromisoformat(
                data.get("obtained_at", datetime.now(UTC).isoformat())
            ),
        )


class TokenStore(Protocol):
    """Persistence for tokens. A real deployment encrypts at rest."""

    def load(self, tenant_id: str) -> QboTokens | None: ...

    def save(self, tenant_id: str, tokens: QboTokens) -> None: ...


class FileTokenStore:
    """One JSON file per tenant in a directory that must never be committed."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, tenant_id: str) -> Path:
        return self.directory / f"{tenant_id}.qbo-tokens.json"

    def load(self, tenant_id: str) -> QboTokens | None:
        path = self._path(tenant_id)
        if not path.exists():
            return None
        return QboTokens.from_dict(json.loads(path.read_text()))

    def save(self, tenant_id: str, tokens: QboTokens) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(tenant_id)
        path.write_text(json.dumps(tokens.to_dict(), indent=2))
        try:
            path.chmod(0o600)
        except OSError:
            pass


class MemoryTokenStore:
    """In-memory store. For tests and local runs only; nothing survives a restart."""

    def __init__(self) -> None:
        self._data: dict[str, QboTokens] = {}

    def load(self, tenant_id: str) -> QboTokens | None:
        return self._data.get(tenant_id)

    def save(self, tenant_id: str, tokens: QboTokens) -> None:
        self._data[tenant_id] = tokens


HttpCall = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_http(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        return exc.code, exc.read()


class QboAuth:
    """The OAuth dance. Nothing here can read or write company data."""

    def __init__(
        self,
        config: QboConfig,
        store: TokenStore | None = None,
        *,
        http: HttpCall = _default_http,
    ) -> None:
        self.config = config
        self.store = store or MemoryTokenStore()
        self._http = http
        self._locks: dict[str, threading.Lock] = {}

    def authorize_url(self, state: str) -> str:
        """The URL the client visits to grant access.

        ``state`` must be unguessable and checked on the way back; it is the only
        thing standing between this flow and an attacker attaching their own
        QuickBooks company to someone else's tenant.
        """
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": ACCOUNTING_SCOPE,
            "response_type": "code",
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(form).encode()
        headers = {
            "Authorization": f"Basic {self.config.basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        status, raw = self._http("POST", TOKEN_URL, headers, body)
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError as exc:
            raise QboAuthError(f"token endpoint returned non-JSON (status {status})") from exc
        if status >= 400 or "error" in payload:
            raise QboAuthError(
                payload.get("error_description") or payload.get("error") or f"status {status}"
            )
        return payload

    def exchange_code(self, tenant_id: str, code: str, realm_id: str) -> QboTokens:
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
        )
        tokens = self._build(realm_id, payload)
        self.store.save(tenant_id, tokens)
        return tokens

    def refresh(self, tenant_id: str, tokens: QboTokens) -> QboTokens:
        if tokens.refresh_expired:
            raise QboAuthError(
                "the refresh token has expired; the client must re-authorise QuickBooks"
            )
        payload = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}
        )
        refreshed = self._build(tokens.realm_id, payload, previous=tokens)
        # Persist before returning. Intuit rotates the refresh token, so losing
        # the write means the next refresh fails and the client has to reconnect.
        self.store.save(tenant_id, refreshed)
        return refreshed

    def access_token(self, tenant_id: str) -> QboTokens:
        """Current tokens, refreshed if needed. Safe to call concurrently."""
        lock = self._locks.setdefault(tenant_id, threading.Lock())
        with lock:
            tokens = self.store.load(tenant_id)
            if tokens is None:
                raise QboAuthError(
                    f"tenant {tenant_id} has not connected QuickBooks"
                )
            if tokens.is_expired():
                tokens = self.refresh(tenant_id, tokens)
            return tokens

    def _build(
        self, realm_id: str, payload: Mapping[str, Any], previous: QboTokens | None = None
    ) -> QboTokens:
        now = datetime.now(UTC)
        refresh_expiry = payload.get("x_refresh_token_expires_in")
        return QboTokens(
            realm_id=realm_id,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token")
            or (previous.refresh_token if previous else ""),
            expires_at=now + timedelta(seconds=int(payload.get("expires_in", 3600))),
            refresh_expires_at=(
                now + timedelta(seconds=int(refresh_expiry)) if refresh_expiry else None
            ),
            obtained_at=now,
        )


class QboReadClient:
    """Read access to one company. Deliberately has no method that writes."""

    def __init__(self, auth: QboAuth, tenant_id: str, *, http: HttpCall = _default_http) -> None:
        self._auth = auth
        self._tenant_id = tenant_id
        self._http = http

    @property
    def realm_id(self) -> str:
        return self._auth.access_token(self._tenant_id).realm_id

    def _get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        tokens = self._auth.access_token(self._tenant_id)
        base = self._auth.config.api_base
        query = dict(params)
        query.setdefault("minorversion", MINOR_VERSION)
        url = f"{base}/v3/company/{tokens.realm_id}/{path}?{urllib.parse.urlencode(query)}"
        headers = {
            "Authorization": f"Bearer {tokens.access_token}",
            "Accept": "application/json",
        }
        status, raw = self._http("GET", url, headers, None)
        if status == 401:
            raise QboAuthError("QuickBooks rejected the access token")
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError as exc:
            raise QboError(f"QuickBooks returned non-JSON (status {status})") from exc
        if status >= 400 or "Fault" in payload:
            fault = payload.get("Fault", {}).get("Error", [{}])[0]
            raise QboError(
                fault.get("Detail") or fault.get("Message") or f"QuickBooks error {status}"
            )
        return payload

    def query(self, statement: str) -> dict[str, Any]:
        """Run one QBO query. The caller escapes any literal it interpolates."""
        return self._get("query", {"query": statement})

    def query_all(
        self, entity: str, *, where: str | None = None, page_size: int = 500
    ) -> list[dict[str, Any]]:
        """Page through every row of an entity.

        QBO caps a single response at 1000 rows and offers no cursor, only
        ``STARTPOSITION``. Paging has to be explicit or a company with more than
        a thousand journal entries silently returns a truncated ledger, which
        would tie out to nothing and look like a data problem at the client's end.
        """
        rows: list[dict[str, Any]] = []
        start = 1
        while True:
            clause = f" WHERE {where}" if where else ""
            statement = (
                f"SELECT * FROM {entity}{clause} "
                f"STARTPOSITION {start} MAXRESULTS {page_size}"
            )
            payload = self.query(statement)
            response = payload.get("QueryResponse", {})
            batch = response.get(entity, [])
            rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return rows

    def report(self, name: str, **params: str) -> dict[str, Any]:
        """Fetch a QuickBooks report, such as ``TrialBalance``."""
        return self._get(f"reports/{name}", params)

    def company_info(self) -> dict[str, Any]:
        rows = self.query_all("CompanyInfo")
        return rows[0] if rows else {}
