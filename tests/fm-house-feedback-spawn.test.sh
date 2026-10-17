#!/usr/bin/env bash
# Drive the real spawn guard with fixture endpoints; no vendor liveness claim.
set -euo pipefail
# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"
TMP_ROOT=$(fm_test_tmproot fm-house-feedback-spawn)
home="$TMP_ROOT/home"
project="$TMP_ROOT/project"
wt="$TMP_ROOT/wt"
fm_test_spawn_home "$home" pi
fm_git_worktree "$project" "$wt" fixture-house
fakebin=$(fm_test_make_spawn_fakebin "$TMP_ROOT/tools")
printf '#!/bin/sh\necho "Pi options: --help --approve --tui-mode"\n' > "$fakebin/pi"
chmod +x "$fakebin/pi"
printf 'manual\n' > "$home/config/backlog-backend"
fm_test_tasks_toml "$home/.tasks.toml"
export TEST_HOUSE_HOME="$home" TEST_HOUSE_PROJECT="$project" TEST_HOUSE_ROOT="$ROOT"
python3 - <<'PY'
import hashlib, hmac, json, os, pathlib, subprocess
home = pathlib.Path(os.environ['TEST_HOUSE_HOME'])
project = os.environ['TEST_HOUSE_PROJECT']
root = pathlib.Path(os.environ['TEST_HOUSE_ROOT'])
key = b'test-key-not-a-customer-secret-1234567890'
(home / 'config/key').write_bytes(key)
(home / 'config/key').chmod(0o600)
(home / 'config/house-feedback.json').write_text(json.dumps(dict(enabled=True, primary='pi', producer='fixture', origin='fixture', project=project, key_file=str(home / 'config/key'))))
env = dict(os.environ, FM_HOME=str(home))
for name in ('FM_TASK_INBOX', 'FM_SUPERVISION_ACTOR', 'FM_STATE_OVERRIDE', 'FM_DATA_OVERRIDE', 'FM_CONFIG_OVERRIDE'):
    env.pop(name, None)
request = '11111111-1111-4111-8111-111111111111'
p = dict(version=1, producer='fixture', origin='fixture', action='implement', request_id=request, body='Change synthetic label', reply_to=None, child=None)
envelope = dict(payload=p, signature=hmac.new(key, json.dumps(p, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest())
def call(*args, body=None):
    result = subprocess.run(['python3', str(root / 'bin/fm-house-feedback.py'), *args], input=json.dumps(body) if body is not None else None, text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
call('accept', body=envelope)
call('plan', request, body=[dict(key='label', scope='Change synthetic label', depends_on=[])])
task = call('reserve', request, 'label')['task']
(home / 'task').write_text(task)
# Manual backlog parser's current durable row format.
(home / 'data/backlog.md').write_text('# Backlog\n\n## Queued\n\n- [ ] ' + task + ' - Change synthetic label\n')
PY
id=$(< "$home/task")
fm_test_spawn_brief "$home" "$id" 'Change synthetic label'
# The attended branch reaches the REAL fresh-spawn path without an away
# record or primary cooperation, still subject to the ordinary queued gate.
if ! out=$(FM_SUPERVISION_ACTOR=branch FM_SUPERVISION_PRIMARY_HARNESS=pi fm_test_run_spawn "$home" "$wt" "$fakebin" "$id" "$project" --harness pi --mode local-only --yolo off); then
  printf '%s\n' "$out" >&2
  exit 1
fi
printf '%s\n' "$out" | grep -q "spawned $id"
[ -f "$home/state/$id.meta" ]
if out=$(fm_test_run_spawn "$home" "$wt" "$fakebin" "$id" "$project" --harness pi --mode local-only --yolo off); then
  echo 'FAIL: consumed House request spawned twice' >&2
  exit 1
fi
printf '%s\n' "$out" | grep -q 'launch outcome uncertain or already launched'
printf 'PASS: real spawn entrypoint consumes authenticated binding once; unknown/repeated launch is refused\n'
