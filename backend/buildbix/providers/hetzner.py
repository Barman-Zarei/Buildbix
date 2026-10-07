"""Hetzner Cloud implementation (https://docs.hetzner.cloud). Written from the public API docs; NOT verified
against the live API from this repo's CI. Creating servers is billable."""
import httpx

from .base import CloudProvider, ProviderError, ServerInfo


class HetznerProvider(CloudProvider):
    name = "hetzner"

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.c = httpx.Client(base_url="https://api.hetzner.cloud/v1", timeout=30, transport=transport,
                              headers={"Authorization": f"Bearer {token}"})

    def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.c.request(method, path, **kw)
        except httpx.HTTPError:
            raise ProviderError("network error talking to the provider") from None
        if r.status_code >= 400:
            try:
                msg = str(r.json().get("error", {}).get("message", ""))[:200]
            except ValueError:
                msg = ""
            raise ProviderError(f"provider returned {r.status_code}: {msg}".strip())
        return r.json() if r.content else {}

    @staticmethod
    def _info(server: dict) -> ServerInfo:
        ip = ((server.get("public_net") or {}).get("ipv4") or {}).get("ip")
        return ServerInfo(str(server["id"]), server.get("status", "unknown"), ip)

    def create_server(self, name, server_type, region, image):
        data = self._req("POST", "/servers", json={"name": name, "server_type": server_type, "location": region,
                                                   "image": image, "start_after_create": True})
        return self._info(data["server"])

    def get_server(self, provider_id):
        return self._info(self._req("GET", f"/servers/{provider_id}")["server"])

    def delete_server(self, provider_id):
        self._req("DELETE", f"/servers/{provider_id}")


PROVIDERS = {"hetzner": (HetznerProvider, "HETZNER_TOKEN")}
