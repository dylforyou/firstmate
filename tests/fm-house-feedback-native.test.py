#!/usr/bin/env python3
"""Opt-in native House intake proof; all Herdr calls use the guarded lab.

Run FM_HOUSE_FEEDBACK_NATIVE=1 python3 tests/fm-house-feedback-native.test.py.
Temporary homes and real treehouse allocations stay inside this checkout.
Set FM_HOUSE_FEEDBACK_EVIDENCE to the approved evidence directory for transcripts.
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
    evidence = Path(os.environ["FM_HOUSE_FEEDBACK_EVIDENCE"]) / lab.name
    evidence.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FM_", "HERDR_", "PI_")) and k not in ("TMUX", "BASH_ENV", "ENV")}
    env["PATH"] = "/Users/dylan/.nvm/versions/node/v24.16.0/bin:" + env["PATH"]
    original_path = env["PATH"]
    for directory in ("tmp", "bookkeeping", "tools", "sessions"):
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
        run([ROOT / "bin/fm-lab-home.sh", "create", home])
        project.mkdir(parents=True)
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
        for name in ("bin", ".pi", ".agents", "docs", "AGENTS.md"):
            (home / name).symlink_to(ROOT / name, target_is_directory=(ROOT / name).is_dir())
        # Admission requires configuration owned by this home, not a code-root link.
        shutil.copyfile(ROOT / ".tasks.toml", home / ".tasks.toml")
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
        # A transport barrier delays Enter, never authorization or allocation.
        # It forwards the exact production source command through the lab guard.
        wrapper.write_text('''#!/usr/bin/env python3
import json, os, pathlib, shlex, subprocess, sys, time
a = sys.argv[1:]
s = SESSION
if '--session' in a:
    i = a.index('--session')
    assert a[i + 1] == s
    del a[i:i + 2]
assert not any(x.startswith('--session=') for x in a)
os.environ['PATH'] = ORIGINAL_PATH
helper = HELPER
barrier = pathlib.Path(BARRIER)
command = [helper, 'run', s, *a]
if barrier.exists() and a[:2] == ['pane', 'send-text']:
    control = json.loads(barrier.read_text())
    if control['task_prefix'] in a[-1] and 'launch.' in a[-1]:
        subprocess.run([helper, 'run', s, 'pane', 'run', a[2], 'exec 2>' + shlex.quote(control['trace']) + '; set -x'], check=True)
        subprocess.run(command, check=True)
        pathlib.Path(control['pending']).write_text(json.dumps({'pane': a[2], 'command': a[-1]}))
        deadline = time.monotonic() + 90
        while not pathlib.Path(control['release']).exists():
            if time.monotonic() > deadline:
                raise SystemExit('transport barrier timed out; no Enter sent')
            time.sleep(.1)
        raise SystemExit(0)
os.execv(helper, command)
'''.replace('SESSION', repr(session)).replace('ORIGINAL_PATH', repr(original_path)).replace('HELPER', repr(str(helper))).replace('BARRIER', repr(str(lab / 'barrier.json'))))
        wrapper.chmod(0o755)
        run([helper, "provision", session])
        provisioned = True
        # Explicit scoped environment for the private primary and its descendants.
        created = json.loads(herdr("workspace", "create", "--cwd", str(home), "--label", "house-primary", "--no-focus"))
        pane = created["result"]["root_pane"]["pane_id"]
        scoped = dict(FM_HOME=str(home), HERDR_SESSION=session, PATH=str(lab / "tools") + ":" + original_path, TMPDIR=str(lab / "tmp"), FM_HERDR_LAB_STATE_DIR=str(lab / "bookkeeping"), PI_CODING_AGENT_SESSION_DIR=str(lab / "sessions"))
        prompt = ("This is a bounded private native validation lab. Operate only this FM_HOME and its toy project; no remote publication, installs, pipelines, other homes or model/config changes. "
                  "Use Pi openai-codex/gpt-6-astra medium for each of at most three sequential independent House feedback requests when its authenticated inbox arrives. Only the first worker will execute; the two later launches test revoked opt-in at delivery. Do not retry refused launches. Implement only the requested label edit, no validation pipeline. "
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
        payload = dict(version=1, producer="fixture", origin="fixture", action="implement", request_id=request, body="Split this bounded request into child label (change label.txt from Before to After) and child dependent (verify the label only after label is Ready/Installed), with dependent depending on label. Only edit label.txt in this disposable project; no pipeline, push, merge or installation.", reply_to=None, child=None)
        envelope = dict(payload=payload, signature=hmac.new(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest())
        env["FM_HOME"] = str(home)
        run(["python3", ROOT / "bin/fm-house-feedback.py", "accept"], input=json.dumps(envelope))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            herdr("pane", "read", pane, "--format", "text")
            projection = json.loads(run(["python3", ROOT / "bin/fm-house-feedback.py", "show", request]))
            (evidence / "projection.json").write_text(json.dumps(projection, indent=2))
            if any(c.get("started_at") for c in projection["children"].values()):
                herdr("agent", "get", pane)
                allocations = list((lab / ".treehouse").rglob("label.txt"))
                assert allocations, "no supported worktree-local allocation"
                if not any(p.read_text().strip() == "After" for p in allocations):
                    time.sleep(5)
                    continue
                assert time.time() < (lab / 'busy-entered').stat().st_mtime + 240, 'MAIN busy window expired'
                allocation_evidence = []
                for meta_path in (home / 'state').glob('*.meta'):
                    meta = dict(line.split('=', 1) for line in meta_path.read_text().splitlines() if '=' in line)
                    if 'worktree' in meta:
                        allocated = Path(meta['worktree']).resolve()
                        assert allocated.is_relative_to(lab), 'allocation escaped lab'
                        allocation_evidence.append(dict(task=meta_path.stem, worktree=str(allocated), label=(allocated / 'label.txt').read_text()))
                (evidence / 'native-allocation.json').write_text(json.dumps(allocation_evidence, indent=2))
                print("PASS: native independent worker executed after authenticated intake")
                report_deadline = time.monotonic() + 90
                while True:
                    store = home / 'state/branch-outcomes.jsonl'
                    outcomes = [json.loads(line) for line in store.read_text().splitlines()] if store.exists() else []
                    reports = [row for row in outcomes if projection['receipt'] in row['wake']]
                    queue = home / 'state/.wake-queue'
                    pending_wakes = queue.read_text() if queue.exists() else ''
                    if reports and 'inbox:' + projection['receipt'] not in pending_wakes:
                        bound_tasks = {child['task'] for child in projection['children'].values()}
                        assert all(row['task'] == 'fleet' or row['task'] in bound_tasks for row in reports)
                        (evidence / 'intake-outcomes.json').write_text(json.dumps(reports, indent=2))
                        print('PASS: intake outcome persisted and wake acknowledged')
                        break
                    if time.monotonic() >= report_deadline:
                        raise RuntimeError('intake lacks durable receipt-linked outcome or wake acknowledgement')
                    time.sleep(2)
                # Signed synthetic manifests exercise the production projection owner;
                # they are not claims about physical installation on a phone.
                child_key, child = next((k, c) for k, c in projection["children"].items() if c.get("started_at"))
                commit = run(["git", "rev-parse", "HEAD"], cwd=project).strip()
                for update_id, stages in (("installed-history", ("checking", "ready", "installed")), ("later-combined", ("checking",))):
                    for stage in stages:
                        update = dict(version=1, producer="fixture", origin="fixture", action=stage,
                                      update_id=update_id, source_commit=commit,
                                      artifact_sha256=hashlib.sha256(update_id.encode()).hexdigest(),
                                      proof_sha256=hashlib.sha256((update_id + stage).encode()).hexdigest(),
                                      includes=[dict(request_id=request, child=child_key, revision=child["revision"])])
                        signed = dict(payload=update, signature=hmac.new(key, json.dumps(update, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest())
                        run(["python3", ROOT / "bin/fm-house-feedback.py", "update"], input=json.dumps(signed))
                history = json.loads(run(["python3", ROOT / "bin/fm-house-feedback.py", "show", request]))
                assert history["children"][child_key]["stage"] == "installed"
                assert not history["fully_installed"], "dependent child is not installed"
                assert {u["update_id"]: u["stage"] for u in history["updates"]} == {"installed-history": "installed", "later-combined": "checking"}
                (evidence / "installed-history.json").write_text(json.dumps(history, indent=2))
                print("PASS: historical Installed survives later Checking")
                dependent = json.loads(run(["python3", ROOT / "bin/fm-house-feedback.py", "reserve", request, "dependent"]))
                assert dependent["depends_on"] == [child_key]
                assert dependent["attempt"]["reserved_at"]
                (evidence / "dependent-reservation.json").write_text(json.dumps(dependent, indent=2))
                print("PASS: dependent reservation during later Checking")
                for allowlist in (False, True):
                    mode = "allowlist" if allowlist else "inherited"
                    allowfile = home / "config/launch-env-allowlist"
                    if allowlist:
                        allowfile.write_text("PATH\nHOME\n")
                    elif allowfile.exists():
                        allowfile.unlink()
                    revoke_request = str(uuid.uuid4())
                    pending, release = lab / (mode + '-pending'), lab / (mode + '-release')
                    (lab / 'barrier.json').write_text(json.dumps(dict(task_prefix='hf-' + revoke_request.replace('-', ''), pending=str(pending), release=str(release), trace=str(lab / (mode + '-trace.txt')))))
                    p = dict(version=1, producer='fixture', origin='fixture', action='implement', request_id=revoke_request,
                             body='Independent bounded request: change label.txt to RevocationProbe. Only that file; no pipeline, push, merge, installation or other operations.', reply_to=None, child=None)
                    signed = dict(payload=p, signature=hmac.new(key, json.dumps(p, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest())
                    run(['python3', ROOT / 'bin/fm-house-feedback.py', 'accept'], input=json.dumps(signed))
                    deadline = time.monotonic() + 360
                    while not pending.exists():
                        if time.monotonic() > deadline:
                            raise RuntimeError('no staged native launch reached barrier: ' + mode)
                        time.sleep(2)
                    target = json.loads(pending.read_text())
                    config_path = home / 'config/house-feedback.json'
                    cfg = json.loads(config_path.read_text())
                    cfg['enabled'] = False
                    config_path.write_text(json.dumps(cfg))
                    release.touch()
                    time.sleep(8)
                    pane_text = herdr('pane', 'read', target['pane'], '--format', 'text')
                    (evidence / (mode + '-revoked-pane.txt')).write_text(pane_text)
                    transcript = (lab / (mode + '-trace.txt')).read_text()
                    (evidence / (mode + '-revoked-trace.txt')).write_text(transcript)
                    assert 'disabled' in transcript or 'opt-in' in transcript or 'not enabled' in transcript, transcript
                    # Trace is actual shell execution output, not source inspection.
                    assert '+ export ' not in transcript and '> export ' not in transcript, 'revoked launch executed exports'
                    info = herdr('pane', 'process-info', '--pane', target['pane'])
                    (evidence / (mode + '-process-info.json')).write_text(info)
                    assert not any(p.read_text().strip() == 'RevocationProbe' for p in (lab / '.treehouse').rglob('label.txt'))
                    cfg['enabled'] = True
                    config_path.write_text(json.dumps(cfg))
                    revoked = json.loads(run(['python3', ROOT / 'bin/fm-house-feedback.py', 'show', revoke_request]))
                    assert not any(c.get('started_at') for c in revoked['children'].values())
                    (evidence / (mode + '-revoked-projection.json')).write_text(json.dumps(revoked, indent=2))
                    (lab / 'barrier.json').unlink()
                    print('PASS: staged revocation ' + mode)
                return
            time.sleep(5)
        raise RuntimeError("no observed native worker start during the busy-primary window")
    finally:
        # Preserve owned runtime evidence before guarded teardown; never repeat old cleanup.
        if (home / "state").exists():
            shutil.copytree(home / "state", evidence / "state", dirs_exist_ok=True)
        if (home / "data").exists():
            shutil.copytree(home / "data", evidence / "data", dirs_exist_ok=True)
        shutil.copytree(lab / "sessions", evidence / "sessions", dirs_exist_ok=True)
        if provisioned:
            run([helper, "teardown", session])
        # Spawn deliberately seals hook directories; unseal only this owned lab.
        for directory, _, _ in os.walk(lab):
            os.chmod(directory, 0o700)
        shutil.rmtree(lab)
        # Treehouse adds its local allocation root to the enclosing gitignore.
        ignore = ROOT / '.gitignore'
        entry = '/' + lab.name + '/.treehouse'
        ignore.write_text(''.join(line for line in ignore.read_text().splitlines(keepends=True) if line.strip() != entry))
        log.close()


if __name__ == "__main__":
    main()
