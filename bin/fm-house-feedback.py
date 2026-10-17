#!/usr/bin/env python3
"""Default-off authenticated House feedback bindings, not a job runner.

Usage: fm-house-feedback.py accept < envelope.json
       fm-house-feedback.py show UUID
       fm-house-feedback.py notice INBOX_NOTE_ID
       fm-house-feedback.py plan UUID < plan.json
       fm-house-feedback.py reserve UUID CHILD
       fm-house-feedback.py authorize TASK PROJECT MODE YOLO
       fm-house-feedback.py verify-launch TASK PROJECT SPAWN_GEN
       fm-house-feedback.py recovery-check TASK
       fm-house-feedback.py started TASK
       fm-house-feedback.py refresh TASK
       fm-house-feedback.py deliver REPLY_UUID CHILD
       fm-house-feedback.py update < signed-update.json

update accepts a producer HMAC envelope with payload fields version, producer,
origin, action (checking|ready|installed), update_id, source_commit,
artifact_sha256, proof_sha256, includes. includes is a nonempty array of exact
{request_id, child, revision} bindings. The trusted companion constructs these
from actual protected integration/installer results, NEVER client claims.
proof_sha256 binds the durable result evidence. This records evidence only;
it does not run checks or install anything. Installed requires recorded Ready.

Config: FM_HOME/config/house-feedback.json (enabled, producer, origin, project,
key_file, primary="pi"). Only the in-process Pi branch is supported. key_file must be absolute,
owned by this uid, and mode 0600; it contains at least 32 raw key bytes.
accept verifies HMAC-SHA256 of canonical JSON payload (sorted keys, compact
separators, ensure_ascii=True). Payload has exactly version=1, producer,
origin, action=implement, request_id (canonical UUID), body, reply_to, child.
The trusted companion signs ONLY after authenticating the owner and origin.
No client-supplied project, model, path or command grants authority.

plan is supervisor-only semantic adjudication, not client input. JSON is an
array of {key, scope, depends_on}; persist once before dispatch. The supervisor
must first reject ambiguous, destructive, security-sensitive, credential,
merge and install requests to the ordinary human decision path. Scope is a
bounded implementation instruction, not authority to extend the request.
Bindings use existing inbox receipts and task IDs. reserve returns the stable
task and revision. An unknown launch never permits another fresh spawn.
authorize is called ONLY by fm-spawn at its guarded action boundary and burns
the reservation before any launch side effect; no separate launch command.
started requires the existing crew-state pane evidence and spawn incarnation.
Replies are durable pending revisions; applying them is an explicit supervisor
steer, never automatic mutation of an existing checked candidate.
accept returns fm-house-feedback-receipt.v1 (request_id, receipt, received_at,
announced, stage=received, reply_to). show returns fm-house-feedback-projection.v1
(request_id, body, reply_to, child, receipt, announced, received_at, sequence,
children, updates, fully_installed). Children expose the stable task, scope,
dependencies, applied/desired revisions, pending reply UUIDs, follow-up bindings,
attempt timestamps and observed start incarnations. Missing children means
unclassified, never complete. Historical updates retain exact manifests and
ordered stage/proof events independently of current activity. Installed evidence
for a child's applied revision takes precedence over later Checking in another
update; that later update retains its own Checking stage. Dependencies accept
Ready or Installed only when desired replies/follow-ups are also settled.
fully_installed requires every child and desired follow-up; no partial parent
completion.
refresh reuses current-state reconciliation on existing supervision events;
missing/unknown endpoints are reconciliation-needed, never permanently Working.
No new monitor is started. started records observation time, not an inferred
OS launch time. Unknown consumed attempts require ordinary endpoint recovery or
human reconciliation; there is intentionally no blind reset/retry command.
All records are local/private. Explicit projections contain request content;
callers must not log that content, keys or customer context as telemetry.
"""
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def uuid_value(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value, "canonical UUID required")
    return value


