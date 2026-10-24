"""Kết nối git (GitHub/GitLab/Gitea… bằng token), đọc repo trước khi tạo app, cập nhật code cho app từ git."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select

from ..core.audit import audit
from ..core.deps import DB, CurrentUser, check_app
from ..core.jobs import enqueue
from ..core.security import encrypt
from ..models import App, GitCredential, User
from ..services import git

router = APIRouter(tags=["git"])


def cred_out(c: GitCredential, user: User) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "provider": c.provider, "host": c.host, "username": c.username,
            "shared": c.shared, "owner_id": c.owner_id, "mine": c.owner_id == user.id, "created_at": c.created_at}


def _visible(user: User):
    q = select(GitCredential).order_by(GitCredential.name)
    if user.role != "admin":
        q = q.where(or_(GitCredential.owner_id == user.id, GitCredential.shared.is_(True)))
    return q


async def get_cred(db, user: User, cred_id: int | None, manage: bool = False) -> GitCredential | None:
    if not cred_id:
        return None
    c = await db.get(GitCredential, cred_id)
    if c is None:
        raise HTTPException(404, "Không thấy kết nối git")
    allowed = user.role == "admin" or c.owner_id == user.id or (c.shared and not manage)
    if not allowed:
        raise HTTPException(403, "Không có quyền dùng kết nối git này")
    return c


@router.get("/api/git/credentials")
async def list_creds(user: CurrentUser, db: DB):
    return [cred_out(c, user) for c in (await db.execute(_visible(user))).scalars()]


class CredIn(BaseModel):
    name: str
    provider: str = "github"
    host: str = ""
    username: str = ""
    token: str
    shared: bool = False


@router.post("/api/git/credentials")
async def add_cred(body: CredIn, user: CurrentUser, db: DB, request: Request):
    if user.role == "member":
        raise HTTPException(403, "Member không tạo được kết nối git")
    if body.provider not in ("github", "gitlab", "generic"):
        raise HTTPException(400, "provider: github | gitlab | generic")
    host = (body.host or {"github": "github.com", "gitlab": "gitlab.com"}.get(body.provider, "")).strip().lower()
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    if not host:
        raise HTTPException(400, "Nhập host (vd git.congty.vn)")
    c = GitCredential(name=body.name.strip() or host, provider=body.provider, host=host, username=body.username.strip(),
                      token_enc=encrypt(body.token.strip()), shared=body.shared and user.role in ("admin", "operator"), owner_id=user.id)
    try:
        info = await git.provider_user(c)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không xác thực được token: {e}") from e
    c.username = info.get("login") or c.username
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await audit(db, user, "git.credential_add", c.name, {"provider": c.provider, "host": c.host}, request)
    return {**cred_out(c, user), "scopes": info.get("scopes")}


@router.delete("/api/git/credentials/{cid}")
async def del_cred(cid: int, user: CurrentUser, db: DB, request: Request):
    c = await get_cred(db, user, cid, manage=True)
    in_use = [a.slug for a in (await db.execute(select(App))).scalars() if (a.git or {}).get("credential_id") == cid]
    await db.delete(c)
    await db.commit()
    await audit(db, user, "git.credential_delete", c.name, {"apps": in_use}, request)
    return {"ok": True, "apps": in_use}


@router.post("/api/git/credentials/{cid}/test")
async def test_cred(cid: int, user: CurrentUser, db: DB):
    c = await get_cred(db, user, cid)
    try:
        return {"ok": True, **await git.provider_user(c)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}


@router.get("/api/git/credentials/{cid}/repos")
async def repos(cid: int, user: CurrentUser, db: DB, q: str = ""):
    c = await get_cred(db, user, cid)
    try:
        return await git.provider_repos(c, q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không lấy được danh sách repo: {e}") from e


async def _repo_and_cred(db, user: User, repo: str, cred_id: int | None) -> tuple[str, GitCredential | None]:
    cred = await get_cred(db, user, cred_id)
    try:
        return git.normalize_repo(repo, cred.host if cred else "github.com"), cred
    except git.GitError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/git/branches")
async def branches(repo: str, user: CurrentUser, db: DB, credential_id: int | None = None):
    url, cred = await _repo_and_cred(db, user, repo, credential_id)
    try:
        return {"repo": url, **await git.branches(url, cred)}
    except git.GitError as e:
        raise HTTPException(400, str(e)) from e


class InspectIn(BaseModel):
    repo: str
    branch: str | None = None
    credential_id: int | None = None
    subdir: str = ""


@router.post("/api/git/inspect")
async def inspect(body: InspectIn, user: CurrentUser, db: DB):
    """Clone tạm (shallow) để tìm compose / Dockerfile / .env.example, gợi ý compose cho app."""
    if user.role == "member":
        raise HTTPException(403, "Member không được tạo app")
    url, cred = await _repo_and_cred(db, user, body.repo, body.credential_id)
    try:
        return {"repo": url, **await git.inspect(url, body.branch or None, cred, body.subdir)}
    except git.GitError as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- git của một app
@router.get("/api/apps/{app_id}/git")
async def app_git(app_id: int, user: CurrentUser, db: DB, remote: bool = True):
    app = await check_app(user, app_id, db, "deployer")
    g = dict(app.git or {})
    out: dict[str, Any] = {"git": g or None, "remote_commit": None, "remote_error": None}
    if g.get("credential_id"):
        c = await db.get(GitCredential, g["credential_id"])
        out["credential"] = {"id": c.id, "name": c.name, "host": c.host} if c else None
    if remote and g.get("repo"):
        try:
            c = await db.get(GitCredential, g["credential_id"]) if g.get("credential_id") else None
            out["remote_commit"] = await git.remote_head(g["repo"], g.get("branch"), c)
        except Exception as e:  # noqa: BLE001
            out["remote_error"] = str(e)[:400]
    out["behind"] = bool(out["remote_commit"] and g.get("commit") and out["remote_commit"] != g["commit"])
    return out


class AppGitIn(BaseModel):
    repo: str
    branch: str | None = None
    credential_id: int | None = None
    subdir: str = ""
    compose_path: str | None = None
    sync_compose: bool = False
    auto_pull: bool = False


@router.put("/api/apps/{app_id}/git")
async def set_app_git(app_id: int, body: AppGitIn, user: CurrentUser, db: DB, request: Request):
    """Gắn / đổi repo cho app managed. Lần kéo code tiếp theo sẽ clone vào <work_dir>/src nếu chưa có."""
    app = await check_app(user, app_id, db, "deployer")
    if app.kind != "managed":
        raise HTTPException(400, "Chỉ app managed mới gắn được nguồn git")
    url, cred = await _repo_and_cred(db, user, body.repo, body.credential_id)
    try:
        branch = git.check_branch(body.branch)
    except git.GitError as e:
        raise HTTPException(400, str(e)) from e
    old = app.git or {}
    if old.get("repo") and old["repo"] != url:
        raise HTTPException(400, "Đổi sang repo khác: gỡ nguồn git trước, đổi tên/xoá thư mục src rồi gắn lại")
    app.git = {**old, "repo": url, "branch": branch, "credential_id": cred.id if cred else None,
               "subdir": body.subdir.strip("/"), "compose_path": body.compose_path or None,
               "sync_compose": bool(body.compose_path) and body.sync_compose, "auto_pull": body.auto_pull}
    await db.commit()
    await audit(db, user, "app.git_set", app.slug, {k: v for k, v in app.git.items() if k in ("repo", "branch", "auto_pull", "sync_compose")}, request)
    return {"git": app.git}


@router.delete("/api/apps/{app_id}/git")
async def unset_app_git(app_id: int, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "deployer")
    app.git = None
    await db.commit()
    await audit(db, user, "app.git_unset", app.slug, request=request)
    return {"ok": True, "note": "Thư mục src được giữ nguyên; compose không đổi"}


@router.post("/api/apps/{app_id}/git/pull")
async def pull(app_id: int, user: CurrentUser, db: DB, request: Request, deploy: bool = True):
    app = await check_app(user, app_id, db, "deployer")
    if not (app.git or {}).get("repo"):
        raise HTTPException(400, "App chưa gắn repo git")
    job = await enqueue(db, "git_update", {"app_id": app.id, "deploy": deploy}, app.id, user.id)
    await audit(db, user, "app.git_pull", app.slug, {"deploy": deploy}, request)
    return {"job_id": job.id}
