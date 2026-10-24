"""Nguồn code từ git: clone/cập nhật repo (kể cả private qua token), đọc compose/Dockerfile trong repo, API GitHub/GitLab.

Token không bao giờ ghi vào .git/config hay URL: truyền qua header HTTP bằng biến môi trường GIT_CONFIG_* cho từng lệnh,
và chỉ gửi tới đúng host của kết nối.
Code của app nằm ở <work_dir>/src; docker-compose.yml của app trỏ vào ./src (build context, bind mount…)."""

import asyncio
import base64
import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from ..config import settings
from ..core.jobs import JobContext
from ..core.procenv import subprocess_env
from ..core.security import decrypt
from ..models import GitCredential

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
# tên nhánh/tag hợp lệ: không bắt đầu bằng "-" (tránh bị hiểu là tuỳ chọn của git), không có "..", khoảng trắng, ký tự điều khiển
BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*[\s~^:?*\[\\])[\w./+-]{1,200}$")


def check_branch(branch: str | None) -> str | None:
    branch = (branch or "").strip()
    if not branch:
        return None
    if not BRANCH_RE.match(branch) or branch.endswith((".lock", "/", ".")):
        raise GitError(f"Tên nhánh không hợp lệ: {branch!r}")
    return branch
ENV_EXAMPLES = (".env.example", ".env.sample", "example.env", ".env.dist", ".env.template", "env.example")
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", "dist", "build"}
SRC = "src"


class GitError(RuntimeError):
    pass


# ---------------------------------------------------------------- URL & xác thực
def normalize_repo(repo: str, default_host: str = "github.com") -> str:
    repo = repo.strip()
    if repo.startswith("git@") or repo.startswith("ssh://"):
        raise GitError("Chỉ hỗ trợ URL HTTPS (vd https://github.com/owner/repo) — repo private dùng kết nối token")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        repo = f"https://{default_host}/{repo}"
    u = urlparse(repo)
    if u.scheme not in ("https", "http") or not u.netloc or len(u.path.strip("/").split("/")) < 2:
        raise GitError("URL repo không hợp lệ")
    if u.username or u.password:
        raise GitError("Không nhúng tài khoản/token vào URL — hãy tạo kết nối git")
    return repo.rstrip("/")


def repo_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def repo_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path[:-4] if path.endswith(".git") else path


def _auth_user(cred: GitCredential) -> str:
    if cred.provider == "github":
        return "x-access-token"
    if cred.provider == "gitlab":
        return "oauth2"
    return cred.username or "git"


