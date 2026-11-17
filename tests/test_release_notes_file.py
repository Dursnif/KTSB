"""Release notes from docs/release-notes/<tag>.txt (v1.11.1 part 7).

The GitHub release was created with --generate-notes only, so the GUI's update panel showed just «Full Changelog: …».
Now the «Publish release assets» step uses `--notes-file docs/release-notes/<tag>.txt` when that file exists; an rc tag
(vX.Y.Z-rcN) uses its own file if there is one, else the final version's file (testers read what the release will
say); with no file it falls back to --generate-notes. TEMPLATE.txt is never read (the name is built from the tag).

The step's real shell script is run here with a fake `gh` that records its arguments.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _publish_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == "Publish release assets":
                return step["run"]
    raise AssertionError("no «Publish release assets» step")


def _run(tmp_path: Path, tag: str, files: dict[str, str]) -> list[str]:
    work = tmp_path / "repo"
    (work / "docs" / "release-notes").mkdir(parents=True)
    for name, text in files.items():
        (work / "docs" / "release-notes" / name).write_text(text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text(f'#!/usr/bin/env bash\n[ "$1 $2" = "release view" ] && exit 1\necho "$*" >> "{log}"\nexit 0\n')
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GITHUB_REF_NAME": tag, "GH_TOKEN": "x"}
    r = subprocess.run(["bash", "-c", _publish_script()], cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return [line for line in log.read_text().splitlines() if line.startswith("release create")]


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash missing")


def test_tag_with_its_own_notes_file(tmp_path):
    (create,) = _run(tmp_path, "v1.11.1", {"v1.11.1.txt": "Fixed: …", "TEMPLATE.txt": "template"})
    assert "--notes-file docs/release-notes/v1.11.1.txt" in create and "--generate-notes" not in create


def test_rc_uses_the_final_versions_notes_when_it_has_none(tmp_path):
    (create,) = _run(tmp_path, "v1.11.1-rc1", {"v1.11.1.txt": "Fixed: …"})
    assert "--notes-file docs/release-notes/v1.11.1.txt" in create and "--prerelease" in create


def test_rc_with_its_own_file_uses_that(tmp_path):
    (create,) = _run(tmp_path, "v1.11.1-rc2", {"v1.11.1.txt": "final", "v1.11.1-rc2.txt": "rc text"})
    assert "--notes-file docs/release-notes/v1.11.1-rc2.txt" in create


def test_no_file_falls_back_to_generated_notes_and_never_the_template(tmp_path):
    (create,) = _run(tmp_path, "v1.12.0", {"TEMPLATE.txt": "template"})
    assert "--generate-notes" in create and "TEMPLATE" not in create and "--notes-file" not in create
