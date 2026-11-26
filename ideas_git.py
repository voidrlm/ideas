"""Git for folder-linked lists: what is uncommitted or unpushed, and commit-and-push.

No GTK in here.  The commit message is written by the smart-commit script that ships with
Ideas (it reads the diff and follows the repository's own commit style).
"""

import os
import subprocess
import sys

GIT_TIMEOUT = 20
PUSH_TIMEOUT = 180
SMART_COMMIT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "smart-commit")
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "dist", "build", "target", "vendor"}


def _env():
    # Never stop to ask for a password in a terminal nobody can see: fail with a message instead.
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                GIT_SSH_COMMAND=os.environ.get("GIT_SSH_COMMAND") or "ssh -o BatchMode=yes")


def git(repo, *args, timeout=GIT_TIMEOUT):
    """(exit code, stdout, stderr); a missing git or a timeout counts as a failure."""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", repo, *args],
                           capture_output=True, text=True, errors="replace", timeout=timeout, env=_env())
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as e:
        return 127, "", str(e)


def last_line(text, fallback=""):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else fallback


def git_error(text, fallback):
    """The line of git's complaint that says what went wrong."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for prefix in ("fatal: ", "error: ", "remote: error: "):
        hit = next((ln for ln in lines if ln.startswith(prefix)), None)
        if hit:
            return hit[len(prefix):][:200]
    return lines[-1][:200] if lines else fallback


MAX_DEPTH = 4                           # how far below a list's folder to look for repositories


def _is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def find_repos(folder):
    """The repositories a list's folder stands for.

    The folder itself when it is one, plus every repository in its subfolders (up to MAX_DEPTH
    levels down: a project folder holding, say, apps/web and apps/api).  With none of those, the
    repository the folder is part of.
    """
    if not folder or not os.path.isdir(folder):
        return []
    found = []

    def walk(path, depth):
        if _is_repo(path):
            found.append(path)
        if depth >= MAX_DEPTH:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    walk(e.path, depth + 1)
            except OSError:
                pass
    walk(os.path.normpath(folder), 0)
    if found:
        return found
    code, out, _err = git(folder, "rev-parse", "--show-toplevel")
    return [out.strip()] if code == 0 and out.strip() else []


def repo_status(repo, name=None, nested=()):
    """{"path", "name", "branch", "files": [(status, path)], "ahead": n, "remote": name or None, "skip": [path]}.

    files: everything uncommitted (changed, staged, new).  ahead: commits not on the remote yet,
    0 when the branch has no upstream.  nested: paths of repositories inside this one; those
    that this repository doesn't track are left out of files and listed in skip, so committing
    here never swallows another repository.
    """
    st = {"path": repo, "name": name or os.path.basename(repo.rstrip(os.sep)) or repo, "branch": None,
          "files": [], "ahead": 0, "remote": None, "skip": []}
    inner = {os.path.relpath(n, repo).replace(os.sep, "/").rstrip("/") for n in nested}
    code, out, _err = git(repo, "status", "--porcelain=v1", "--branch", "--untracked-files=all")
    if code != 0:
        return st
    for line in out.splitlines():
        if line.startswith("## "):
            head = line[3:]
            if head.startswith(("HEAD (no branch)", "No commits yet on ")):
                st["branch"] = head[len("No commits yet on "):] if head.startswith("No commits") else None
            else:
                st["branch"] = head.split("...")[0].split(" ")[0]
            if "[ahead " in head or ", ahead " in head:
                try:
                    st["ahead"] = int(head.split("ahead ")[1].split("]")[0].split(",")[0])
                except ValueError:
                    pass
        elif len(line) > 3:
            path = line[3:]
            if " -> " in path and line[0] in "RC":
                path = path.split(" -> ", 1)[1]
            path = path.strip('"')
            if line[:2] == "??" and path.rstrip("/") in inner:
                st["skip"].append(path.rstrip("/"))
                continue
            st["files"].append((line[:2].strip() or "?", path))
    code, out, _err = git(repo, "remote")
    remotes = out.split() if code == 0 else []
    st["remote"] = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
    return st


def folder_repos(folder):
    """Status of every repository under a list's folder, named by where it sits in the folder."""
    repos = find_repos(folder)
    base = os.path.normpath(folder) if folder else ""
    out = []
    for r in repos:
        rel = os.path.relpath(r, base) if base else "."
        name = os.path.basename(r.rstrip(os.sep)) if rel == "." or rel.startswith("..") else rel.replace(os.sep, "/")
        nested = [o for o in repos if o != r and o.startswith(r.rstrip(os.sep) + os.sep)]
        out.append(repo_status(r, name, nested))
    return out


def has_work(st):
    return bool(st["files"] or st["ahead"])


def folder_status(folder):
    """Status of every repository under a list's folder that has something to commit or push."""
    return [st for st in folder_repos(folder) if has_work(st)]


def find_smart_commit():
    """The smart-commit script that ships with Ideas (next to this file)."""
    return SMART_COMMIT if os.path.isfile(SMART_COMMIT) else None


def commit_and_push(repo, smart_commit, name=None, nested=()):
    """Commit everything uncommitted in `repo` (message by smart-commit), then push.

    Returns {"name", "committed": subject or None, "pushed": bool, "error": text or None}.
    A commit that was made stays made when the push fails; the next try only pushes.
    """
    st = repo_status(repo, name, nested)
    res = {"name": st["name"], "committed": None, "pushed": False, "error": None}
    if not st["branch"]:
        res["error"] = "not on a branch (detached HEAD), so nothing was committed"
        return res
    if st["files"]:
        if not smart_commit:
            res["error"] = "smart-commit is missing from the Ideas folder, so no commit message could be written"
            return res
        if st["skip"]:          # stage by hand so the repositories inside this one stay out of it
            code, _out, err = git(repo, "add", "-A", "--", ".", *(f":(exclude){p}" for p in st["skip"]))
            if code != 0:
                res["error"] = "commit failed: " + git_error(err, "git add failed")
                return res
        try:
            cmd = [sys.executable, smart_commit, "-y"] + ([] if st["skip"] else ["-a"])
            r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, errors="replace",
                               timeout=PUSH_TIMEOUT, env=_env(), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            res["error"] = "the commit took too long and was stopped"
            return res
        except OSError as e:
            res["error"] = f"couldn't run smart-commit: {e}"
            return res
        if r.returncode != 0:
            res["error"] = "commit failed: " + last_line(r.stderr or r.stdout, "smart-commit stopped with an error")
            return res
        code, out, _err = git(repo, "log", "-1", "--pretty=%s")
        res["committed"] = out.strip() if code == 0 else "changes"
    if not st["remote"]:
        res["error"] = "committed, but this repository has no remote to push to" if res["committed"] else "no remote to push to"
        return res
    has_upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")[0] == 0
    args = ["push"] if has_upstream else ["push", "--set-upstream", st["remote"], st["branch"]]
    code, _out, err = git(repo, *args, timeout=PUSH_TIMEOUT)
    if code != 0:
        why = git_error(err, "git push failed")
        if "rejected" in err or "non-fast-forward" in err or "fetch first" in err:
            why = "the remote has commits you don't have yet. Pull first, then push again."
        elif code == 124:
            why = "no answer from the remote"
        res["error"] = ("committed, but the push failed: " if res["committed"] else "push failed: ") + why
        return res
    res["pushed"] = True
    return res