def load(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".feedback-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(canonical(value))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Feedback:
    def __init__(self):
        require(bool(os.environ.get("FM_HOME")), "FM_HOME must be explicit")
        self.home = Path(os.environ["FM_HOME"]).resolve()
        self.directory = self.home / "state/house-feedback"

    def environment(self, **extra):
        env = {key: value for key, value in os.environ.items() if key not in ("BASH_ENV", "ENV", "FM_ROOT_OVERRIDE", "FM_STATE_OVERRIDE", "FM_DATA_OVERRIDE", "FM_CONFIG_OVERRIDE", "FM_PROJECTS_OVERRIDE", "FM_WAKE_QUEUE", "FM_WAKE_QUEUE_LOCK")}
        env.update(FM_HOME=str(self.home), FM_STATE_OVERRIDE=str(self.home / "state"), FM_DATA_OVERRIDE=str(self.home / "data"), FM_CONFIG_OVERRIDE=str(self.home / "config"))
        env.update(extra)
        return env

    def config(self):
        cfg = load(self.home / "config/house-feedback.json")
        require(cfg.get("enabled") is True, "House automatic feedback is disabled")
        require(cfg.get("primary") == "pi", "only Pi primary is supported")
        require(all(isinstance(cfg.get(field), str) and 0 < len(cfg[field]) <= 256 for field in ("producer", "origin")), "fixed producer and origin required")
        require(isinstance(cfg.get("project"), str), "fixed project required")
        project = Path(cfg["project"])
        require(project.is_absolute() and str(project.resolve()) == str(project) and project.is_dir(), "canonical fixed project required")
        key = Path(cfg["key_file"])
        info = key.lstat()
        require(key.is_absolute() and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600, "private producer key required")
        secret = key.read_bytes()
        require(len(secret) >= 32, "producer key too short")
        return cfg, secret

    def verify(self, envelope):
        cfg, secret = self.config()
        require(set(envelope) == {"payload", "signature"}, "invalid envelope")
        p = envelope["payload"]
        require(set(p) == {"version", "producer", "origin", "action", "request_id", "body", "reply_to", "child"}, "invalid payload fields")
        require(type(p["version"]) is int and p["version"] == 1 and p["action"] == "implement", "unsupported feedback action")
        require(p["producer"] == cfg["producer"] and p["origin"] == cfg["origin"], "wrong producer or origin")
        require(isinstance(envelope["signature"], str) and hmac.compare_digest(hmac.new(secret, canonical(p), hashlib.sha256).hexdigest(), envelope["signature"]), "invalid producer proof")
        uuid_value(p["request_id"])
        require(isinstance(p["body"], str) and "\x00" not in p["body"] and 0 < len(p["body"].strip()) <= 32000, "invalid feedback body")
        if p["reply_to"] is not None:
            uuid_value(p["reply_to"])
        require(p["child"] is None or (p["reply_to"] and isinstance(p["child"], str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", p["child"])), "invalid child reference")
        return cfg, p

    @contextlib.contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def path(self, request):
        return self.directory / (uuid_value(request) + ".json")

    def get(self, request):
        record = load(self.path(request))
        cfg, _ = self.verify(record["envelope"])
        require(record["project"] == cfg["project"], "project binding changed")
        return record

    def put(self, record):
        save(self.path(record["envelope"]["payload"]["request_id"]), record)

    def accept(self, envelope):
        with self.lock():
            cfg, p = self.verify(envelope)
            path = self.path(p["request_id"])
            if path.exists():
                record = self.get(p["request_id"])
                require(record["envelope"] == envelope, "request ID content conflict")
            else:
                requests = self.home / "state/inbox/.requests"
                require(not (requests / p["request_id"]).exists() and not any(requests.glob("house-lab-*-" + p["request_id"])), "legacy receipt cannot be selected automatically; send a new explicit request")
                if p["reply_to"]:
                    target = self.get(p["reply_to"])
                    require(target["envelope"]["payload"]["reply_to"] is None or target["children"], "reply must target a bound request")
                    self.reply_child(p, target)
                record = {"envelope": envelope, "project": cfg["project"], "received_at": time.time(), "sequence": max((load(f).get("sequence", 0) for f in self.directory.glob("*.json")), default=0) + 1, "children": {}, "receipt": None, "delivered": {}}
                self.put(record)
            if record["receipt"] is None or not record.get("announced"):
                # Existing inbox binding is durable before wake publication. An
                # interrupted call replays the same body and immutable UUID.
                result = subprocess.run([str(ROOT / "fm-inbox.sh"), "note", "--request-id", p["request_id"], "--json", "-"], input=p["body"], text=True, capture_output=True, check=False, env=self.environment())
                require(result.returncode in (0, 3), "inbox receipt failed; retry same request")
                receipt = json.loads(result.stdout)
                note = Path(receipt["path"]).read_bytes().decode("utf-8")
                header, note_body = note.split("\n--\n", 1)
                require("request_id=" + p["request_id"] in header.splitlines() and note_body.rstrip("\n") == p["body"].rstrip("\n"), "inbox request body or owner conflict")
                record["receipt"] = receipt["id"]
                record["announced"] = receipt.get("announced") is True or receipt.get("acknowledged") is True
                self.put(record)
            return {"schema": "fm-house-feedback-receipt.v1", "request_id": p["request_id"], "receipt": record["receipt"], "received_at": record["received_at"], "stage": "received", "announced": record.get("announced", False), "reply_to": p["reply_to"]}

    def notice(self, note_id):
        # This is a reconciliation hint, NEVER an authority grant. It also
        # survives opt-out/key revocation so MAIN cannot accidentally dispatch
        # a second logical task from a still-unacknowledged receipt.
        require(re.fullmatch(r"[A-Za-z0-9._-]+", note_id) is not None, "invalid note identity")
        path = self.home / "state/inbox" / (note_id + ".note")
        if not path.exists():
            path = self.home / "state/inbox/handled" / (note_id + ".note")
        header = path.read_text().split("\n--\n", 1)[0]
        meta = dict(line.split("=", 1) for line in header.splitlines() if "=" in line)
        request = uuid_value(meta["request_id"])
        record = load(self.path(request))
        require(record["envelope"]["payload"]["request_id"] == request and record["receipt"] in (None, note_id), "receipt binding conflict")
        return "Recorded House request " + request + ": use bin/fm-house-feedback.py show " + request + " and its stable bound task IDs, never create an unrelated duplicate task. This hint grants no authority; disabled, unreadable or uncertain bindings require reconciliation. Verified feedback authorizes bounded code preparation only, never merge, install, credentials, activation or model changes. The helper guards own classification/reservation/replies; acknowledge this inbox receipt after handling."

    def reply_child(self, payload, target):
        key = payload["child"]
        if key is None:
            require(len(target["children"]) == 1, "reply requires one explicit child for a multi-child or unbound parent")
            key = next(iter(target["children"]))
        require(key in target["children"], "unknown reply child")
        return key

    def replies(self, request, key):
        replies = []
        for path in self.directory.glob("*.json"):
            item = load(path)
            p = item["envelope"]["payload"]
            if p["reply_to"] == request and self.reply_child(p, self.get(request)) == key:
                self.verify(item["envelope"])
                replies.append(item)
        return sorted(replies, key=lambda item: item["sequence"])

    def stage(self, record, key):
        child = record["children"][key]
        request = record["envelope"]["payload"]["request_id"]
        events = []
        for path in (self.directory / "updates").glob("*.json"):
            update = load(path)
            if {"request_id": request, "child": key, "revision": child["revision"]} in update["manifest"]["includes"]:
                events.extend(update["events"])
        if any(event["envelope"]["payload"]["action"] == "installed" for event in events):
            return "installed"
        if events:
            return max(events, key=lambda event: event["at"])["envelope"]["payload"]["action"]
        if child["stage"] == "working" and not (self.home / "state" / (child["task"] + ".meta")).exists():
            return "reconciliation-needed"
        return child["stage"]

    def settled_child(self, record, key, stages):
        request = record["envelope"]["payload"]["request_id"]
        return self.stage(record, key) in stages and all(key in reply["delivered"] or self.settled(reply, stages) for reply in self.replies(request, key))

    def settled(self, record, stages):
        return bool(record["children"]) and all(self.settled_child(record, key, stages) for key in record["children"])

    def fulfilled(self, record):
        return self.settled(record, ("installed",))

    def update(self, envelope):
        with self.lock():
            cfg, secret = self.config()
            require(set(envelope) == {"payload", "signature"}, "invalid update envelope")
            p = envelope["payload"]
            require(set(p) == {"version", "producer", "origin", "action", "update_id", "source_commit", "artifact_sha256", "proof_sha256", "includes"}, "invalid update fields")
            require(type(p["version"]) is int and p["version"] == 1 and p["producer"] == cfg["producer"] and p["origin"] == cfg["origin"], "wrong update producer")
            require(hmac.compare_digest(hmac.new(secret, canonical(p), hashlib.sha256).hexdigest(), envelope["signature"]), "invalid update proof")
            require(p["action"] in ("checking", "ready", "installed"), "invalid update stage")
            require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", p["update_id"]) is not None, "invalid update identity")
            require(re.fullmatch(r"[0-9a-f]{40}", p["source_commit"]) is not None, "exact source commit required")
            for field in ("artifact_sha256", "proof_sha256"):
                require(re.fullmatch(r"[0-9a-f]{64}", p[field]) is not None, "exact artifact and proof digest required")
            require(isinstance(p["includes"], list) and 0 < len(p["includes"]) <= 100, "exact nonempty inclusion required")
            seen = set()
            for entry in p["includes"]:
                require(set(entry) == {"request_id", "child", "revision"}, "invalid inclusion")
                record = self.get(entry["request_id"])
                child = record["children"][entry["child"]]
                require(type(entry["revision"]) is int and 1 <= entry["revision"] <= child["revision"], "unimplemented revision cannot be included")
                require(child.get("started_at") is not None, "unstarted work cannot be included")
                require(not child.get("revising"), "reply delivery outcome must settle before inclusion")
                identity = (entry["request_id"], entry["child"])
                require(identity not in seen, "duplicate inclusion")
                seen.add(identity)
            manifest = {field: p[field] for field in ("update_id", "source_commit", "artifact_sha256", "includes")}
            path = self.directory / "updates" / (p["update_id"] + ".json")
            update = load(path) if path.exists() else {"manifest": manifest, "events": []}
            require(update["manifest"] == manifest, "update manifest is immutable")
            if any(event["envelope"] == envelope for event in update["events"]):
                return update
            previous = update["events"][-1]["envelope"]["payload"]["action"] if update["events"] else None
            require((previous, p["action"]) in ((None, "checking"), ("checking", "ready"), ("ready", "installed")), "update transition lacks preceding evidence")
            update["events"].append({"at": time.time(), "envelope": envelope})
            save(path, update)
            return update

    def show(self, request):
        with self.lock():
            record = self.get(request)
            for key, child in record["children"].items():
                replies = self.replies(request, key)
                child["stage"] = self.stage(record, key)
                child["desired_revision"] = 1 + len(replies)
                child["pending_replies"] = [r["envelope"]["payload"]["request_id"] for r in replies if key not in r["delivered"] and not self.fulfilled(r)]
                child["followups"] = [{"request_id": r["envelope"]["payload"]["request_id"], "children": list(r["children"]), "fully_installed": self.fulfilled(r)} for r in replies if r["children"]]
            record["updates"] = [load(f) for f in (self.directory / "updates").glob("*.json") if any(i["request_id"] == request for i in load(f)["manifest"]["includes"])]
            payload = record["envelope"]["payload"]
            updates = []
            for update in record["updates"]:
                last = update["events"][-1]
                updates.append({**update["manifest"], "stage": last["envelope"]["payload"]["action"], "proof_sha256": last["envelope"]["payload"]["proof_sha256"], "events": [{"stage": event["envelope"]["payload"]["action"], "observed_at": event["at"], "proof_sha256": event["envelope"]["payload"]["proof_sha256"]} for event in update["events"]]})
            return {"schema": "fm-house-feedback-projection.v1", "request_id": request, "body": payload["body"], "reply_to": payload["reply_to"], "child": payload["child"], "receipt": record["receipt"], "announced": record.get("announced", False), "received_at": record["received_at"], "sequence": record["sequence"], "children": record["children"], "updates": updates, "fully_installed": bool(record["children"]) and all(c["stage"] == "installed" and not c["pending_replies"] for c in record["children"].values())}

    def deliver(self, reply, key):
        require(os.environ.get("FM_TASK_INBOX") is None, "workers cannot apply feedback revisions")
        with self.lock():
            message = self.get(reply)
            payload = message["envelope"]["payload"]
            require(payload["reply_to"] is not None, "not a reply for this child")
            record = self.get(payload["reply_to"])
            require(self.reply_child(payload, record) == key, "not a reply for this child")
            child = record["children"][key]
            if key in message["delivered"]:
                return message["delivered"][key]
            require(not message["children"], "reply already bound to a separate follow-up")
            replies = self.replies(payload["reply_to"], key)
            position = next(i for i, item in enumerate(replies) if item["envelope"]["payload"]["request_id"] == reply)
            require(all(key in earlier["delivered"] for earlier in replies[:position]), "earlier reply must be handled first")
            revision = position + 2
            if child["revision"] >= revision:
                message["delivered"][key] = {"revision": revision, "reconciled_at": time.time()}
                self.put(message)
                return message["delivered"][key]
            require(self.stage(record, key) == "working" or child.get("revising") == reply, "target is not mutable; preserve pending revision for follow-up")
            text = "House feedback reply " + reply + " revision " + str(revision) + "\n" + payload["body"]
            child["revising"] = reply
            child["stage"] = "revising"
            self.put(record)
            task, generation = child["task"], child["spawn_gen"]
        # No request-wide I/O lock while the existing send plane rings its
        # endpoint. The reservation prevents Checking racing a desired change.
        result = subprocess.run([str(ROOT / "fm-send.sh"), task, text], text=True, capture_output=True, check=False, env=self.environment(FM_SEND_IDEMPOTENT="1", FM_SEND_EXPECTED_SPAWN_GEN=generation))
        require(result.returncode == 0, "reply delivery unconfirmed; retry same reply")
        with self.lock():
            message = self.get(reply)
            record = self.get(payload["reply_to"])
            child = record["children"][key]
            require(child.get("revising") == reply or key in message["delivered"], "reply reservation changed")
            if key in message["delivered"]:
                return message["delivered"][key]
            child.pop("revising", None)
            child["stage"] = "working"
            child["revision"] = revision
            self.put(record)
            message["delivered"][key] = {"revision": revision, "delivered_at": time.time()}
            self.put(message)
            return message["delivered"][key]

    def plan(self, request, plan):
        require(os.environ.get("FM_TASK_INBOX") is None, "workers cannot classify feedback")
        with self.lock():
            record = self.get(request)
            require(record["receipt"] is not None, "receipt binding is not yet confirmed; retry acceptance before classification")
            payload = record["envelope"]["payload"]
            if payload["reply_to"] is not None:
                target = self.get(payload["reply_to"])
                targets = [self.reply_child(payload, target)]
                require(targets and not record["delivered"], "delivered or unbound reply cannot become follow-up")
                require(all(self.stage(target, key) in ("ready", "installed", "closed", "failed") for key in targets), "live or checking reply must wait or use ordered delivery")
            require(isinstance(plan, list) and 0 < len(plan) <= 20, "bounded plan required")
            keys = set()
            children = {}
            for item in plan:
                require(set(item) == {"key", "scope", "depends_on"}, "invalid plan fields")
                key = item["key"]
                require(isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", key) and key not in keys, "unique stable child key required")
                require(isinstance(item["scope"], str) and 0 < len(item["scope"].strip()) <= 8000, "bounded scope required")
                require(isinstance(item["depends_on"], list) and all(x in keys for x in item["depends_on"]), "dependencies must name preceding children")
                keys.add(key)
                children[key] = {"scope": item["scope"], "depends_on": item["depends_on"], "task": "hf-" + request.replace("-", "") + "-" + key, "revision": 1, "stage": "queued", "attempt": None}
            if record["children"]:
                previous = [{"key": key, "scope": child["scope"], "depends_on": child["depends_on"]} for key, child in record["children"].items()]
                require(sorted(previous, key=lambda x: x["key"]) == sorted(plan, key=lambda x: x["key"]), "persisted decomposition conflicts")
            else:
                record["children"] = children
                self.put(record)
            return record["children"]

    def reserve(self, request, key):
        with self.lock():
            record = self.get(request)
            child = record["children"][key]
            require(all(self.settled_child(record, dep, ("ready", "installed")) for dep in child["depends_on"]), "dependency pending")
            if child["attempt"] is None:
                child["attempt"] = {"reserved_at": time.time(), "consumed_at": None}
                self.put(record)
            return child

    def bound(self, task):
        match = re.fullmatch(r"hf-([0-9a-f]{32})-([a-z][a-z0-9-]{0,31})", task)
        require(match is not None, "not a House task identity")
        require(self.path(str(uuid.UUID(match.group(1)))).exists(), "no House binding record")
        return {"bound": True, "task": task}

    def find(self, task):
        require(re.fullmatch(r"hf-([0-9a-f]{32})-([a-z][a-z0-9-]{0,31})", task) is not None, "not a bound House task")
        compact, key = task[3:].split("-", 1)
        request = str(uuid.UUID(compact))
        record = self.get(request)
        child = record["children"][key]
        require(child["task"] == task, "task binding mismatch")
        return record, child

    def authorize(self, task, project, mode, yolo, consume=True):
        with self.lock():
            record, child = self.find(task)
            require(project == record["project"] and mode == "local-only" and yolo == "off", "House launch requires fixed project, local-only, yolo off")
            require(child["attempt"] is not None, "reserve before spawn")
            require(child["attempt"]["consumed_at"] is None, "launch outcome uncertain or already launched; reconcile, never repeat fresh spawn")
            require(not (self.home / "state" / (task + ".meta")).exists(), "existing task requires reconciliation")
            require(all(self.settled_child(record, dep, ("ready", "installed")) for dep in child["depends_on"]), "dependency pending")
            if os.environ.get("FM_SUPERVISION_ACTOR") == "branch":
                require(os.environ.get("FM_SUPERVISION_PRIMARY_HARNESS") == "pi", "only Pi branch may launch House feedback")
            if consume:
                child["attempt"]["consumed_at"] = time.time()
                child["stage"] = "launch-pending"
                self.put(record)
            return {"authorized": True, "task": task}

    def recovery_check(self, task):
        with self.lock():
            record, child = self.find(task)
            meta = dict(line.split("=", 1) for line in (self.home / "state" / (task + ".meta")).read_text().splitlines() if "=" in line)
            require(child["attempt"] and child["attempt"]["consumed_at"] and meta.get("project") == record["project"], "recovery is not bound to an admitted task")
            require(meta.get("harness") == "pi" and meta.get("mode") == "local-only" and meta.get("yolo") == "off" and meta.get("backend", "tmux") in ("tmux", "herdr"), "unsupported House recovery authority")
            return {"authorized": True, "task": task}

    def verify_launch(self, task, project, generation):
        with self.lock():
            record, child = self.find(task)
            require(record["project"] == project and child["attempt"] and child["attempt"]["consumed_at"], "no consumed launch authority")
            meta = dict(line.split("=", 1) for line in (self.home / "state" / (task + ".meta")).read_text().splitlines() if "=" in line)
            require(meta.get("project") == project and meta.get("spawn_gen") == generation and meta.get("harness") == "pi" and meta.get("mode") == "local-only" and meta.get("yolo") == "off", "launch incarnation or authority changed")
            require(all(self.settled_child(record, dep, ("ready", "installed")) for dep in child["depends_on"]), "dependency changed before launch")
            return {"authorized": True, "task": task, "spawn_gen": generation}

    def refresh(self, task):
        # Reuse the existing reconciler, never infer completion from a pane
        # disappearing or a historical status tail. No new polling owner.
        metadata = self.home / "state" / (task + ".meta")
        with self.lock():
            self.find(task)
            before = metadata.read_text() if metadata.exists() else ""
        result = subprocess.run([str(ROOT / "fm-crew-state.sh"), task], text=True, capture_output=True, check=False, env=self.environment(FM_CREW_STATE_NO_FORGE="1"))
        match = re.match(r"state: ([a-z-]+) · source: ([a-z-]+) ·", result.stdout)
        state, source = match.groups() if result.returncode == 0 and match else ("unknown", "none")
        with self.lock():
            record, child = self.find(task)
            after = metadata.read_text() if metadata.exists() else ""
            meta = dict(line.split("=", 1) for line in after.splitlines() if "=" in line)
            if before != after or meta.get("project") != record["project"] or meta.get("spawn_gen") != child.get("spawn_gen"):
                state, source = "unknown", "none"
            child["activity"] = {"state": state, "source": source, "observed_at": time.time()}
            if child.get("started_at") and not child.get("revising"):
                child["stage"] = {"working": "working", "done": "closed", "failed": "failed", "paused": "waiting", "parked": "waiting", "blocked": "needs-attention"}.get(state, "reconciliation-needed")
                if state == "working" and source not in ("pane", "run-step"):
                    child["stage"] = "reconciliation-needed"
            self.put(record)
            return child

    def started(self, task):
        with self.lock():
            record, child = self.find(task)
            require(child["attempt"] and child["attempt"]["consumed_at"], "no authorized launch")
            meta = dict(line.split("=", 1) for line in (self.home / "state" / (task + ".meta")).read_text().splitlines() if "=" in line)
            require(meta.get("project") == record["project"] and meta.get("spawn_gen") and meta.get("window"), "missing owned endpoint evidence")
        result = subprocess.run([str(ROOT / "fm-crew-state.sh"), task], text=True, capture_output=True, check=False, env=self.environment(FM_CREW_STATE_NO_FORGE="1"))
        require(result.returncode == 0 and result.stdout.startswith("state: working · source: pane ·"), "actual worker start not yet proven")
        with self.lock():
            record, child = self.find(task)
            current = dict(line.split("=", 1) for line in (self.home / "state" / (task + ".meta")).read_text().splitlines() if "=" in line)
            require(current == meta, "endpoint incarnation changed during start observation")
            if child.get("spawn_gen") != meta["spawn_gen"]:
                observed = time.time()
                child.setdefault("started_at", observed)
                child.setdefault("starts", []).append({"observed_at": observed, "spawn_gen": meta["spawn_gen"]})
                child["spawn_gen"] = meta["spawn_gen"]
                if not child.get("revising"):
                    child["stage"] = "working"
                self.put(record)
            return child


def main():
    args = sys.argv[1:]
    if not args or args == ["--help"]:
        print(__doc__)
        return
    core = Feedback()
    command, *rest = args
    if command == "notice" and len(rest) == 1:
        print(core.notice(rest[0]))
        return
    if command == "accept" and not rest:
        result = core.accept(json.load(sys.stdin))
    elif command == "show" and len(rest) == 1:
        result = core.show(rest[0])
    elif command == "plan" and len(rest) == 1:
        result = core.plan(rest[0], json.load(sys.stdin))
    elif command == "reserve" and len(rest) == 2:
        result = core.reserve(*rest)
    elif command in ("authorize", "check") and len(rest) == 4:
        result = core.authorize(*rest, consume=command == "authorize")
    elif command == "bound" and len(rest) == 1:
        result = core.bound(*rest)
    elif command == "recovery-check" and len(rest) == 1:
        result = core.recovery_check(*rest)
    elif command == "verify-launch" and len(rest) == 3:
        result = core.verify_launch(*rest)
    elif command == "started" and len(rest) == 1:
        result = core.started(*rest)
    elif command == "refresh" and len(rest) == 1:
        result = core.refresh(*rest)
    elif command == "deliver" and len(rest) == 2:
        result = core.deliver(*rest)
    elif command == "update" and not rest:
        result = core.update(json.load(sys.stdin))
    else:
        raise ValueError("invalid command; use --help")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print("house-feedback: " + str(error), file=sys.stderr)
        sys.exit(1)
