## GitHub Releases Setup

1. Tao repo tren GitHub.
2. Day source code len repo.
3. Trong `Comicsubtool.py`, sua:
   - `GITHUB_REPO = "yourname/your-repo"`
   - `UPDATE_ASSET_NAME = "ComicSubTool.exe"`
4. Moi lan phat hanh ban moi:
   - Tang `APP_VERSION`
   - Build file `.exe`
   - Tao GitHub Release moi, tag nen giong version, vi du `v0.1.1`
   - Upload file `.exe` voi dung ten trong `UPDATE_ASSET_NAME`
5. Tren may nguoi dung, bam `Cap nhat` de tai release moi nhat.

Goi y:
- Neu tag la `v0.1.1`, app van so sanh dung voi `0.1.0`.
- Nen giu ten file `.exe` on dinh giua cac release.

## Push source tu dong

Chay:

```powershell
python release.py
```

Script se tu:
- `git add .`
- `git commit`
- `git push`

Neu muon tao va push tag theo `APP_VERSION`:

```powershell
python release.py --tag
```