def _env(url: str | None, cred: GitCredential | None) -> dict[str, str]:
    env = subprocess_env(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/true", LC_ALL="C")
    if cred is not None and url:
        if repo_host(url) != cred.host.lower():
            raise GitError(f"Kết nối '{cred.name}' dành cho {cred.host}, không dùng được cho {repo_host(url)}")
        basic = base64.b64encode(f"{_auth_user(cred)}:{decrypt(cred.token_enc)}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


async def run_git(args: list[str], cwd: str | Path | None = None, url: str | None = None, cred: GitCredential | None = None,
                  ctx: JobContext | None = None, timeout: int = 900) -> str:
    if ctx:
        await ctx.log("$ git " + " ".join(args))
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, env=_env(url, cred),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as e:
        proc.kill()
        raise GitError(f"git {args[0]} quá {timeout}s") from e
    text = out.decode("utf-8", "replace").strip()
    if ctx and text:
        await ctx.log(text[-3000:])
    if proc.returncode != 0:
        hint = ""
        if re.search(r"Authentication failed|could not read Username|403|401|not found", text, re.I):
            hint = " — repo private? kiểm tra kết nối git / quyền của token"
        raise GitError(f"git {args[0]} thất bại{hint}: {text[-800:]}")
    return text


async def clone(url: str, branch: str | None, dest: Path, cred: GitCredential | None, ctx: JobContext | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = check_branch(branch)
    args = ["clone", "--depth", "1", "--single-branch", "--no-tags"]
    if branch:
        args += ["--branch", branch]
    await run_git(args + ["--", url, str(dest)], url=url, cred=cred, ctx=ctx)


async def update(src: Path, url: str, branch: str | None, cred: GitCredential | None, ctx: JobContext | None = None) -> None:
    """fetch + reset --hard: file đã theo dõi về đúng commit trên remote; file không theo dõi (dữ liệu runtime) giữ nguyên."""
    await run_git(["remote", "set-url", "--", "origin", url], cwd=src)
    ref = check_branch(branch) or (await current_branch(src)) or "HEAD"
    await run_git(["fetch", "--depth", "1", "--no-tags", "--", "origin", ref], cwd=src, url=url, cred=cred, ctx=ctx)
    await run_git(["reset", "--hard", "FETCH_HEAD"], cwd=src, ctx=ctx)


async def current_branch(src: Path) -> str | None:
    try:
        b = await run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=src)
        return None if b == "HEAD" else b
    except GitError:
        return None


async def head(src: Path) -> dict[str, Any]:
    out = await run_git(["log", "-1", "--format=%H%x1f%s%x1f%an%x1f%cI"], cwd=src)
    sha, msg, author, date = (out.split("\x1f") + ["", "", "", ""])[:4]
    return {"commit": sha, "commit_msg": msg[:300], "author": author, "date": date, "branch": await current_branch(src)}


async def remote_head(url: str, branch: str | None, cred: GitCredential | None) -> str | None:
    branch = check_branch(branch)
    out = await run_git(["ls-remote", "--", url, f"refs/heads/{branch}" if branch else "HEAD"], url=url, cred=cred, timeout=60)
    return out.split()[0] if out else None


async def branches(url: str, cred: GitCredential | None) -> dict[str, Any]:
    out = await run_git(["ls-remote", "--symref", "--", url, "HEAD", "refs/heads/*"], url=url, cred=cred, timeout=60)
    default, names = None, []
    for line in out.splitlines():
        if line.startswith("ref: "):
            default = line.split()[1].removeprefix("refs/heads/")
        elif "\trefs/heads/" in line:
            names.append(line.split("\trefs/heads/", 1)[1])
    return {"default": default, "branches": sorted(names, key=lambda b: (b != default, b))}


def chown_tree(root: Path, owner: tuple[int, int] | None) -> None:
    if owner is None or not root.exists():
        return
    uid, gid = owner
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames, None]:
            p = os.path.join(dirpath, name) if name else dirpath
            try:
                st = os.lstat(p)
                if st.st_uid == 0:
                    os.lchown(p, uid, gid)
            except OSError:
                pass


# ---------------------------------------------------------------- compose trong repo
def _rebase(path: Any, prefix: str) -> Any:
    if not isinstance(path, str) or not path or path.startswith(("/", "~", "$")) or "://" in path:
        return path
    p = posixpath.normpath(posixpath.join(prefix, path))
    return p if p.startswith("../") else "./" + p.removeprefix("./")


def rebase_compose(doc: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Đổi đường dẫn tương đối (build, bind mount, env_file, configs/secrets, include) sang gốc mới, vd ./src/<thư mục compose>."""
    for svc in (doc.get("services") or {}).values():
        if not isinstance(svc, dict):
            continue
        b = svc.get("build")
        if isinstance(b, str):
            svc["build"] = _rebase(b, prefix)
        elif isinstance(b, dict):
            b["context"] = _rebase(b.get("context") or ".", prefix)
        vols = svc.get("volumes") or []
        for i, v in enumerate(vols):
            if isinstance(v, str):
                src, sep, rest = v.partition(":")
                if sep and src.startswith("."):
                    vols[i] = f"{_rebase(src, prefix)}:{rest}"
            elif isinstance(v, dict) and v.get("type") == "bind":
                v["source"] = _rebase(v.get("source"), prefix)
        ef = svc.get("env_file")
        if isinstance(ef, str):
            svc["env_file"] = _rebase(ef, prefix)
        elif isinstance(ef, list):
            svc["env_file"] = [_rebase(e, prefix) if isinstance(e, str) else {**e, "path": _rebase(e.get("path"), prefix)} for e in ef]
        if isinstance(svc.get("extends"), dict) and svc["extends"].get("file"):
            svc["extends"]["file"] = _rebase(svc["extends"]["file"], prefix)
    for top in ("configs", "secrets"):
        for item in (doc.get(top) or {}).values():
            if isinstance(item, dict) and item.get("file"):
                item["file"] = _rebase(item["file"], prefix)
    inc = doc.get("include")
    if isinstance(inc, list):
        doc["include"] = [_rebase(i, prefix) if isinstance(i, str) else
                          {**i, **{k: _rebase(i[k], prefix) for k in ("path", "project_directory", "env_file") if isinstance(i.get(k), str)}}
                          for i in inc]
    return doc


def compose_from_repo(src: Path, compose_path: str) -> str:
    """Đọc compose trong repo, đổi đường dẫn tương đối để chạy từ thư mục app (cha của src/)."""
    f = (src / compose_path).resolve()
    if not str(f).startswith(str(src.resolve()) + os.sep) or not f.is_file():
        raise GitError(f"Không thấy {compose_path} trong repo")
    doc = yaml.safe_load(f.read_text()) or {}
    if not isinstance(doc, dict):
        raise GitError(f"{compose_path} không phải compose hợp lệ")
    prefix = posixpath.join(".", SRC, posixpath.dirname(compose_path))
    doc = rebase_compose(doc, prefix)
    return f"# Sinh từ repo: {compose_path} (đường dẫn đã đổi sang ./{SRC}/…)\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def dockerfile_compose(subdir: str, port: int | None, service: str = "app") -> str:
    ctx = posixpath.join(".", SRC, subdir) if subdir else f"./{SRC}"
    lines = ["services:", f"  {service}:", f"    build: {ctx}", "    restart: unless-stopped", "    env_file: .env"]
    if port:
        lines += ["    expose:", f'      - "{port}"']
    return "\n".join(lines) + "\n"


def _web_guess(doc: dict[str, Any]) -> dict[str, Any] | None:
    for name, svc in (doc.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        for p in (svc.get("ports") or []) + (svc.get("expose") or []):
            target = p.get("target") if isinstance(p, dict) else str(p).split("/")[0].split(":")[-1]
            if str(target).isdigit():
                return {"service": name, "port": int(target)}
    return None


def scan_repo(src: Path, subdir: str = "") -> dict[str, Any]:
    base = (src / subdir).resolve() if subdir else src.resolve()
    if not str(base).startswith(str(src.resolve())) or not base.is_dir():
        raise GitError(f"Không có thư mục {subdir} trong repo")
    composes = []
    for dirpath, dirnames, filenames in os.walk(base):
        depth = Path(dirpath).relative_to(base).parts
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and len(depth) < 3]
        for fn in sorted(filenames):
            if fn in COMPOSE_NAMES or re.fullmatch(r"(docker-)?compose[.-][\w.-]*\.ya?ml", fn):
                rel = str((Path(dirpath) / fn).relative_to(src))
                item: dict[str, Any] = {"path": rel}
                try:
                    item["compose"] = compose_from_repo(src, rel)
                    doc = yaml.safe_load(item["compose"]) or {}
                    item["services"] = list((doc.get("services") or {}).keys())
                    item["web"] = _web_guess(doc)
                    item["builds"] = any(isinstance(s, dict) and s.get("build") for s in (doc.get("services") or {}).values())
                except Exception as e:  # noqa: BLE001
                    item["error"] = str(e)[:300]
                composes.append(item)
    composes.sort(key=lambda c: (c["path"].count("/"), Path(c["path"]).name not in COMPOSE_NAMES, c["path"]))
    dockerfile = base / "Dockerfile"
    expose = []
    if dockerfile.is_file():
        for m in re.finditer(r"^\s*EXPOSE\s+(.+)$", dockerfile.read_text(errors="replace"), re.M | re.I):
            expose += [int(x.split("/")[0]) for x in m.group(1).split() if x.split("/")[0].isdigit()]
    env_example, env_file = "", None
    dirs = [base] + [(src / c["path"]).parent for c in composes[:1]]
    for d in dirs:
        for n in ENV_EXAMPLES:
            if (d / n).is_file():
                env_example, env_file = (d / n).read_text(errors="replace")[:20000], str((d / n).relative_to(src))
                break
        if env_file:
            break
    return {
        "composes": composes, "dockerfile": dockerfile.is_file(), "expose": expose,
        "dockerfile_compose": dockerfile_compose(subdir, expose[0] if expose else None) if dockerfile.is_file() else None,
        "env_example": env_example, "env_file": env_file,
    }


async def inspect(url: str, branch: str | None, cred: GitCredential | None, subdir: str = "") -> dict[str, Any]:
    tmp_root = settings.data_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="git-", dir=tmp_root))
    try:
        await clone(url, branch, tmp / SRC, cred)
        info = await head(tmp / SRC)
        scan = await asyncio.to_thread(scan_repo, tmp / SRC, subdir.strip("/"))
        return {**info, **scan}
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, True)


# ---------------------------------------------------------------- API của nhà cung cấp
def _api_base(cred: GitCredential) -> str:
    if cred.provider == "github":
        return "https://api.github.com" if cred.host == "github.com" else f"https://{cred.host}/api/v3"
    return f"https://{cred.host}/api/v4"


def _headers(cred: GitCredential) -> dict[str, str]:
    token = decrypt(cred.token_enc)
    if cred.provider == "github":
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    return {"PRIVATE-TOKEN": token}


async def provider_user(cred: GitCredential) -> dict[str, Any]:
    """Kiểm tra token, trả về tài khoản. generic: thử ls-remote không được nên chỉ xác nhận có token."""
    if cred.provider not in ("github", "gitlab"):
        return {"login": cred.username or None, "scopes": None}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_api_base(cred)}/user", headers=_headers(cred))
    if r.status_code in (401, 403):
        raise GitError("Token không hợp lệ hoặc đã hết hạn")
    r.raise_for_status()
    d = r.json()
    return {"login": d.get("login") or d.get("username"), "name": d.get("name"), "scopes": r.headers.get("x-oauth-scopes")}


async def provider_repos(cred: GitCredential, q: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20) as c:
        for page in range(1, 4):
            if cred.provider == "github":
                r = await c.get(f"{_api_base(cred)}/user/repos", headers=_headers(cred),
                                params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
                r.raise_for_status()
                rows = r.json()
                out += [{"full_name": x["full_name"], "url": x["clone_url"].removesuffix(".git"), "private": x["private"],
                         "default_branch": x.get("default_branch"), "description": x.get("description") or "", "updated_at": x.get("pushed_at")}
                        for x in rows]
            elif cred.provider == "gitlab":
                r = await c.get(f"{_api_base(cred)}/projects", headers=_headers(cred),
                                params={"membership": "true", "per_page": 100, "page": page, "order_by": "last_activity_at", "simple": "true"})
                r.raise_for_status()
                rows = r.json()
                out += [{"full_name": x["path_with_namespace"], "url": x["http_url_to_repo"].removesuffix(".git"),
                         "private": x.get("visibility") != "public", "default_branch": x.get("default_branch"),
                         "description": x.get("description") or "", "updated_at": x.get("last_activity_at")} for x in rows]
            else:
                return []
            if len(rows) < 100:
                break
    if q:
        out = [x for x in out if q.lower() in x["full_name"].lower()]
    return out
