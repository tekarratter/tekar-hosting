"""Phát hiện stack compose do công cụ khác quản lý (Dockhand, Portainer, Dockge…), đọc compose/env,
và chuyển (convert) về thành app của panel dạng thư mục vendor-free.

Convert giữ nguyên tên project compose → volume/network (<project>_xxx) giữ nguyên, dữ liệu không mất.
"""

import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import yaml

from ..config import settings
from . import docker_svc

KNOWN_MANAGERS = {
    "dockhand": "Dockhand", "portainer": "Portainer", "dockge": "Dockge", "yacht": "Yacht",
    "coolify": "Coolify", "komodo": "Komodo", "casaos": "CasaOS", "cosmos": "Cosmos",
}
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


# ======================================================================= phát hiện
def _manager_of(work_dir: str, containers) -> dict[str, Any] | None:
    """Container nào mount thư mục chứa work_dir → đó là công cụ quản lý stack."""
    best = None
    for c in containers:
        if (c.labels or {}).get(docker_svc.COMPOSE_PROJECT) == settings.compose_project:
            continue  # bỏ qua chính panel (mount /data cùng đường dẫn)
        known = any(k in (c.attrs["Config"].get("Image") or "").lower() or k in c.name.lower() for k in KNOWN_MANAGERS)
        for m in c.attrs.get("Mounts", []):
            dest = (m.get("Destination") or "").rstrip("/")
            if dest and dest != "/" and (work_dir == dest or work_dir.startswith(dest + "/")):
                score = (len(dest), known)
                if best is None or score > best[1]:
                    best = (c, score, m)
    if best is None:
        return None
    c, _, m = best
    image = (c.attrs["Config"].get("Image") or "").lower()
    key = next((k for k in KNOWN_MANAGERS if k in image or k in c.name.lower()), None)
    return {"key": key or "other", "label": KNOWN_MANAGERS.get(key, c.name), "container": c.name,
            "mount_dest": (m.get("Destination") or "").rstrip("/"), "mount_source": m.get("Source"), "mount_type": m.get("Type"),
            "mount_name": m.get("Name")}


def detect_all() -> list[dict[str, Any]]:
    cli = docker_svc.client()
    all_c = cli.containers.list(all=True)
    projects: dict[str, dict[str, Any]] = {}
    for c in all_c:
        lb = c.labels or {}
        p = lb.get(docker_svc.COMPOSE_PROJECT)
        if not p:
            continue
        info = projects.setdefault(p, {
            "project": p, "work_dir": lb.get(docker_svc.COMPOSE_WORKDIR) or "",
            "config_files": [f for f in (lb.get(docker_svc.COMPOSE_FILES) or "").split(",") if f],
            "containers": [], "running": 0, "images": set(),
        })
        info["containers"].append({"name": c.name, "state": c.status, "service": lb.get(docker_svc.COMPOSE_SERVICE)})
        info["images"].add(c.attrs["Config"].get("Image"))
        if c.status == "running":
            info["running"] += 1
    out = []
    for info in projects.values():
        info["images"] = sorted(i for i in info["images"] if i)
        wd = info["work_dir"]
        host_wd = docker_svc._translate_path(wd) if wd else None
        mgr = _manager_of(wd, all_c) if wd and not os.path.isdir(wd) else None
        if info["project"] == settings.compose_project:
            mgr = {"key": "tekar-hosting", "label": "Tekar Hosting (hệ thống)"}
        elif mgr is None:
            mgr = {"key": "compose", "label": "Docker Compose (CLI/tay)"} if host_wd else {"key": "unknown", "label": "Không rõ"}
        info["manager"] = mgr
        info["host_work_dir"] = host_wd
        out.append(info)
    return sorted(out, key=lambda x: x["project"])


# ======================================================================= đọc file stack
def _dockhand_db(mgr: dict[str, Any]) -> Path | None:
    c = docker_svc.client().containers.get(mgr["container"])
    for m in c.attrs.get("Mounts", []):
        cand = Path(m.get("Source") or "") / "db" / "dockhand.db"
        if cand.exists():
            return cand
    return None


