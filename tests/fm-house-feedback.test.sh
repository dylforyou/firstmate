#!/usr/bin/env bash
# Synthetic House feedback policy/transaction coverage. No vendor classifier,
# real worker launch, private home, phone, service or install is exercised.
set -euo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP_ROOT=$(fm_test_tmproot fm-house-feedback)
export TEST_FEEDBACK_ROOT="$ROOT" TEST_FEEDBACK_TMP="$TMP_ROOT"
python3 - <<'PY'
import concurrent.futures
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

root = Path(os.environ['TEST_FEEDBACK_ROOT'])
tmp = Path(os.environ['TEST_FEEDBACK_TMP'])
home = tmp / 'home'
for directory in ('state', 'config', 'project', 'bin'):
    (home / directory).mkdir(parents=True)
shutil.copy(root / 'bin/fm-house-feedback.py', home / 'bin/fm-house-feedback.py')
(home / 'bin/fm-inbox.sh').write_text('#!/bin/sh\nexec "' + str(root / 'bin/fm-inbox.sh') + '" "$@"\n')
(home / 'bin/fm-inbox.sh').chmod(0o700)
# Stub only the policy boundary's observation. This is NOT lifecycle proof.
(home / 'bin/fm-crew-state.sh').write_text('#!/bin/sh\nprintf "state: working · source: pane · synthetic active\\n"\n')
(home / 'bin/fm-crew-state.sh').chmod(0o700)
(home / 'bin/fm-send.sh').write_text('#!/bin/sh\nexit 0\n')
(home / 'bin/fm-send.sh').chmod(0o700)
key = b'synthetic-test-producer-key-only-1234567890'
(home / 'config/key').write_bytes(key)
(home / 'config/key').chmod(0o600)
cfg = dict(enabled=True, primary='pi', producer='house-companion', origin='fixture', project=str(home / 'project'), key_file=str(home / 'config/key'))
def configure(**changes):
    (home / 'config/house-feedback.json').write_text(json.dumps(dict(cfg, **changes)))
configure()
env = dict(os.environ, FM_HOME=str(home))
for name in ('FM_TASK_INBOX', 'FM_STATE_OVERRIDE', 'FM_CONFIG_OVERRIDE', 'FM_DATA_OVERRIDE', 'FM_SUPERVISION_ACTOR', 'FM_WAKE_QUEUE', 'FM_WAKE_QUEUE_LOCK'):
    env.pop(name, None)
def sign(payload):
    data = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return dict(payload=payload, signature=hmac.new(key, data, hashlib.sha256).hexdigest())
def request(**fields):
    return sign(dict(version=1, producer='house-companion', origin='fixture', action='implement', request_id=str(uuid.uuid4()), body='Improve the feedback label 🏠', reply_to=None, child=None, **fields))
def call(*args, body=None, ok=True):
    p = subprocess.run(['python3', str(home / 'bin/fm-house-feedback.py'), *args], input=json.dumps(body) if body is not None else None, text=True, capture_output=True, env=env)
    assert (p.returncode == 0) == ok, (args, p.returncode, p.stdout, p.stderr)
    return json.loads(p.stdout) if p.returncode == 0 else p.stderr

a = request()
rid = a['payload']['request_id']
configure(enabled=False)
call('accept', body=a, ok=False)
configure()
for field, value in [('origin', 'wrong'), ('producer', 'wrong'), ('action', 'install'), ('project', '/tmp/other')]:
    invalid = sign(dict(a['payload'], **{field: value}))
    call('accept', body=invalid, ok=False)
invalid = dict(a, signature='0' * 64)
call('accept', body=invalid, ok=False)
# Concurrent wake/receipt retries converge on one immutable inbox binding.
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    receipts = list(pool.map(lambda _: call('accept', body=a), range(4)))
assert len({r['receipt'] for r in receipts}) == 1
assert all(r['announced'] for r in receipts)
assert len((home / 'state/.wake-queue').read_text().splitlines()) == 1
assert len(list((home / 'state/inbox').glob('*.note'))) == 1
call('accept', body=sign(dict(a['payload'], body='Changed body')), ok=False)
plan = [dict(key='capture', scope='Improve feedback capture label', depends_on=[]), dict(key='detail', scope='Improve detail label', depends_on=['capture'])]
bindings = call('plan', rid, body=plan)
assert call('plan', rid, body=plan) == bindings
call('plan', rid, body=[dict(key='different', scope='Other', depends_on=[])], ok=False)
call('reserve', rid, 'detail', ok=False)
reservation = call('reserve', rid, 'capture')
assert call('reserve', rid, 'capture') == reservation
task = reservation['task']
call('check', task, str(home / 'project'), 'local-only', 'off')
call('check', task, str(home / 'project'), 'direct-PR', 'off', ok=False)
call('check', task, '/wrong', 'local-only', 'off', ok=False)
call('authorize', task, str(home / 'project'), 'local-only', 'off')
call('authorize', task, str(home / 'project'), 'local-only', 'off', ok=False)
assert call('show', rid)['children']['capture']['stage'] == 'launch-pending'
call('started', task, ok=False)
(home / 'state' / (task + '.meta')).write_text('project=' + str(home / 'project') + '\nspawn_gen=fixture-incarnation\nwindow=fixture-endpoint\n')
call('started', task)
assert call('show', rid)['children']['capture']['stage'] == 'working'
# Ordered explicit replies; independent new requests retain independent tasks.
r1 = sign(dict(a['payload'], request_id=str(uuid.uuid4()), reply_to=rid, child='capture', body='First detail'))
r2 = sign(dict(a['payload'], request_id=str(uuid.uuid4()), reply_to=rid, child='capture', body='Second detail'))
for reply in (r1, r2):
    call('accept', body=reply)
