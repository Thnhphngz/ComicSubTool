import argparse
import json
import mimetypes
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_FILE = ROOT / "Comicsubtool.py"


def run(cmd, check=True):
    print(f"> {' '.join(str(part) for part in cmd)}")
    return subprocess.run(cmd, cwd=ROOT, check=check)


def capture(cmd):
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def read_app_text():
    return APP_FILE.read_text(encoding="utf-8")


def write_app_text(text):
    APP_FILE.write_text(text, encoding="utf-8")


def extract_setting(name):
    text = read_app_text()
    match = re.search(rf'^{name}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Khong tim thay {name} trong {APP_FILE.name}")
    return match.group(1)


def replace_setting(name, value):
    text = read_app_text()
    new_text, count = re.subn(
        rf'^{name}\s*=\s*"([^"]*)"',
        f'{name} = "{value}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"Khong sua duoc {name} trong {APP_FILE.name}")
    write_app_text(new_text)


def set_app_version(value):
    replace_setting("APP_VERSION", value)


def get_app_version():
    return extract_setting("APP_VERSION")


def get_repo_name():
    repo = extract_setting("GITHUB_REPO").strip().strip("/")
    if not repo:
        raise RuntimeError("GITHUB_REPO dang rong trong Comicsubtool.py")
    return repo


def get_update_asset_name():
    return extract_setting("UPDATE_ASSET_NAME").strip()


def parse_version(version):
    parts = [int(piece) for piece in re.findall(r"\d+", version or "")]
    return parts or [0, 0, 0]


def format_version(parts):
    return ".".join(str(part) for part in parts)


def bump_version(version, part):
    parts = parse_version(version)
    while len(parts) < 3:
        parts.append(0)

    if part == "major":
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif part == "minor":
        parts[1] += 1
        parts[2] = 0
    else:
        parts[2] += 1
    return format_version(parts[:3])


def ensure_git_repo():
    try:
        capture(["git", "rev-parse", "--is-inside-work-tree"])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Thu muc hien tai chua la git repository.") from exc


def ensure_remote():
    remotes = capture(["git", "remote"]).splitlines()
    if "origin" not in remotes:
        raise RuntimeError("Khong tim thay remote 'origin'.")


def working_tree_has_changes():
    status = capture(["git", "status", "--short"])
    return bool(status.strip())


def current_branch():
    return capture(["git", "branch", "--show-current"]) or "main"


def git_commit(message):
    try:
        run(["git", "commit", "-m", message])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Git commit that bai. Kiem tra user.name / user.email hoac noi dung staged."
        ) from exc


def ensure_tag_not_exists(tag_name):
    existing = capture(["git", "tag", "--list", tag_name])
    if existing.strip():
        raise RuntimeError(f"Tag {tag_name} da ton tai. Hay tang version roi chay lai.")


def github_api_request(url, method="GET", data=None, token=None, extra_headers=None):
    headers = {
        "User-Agent": "ComicSubTool-release-script",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)

    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_github_release(repo, tag_name, version, notes, token):
    url = f"https://api.github.com/repos/{repo}/releases"
    payload = {
        "tag_name": tag_name,
        "name": tag_name,
        "body": notes or f"Release v{version}",
        "draft": False,
        "prerelease": False,
        "generate_release_notes": False,
    }
    try:
        return github_api_request(url, method="POST", data=payload, token=token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tao GitHub Release that bai: {body}") from exc


def upload_release_asset(upload_url_template, asset_path, token):
    upload_url = upload_url_template.split("{", 1)[0]
    asset_name = asset_path.name
    query = urllib.parse.urlencode({"name": asset_name})
    url = f"{upload_url}?{query}"
    content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
    data = asset_path.read_bytes()
    headers = {
        "User-Agent": "ComicSubTool-release-script",
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Upload asset that bai: {body}") from exc


def choose_asset_path(user_asset_path=None):
    if user_asset_path:
        path = (ROOT / user_asset_path).resolve() if not Path(user_asset_path).is_absolute() else Path(user_asset_path)
        if not path.exists():
            raise RuntimeError(f"Khong tim thay asset: {path}")
        return path

    asset_name = get_update_asset_name() or "ComicSubTool-win.zip"
    candidates = [
        ROOT / asset_name,
        ROOT / "dist" / asset_name,
        ROOT / "dist" / "ComicSubTool-win.zip",
        ROOT / "dist" / "ComicSubTool.exe",
        ROOT / "Comicsubtool.py",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise RuntimeError("Khong tim thay asset de upload.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tu dong bump version, push va tao GitHub Release cho ComicSubTool."
    )
    parser.add_argument(
        "--part",
        choices=["patch", "minor", "major"],
        default="patch",
        help="Phan version se tang. Mac dinh: patch.",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Commit message. Mac dinh se tu tao theo version moi.",
    )
    parser.add_argument(
        "--notes",
        help="Release notes cho GitHub Release.",
    )
    parser.add_argument(
        "--asset",
        help="Duong dan asset can upload. Neu bo trong, script se uu tien .exe roi den Comicsubtool.py.",
    )
    parser.add_argument(
        "--no-release",
        action="store_true",
        help="Chi bump version + git push, khong tao GitHub Release.",
    )
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="Khong tang version, dung APP_VERSION hien tai.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_git_repo()
    ensure_remote()

    token = None if args.no_release else (
        capture(["git", "config", "--get", "release.githubToken"]) or ""
    )
    if not args.no_release and not token:
        token = ""
        try:
            import os
            token = os.environ.get("GITHUB_TOKEN", "").strip()
        except Exception:
            token = ""
        if not token:
            raise RuntimeError(
                "Chua co GITHUB_TOKEN. Hay set env GITHUB_TOKEN hoac git config release.githubToken <token>."
            )

    old_version = get_app_version()
    if args.no_bump:
        new_version = old_version
    else:
        new_version = bump_version(old_version, args.part)
        set_app_version(new_version)

    repo = get_repo_name()
    branch = current_branch()
    tag_name = f"v{new_version}"
    ensure_tag_not_exists(tag_name)

    asset_path = None if args.no_release else choose_asset_path(args.asset)
    commit_message = args.message or f"Release v{new_version}"
    release_notes = args.notes or f"Auto release v{new_version}"

    run(["git", "add", "."])
    git_commit(commit_message)
    run(["git", "push", "-u", "origin", branch])
    run(["git", "tag", tag_name])
    run(["git", "push", "origin", tag_name])

    release_url = ""
    if not args.no_release:
        release = create_github_release(repo, tag_name, new_version, release_notes, token)
        upload_release_asset(release["upload_url"], asset_path, token)
        release_url = release.get("html_url", "")

    print("")
    print("Xong.")
    print(f"- Version cu: {old_version}")
    print(f"- Version moi: {new_version}")
    print(f"- Branch: {branch}")
    print(f"- Tag: {tag_name}")
    if asset_path is not None:
        print(f"- Asset: {asset_path.name}")
    if release_url:
        print(f"- GitHub Release: {release_url}")
    elif args.no_release:
        print("- GitHub Release: bo qua theo --no-release")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Loi: {exc}", file=sys.stderr)
        raise SystemExit(1)
