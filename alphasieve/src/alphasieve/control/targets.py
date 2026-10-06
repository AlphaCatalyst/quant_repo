"""Compute target configuration (Ray clusters, SSH hosts, LLM endpoints)."""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass(frozen=True)
class RayCluster:
    name: str
    address: str
    r2: bool
    priority: int


@dataclass(frozen=True)
class SshHost:
    name: str
    alias: str
    mounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LlmEndpoint:
    name: str
    url: str
    note: str = ""


@dataclass(frozen=True)
class Targets:
    ray_clusters: tuple[RayCluster, ...]
    ssh_hosts: tuple[SshHost, ...]
    llm_endpoints: tuple[LlmEndpoint, ...]
    remote_root: str
    extra: dict = field(default_factory=dict)

    def clusters(self, *, need_r2: bool = False) -> list[RayCluster]:
        return sorted((c for c in self.ray_clusters if c.r2 or not need_r2), key=lambda c: c.priority)


def load_targets(settings) -> Targets:
    data = yaml.safe_load((settings.config_dir / "control" / "targets.yaml").read_text(encoding="utf-8"))
    return Targets(
        ray_clusters=tuple(RayCluster(**c) for c in data.get("ray_clusters", [])),
        ssh_hosts=tuple(SshHost(name=h["name"], alias=h["alias"], mounts=tuple(h.get("mounts", [])))
                        for h in data.get("ssh_hosts", [])),
        llm_endpoints=tuple(LlmEndpoint(**e) for e in data.get("llm_endpoints", [])),
        remote_root=data["remote_root"],
    )
