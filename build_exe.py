from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent
APP_FILE = ROOT / "Comicsubtool.py"
ICON_FILE = ROOT / "app_icon.ico"


def main():
    PyInstaller.__main__.run([
        str(APP_FILE),
        "--noconfirm",
        "--clean",
        "--name=ComicSubTool",
        "--windowed",
        f"--icon={ICON_FILE}",
        f"--add-data={ICON_FILE};.",
    ])


if __name__ == "__main__":
    main()
