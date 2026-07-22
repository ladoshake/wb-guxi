#!/usr/bin/env python3
# 通过 GitHub REST Contents API 推送文件（绕过被网络拦截的 git 协议）。
# token 来源：环境变量 GITHUB_TOKEN，否则读取 ~/.github_token 或同目录 .github_token。
# 用法：python3 gh_push.py <文件1> [文件2 ...] [最后参数作为 commit message]
import os, sys, json, base64, subprocess, urllib.request, urllib.parse

REPO = "ladoshake/wb-guxi"
API = "https://api.github.com/repos/" + REPO


def load_token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    for p in (os.path.expanduser("~/.github_token"),
              os.path.join(os.path.dirname(__file__), ".github_token")):
        try:
            with open(p) as f:
                return f.read().strip()
        except Exception:
            pass
    return None


def api(method, path, data=None):
    url = API + path
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "wb-guxi-push")
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def get_sha(repo_path):
    try:
        r = api("GET", "/git/trees/heads/main?recursive=1")
        tree = json.loads(r).get("tree", [])
        for item in tree:
            if item.get("path") == repo_path and item.get("type") == "blob":
                return item.get("sha")
        return None
    except Exception:
        try:
            r = api("GET", "/contents/" + urllib.parse.quote(repo_path))
            return json.loads(r)["sha"]
        except Exception:
            return None


def put_file(local_path, repo_path, message):
    sha = get_sha(repo_path)
    with open(local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")
    data = {"message": message, "content": content}
    if sha:
        data["sha"] = sha
    api("PUT", "/contents/" + urllib.parse.quote(repo_path), data)
    print("  pushed", repo_path)


def reconcile_local():
    # 尝试用 git fetch 同步远端对象，再 update-ref + reset
    try:
        subprocess.run(["git", "fetch", "origin", "main"],
                       cwd=os.path.dirname(__file__) or ".",
                       capture_output=True, timeout=30)
        ref = json.loads(api("GET", "/git/refs/heads/main"))
        sha = ref["object"]["sha"]
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", sha],
                       cwd=os.path.dirname(__file__) or ".", check=True)
        subprocess.run(["git", "reset", "--hard", "origin/main"],
                       cwd=os.path.dirname(__file__) or ".", check=True)
        print("  local git synced to remote", sha[:8])
    except Exception as e:
        # 非关键：推送已成功，本地同步失败不影响
        print("  (skip local sync:", str(e)[:80], ")")


if __name__ == "__main__":
    TOKEN = load_token()
    if not TOKEN:
        print("ERROR: token not found (set GITHUB_TOKEN or ~/.github_token)")
        sys.exit(1)
    args = sys.argv[1:]
    if not args:
        print("usage: python3 gh_push.py <file1> [file2 ...] [message]")
        sys.exit(1)
    message = "update via GitHub API"
    files = args
    if len(args) >= 2 and not os.path.exists(args[-1]):
        message = args[-1]
        files = args[:-1]
    print(f"Pushing {len(files)} file(s) to {REPO} ...")
    for p in files:
        put_file(p, os.path.basename(p), message)
    reconcile_local()
    print("Done.")
