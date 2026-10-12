# environment

| File | Content |
|---|---|
| `SYSTEM.txt` | Recorded execution environments: Azure experiment VM (E38-E39 and E52 single-node kind replication, re-analyses), AKS (E40), original local host (E5-E33), collaborator host (E34-E37) |
| `CONTAINER_IMAGES_AND_BUILD_INFO.txt` | Container image digests (kindest/node v1.37.0, pause 3.10, python 3.12-slim, control-plane images) and the Go build info of the E39-F client-go responder |
| `requirements/requirements-reproduce.txt` | **The only file needed for L0-L2**: exact pinned versions (union of the authors' analysis and builder venvs) |
| `requirements/requirements-*.txt` | Per-component `pip freeze` of the authors' venvs: manuscript builders, latency/offline re-analysis, E38v2 analysis/runner/deploy, E39/E40 analysis/runner/deploy. The E38v2 confirmatory run reused the preliminary venvs unchanged (deviation R12), so their freezes are identical. |
| `setup_python_env.sh` / `setup_python_env.ps1` | Create a fresh Python 3.12 venv from `requirements-reproduce.txt` (Linux/macOS, Windows) |

Tested: on 2026-09-26/27 a fresh venv was created on the experiment VM (Ubuntu 24.04, Python 3.12.3) only from `requirements-reproduce.txt`, with `pip install` from PyPI in about 40 s. L0-L2 then ran successfully (`scripts/TEST_REPORT.md`). Other Python versions and operating systems were not tested. The Windows PowerShell checksum script (`scripts/L0_verify_checksums.ps1`) was tested on Windows 11 without Python.
