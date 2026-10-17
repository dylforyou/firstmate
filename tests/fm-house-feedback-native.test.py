#!/usr/bin/env python3
"""Opt-in native House intake proof; all Herdr calls use the guarded lab.

Run FM_HOUSE_FEEDBACK_NATIVE=1 python3 tests/fm-house-feedback-native.test.py.
Temporary homes and real treehouse allocations stay inside this checkout.
Transcripts survive under data/house-feedback-auto-dispatch/native-evidence.
No default session, installed home, device, or pipeline is operated on.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.environ.get("FM_HOUSE_FEEDBACK_NATIVE") != "1":
        print("SKIP: set FM_HOUSE_FEEDBACK_NATIVE=1 (real model tokens)")
        return
    def interrupted(signum, frame):
        raise RuntimeError(f"native test interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    lab = Path(tempfile.mkdtemp(prefix=".house-native-", dir=ROOT))
    evidence = ROOT / "data/house-feedback-auto-dispatch/native-evidence" / lab.name
    evidence.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FM_", "HERDR_", "PI_")) and k not in ("TMUX", "BASH_ENV", "ENV")}
    env["PATH"] = "/Users/dylan/.nvm/versions/node/v24.16.0/bin:" + env["PATH"]
    original_path = env["PATH"]
    for directory in ("tmp", "bookkeeping", "tools", "home/config", "home/data", "home/state", "home/projects/toy", "sessions"):
        (lab / directory).mkdir(parents=True)
    env.update(TMPDIR=str(lab / "tmp"), FM_HERDR_LAB_STATE_DIR=str(lab / "bookkeeping"))
    helper = ROOT / "bin/fm-herdr-lab.sh"
    log = (evidence / "commands.log").open("w")

    def run(args, *, cwd=ROOT, input=None, check=True):
        log.write("$ " + shlex.join(map(str, args)) + "\n")
        log.flush()
        result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log.write(result.stdout + f"\nexit={result.returncode}\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError(result.stdout)
        return result.stdout

    session = run([helper, "name", "house-feedback"]).strip()

    def herdr(*args):
        return run([helper, "run", session, *args])

    home = lab / "home"
    project = home / "projects/toy"
    provisioned = False
    try:
        run(["git", "init", "-q", project])
        # init resolves the repository root: create the disposable repo first.
        run(["treehouse", "init"], cwd=project)
        config = project / "treehouse.toml"
        generated = tomllib.loads(config.read_text())
        assert generated.get("root") == "", "unexpected allocator config; refuse guessing"
        config.write_text(f'max_trees = {generated["max_trees"]}\nroot = {json.dumps(str(lab))}\n')
        (evidence / "treehouse.toml").write_text(config.read_text())
        (project / "label.txt").write_text("Before\n")
        run(["git", "add", "."], cwd=project)
        run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"], cwd=project)
        run(["treehouse", "status"], cwd=project)
        # Source links retain current-source extensions and production owners.
        for name in ("bin", ".pi", ".agents", "docs", "AGENTS.md", ".tasks.toml"):
            (home / name).symlink_to(ROOT / name, target_is_directory=(ROOT / name).is_dir())
        (home / "config/backend").write_text("herdr\n")
        (home / "config/crew-harness").write_text("pi\n")
        (home / "config/backlog-backend").write_text("manual\n")
        (home / "data/projects.md").write_text("# Projects\n\n- toy [local-only] - disposable label fixture\n")
        (home / "data/backlog.md").write_text("# Backlog\n\n## Queued\n")
        key = os.urandom(32)
        keyfile = home / "config/fixture-key"
        keyfile.write_bytes(key)
        keyfile.chmod(0o600)
        (home / "config/house-feedback.json").write_text(json.dumps(dict(enabled=True, primary="pi", producer="fixture", origin="fixture", project=str(project), key_file=str(keyfile))))
        # Route production adapter calls through the same guard, never a fake backend.
        wrapper = lab / "tools/herdr"
        wrapper.write_text("#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\ns=" + repr(session) + "\nif '--session' in a:\n i=a.index('--session')\n assert a[i+1]==s\n del a[i:i+2]\nassert not any(x.startswith('--session=') for x in a)\nos.environ['PATH']=" + repr(original_path) + "\nos.execv(" + repr(str(helper)) + ", [" + repr(str(helper)) + ", 'run', s, *a])\n")
        wrapper.chmod(0o755)
        run([helper, "provision", session])
        provisioned = True
        # Explicit scoped environment for the private primary and its descendants.
        created = json.loads(herdr("workspace", "create", "--cwd", str(home), "--label", "house-primary", "--no-focus"))
        pane = created["result"]["root_pane"]["pane_id"]
        scoped = dict(FM_HOME=str(home), HERDR_SESSION=session, PATH=str(lab / "tools") + ":" + original_path, TMPDIR=str(lab / "tmp"), FM_HERDR_LAB_STATE_DIR=str(lab / "bookkeeping"), PI_CODING_AGENT_SESSION_DIR=str(lab / "sessions"))
        prompt = ("This is a bounded private native validation lab. Operate only this FM_HOME and its toy project; no remote publication, installs, pipelines, other homes or model/config changes. "
                  "Use Pi openai-codex/gpt-6-astra medium for one House feedback worker when its authenticated inbox arrives. Implement only the requested label edit, no validation pipeline. "
                  "First complete normal startup and arm the Pi watcher. Then execute one foreground bash command: touch " + shlex.quote(str(lab / "busy-entered")) + "; sleep 240. Do not handle feedback on MAIN during that sleep; the supervision branch must handle the independent request.")
        command = shlex.join(["exec", "env", *[f"{k}={v}" for k, v in scoped.items()], "nice", "-n", "10", "pi", "--approve", "--session-dir", str(lab / "sessions"), "--model", "openai-codex/gpt-6-astra", "--thinking", "medium", prompt])
        launch = lab / "primary.sh"
        launch.write_text("#!/usr/bin/env bash\n" + command + "\n")
        # Short delivery avoids terminal canonical-input truncation during shell startup.
        time.sleep(2)
        herdr("pane", "run", pane, shlex.join(["bash", str(launch)]))
        deadline = time.monotonic() + 180
        while not (lab / "busy-entered").exists():
            run([helper, "run", session, "pane", "read", pane, "--format", "text"], check=False)
            if time.monotonic() >= deadline:
                raise RuntimeError("native primary never entered the busy command; inspect commands.log")
            time.sleep(5)
        request = str(uuid.uuid4())
        payload = dict(version=1, producer="fixture", origin="fixture", action="implement", request_id=request, body="Change label.txt from Before to After in this disposable toy project. Only edit that file; no pipeline, push, merge or installation.", reply_to=None, child=None)
        envelope = dict(payload=payload, signature=hmac.new(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest())
        env["FM_HOME"] = str(home)
        run(["python3", ROOT / "bin/fm-house-feedback.py", "accept"], input=json.dumps(envelope))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            projection = json.loads(run(["python3", ROOT / "bin/fm-house-feedback.py", "show", request]))
            (evidence / "projection.json").write_text(json.dumps(projection, indent=2))
            if any(c.get("started_at") for c in projection["children"].values()):
                herdr("agent", "get", pane)
                allocations = list((lab / ".treehouse").rglob("label.txt"))
                assert allocations, "no supported worktree-local allocation"
                assert any(p.read_text().strip() == "After" for p in allocations), "worker did not execute the edit"
                print("PASS: native independent worker executed after authenticated intake")
                raise RuntimeError("first milestone only: four remaining acceptance cases not yet implemented")
            time.sleep(5)
        raise RuntimeError("no observed native worker start during the busy-primary window")
    finally:
        # Preserve owned runtime evidence before guarded teardown; never repeat old cleanup.
        if (home / "state").exists():
            shutil.copytree(home / "state", evidence / "state", dirs_exist_ok=True)
        shutil.copytree(lab / "sessions", evidence / "sessions", dirs_exist_ok=True)
        if provisioned:
            run([helper, "teardown", session])
        shutil.rmtree(lab)
        log.close()


if __name__ == "__main__":
    main()
