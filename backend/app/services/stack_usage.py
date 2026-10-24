"""Dung lượng theo từng stack: thư mục compose, bind mount, named volume, lớp ghi container, image, backup.

Tính bằng `du` (chậm với thư mục lớn) nên chạy trong worker, kết quả lưu vào setting `stack_usage`.
"""

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from ..config import settings
from . import docker_svc, stacks

# Thư mục quá rộng / hệ thống: không `du` (thường là mount của công cụ quản lý, không phải dữ liệu của stack)
BROAD = {"/", "/data", "/home", "/srv", "/opt", "/mnt", "/media", "/var", "/var/lib", "/var/lib/docker", "/root", "/tmp", "/usr"}
SYSTEM = ("/proc", "/sys", "/dev", "/run", "/var/run", "/etc", "/usr", "/lib", "/boot", "/bin", "/sbin", "/var/lib/docker")


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") or root == "/"


def du(path: str, cache: dict[str, int | None]) -> int | None:
    if path in cache:
        return cache[path]
    size: int | None = None
    try:
        if os.path.isfile(path):
            size = os.lstat(path).st_size
        elif os.path.isdir(path):
            r = subprocess.run(["du", "-sB1", path], capture_output=True, text=True, timeout=900)
            if r.stdout.strip():
                size = int(r.stdout.split()[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        size = None
    cache[path] = size
    return size


def _skip_reason(path: str, work_dirs: set[str], own_wd: str | None) -> str | None:
    if path in BROAD:
        return "thư mục quá rộng"
    if any(_under(path, s) for s in SYSTEM) or path.endswith(".sock"):
        return "hệ thống"
    if any(wd != own_wd and _under(wd, path) and wd != path for wd in work_dirs):
        return "chứa stack khác"
    return None


def collect(backups_root: str | None, apps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """apps: compose_project → {id, name, slug} của app panel."""
    t0 = time.time()
    cli = docker_svc.client()
    df = cli.api.df()
    vol_info = {v["Name"]: v for v in df.get("Volumes") or []}
    rw = {c["Id"]: c.get("SizeRw") or 0 for c in df.get("Containers") or []}
    img_size = {i["Id"]: i.get("Size") or 0 for i in df.get("Images") or []}

    detected = {s["project"]: s for s in stacks.detect_all()}
    groups: dict[str, dict[str, Any]] = {}
    image_users: dict[str, set[str]] = {}
    for c in cli.containers.list(all=True):
        lb = c.labels or {}
        key = lb.get(docker_svc.COMPOSE_PROJECT) or f"container:{c.name}"
        g = groups.setdefault(key, {"containers": [], "mounts": [], "images": {}})
        g["containers"].append({"name": c.name, "state": c.status, "rw": rw.get(c.id, 0)})
        g["mounts"].extend(c.attrs.get("Mounts", []))
        g["images"][c.attrs.get("Image")] = c.attrs["Config"].get("Image") or c.attrs.get("Image")
        image_users.setdefault(c.attrs.get("Image"), set()).add(key)

    work_dirs = {s["host_work_dir"] for s in detected.values() if s.get("host_work_dir")}
    cache: dict[str, int | None] = {}
    counted: dict[str, str] = {}  # path → stack đầu tiên đã tính (tránh cộng trùng tổng chung)
    out = []
    for key, g in groups.items():
        info = detected.get(key)
        standalone = key.startswith("container:")
        wd = (info or {}).get("host_work_dir")
        if wd and _skip_reason(wd, work_dirs, wd) in ("thư mục quá rộng", "hệ thống"):
            wd = None
        items: list[dict[str, Any]] = []
        sizes = {"folder": 0, "binds": 0, "volumes": 0, "containers": 0, "images": 0, "backups": 0}

        def add(kind: str, name: str, path: str | None, size: int | None, note: str | None = None, shared: bool = False):
            items.append({"kind": kind, "name": name, "path": path, "size": size, "note": note, "shared": shared})
            if size and not note:
                sizes[kind] += size

        if wd:
            # thư mục stack khác nằm bên trong (vd thư mục dữ liệu của Dockhand chứa các stack) → trừ ra
            nested = [o for o in work_dirs if o != wd and _under(o, wd)]
            nested = [o for o in nested if not any(x != o and _under(o, x) for x in nested)]
            size = du(wd, cache)
            if size is not None and nested:
                size = max(0, size - sum(du(o, cache) or 0 for o in nested))
            add("folder", "Thư mục compose", wd, size)
            if nested:
                items[-1]["detail"] = f"đã trừ {len(nested)} thư mục stack con"
            counted.setdefault(wd, key)
        seen: set[str] = set()
        for m in g["mounts"]:
            if m.get("Type") == "bind":
                src = (m.get("Source") or "").rstrip("/") or "/"
                if not os.path.exists(src):
                    src = docker_svc._translate_path(src) or src  # đường dẫn trong container Dockhand/Portainer
                if src in seen or (wd and _under(src, wd)):
                    continue
                seen.add(src)
                reason = _skip_reason(src, work_dirs, wd)
                if reason:
                    add("binds", m.get("Destination") or src, src, None, reason)
                elif not os.path.exists(src):
                    add("binds", m.get("Destination") or src, src, None, "panel không truy cập được")
                else:
                    shared = counted.setdefault(src, key) != key
                    add("binds", m.get("Destination") or src, src, du(src, cache), shared=shared)
            elif m.get("Type") == "volume":
                name = m.get("Name") or ""
                if not name or name in seen:
                    continue
                seen.add(name)
                v = vol_info.get(name) or {}
                if "Options" not in v:
                    try:
                        v = {**v, **cli.api.inspect_volume(name)}
                    except Exception:  # noqa: BLE001
                        pass
                opts = v.get("Options") or {}
                dev = opts.get("device") if "bind" in (opts.get("o") or "") else None
                if dev:
                    if wd and _under(dev, wd):
                        continue
                    size = du(dev, cache) if os.path.exists(dev) else None
                    add("volumes", name, dev, size, None if size is not None else "panel không truy cập được",
                        shared=counted.setdefault(dev, key) != key)
                else:
                    size = (v.get("UsageData") or {}).get("Size", -1)
                    add("volumes", name, v.get("Mountpoint"), size if size >= 0 else None,
                        None if size >= 0 else "Docker chưa tính", shared=counted.setdefault(f"vol:{name}", key) != key)
        for c in g["containers"]:
            add("containers", f"Lớp ghi: {c['name']}", None, c["rw"])
        for img, label in sorted(g["images"].items(), key=lambda x: x[1] or ""):
            if not img:
                continue
            others = image_users.get(img, set()) - {key}
            items.append({"kind": "images", "name": label, "path": None,
                          "size": img_size.get(img), "note": None, "shared": bool(others)})
            sizes["images"] += img_size.get(img) or 0
        app = apps.get(key)
        if app and backups_root:
            bdir = os.path.join(backups_root, app["slug"])
            if os.path.isdir(bdir):
                add("backups", "Backup cục bộ", bdir, du(bdir, cache))
        out.append({
            "project": key.removeprefix("container:"), "standalone": standalone,
            "manager": (info or {}).get("manager") or {"key": "standalone", "label": "Container lẻ"},
            "app": app, "work_dir": wd,
            "containers": len(g["containers"]), "running": sum(1 for c in g["containers"] if c["state"] == "running"),
            "sizes": sizes, "total": sum(v for k, v in sizes.items() if k != "images"),
            "items": items,
        })
    out.sort(key=lambda s: s["total"], reverse=True)

    roots = []
    for r in sorted({os.environ.get("TH_STORAGE_ROOT", "/data"), str(settings.data_dir)}):
        if os.path.isdir(r):
            u = shutil.disk_usage(r)
            roots.append({"path": r, "total": u.total, "used": u.used, "free": u.free})
    return {
        "updated_at": datetime.now(UTC).isoformat(), "duration": round(time.time() - t0, 1),
        "stacks": out, "disks": roots,
        "images_total": sum(img_size.values()), "volumes_total": sum(max(0, (v.get("UsageData") or {}).get("Size", 0)) for v in vol_info.values()),
    }
