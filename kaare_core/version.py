"""
KTSB version string.

Git tags are the only source of truth (no VERSION file in the repo). The Docker build passes the
tag in as KTSB_VERSION (see Dockerfile + .github/workflows/build.yml); a plain checkout reports
"dev". Used where KTSB identifies itself, e.g. the web User-Agent (P121 Phase 1).
"""

import os


def get_version() -> str:
    return (os.environ.get("KTSB_VERSION") or "").strip() or "dev"
