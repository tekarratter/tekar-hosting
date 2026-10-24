"""Truy cập Docker Engine: liệt kê container, nhóm theo project compose, logs, stats."""

import asyncio
import os
import re
from functools import lru_cache
from typing import Any

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from ..config import settings

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_WORKDIR = "com.docker.compose.project.working_dir"
COMPOSE_FILES = "com.docker.compose.project.config_files"
SECRET_RE = re.compile(r"(pass|secret|token|key|credential|private|auth)", re.I)


@lru_cache
def client() -> docker.DockerClient:
    return docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=60)


async def run(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def redact_env(env: list[str] | None) -> list[str]:
    out = []
    for item in env or []:
        k, _, v = item.partition("=")
        out.append(f"{k}=***" if SECRET_RE.search(k) and v else item)
    return out


def summarize(c: Container) -> dict[str, Any]:
    attrs = c.attrs
    labels = attrs["Config"].get("Labels") or {}
    state = attrs["State"]
    ports = []
    for cport, binds in (attrs["NetworkSettings"].get("Ports") or {}).items():
        if binds:
            for b in binds:
                ports.append({"container": cport, "host_ip": b.get("HostIp"), "host_port": b.get("HostPort")})
        else:
            ports.append({"container": cport, "host_ip": None, "host_port": None})
    hc = attrs["HostConfig"]
    return {
        "id": c.id[:12],
        "name": c.name,
        "image": attrs["Config"].get("Image"),
        "state": state.get("Status"),
        "health": (state.get("Health") or {}).get("Status"),
        "started_at": state.get("StartedAt"),
        "restart_count": attrs.get("RestartCount", 0),
        "project": labels.get(COMPOSE_PROJECT),
        "service": labels.get(COMPOSE_SERVICE),
        "ports": ports,
        "networks": list((attrs["NetworkSettings"].get("Networks") or {}).keys()),
        "cpu_limit": (hc.get("NanoCpus") or 0) / 1e9 or ((hc.get("CpuQuota") or 0) / (hc.get("CpuPeriod") or 100000)) or None,
        "mem_limit": hc.get("Memory") or None,
        "app": labels.get("th.app"),
    }


def _list(all_: bool = True) -> list[dict[str, Any]]:
    return [summarize(c) for c in client().containers.list(all=all_)]


async def list_containers(all_: bool = True) -> list[dict[str, Any]]:
    return await run(_list, all_)


def _projects() -> dict[str, dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for c in client().containers.list(all=True):
        labels = c.labels or {}
        p = labels.get(COMPOSE_PROJECT)
        if not p:
            continue
        info = projects.setdefault(
            p,
            {
                "project": p,
                "work_dir": labels.get(COMPOSE_WORKDIR),
                "config_files": (labels.get(COMPOSE_FILES) or "").split(",") if labels.get(COMPOSE_FILES) else [],
                "containers": [],
                "running": 0,
            },
        )
        info["containers"].append(c.name)
        if c.status == "running":
            info["running"] += 1
    return projects


async def compose_projects() -> dict[str, dict[str, Any]]:
    return await run(_projects)


def _translate_path(path: str) -> str | None:
    """work_dir do Dockhand/Portainer báo là đường dẫn *trong* container của chúng.
    Dò bind mount của các container để đổi sang đường dẫn host (nếu panel truy cập được)."""
    if not path:
        return None
    if os.path.exists(path):
        return path
    best: tuple[int, str] | None = None
    from ..config import settings as _s

    for c in client().containers.list(all=True):
        if (c.labels or {}).get(COMPOSE_PROJECT) == _s.compose_project:
            continue
        for m in c.attrs.get("Mounts", []):
            dest, src = (m.get("Destination") or "").rstrip("/"), m.get("Source") or ""
            if dest and (path == dest or path.startswith(dest + "/")):
                cand = src + path[len(dest):]
                if os.path.exists(cand) and (best is None or len(dest) > best[0]):
                    best = (len(dest), cand)
    return best[1] if best else None


async def translate_path(path: str) -> str | None:
    return await run(_translate_path, path)


def _get(name_or_id: str) -> Container:
    return client().containers.get(name_or_id)


async def get_container(name_or_id: str) -> Container | None:
    try:
        return await run(_get, name_or_id)
    except NotFound:
        return None


async def project_containers(project: str) -> list[Container]:
    return await run(client().containers.list, all=True, filters={"label": f"{COMPOSE_PROJECT}={project}"})


def inspect_safe(c: Container) -> dict[str, Any]:
    a = c.attrs
    hc = a["HostConfig"]
    return {
        **summarize(c),
        "command": a["Config"].get("Cmd"),
        "entrypoint": a["Config"].get("Entrypoint"),
        "env": redact_env(a["Config"].get("Env")),
        "labels": a["Config"].get("Labels") or {},
        "user": a["Config"].get("User") or "root",
        "mounts": [
            {"type": m.get("Type"), "source": m.get("Source"), "name": m.get("Name"), "destination": m.get("Destination"), "rw": m.get("RW")}
            for m in a.get("Mounts", [])
        ],
        "restart_policy": (hc.get("RestartPolicy") or {}).get("Name"),
        "privileged": hc.get("Privileged"),
        "network_mode": hc.get("NetworkMode"),
        "cap_add": hc.get("CapAdd"),
        "exit_code": a["State"].get("ExitCode"),
        "error": a["State"].get("Error"),
        "pid": a["State"].get("Pid"),
        "created": a.get("Created"),
        "image_id": a.get("Image"),
    }


async def logs(name_or_id: str, tail: int = 200, since: int | None = None) -> str:
    c = await get_container(name_or_id)
    if c is None:
        return ""
    raw = await run(c.logs, tail=tail, timestamps=True, since=since)
    return raw.decode("utf-8", "replace")


async def ensure_network(name: str) -> None:
    def _ensure():
        try:
            client().networks.get(name)
        except NotFound:
            client().networks.create(name, driver="bridge")

    await run(_ensure)


async def connect_network(container: str, network: str) -> bool:
    def _connect():
        c = _get(container)
        if network in (c.attrs["NetworkSettings"].get("Networks") or {}):
            return False
        client().networks.get(network).connect(c)
        return True

    return await run(_connect)


async def remove_network(name: str) -> None:
    """Gỡ mọi container còn gắn rồi xoá mạng (bỏ qua nếu không có)."""
    def _rm():
        try:
            net = client().networks.get(name)
        except NotFound:
            return
        net.reload()
        for cid in (net.attrs.get("Containers") or {}):
            try:
                net.disconnect(cid, force=True)
            except Exception:  # noqa: BLE001
                pass
        net.remove()

    await run(_rm)


async def ensure_image(image: str) -> None:
    def _ensure():
        try:
            client().images.get(image)
        except NotFound:
            client().images.pull(image)

    await run(_ensure)


def helper_run(image: str, command: list[str], volumes: dict[str, dict], network: str | None = None, env: dict | None = None) -> str:
    """Chạy container tạm, trả về output; raise nếu exit != 0."""
    c = client().containers.run(image, command, volumes=volumes, network=network, environment=env or {}, detach=True)
    try:
        res = c.wait(timeout=3600)
        out = c.logs().decode("utf-8", "replace")
        if res.get("StatusCode", 1) != 0:
            raise RuntimeError(f"{' '.join(command)} -> exit {res.get('StatusCode')}: {out[-2000:]}")
        return out
    finally:
        c.remove(force=True)


def edge_upstream_ports(c: Container) -> list[int]:
    ports = set()
    for cport in (c.attrs["Config"].get("ExposedPorts") or {}).keys():
        num, _, proto = cport.partition("/")
        if proto in ("", "tcp"):
            ports.add(int(num))
    return sorted(ports)
