from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(RuntimeError):
    pass


@dataclass
class ServerInfo:
    provider_id: str
    status: str
    ip: str | None


class CloudProvider(ABC):
    """Implement this once per infrastructure provider; BUILDBIX is not tied to any single one."""
    name: str

    @abstractmethod
    def create_server(self, name: str, server_type: str, region: str, image: str) -> ServerInfo: ...
    @abstractmethod
    def get_server(self, provider_id: str) -> ServerInfo: ...
    @abstractmethod
    def delete_server(self, provider_id: str) -> None: ...
