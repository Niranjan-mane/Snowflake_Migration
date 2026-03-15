"""
Snowflake connector — supports password, key-pair, SSO, and OAuth authentication.
"""
from __future__ import annotations

import os
from typing import Any, Optional

try:
    import snowflake.connector
    from snowflake.connector import DictCursor
    SF_AVAILABLE = True
except ImportError:
    SF_AVAILABLE = False


class SnowflakeConnector:
    """
    Manages a Snowflake connection.

    Config keys (from config/snowflake.yaml, env-interpolated):
      account, user, warehouse, database, schema, role,
      auth_method (password|keypair|sso|oauth),
      password, private_key_path, private_key_passphrase
    """

    def __init__(self, config: dict):
        if not SF_AVAILABLE:
            raise ImportError(
                "snowflake-connector-python is not installed. "
                "Run: pip install snowflake-connector-python"
            )
        self._config = config
        self._conn = None

    def connect(self):
        """Open the Snowflake connection."""
        params = self._build_params()
        self._conn = snowflake.connector.connect(**params)
        return self

    def _build_params(self) -> dict:
        cfg = self._config
        auth = cfg.get("auth_method", "password").lower()

        params: dict[str, Any] = {
            "account":   self._resolve(cfg["account"]),
            "user":      self._resolve(cfg["user"]),
            "warehouse": self._resolve(cfg.get("warehouse", "")),
            "database":  self._resolve(cfg.get("database", "")),
            "schema":    self._resolve(cfg.get("schema", "")),
            "role":      self._resolve(cfg.get("role", "")),
            "login_timeout":  cfg.get("login_timeout", 60),
            "network_timeout": cfg.get("network_timeout", 120),
            "client_session_keep_alive": cfg.get("client_session_keep_alive", True),
        }

        if auth == "password":
            params["password"] = self._resolve(cfg.get("password", ""))

        elif auth == "keypair":
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.serialization import (
                Encoding, NoEncryption, PrivateFormat, load_pem_private_key,
            )
            key_path = self._resolve(cfg.get("private_key_path", ""))
            passphrase_str = self._resolve(cfg.get("private_key_passphrase", ""))
            passphrase = passphrase_str.encode() if passphrase_str else None
            with open(key_path, "rb") as f:
                private_key = load_pem_private_key(f.read(), password=passphrase, backend=default_backend())
            params["private_key"] = private_key.private_bytes(
                encoding=Encoding.DER,
                format=PrivateFormat.PKCS8,
                encryption_algorithm=NoEncryption(),
            )

        elif auth == "sso":
            params["authenticator"] = "externalbrowser"

        elif auth == "oauth":
            params["authenticator"] = "oauth"
            params["token"] = self._resolve(cfg.get("token", os.getenv("SF_TOKEN", "")))

        return {k: v for k, v in params.items() if v not in (None, "")}

    def execute(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        """Execute SQL and return rows as list of dicts."""
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        with self._conn.cursor(DictCursor) as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def execute_scalar(self, sql: str) -> Any:
        """Execute SQL and return first column of first row."""
        rows = self.execute(sql)
        if rows:
            return list(rows[0].values())[0]
        return None

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _resolve(value: str) -> str:
        """Resolve ${ENV_VAR} placeholders in config values."""
        if not isinstance(value, str):
            return value
        import re
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.getenv(m.group(1), m.group(0)),
            value,
        )
