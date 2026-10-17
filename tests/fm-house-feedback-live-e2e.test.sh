#!/usr/bin/env bash
# Opt-in real Pi/tmux worker-start evidence for the authenticated spawn path.
# Uses a private tmux socket and a toy git worktree. Treehouse allocation and
# unrelated validation queries are fixture-only; Pi and its pane signals are
# real. No live home, device, integration service, install or global config.
set -euo pipefail
# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"
fm_live_gate opt-in FM_HOUSE_FEEDBACK_LIVE_E2E tmux pi
TMP_ROOT=$(fm_test_tmproot fm-house-feedback-live)
home="$TMP_ROOT/home"
project="$TMP_ROOT/project"
wt="$TMP_ROOT/wt"
real_tmux=$(command -v tmux)
real_pi=$(command -v pi)
pi_version=$("$real_pi" --version)
socket="house-feedback-$$"
cleanup_house_live() {
  "$real_tmux" -L "$socket" kill-server 2>/dev/null || true
}
trap cleanup_house_live EXIT
fm_test_spawn_home "$home" pi
fm_test_tasks_toml "$home/.tasks.toml"
fm_git_worktree "$project" "$wt" fixture-house-live
fakebin=$(fm_test_make_spawn_fakebin "$TMP_ROOT/tools")
# The only intercepted lifecycle is the isolated tmux namespace; every Pi
# observation comes from a real process, not fixture terminal output.
printf '#!/bin/sh\nexec "%s" -L "%s" "$@"\n' "$real_tmux" "$socket" > "$fakebin/tmux"
printf '#!/bin/sh\nexec "%s" --session-dir "%s/sessions" "$@"\n' "$real_pi" "$TMP_ROOT" > "$fakebin/pi"
printf '#!/bin/sh\nexit 1\n' > "$fakebin/no-mistakes"
chmod +x "$fakebin/tmux" "$fakebin/pi" "$fakebin/no-mistakes"
# Only this toy shell's allocator changes directory; no real pool/global state.
# shellcheck disable=SC2016 # PATH expands in the isolated pane, not this writer.
printf 'treehouse() { builtin cd "%s"; }\nexport PATH="%s:$PATH"\n' "$wt" "$fakebin" > "$TMP_ROOT/bashrc"
"$fakebin/tmux" new-session -d -s house-fixture -c "$project"
"$fakebin/tmux" set-option -g default-command "bash --noprofile --rcfile '$TMP_ROOT/bashrc'"
printf 'manual\n' > "$home/config/backlog-backend"
printf 'tmux\n' > "$home/config/backend"
export TEST_HOUSE_HOME="$home" TEST_HOUSE_PROJECT="$project" TEST_HOUSE_ROOT="$ROOT"
python3 - <<'PY'
import hashlib, hmac, json, os, pathlib, subprocess, time
home = pathlib.Path(os.environ['TEST_HOUSE_HOME']).resolve()
key = b'DISPOSABLE-LIVE-POLICY-FIXTURE-KEY-123456789'
(home / 'config/key').write_bytes(key)
(home / 'config/key').chmod(0o600)
(home / 'config/house-feedback.json').write_text(json.dumps(dict(enabled=True, primary='pi', producer='fixture', origin='fixture', project=str(pathlib.Path(os.environ['TEST_HOUSE_PROJECT']).resolve()), key_file=str(home / 'config/key'))))
env = {k:v for k,v in os.environ.items() if not k.startswith('FM_') and k not in ('BASH_ENV','ENV')}
env['FM_HOME'] = str(home)
p = dict(version=1, producer='fixture', origin='fixture', action='implement', request_id='22222222-2222-4222-8222-222222222222', body='Run the bounded synthetic worker-start test', reply_to=None, child=None)
e = dict(payload=p, signature=hmac.new(key, json.dumps(p, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest())
def call(*args, body=None):
    return json.loads(subprocess.check_output(['python3', str(pathlib.Path(os.environ['TEST_HOUSE_ROOT']) / 'bin/fm-house-feedback.py'), *args], input=json.dumps(body).encode() if body is not None else None, env=env))
captured_at = time.time()
receipt = call('accept', body=e)
(home / 'timing.json').write_text(json.dumps(dict(captured_at=captured_at, received_at=receipt['received_at'])))
call('plan', p['request_id'], body=[dict(key='start', scope=p['body'], depends_on=[])])
task = call('reserve', p['request_id'], 'start')['task']
(home / 'task').write_text(task)
(home / 'data/backlog.md').write_text('## Queued\n- [ ] ' + task + ' - Bounded synthetic start test\n')
PY
id=$(< "$home/task")
fm_test_spawn_brief "$home" "$id" "This is an isolated worker-start fixture, not project development. Use one bash call to first write only 'working: synthetic worker executed its instructions' to '$home/state/$id.status', then sleep 45 seconds. Do not inspect other files, perform network operations, edit code, run validation, or claim completion. Remain idle after that one command."
# Real tools/auth remain in their normal account; no credentials are inspected
# or copied and no worker-account pin is created or changed.
if ! out=$(env -u FM_TASK_INBOX -u FM_BACKEND -u HERDR_SESSION -u HERDR_PANE_ID \
  FM_HOME="$home" FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
  FM_CONFIG_OVERRIDE="$home/config" FM_SPAWN_NO_GUARD=1 \
  FM_SUPERVISION_ACTOR=branch FM_SUPERVISION_PRIMARY_HARNESS=pi \
  TMUX="$socket,1,0" PATH="$fakebin:$PATH" \
  "$ROOT/bin/fm-spawn.sh" "$id" "$project" --harness pi \
  --model openai-codex/gpt-6-astra --effort medium --mode local-only --yolo off 2>&1); then
  printf '%s\n' "$out" >&2
  exit 1
fi
for _ in $(seq 1 60); do
  if grep -Fxq 'working: synthetic worker executed its instructions' "$home/state/$id.status" 2>/dev/null &&
    out=$(env -u FM_TASK_INBOX FM_HOME="$home" PATH="$fakebin:$PATH" \
    python3 "$ROOT/bin/fm-house-feedback.py" started "$id" 2>&1); then
    printf 'PASS: real Pi worker-start evidence from the guarded attended branch spawn\n'
    printf '%s\n' "$out" | python3 -c 'import json,sys; from pathlib import Path; child=json.load(sys.stdin); timing=json.loads(Path(sys.argv[1]).read_text()); print(json.dumps(dict(timing, task=child["task"], reserved_at=child["attempt"]["reserved_at"], consumed_at=child["attempt"]["consumed_at"], started_at=child["started_at"], spawn_gen=child["spawn_gen"]),sort_keys=True))' "$home/timing.json"
    exit 0
  fi
  sleep 1
done
printf 'FAIL: real Pi %s worker execution/start observation did not become provable: %s\n' "$pi_version" "$out" >&2
exit 1