def _dockhand_paths(mgr: dict[str, Any], project: str) -> dict[str, str | None]:
    db = _dockhand_db(mgr)
    if not db:
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("SELECT compose_path, env_path FROM stack_sources WHERE stack_name = ? ORDER BY id DESC LIMIT 1", (project,)).fetchone()
        git = con.execute("SELECT compose_path, env_file_path FROM git_stacks WHERE stack_name = ? ORDER BY id DESC LIMIT 1", (project,)).fetchone()
        con.close()
    except sqlite3.Error:
        return {}
    return {"compose": (row[0] if row else None) or None, "env": (row[1] if row else None) or None,
            "git_compose": git[0] if git else None, "git_env": git[1] if git else None}


def _read_from_container(container: str, path: str) -> dict[str, str]:
    """Đọc thư mục/file trong container quản lý qua Docker API (không cần shell)."""
    c = docker_svc.client().containers.get(container)
    stream, _ = c.get_archive(path)
    buf = io.BytesIO(b"".join(stream))
    files: dict[str, str] = {}
    with tarfile.open(fileobj=buf) as tar:
        for m in tar.getmembers():
            if m.isfile() and m.size < 2_000_000:
                f = tar.extractfile(m)
                if f:
                    files[m.name.split("/", 1)[1] if "/" in m.name else m.name] = f.read().decode("utf-8", "replace")
    return files


def read_stack(info: dict[str, Any]) -> dict[str, Any]:
    """→ {compose, compose_path, env, env_path, source_dir, notes}"""
    mgr = info["manager"]
    wd = info["work_dir"]
    host_wd = info.get("host_work_dir")
    notes: list[str] = []
    compose = env = None
    compose_path = env_path = None

    if mgr["key"] == "dockhand" and host_wd:
        paths = _dockhand_paths(mgr, info["project"])
        cand = []
        if paths.get("compose"):
            cand.append(docker_svc._translate_path(paths["compose"]))
        if paths.get("git_compose"):
            cand.append(os.path.join(host_wd, paths["git_compose"]))
        cand += [os.path.join(host_wd, n) for n in COMPOSE_NAMES]
        compose_path = next((p for p in cand if p and os.path.isfile(p)), None)
        for e in [docker_svc._translate_path(paths["env"]) if paths.get("env") else None, os.path.join(host_wd, ".env.dockhand"), os.path.join(host_wd, ".env")]:
            if e and os.path.isfile(e):
                env_path = e
                break
        if paths.get("git_compose"):
            notes.append("Stack Dockhand lấy từ git — sau khi chuyển, panel không tự đồng bộ git (dùng deploy webhook/CI)")
    elif mgr["key"] == "portainer":
        try:
            files = _read_from_container(mgr["container"], wd)
            name = next((n for n in COMPOSE_NAMES if n in files), None) or next((n for n in files if n.endswith((".yml", ".yaml"))), None)
            if name:
                compose, compose_path = files[name], f"{mgr['label']}:{wd}/{name}"
            for en in ("stack.env", ".env"):
                if en in files:
                    env, env_path = files[en], f"{mgr['label']}:{wd}/{en}"
            if env is None:
                notes.append("Portainer lưu biến môi trường trong DB nội bộ — đã khôi phục từ môi trường container đang chạy, hãy kiểm tra lại")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Không đọc được file từ {mgr['label']}: {e}")
    elif host_wd:
        files = [docker_svc._translate_path(f) for f in info["config_files"] if f != "-"]
        files = [f for f in files if f and os.path.isfile(f)] or [os.path.join(host_wd, n) for n in COMPOSE_NAMES if os.path.isfile(os.path.join(host_wd, n))]
        compose_path = files[0] if files else None
        if len(files) > 1:
            notes.append(f"Dự án dùng nhiều file compose: {', '.join(files)} — chỉ file đầu được chuyển, các file khác cần gộp tay")
        if os.path.isfile(os.path.join(host_wd, ".env")):
            env_path = os.path.join(host_wd, ".env")

    if compose is None and compose_path and os.path.isfile(compose_path):
        compose = Path(compose_path).read_text()
    if env is None and env_path and os.path.isfile(env_path):
        env = Path(env_path).read_text()
        if mgr["key"] == "dockhand":
            env = "\n".join(line for line in env.splitlines() if not line.startswith("# Auto-generated by Dockhand")) + "\n"
    if compose and env is None:
        env = recover_env(compose, info["project"])
    return {"compose": compose, "compose_path": compose_path, "env": env or "", "env_path": env_path,
            "source_dir": host_wd, "notes": notes}


