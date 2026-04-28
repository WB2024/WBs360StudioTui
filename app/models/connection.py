"""Connection profile dataclass."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectionProfile:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "My Xbox 360"
    host: str = ""
    port: int = 21
    username: str = "xbox"
    password: str = "xbox"
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConnectionProfile":
        return cls(
            id=d.get("id") or str(uuid.uuid4()),
            name=d.get("name", "My Xbox 360"),
            host=d.get("host", ""),
            port=int(d.get("port", 21)),
            username=d.get("username", "xbox"),
            password=d.get("password", "xbox"),
            is_default=bool(d.get("is_default", False)),
        )