call('deliver', r2['payload']['request_id'], 'capture', ok=False)
first = call('deliver', r1['payload']['request_id'], 'capture')
assert call('deliver', r1['payload']['request_id'], 'capture') == first
call('deliver', r2['payload']['request_id'], 'capture')
assert call('show', rid)['children']['capture']['revision'] == 3
independent = request()
call('accept', body=independent)
other = call('plan', independent['payload']['request_id'], body=plan)
assert other['capture']['task'] != task
# Signed exact update evidence: immutable manifest, no partial parent Installed.
update = dict(version=1, producer='house-companion', origin='fixture', action='checking', update_id='fixture-update', source_commit='a' * 40, artifact_sha256='b' * 64, proof_sha256='c' * 64, includes=[dict(request_id=rid, child='capture', revision=3)])
call('update', body=sign(dict(update, action='installed')), ok=False)
call('update', body=sign(update))
late = sign(dict(r1['payload'], request_id=str(uuid.uuid4()), body='Revision during checking'))
call('accept', body=late)
call('deliver', late['payload']['request_id'], 'capture', ok=False)
for action in ('ready', 'installed'):
    event = sign(dict(update, action=action))
    call('update', body=event)
    call('update', body=event)
call('update', body=sign(dict(update, action='installed', source_commit='f' * 40)), ok=False)
projection = call('show', rid)
assert projection['children']['capture']['stage'] == 'installed'
assert projection['children']['capture']['desired_revision'] == 4
assert projection['children']['detail']['stage'] == 'queued'
assert projection['children']['capture']['pending_replies'] == [late['payload']['request_id']]
call('deliver', late['payload']['request_id'], 'capture', ok=False)
call('reserve', rid, 'detail', ok=False)  # pending desired dependency still waits
# Late replies become explicitly bounded new work, never revive the old task.
follow_id = late['payload']['request_id']
follow_plan = [dict(key='revision', scope='Apply the late requested revision', depends_on=[])]
call('plan', follow_id, body=follow_plan)
follow_task = call('reserve', follow_id, 'revision')['task']
assert follow_task != task
call('authorize', follow_task, str(home / 'project'), 'local-only', 'off')
(home / 'state' / (follow_task + '.meta')).write_text('project=' + str(home / 'project') + '\nspawn_gen=follow-up-incarnation\nwindow=follow-up-endpoint\n')
call('started', follow_task)
follow_update = dict(update, update_id='follow-up', includes=[dict(request_id=follow_id, child='revision', revision=1)])
for action in ('checking', 'ready', 'installed'):
    call('update', body=sign(dict(follow_update, action=action)))
assert call('show', rid)['children']['capture']['pending_replies'] == []
assert call('show', rid)['fully_installed'] is False  # second original child unfinished
detail_task = call('reserve', rid, 'detail')['task']
call('authorize', detail_task, str(home / 'project'), 'local-only', 'off')
(home / 'state' / (detail_task + '.meta')).write_text('project=' + str(home / 'project') + '\nspawn_gen=detail-incarnation\nwindow=detail-endpoint\n')
call('started', detail_task)
detail_update = dict(update, update_id='detail-update', includes=[dict(request_id=rid, child='detail', revision=1)])
for action in ('checking', 'ready', 'installed'):
    call('update', body=sign(dict(detail_update, action=action)))
