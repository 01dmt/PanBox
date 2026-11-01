from __future__ import annotations

import ipaddress
import os
from typing import Iterable, Union

DEFAULT_ADMIN_NETWORKS = "127.0.0.1/32,::1/128"
Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def parse_admin_networks(value: str | None = None) -> tuple[Network, ...]:
    raw = value if value is not None else os.getenv("MEDIA_ADMIN_NETWORKS", DEFAULT_ADMIN_NETWORKS)
    if not raw.strip():
        raw = DEFAULT_ADMIN_NETWORKS
    networks: list[Network] = []
    for chunk in raw.replace(",", " ").split():
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError as exc:
            raise ValueError(f"MEDIA_ADMIN_NETWORKS 包含无效网段：{chunk}") from exc
    if not networks:
        raise ValueError("MEDIA_ADMIN_NETWORKS 不能为空；默认只允许本机 127.0.0.1/32,::1/128")
    return tuple(networks)


def is_admin_client(host: str | None, networks: Iterable[Network]) -> bool:
    if not host:
        return False
    if host in {"localhost", "testclient"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network for network in networks)
