import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_with_retry.sh"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not (GIT and shutil.which("bash")), reason="needs git and bash")


def run_git(cwd, *args):
    subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_clone(tmp_path):
    """A bare origin plus a clone holding one unpushed commit, like the runner has."""
    origin = tmp_path / "origin.git"
    subprocess.run([GIT, "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run([GIT, "clone", str(origin), str(work)], check=True, capture_output=True)
    for key, value in (("user.email", "bot@example.com"), ("user.name", "bot")):
        run_git(work, "config", key, value)
    (work / "data.json").write_text("{}")
    run_git(work, "add", "data.json")
    run_git(work, "commit", "-m", "first")
    run_git(work, "push", "origin", "main")
    (work / "data.json").write_text('{"links": 1}')
    run_git(work, "add", "data.json")
    run_git(work, "commit", "-m", "today's data")
    return work


def shim(tmp_path, failures):
    """A `git` that fails the first N pushes with a transient error, then behaves normally."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "remaining"
    counter.write_text(str(failures))
    script = bin_dir / "git"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "push" ]; then\n'
        f'  remaining=$(cat "{counter}")\n'
        '  if [ "$remaining" -gt 0 ]; then\n'
        f'    echo $((remaining - 1)) > "{counter}"\n'
        '    echo "remote: Internal Server Error" >&2\n'
        '    exit 1\n'
        "  fi\n"
        "fi\n"
        f'exec {GIT} "$@"\n'
    )
    script.chmod(0o755)
    return bin_dir


def publish(work, bin_dir, attempts=3):
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PUSH_ATTEMPTS": str(attempts),
        "PUSH_BACKOFF_SECONDS": "0",
    }
    return subprocess.run(["bash", str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True, timeout=60)


def published_commit(work):
    remote = subprocess.run([GIT, "ls-remote", "origin", "refs/heads/main"], cwd=work, capture_output=True, text=True, check=True)
    return remote.stdout.split()[0]


def local_commit(work):
    return subprocess.run([GIT, "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True).stdout.strip()


def test_a_clean_push_publishes_immediately(tmp_path):
    work = make_clone(tmp_path)

    result = publish(work, shim(tmp_path, failures=0))

    assert result.returncode == 0, result.stderr
    assert published_commit(work) == local_commit(work)


def test_a_transient_push_error_is_retried_until_it_lands(tmp_path):
    work = make_clone(tmp_path)

    result = publish(work, shim(tmp_path, failures=2))

    # The Sep 15 failure: the work was done, only the push failed.
    assert result.returncode == 0, result.stderr
    assert "Push attempt 1 of 3 failed" in result.stdout
    assert published_commit(work) == local_commit(work)


def test_a_persistent_failure_still_fails_the_step(tmp_path):
    work = make_clone(tmp_path)

    result = publish(work, shim(tmp_path, failures=99))

    assert result.returncode == 1
    assert "Could not publish after 3 attempts" in result.stdout
    assert published_commit(work) != local_commit(work)


def test_a_push_that_raced_with_another_publisher_rebases_and_lands(tmp_path):
    """The retry rebases first, so a push rejected because origin moved still publishes."""
    work = make_clone(tmp_path)
    other = tmp_path / "other"
    subprocess.run([GIT, "clone", str(tmp_path / "origin.git"), str(other)], check=True, capture_output=True)
    for key, value in (("user.email", "other@example.com"), ("user.name", "other")):
        run_git(other, "config", key, value)
    (other / "predictions.json").write_text("{}")
    run_git(other, "add", "predictions.json")
    run_git(other, "commit", "-m", "other publisher")
    run_git(other, "push", "origin", "main")

    result = publish(work, shim(tmp_path, failures=1))

    assert result.returncode == 0, result.stderr
    assert published_commit(work) == local_commit(work)
    assert (work / "predictions.json").exists()  # the other publisher's commit survived