assert call('show', rid)['fully_installed'] is True  # every original and follow-up included
# A disappeared endpoint is not permanently advertised as Working.
(home / 'bin/fm-crew-state.sh').write_text('#!/bin/sh\nprintf "state: unknown · source: none · synthetic missing\\n"\n')
call('refresh', follow_task)
assert call('show', follow_id)['children']['revision']['stage'] == 'installed'  # historical inclusion survives
# Simulate a response loss after durable receipt; retry never makes a new note.
path = home / 'state/house-feedback' / (rid + '.json')
saved = json.loads(path.read_text())
saved['receipt'] = None
saved['announced'] = False
path.write_text(json.dumps(saved))
assert call('accept', body=a)['receipt'] == receipts[0]['receipt']
assert len(list((home / 'state/inbox').glob('*.note'))) == 5
# Feature changes are checked again at the action boundary.
other_task = call('reserve', independent['payload']['request_id'], 'capture')['task']
configure(enabled=False)
call('authorize', other_task, str(home / 'project'), 'local-only', 'off', ok=False)
notice = subprocess.run([str(root / 'bin/fm-inbox.sh'), 'list'], capture_output=True, text=True, env=env, check=True)
assert 'Recorded House request ' + rid in notice.stdout  # MAIN keeps the binding after opt-out
configure()
call('authorize', other_task, str(home / 'project'), 'local-only', 'off')
(home / 'state' / (other_task + '.meta')).write_text('project=' + str(home / 'project') + '\nspawn_gen=other-incarnation\nwindow=other-endpoint\n')
(home / 'bin/fm-crew-state.sh').write_text('#!/bin/sh\nprintf "state: working · source: pane · synthetic active\\n"\n')
call('started', other_task)
for observed, expected in [('failed', 'failed'), ('unknown', 'reconciliation-needed'), ('done', 'closed')]:
    (home / 'bin/fm-crew-state.sh').write_text('#!/bin/sh\nprintf "state: ' + observed + ' · source: status-log · synthetic lifecycle\\n"\n')
    call('refresh', other_task)
    assert call('show', independent['payload']['request_id'])['children']['capture']['stage'] == expected
closed_reply = sign(dict(a['payload'], request_id=str(uuid.uuid4()), reply_to=independent['payload']['request_id'], child='capture', body='Bounded follow-up to closed work'))
call('accept', body=closed_reply)
call('deliver', closed_reply['payload']['request_id'], 'capture', ok=False)
closed_plan = call('plan', closed_reply['payload']['request_id'], body=follow_plan)
assert closed_plan['revision']['task'] != other_task
assert call('show', independent['payload']['request_id'])['children']['capture']['pending_replies'] == [closed_reply['payload']['request_id']]
# Legacy receipt is never replayed into automatic authority on enable.
legacy = request()
p = subprocess.run([str(root / 'bin/fm-inbox.sh'), 'note', '--request-id', legacy['payload']['request_id'], '--json', '-'], input=legacy['payload']['body'], text=True, capture_output=True, env=env)
assert p.returncode == 0, p.stderr
call('accept', body=legacy, ok=False)
scoped = request()
scoped_id = 'house-lab-' + 'a' * 32 + '-' + scoped['payload']['request_id']
subprocess.run([str(root / 'bin/fm-inbox.sh'), 'note', '--request-id', scoped_id, '--json', '-'], input=scoped['payload']['body'], text=True, capture_output=True, env=env, check=True)
call('accept', body=scoped, ok=False)
# Export fixture for the actual routing interface below.
(home / 'routing.json').write_text(json.dumps(dict(receipt=receipts[0]['receipt'], request=rid, legacy=json.loads(p.stdout)['id'])))
print('PASS: authenticated receipt, conflicts, concurrent dedup, stable children, reservation uncertainty, ordered replies, exact inclusion and default-off policy')
PY
node --experimental-strip-types --input-type=module - <<'JS'
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
const { branchOfferForWake } = await import(`${process.env.TEST_FEEDBACK_ROOT}/.pi/extensions/lib/fm-branch-dispatch.ts`);
const home = `${process.env.TEST_FEEDBACK_TMP}/home`;
const { receipt, request, legacy } = JSON.parse(readFileSync(`${home}/routing.json`, 'utf8'));
const state = `${home}/state`;
// Replace only the disposable fixture queue with controlled routing rows.
writeFileSync(`${state}/.wake-queue`, `1\t1\tcheck\tinbox:${receipt}\tcheck: captain inbox note ${receipt} - idea\n2\t2\tcheck\tinbox:${legacy}\tcheck: captain inbox note ${legacy} - legacy\n`);
const message = `check: captain inbox note ${receipt} - idea`;
assert.equal(branchOfferForWake(state, message, false).eligible, true);
assert.deepEqual(branchOfferForWake(state, message, false).scope.eligibleSeqs, ['1']);
const recordPath = `${state}/house-feedback/${request}.json`;
const original = readFileSync(recordPath, 'utf8');
const tampered = JSON.parse(original);
tampered.envelope.signature += 'zz';
writeFileSync(recordPath, JSON.stringify(tampered));
assert.equal(branchOfferForWake(state, message, false).eligible, false);
writeFileSync(recordPath, original);
assert.equal(branchOfferForWake(state, message, false, true).eligible, false);
assert.equal(branchOfferForWake(state, `check: captain inbox note ${legacy} - legacy`, false).eligible, false);
assert.equal(branchOfferForWake(state, 'check: credentials needed', false).eligible, false);
const configPath = `${home}/config/house-feedback.json`;
const cfg = JSON.parse(readFileSync(configPath, 'utf8'));
writeFileSync(configPath, JSON.stringify({ ...cfg, project: '/wrong-project' }));
assert.equal(branchOfferForWake(state, message, false).eligible, false);
writeFileSync(configPath, JSON.stringify({ ...cfg, enabled: false }));
assert.equal(branchOfferForWake(state, message, false).eligible, false);
console.log('PASS: actual branch routing interface narrows authenticated checks; generic, legacy, unsupported host and feature-off remain MAIN-owned');
JS