def recover_env(compose_text: str, project: str) -> str:
    """Biến ${VAR} trong compose mà không có file .env → lấy giá trị từ env của container đang chạy."""
    try:
        data = yaml.safe_load(compose_text) or {}
    except yaml.YAMLError:
        return ""
    containers = {(c.labels or {}).get(docker_svc.COMPOSE_SERVICE): c
                  for c in docker_svc.client().containers.list(all=True, filters={"label": f"{docker_svc.COMPOSE_PROJECT}={project}"})}
    found: dict[str, str] = {}
    missing: set[str] = set()
    var_re = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:?-][^}]*)?\}")
    for sname, svc in (data.get("services") or {}).items():
        c = containers.get(sname)
        cenv = dict(e.split("=", 1) for e in (c.attrs["Config"].get("Env") or []) if "=" in e) if c else {}
        env = svc.get("environment") or {}
        pairs = env.items() if isinstance(env, dict) else [(e.split("=", 1)[0], e.split("=", 1)[1] if "=" in e else "") for e in env]
        for k, v in pairs:
            m = var_re.fullmatch(str(v or "").strip())
            if m:
                if k in cenv:
                    found[m.group(1)] = cenv[k]
                else:
                    missing.add(m.group(1))
    for v in var_re.findall(compose_text):
        if v not in found:
            missing.add(v)
    lines = ["# Khôi phục tự động từ container đang chạy — kiểm tra lại trước khi deploy"]
    lines += [f"{k}={v}" for k, v in sorted(found.items())]
    lines += [f"# {k}=   (chưa xác định được giá trị)" for k in sorted(missing - set(found))]
    return "\n".join(lines) + "\n"


def mask_env(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out.append(f"{k}=***" if re.search(r"(pass|secret|token|key|credential|private|auth|database_url|dsn)", k, re.I) and v else line)
        else:
            out.append(line)
    return "\n".join(out)


# ======================================================================= convert
def _abs(p: str, base: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(p)))


def _is_rel(p: Any) -> bool:
    return isinstance(p, str) and (p in (".", "..") or p.startswith(("./", "../")))


def rewrite_relative(compose_text: str, base: str) -> tuple[str, list[str]]:
    """Đổi đường dẫn tương đối (bind mount, build, env_file, configs/secrets) thành tuyệt đối theo thư mục gốc,
    để dữ liệu vẫn nằm tại chỗ cũ khi chuyển file compose sang thư mục mới."""
    data = yaml.safe_load(compose_text) or {}
    changed: list[str] = []
    for sname, svc in (data.get("services") or {}).items():
        vols = svc.get("volumes") or []
        for i, v in enumerate(vols):
            if isinstance(v, str):
                parts = v.split(":")
                if len(parts) >= 2 and _is_rel(parts[0]):
                    parts[0] = _abs(parts[0], base)
                    vols[i] = ":".join(parts)
                    changed.append(f"{sname}: volume {v} → {parts[0]}")
            elif isinstance(v, dict) and v.get("type") == "bind" and _is_rel(v.get("source")):
                v["source"] = _abs(v["source"], base)
                changed.append(f"{sname}: bind → {v['source']}")
        b = svc.get("build")
        if _is_rel(b):
            svc["build"] = _abs(b, base)
            changed.append(f"{sname}: build → {svc['build']}")
        elif isinstance(b, dict) and _is_rel(b.get("context", ".")):
            b["context"] = _abs(b.get("context", "."), base)
            changed.append(f"{sname}: build.context → {b['context']}")
        ef = svc.get("env_file")
        if ef:
            items = ef if isinstance(ef, list) else [ef]
            new = []
            for e in items:
                if isinstance(e, dict) and _is_rel(e.get("path")):
                    e = {**e, "path": _abs(e["path"], base)}
                elif _is_rel(e) or (isinstance(e, str) and not e.startswith("/")):
                    e = _abs(e, base)
                new.append(e)
            svc["env_file"] = new
        ext = svc.get("extends")
        if isinstance(ext, dict) and _is_rel(ext.get("file")):
            ext["file"] = _abs(ext["file"], base)
    for top in ("configs", "secrets"):
        for _, d in (data.get(top) or {}).items():
            if isinstance(d, dict) and _is_rel(d.get("file")):
                d["file"] = _abs(d["file"], base)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True), changed


def copy_tree(src: str, dst: str) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv", ".env.dockhand"))


def strip_dockhand_env(env: str) -> str:
    return "\n".join(line for line in env.splitlines() if "Auto-generated by Dockhand" not in line) + "\n"


def json_safe(info: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(info, default=str))
