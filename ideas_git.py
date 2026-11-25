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


def find_repos(folder):
    """The repositories a list's folder stands for.

    The folder itself when it is one; otherwise the repositories directly inside it (a project
    folder holding, say, a backend and a frontend); otherwise the repository the folder is part of.
    """
    if not folder or not os.path.isdir(folder):
        return []
    if os.path.exists(os.path.join(folder, ".git")):
        return [folder]
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    inside = [os.path.join(folder, n) for n in names
              if not n.startswith(".") and n not in SKIP_DIRS and os.path.exists(os.path.join(folder, n, ".git"))]
    if inside:
        return inside
    code, out, _err = git(folder, "rev-parse", "--show-toplevel")
    return [out.strip()] if code == 0 and out.strip() else []


def repo_status(repo):
    """{"path", "name", "branch", "files": [(status, path)], "ahead": n, "remote": name or None}.

    files: everything uncommitted (changed, staged, new).  ahead: commits not on the remote yet,
    0 when the branch has no upstream.
    """
    st = {"path": repo, "name": os.path.basename(repo.rstrip(os.sep)) or repo, "branch": None,
          "files": [], "ahead": 0, "remote": None}
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
            st["files"].append((line[:2].strip() or "?", path.strip('"')))
    code, out, _err = git(repo, "remote")
    remotes = out.split() if code == 0 else []
    st["remote"] = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
    return st


def folder_status(folder):
    """Status of every repository under a list's folder that has something to commit or push."""
    return [st for st in (repo_status(r) for r in find_repos(folder)) if st["files"] or st["ahead"]]


def find_smart_commit():
    """The smart-commit script that ships with Ideas (next to this file)."""
    return SMART_COMMIT if os.path.isfile(SMART_COMMIT) else None


def commit_and_push(repo, smart_commit):
    """Commit everything uncommitted in `repo` (message by smart-commit), then push.

    Returns {"name", "committed": subject or None, "pushed": bool, "error": text or None}.
    A commit that was made stays made when the push fails; the next try only pushes.
    """
    st = repo_status(repo)
    res = {"name": st["name"], "committed": None, "pushed": False, "error": None}
    if not st["branch"]:
        res["error"] = "not on a branch (detached HEAD), so nothing was committed"
        return res
    if st["files"]:
        if not smart_commit:
            res["error"] = "smart-commit is missing from the Ideas folder, so no commit message could be written"
            return res
        try:
            r = subprocess.run([sys.executable, smart_commit, "-a", "-y"], cwd=repo, capture_output=True, text=True, errors="replace",
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
