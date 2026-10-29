# API Sentinel — Development Setup

**Document status:** Initial  
**Last updated:** 2026-10-30

## 1. Development Environment

Current development configuration:

| Component | Configuration |
|---|---|
| OS | Windows |
| IDE | PyCharm 2025.3.6.1 |
| Python | 3.15.2 |
| Git | 2.33.1 |
| Virtual environment | `.venv` |
| Repository | GitHub |

Local repository:

`D:\PETS\api-sentinel`

## 2. Prerequisites

Required tools:

- Windows 10/11.
- Git.
- Python 3.15.2 (initial development version).
- PyCharm or another compatible Python IDE.

Python and dependency compatibility must be verified before setting up a production environment.

## 3. Clone Repository

```powershell
git clone https://github.com/RSiniavski/api-sentinel.git
cd api-sentinel
```

## 4. Create Virtual Environment

```powershell
py -3.15 -m venv .venv
```

## 5. Activate Virtual Environment

```powershell
.\.venv\Scripts\Activate.ps1
```

Expected terminal prefix:

```text
(.venv)
```

## 6. Verify Python Version

```powershell
python --version
```

Expected output:

```text
Python 3.15.2
```

## 7. PyCharm Configuration

PyCharm should use the project's existing virtual environment.

Interpreter path:

```text
D:\PETS\api-sentinel\.venv\Scripts\python.exe
```

The IDE may detect and configure the environment automatically.

## 8. Git Configuration

Check repository status:

```powershell
git status
```

Verify that the virtual environment is ignored:

```powershell
git check-ignore -v .venv
```

The `.venv` directory must never be committed.

## 9. Dependency Management

All Python dependencies must be installed inside `.venv`.

Dependency versions should be recorded and locked to ensure reproducibility.

The exact dependency management workflow will be established when the backend implementation begins.

## 10. Development Workflow

Recommended workflow:

1. Pull the latest changes.
2. Activate `.venv`.
3. Implement a focused change.
4. Run automated tests.
5. Review the Git diff.
6. Commit the changes.
7. Push changes to GitHub.

## 11. Initial Project Status

The following setup steps have been completed:

- [x] GitHub repository created.
- [x] Repository cloned.
- [x] Python 3.15.2 installed.
- [x] Virtual environment created.
- [x] Virtual environment activated and verified.
- [x] `.venv` excluded from Git.
- [x] PyCharm interpreter configured.

---

**Document status:** Initial  
**Last updated:** 2026-10-30