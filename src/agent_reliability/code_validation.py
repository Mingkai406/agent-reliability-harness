"""Validate candidate diffs using trusted checks in disposable Docker containers.

Repository, image and commands are supplied by the evaluator, never by the patch.
The host only reads Git objects and applies text diffs; candidate code runs in Docker.
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from .contracts import Assessment, Execution

REQUIRED = {"build", "types", "unit", "integration", "static"}


def command(args, *, cwd=None, timeout=20):
    with tempfile.TemporaryFile() as log:
        try:
            result = subprocess.run(args, cwd=cwd, stdout=log, stderr=log, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        log.seek(0)
        return {"returncode": code, "output": log.read(16000).decode(errors="replace")}


def patch_paths(patch):
    """Only ordinary text modifications; no renames, links, modes or binary payloads."""
    if len(patch.encode()) > 1_000_000:
        raise ValueError("Patch exceeds size budget")
    forbidden = (
        "new file mode",
        "deleted file mode",
        "old mode",
        "new mode",
        "rename ",
        "copy ",
        "GIT binary",
        "Binary files",
    )
    paths = []
    for line in patch.splitlines():
        if line.startswith(forbidden):
            raise ValueError("Only text modifications to existing files are supported")
        if line.startswith("--- ") or line.startswith("+++ "):
            value = line[4:]
            if not value.startswith(("a/", "b/")):
                raise ValueError("Expected ordinary a/ and b/ diff paths")
            path = value[2:]
            if not path or "\\" in path or any(c.isspace() for c in path):
                raise ValueError("Unsupported patch path")
            if PurePosixPath(path).is_absolute() or any(
                p in {".", "..", ".git"} for p in path.split("/")
            ):
                raise ValueError("Unsafe patch path")
            paths.append(path)
    if not paths or len(paths) % 2 or any(a != b for a, b in zip(paths[::2], paths[1::2])):
        raise ValueError("Patch must modify existing paths without renaming")
    return set(paths)


class CodeValidationAdapter:
    """Configure in a trusted plugin factory; suite JSON only selects the candidate diff."""

    def __init__(
        self,
        repository,
        patches,
        checks,
        *,
        image="harness-code-validator:local",
        allowed=("src/",),
        timeout=10,
    ):
        self.repository = Path(repository).resolve()
        self.patches = dict(patches)
        self.checks = dict(checks)
        self.image, self.allowed, self.timeout = image, tuple(allowed), timeout
        if set(self.checks) != REQUIRED or any(
            not isinstance(v, list) or not v or any(not isinstance(s, str) for s in v)
            for v in self.checks.values()
        ):
            raise ValueError("Exactly five trusted command lists are required")
        if not 0 < timeout <= 15:
            raise ValueError("Check timeout must be in (0, 15]")

    def validate(self, case):
        if set(case.config) != {"candidate"} or case.config["candidate"] not in self.patches:
            raise ValueError("Select a configured candidate")
        if case.faults:
            raise ValueError("Code validation uses candidate fixtures, not tool fault schedules")

    async def execute(self, case, directory, faults, tracer):
        return await asyncio.to_thread(self._execute, case, directory, tracer)

    def _execute(self, case, directory, tracer):
        patch = self.patches[case.config["candidate"]]
        snapshot = {
            "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            "gates": {},
            "policy_passed": False,
            "image": self.image,
            "commands": self.checks,
            "allowed_prefixes": list(self.allowed),
            "timeout_seconds": self.timeout,
        }
        workspace = directory / "workspace"
        workspace.mkdir()
        # Read committed files only: no host .env, ignored files, Git hooks or credentials.
        base = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repository, timeout=10)
            .decode()
            .strip()
        )
        snapshot["base_commit"] = base
        tree = subprocess.check_output(
            ["git", "ls-tree", "-rz", base], cwd=self.repository, timeout=10
        )
        for record in tree.split(b"\0"):
            if not record:
                continue
            meta, filename = record.split(b"\t", 1)
            mode, kind, oid = meta.split()
            if mode not in {b"100644", b"100755"} or kind != b"blob":
                raise ValueError("Source snapshot must contain regular files only")
            target = workspace / filename.decode()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.check_output(
                    ["git", "cat-file", "blob", oid.decode()], cwd=self.repository, timeout=10
                )
            )
        before = {
            str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob("*")
            if p.is_file()
        }
        try:
            paths = patch_paths(patch)
            if any(not p.startswith(self.allowed) or p not in before for p in paths):
                raise ValueError("Candidate changes a protected or unknown path")
            patch_file = directory / "candidate.patch"
            patch_file.write_text(patch)
            initialized = command(["git", "init", "--quiet"], cwd=workspace)
            if initialized["returncode"]:
                raise RuntimeError("Unable to prepare patch workspace")
            checked = command(["git", "apply", "--check", str(patch_file.resolve())], cwd=workspace)
            if checked["returncode"]:
                raise ValueError("Patch cannot be applied cleanly")
            applied = command(["git", "apply", str(patch_file.resolve())], cwd=workspace)
            if applied["returncode"]:
                raise ValueError("Patch application failed")
            shutil.rmtree(workspace / ".git")
            changed = {
                str(p.relative_to(workspace))
                for p in workspace.rglob("*")
                if p.is_file()
                and hashlib.sha256(p.read_bytes()).hexdigest()
                != before.get(str(p.relative_to(workspace)))
            }
            if changed != paths:
                raise ValueError("Actual modifications differ from declared diff")
            snapshot["policy_passed"] = True
        except ValueError as exc:
            snapshot["rejection"] = str(exc)
            return Execution({"status": "rejected"}, snapshot, "Git patch policy validation")
        # Resolve the image before accepting any result; missing infrastructure is an error.
        image = (
            subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", self.image], timeout=15
            )
            .decode()
            .strip()
        )
        snapshot["image_id"] = image
        staging = "harness-stage-" + uuid.uuid4().hex
        candidate_image = "harness-candidate-" + uuid.uuid4().hex
        try:
            created = command(["docker", "create", "--name", staging, image, "true"])
            if created["returncode"]:
                raise RuntimeError("Snapshot container creation failed")
            copied = command(["docker", "cp", str(workspace) + "/.", staging + ":/workspace"])
            if copied["returncode"]:
                raise RuntimeError("Snapshot copy failed")
            committed = command(["docker", "commit", staging, candidate_image])
            if committed["returncode"]:
                raise RuntimeError("Snapshot image creation failed")
        finally:
            command(["docker", "rm", "--force", staging])
        try:
            return self._check_candidate(case, snapshot, candidate_image, tracer)
        finally:
            command(["docker", "image", "rm", candidate_image])

    def _check_candidate(self, case, snapshot, image, tracer):
        # Each gate gets a fresh container and an immutable evaluator-owned command.
        # A failing gate cannot alter the tests for the next gate.
        for name, argv in self.checks.items():
            container = "harness-check-" + uuid.uuid4().hex
            try:
                with tracer.start_as_current_span("code_validation." + name):
                    created = command(
                        [
                            "docker",
                            "create",
                            "--name",
                            container,
                            "--network",
                            "none",
                            "--read-only",
                            "--cap-drop",
                            "ALL",
                            "--security-opt",
                            "no-new-privileges",
                            "--pids-limit",
                            "64",
                            "--memory",
                            "256m",
                            "--cpus",
                            "1",
                            "--user",
                            "65534:65534",
                            "--tmpfs",
                            "/tmp:rw,noexec,nosuid,size=32m",
                            "--workdir",
                            "/workspace",
                            "--env",
                            "PYTHONDONTWRITEBYTECODE=1",
                            image,
                            *argv,
                        ]
                    )
                    if created["returncode"]:
                        raise RuntimeError("Container creation failed")
                    result = command(
                        ["docker", "start", "--attach", container], timeout=self.timeout
                    )
                    inspected = command(
                        ["docker", "inspect", "--format", "{{json .State}}", container]
                    )
                    if inspected["returncode"]:
                        raise RuntimeError("Container state unavailable")
                    state = json.loads(inspected["output"])
                    result["timed_out"] = result["returncode"] == 124
                    result["passed"] = (
                        not result["timed_out"]
                        and not state["Running"]
                        and not state["OOMKilled"]
                        and not state["Error"]
                        and state["ExitCode"] == 0
                        and result["returncode"] == 0
                    )
                    result["container_exit_code"] = state["ExitCode"]
                    snapshot["gates"][name] = result
            finally:
                command(["docker", "rm", "--force", container])
        accepted = all(v["passed"] for v in snapshot["gates"].values())
        return Execution(
            {"status": "accepted" if accepted else "rejected"},
            snapshot,
            "Real Docker execution; evaluator-owned checks; no model calls",
        )

    def assess(self, case, execution):
        snapshot, receipt = execution.snapshot, execution.receipt
        gates = snapshot["gates"]
        accepted = (
            snapshot["policy_passed"]
            and set(gates) == REQUIRED
            and all(
                g["passed"]
                and g["returncode"] == 0
                and g["container_exit_code"] == 0
                and not g["timed_out"]
                for g in gates.values()
            )
        )
        rejected = (not snapshot["policy_passed"] and bool(snapshot.get("rejection"))) or (
            set(gates) == REQUIRED and any(not g["passed"] for g in gates.values())
        )
        checks = {
            "receipt_matches_gates": receipt.get("status")
            == ("accepted" if accepted else "rejected"),
            "complete_evidence": bool(accepted or rejected),
        }
        valid = all(checks.values())
        return Assessment(bool(accepted and valid), bool(rejected and valid), checks)
