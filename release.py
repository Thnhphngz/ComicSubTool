import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_FILE = ROOT / "Comicsubtool.py"


def run(cmd, check=True):
    print(f"> {' '.join(cmd)}")
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


def get_app_version():
    text = APP_FILE.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Khong tim thay APP_VERSION trong Comicsubtool.py")
    return match.group(1)


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
    return capture(["git", "branch", "--show-current"])


def build_default_message():
    version = get_app_version()
    return f"Release source v{version}"


def git_commit(message):
    try:
        run(["git", "commit", "-m", message])
    except subprocess.CalledProcessError:
        raise RuntimeError("Git commit that bai. Kiem tra user.name / user.email hoac noi dung staged.")


def maybe_create_tag(tag_name):
    existing = capture(["git", "tag", "--list", tag_name])
    if existing.strip():
        print(f"Tag {tag_name} da ton tai, bo qua tao tag.")
        return
    run(["git", "tag", tag_name])


def maybe_push_tag(tag_name):
    run(["git", "push", "origin", tag_name])


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tu dong git add / commit / push cho ComicSubTool."
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Commit message. Mac dinh se lay theo APP_VERSION.",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Tao git tag v<APP_VERSION> va push tag len origin.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Van commit/push du khong co thay doi.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_git_repo()
    ensure_remote()

    has_changes = working_tree_has_changes()
    if not has_changes and not args.allow_empty:
        print("Khong co thay doi nao de push.")
        return 0

    branch = current_branch() or "main"
    message = args.message or build_default_message()
    version = get_app_version()
    tag_name = f"v{version}"

    run(["git", "add", "."])
    if has_changes:
        git_commit(message)
    else:
        run(["git", "commit", "--allow-empty", "-m", message])

    run(["git", "push", "-u", "origin", branch])

    if args.tag:
        maybe_create_tag(tag_name)
        maybe_push_tag(tag_name)

    print("")
    print("Xong.")
    print(f"- Branch: {branch}")
    print(f"- Version: {version}")
    if args.tag:
        print(f"- Tag: {tag_name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Loi: {exc}", file=sys.stderr)
        raise SystemExit(1)
