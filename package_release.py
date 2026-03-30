from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIST_DIR = ROOT / "build_dist"
APP_DIR = BUILD_DIST_DIR / "ComicSubTool"
ZIP_BASE = DIST_DIR / "ComicSubTool-win"


def main():
    if not APP_DIR.exists():
        raise SystemExit("Khong tim thay build_dist\\ComicSubTool. Hay chay python build_exe.py truoc.")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = shutil.make_archive(str(ZIP_BASE), "zip", root_dir=BUILD_DIST_DIR, base_dir="ComicSubTool")
    print(f"Da tao goi release: {zip_path}")


if __name__ == "__main__":
    main()
