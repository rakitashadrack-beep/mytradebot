# Phase 7 — Windows packaging, Vercel deployment assets, and final handoff

Status: complete as release-ready source assets; external signing, domain, payment activation, and deployment remain owner-controlled release actions.

## Delivered

- Windows build script using Python 3.11, PyInstaller, and a complete test gate.
- Inno Setup installer source for a 64-bit per-user Windows installer.
- Update-manifest checker that verifies semantic versions and manifest SHA-256 format before showing an update; installer signing and hosted manifests remain release-owner responsibilities.
- Vercel Python configuration, FastAPI entrypoint, production environment template, and deployment guide.
- README, user guide, terms/risk-disclosure template, and launch checklist.

## Build a Windows installer

On a clean Windows 11 build machine, install Python 3.11, MT5, and Inno Setup. Then run:

```powershell
cd C:\path\to\mytradebot
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1
```

Install Inno Setup with `winget install --id JRSoftware.InnoSetup -e -s winget -i`, open `installer\mytradebot.iss`, and compile it. Sign the resulting installer and all executable artifacts with a certificate owned by your business before distributing them.

## Update manifest format

Host this JSON over HTTPS only after the download is code-signed:

```json
{
  "version": "0.2.0",
  "download_url": "https://downloads.yourdomain.com/MyTradeBot-Setup-0.2.0.exe",
  "sha256": "64-character-lowercase-sha256-of-the-installer",
  "notes": "Concise release notes"
}
```

The manifest hash detects accidental corruption; Authenticode signing is still required to authenticate the publisher.

## Verification

```bash
python3 -m py_compile main.py core/*.py ui/*.py backend/*.py api/*.py
pytest -q tests tests_backend
```

Assumption: production releases are built and signed on Windows because PyInstaller is not a cross-compiler for Windows executables.
