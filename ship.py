import argparse
import subprocess
import sys
from pathlib import Path

import release


ROOT = Path(__file__).resolve().parent


def run(cmd):
    print(f"> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="1 lenh de bump version, build onedir, zip va tao GitHub Release."
    )
    parser.add_argument(
        "--part",
        choices=["patch", "minor", "major"],
        default="patch",
        help="Phan version se tang. Mac dinh: patch.",
    )
    parser.add_argument(
        "--notes",
        help="Release notes cho GitHub Release.",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="Commit message. Mac dinh se tu tao theo version moi.",
    )
    parser.add_argument(
        "--no-release",
        action="store_true",
        help="Chi bump version + build, khong tao GitHub Release.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    old_version = release.get_app_version()
    new_version = release.bump_version(old_version, args.part)
    release.set_app_version(new_version)
    print(f"Version: {old_version} -> {new_version}")

    try:
        run([sys.executable, "build_exe.py"])
        run([sys.executable, "package_release.py"])
        cmd = [
            sys.executable, "release.py",
            "--no-bump",
            "--asset", "dist\\ComicSubTool-win.zip",
        ]
        if args.notes:
            cmd.extend(["--notes", args.notes])
        if args.message:
            cmd.extend(["-m", args.message])
        if args.no_release:
            cmd.append("--no-release")
        run(cmd)
    except Exception:
        release.set_app_version(old_version)
        raise


if __name__ == "__main__":
    main()
