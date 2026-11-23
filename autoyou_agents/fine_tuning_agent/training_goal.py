# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""A goal-driven training loop: plan, try, verify, repair, install.

This is the training counterpart of ``self_improvement/goal_loop.py``, which
drives AutoYou's agents through the same loop: state a goal, attempt it, check
the result against ground truth rather than the attempt's own claim, repair what
failed and go again, and write a structured report either way. The parts that
carry over are the ones that make a long job trustworthy:

* **Stages with explicit results** (``success`` / ``skipped`` / ``error``), so a
  run can say exactly where it stopped, and a resumed run starts at the first
  stage that has not completed instead of at the top.
* **A stop event checked between and during stages**, with ``pause`` (keep the
  work, stop) distinct from ``cancel`` (stop and mark finished).
* **Verification against evidence.** A job reporting "completed" is not trusted:
  the adapter files must exist and hold finite numbers, the loss must be finite,
  and an optional target loss must be met.
* **Repair, not retry.** A failure is classified (:func:`planner.classify_failure`)
  and each class has a different next step: out of memory shrinks the plan, a
  non-finite loss lowers the learning rate, and a process that vanished with no
  traceback is simply resumed, because nothing about the plan was wrong.
* **State on disk.** Every transition is persisted, so a server restart - or the
  OS killing the trainer - leaves a run that can be resumed, not a mystery.

All side effects go through :class:`GoalOps`, so the whole loop is testable with
fakes and none of it needs a GPU.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import dataclasses
import datetime as _dt
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .hardware import HardwareReport
from .model_registry import license_status, lookup, recommend_base
from .planner import FAILURE_HINTS, TrainingPlan, classify_failure, plan_training, repair_plan

STAGES = ("probe", "plan", "train", "install")
#: Job settings an operator may set that the plan does not decide.
PASSTHROUGH_KEYS = ("max_steps", "epochs", "vision_min_pixels", "vision_max_pixels", "prepare_only",
                    "allow_cpu_training", "lora_dropout", "eval_steps")
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancelling"})
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class TrainingGoalConfig:
    """What the operator asked for. Everything has a default that works."""

    dataset_id: str
    model_name: Optional[str] = None
    title: Optional[str] = None
    training_model_id: Optional[str] = None     # None: the base recommended for this hardware
    ollama_base_model: Optional[str] = None
    max_attempts: int = 3
    max_hours: float = 24.0
    target_eval_loss: Optional[float] = None
    quality_boosts: int = 1                      # extra, longer attempts allowed when the target loss is missed
    require_permissive_license: bool = False
    install: bool = False
    vision: Optional[bool] = None                # None: decided from the dataset
    config: Dict[str, Any] = field(default_factory=dict)   # explicit overrides win over the plan
    poll_seconds: float = 5.0
    # How long to wait before retrying after the computer ran out of system resources; the second retry waits twice as long.
    resource_pause_seconds: float = 45.0


@dataclass
class TrainingGoalRun:
    goal_run_id: str
    dataset_id: str
    status: str                                  # queued | running | paused | completed | failed | cancelled
    stages: tuple = STAGES
    current_stage: str = ""
    completed_stages: List[str] = field(default_factory=list)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    plan: Dict[str, Any] = field(default_factory=dict)
    hardware: Dict[str, Any] = field(default_factory=dict)
    license: Dict[str, Any] = field(default_factory=dict)
    training_model_id: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    goal_config: Dict[str, Any] = field(default_factory=dict)   # the TrainingGoalConfig, so a resume can rebuild it
    last_result: Dict[str, Any] = field(default_factory=dict)
    quality_boosts_used: int = 0
    error: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    report_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["stages"] = list(self.stages)
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TrainingGoalRun":
        known = {f.name for f in dataclasses.fields(TrainingGoalRun)}
        values = {k: v for k, v in dict(data).items() if k in known}
        values["stages"] = tuple(values.get("stages") or STAGES)
        return TrainingGoalRun(**values)


@dataclass
class GoalOps:
    """Every side effect the loop performs, so tests can replace them.

    ``start_job`` takes ``(dataset_id, model_name, title, config)`` and returns
    ``{"status": "started", "job": {...}}`` or ``{"status": "error", ...}``.
    ``get_job`` returns the job row (status, progress_percent, return_code,
    output_dir, log_path, error_message). ``read_summary`` returns the trainer's
    ``training_summary.json`` as a dict, or ``{}``.
    """

    probe: Callable[[], HardwareReport]
    get_dataset: Callable[[str], Optional[Dict[str, Any]]]
    start_job: Callable[[str, Optional[str], Optional[str], Dict[str, Any]], Dict[str, Any]]
    get_job: Callable[[str], Optional[Dict[str, Any]]]
    cancel_job: Callable[[str], Dict[str, Any]]
    read_log_tail: Callable[[str, int], str]
    read_summary: Callable[[Dict[str, Any]], Dict[str, Any]]
    install: Callable[[str], Dict[str, Any]]
    adapter_is_finite: Callable[[Path], Optional[bool]] = lambda path: None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    notify: Callable[[str, str], None] = lambda message, status: None


# --------------------------------------------------------------- persistence
class GoalStore:
    """One JSON file per run, written atomically so a crash never leaves half a state."""

    def __init__(self, directory: Path, *, write: Optional[Callable[[Path, bytes], None]] = None,
                 read: Optional[Callable[[Path], bytes]] = None):
        self.directory = Path(directory)
        self._write = write or (lambda path, data: path.write_bytes(data))
        self._read = read or (lambda path: path.read_bytes())

    def _path(self, goal_run_id: str) -> Path:
        return self.directory / f"goal-{_SAFE_ID.sub('_', goal_run_id)[:80]}.json"

    def save(self, run: TrainingGoalRun) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        run.updated_at = _utc_now()
        path = self._path(run.goal_run_id)
        temp = path.with_suffix(".tmp")
        self._write(temp, (json.dumps(run.to_dict(), indent=2, default=str) + "\n").encode("utf-8"))
        temp.replace(path)

    def load(self, goal_run_id: str) -> Optional[TrainingGoalRun]:
        path = self._path(goal_run_id)
        if not path.is_file():
            return None
        try:
            return TrainingGoalRun.from_dict(json.loads(self._read(path).decode("utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> List[TrainingGoalRun]:
        runs = []
        for path in sorted(self.directory.glob("goal-*.json")) if self.directory.is_dir() else []:
            run = self.load(path.stem[len("goal-"):])
            if run is not None:
                runs.append(run)
        return runs

    def write_report(self, run: TrainingGoalRun) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.directory / f"training-goal-{stamp}-{_SAFE_ID.sub('_', run.goal_run_id)[:40]}.json"
        self._write(path, (json.dumps(run.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        run.report_path = str(path)
        return str(path)


# ------------------------------------------------------------- verification
def verify_adapter(job: Mapping[str, Any], summary: Mapping[str, Any], ops: GoalOps,
                   target_eval_loss: Optional[float]) -> Dict[str, Any]:
    """Check a finished job against evidence, not against its own status field."""
    failures: List[str] = []
    metrics = dict(summary.get("metrics") or {})
    if str(job.get("status")) != "completed":
        failures.append(f"job status is {job.get('status')!r}, not 'completed'")
    if summary and str(summary.get("status")) not in {"completed", ""}:
        failures.append(f"trainer reported {summary.get('status')!r}: {summary.get('reason') or summary.get('message') or ''}".strip())
    output_dir = Path(str(job.get("output_dir") or ""))
    weights = output_dir / "adapter_model.safetensors"
    if not weights.is_file() or weights.stat().st_size == 0:
        failures.append("adapter_model.safetensors is missing or empty")
    if not (output_dir / "adapter_config.json").is_file():
        failures.append("adapter_config.json is missing")
    for key in ("train_loss", "eval_loss"):
        if key in metrics:
            try:
                if not math.isfinite(float(metrics[key])):
                    failures.append(f"{key} is not finite")
            except (TypeError, ValueError):
                failures.append(f"{key} is not a number")
    finite = None
    if not failures:
        finite = ops.adapter_is_finite(weights)
        if finite is False:
            failures.append("adapter weights contain NaN or infinity")
    eval_loss = metrics.get("eval_loss")
    below_target = False
    if target_eval_loss is not None and not failures:
        if eval_loss is None:
            failures.append("a target loss was set but the job produced no eval loss")
        elif float(eval_loss) > float(target_eval_loss):
            failures.append(f"eval loss {float(eval_loss):.4f} is above the target {float(target_eval_loss):.4f}")
            below_target = True
    return {
        "passed": not failures, "failures": failures, "below_target": below_target,
        "metrics": {k: metrics[k] for k in ("train_loss", "eval_loss", "train_runtime") if k in metrics},
        "adapter_finite": finite,
    }


def check_adapter_finite(path: Path) -> Optional[bool]:
    """True/False when the adapter's tensors can be read, None when they cannot
    (missing libraries are not evidence of a bad adapter)."""
    try:
        from safetensors import safe_open  # type: ignore[import]
    except ImportError:
        return None
    try:
        try:
            handle = safe_open(str(path), framework="pt")
            import torch  # type: ignore[import]

            with handle as f:
                return all(bool(torch.isfinite(f.get_tensor(key).float()).all()) for key in f.keys())
        except ImportError:
            import numpy as np  # type: ignore[import]

            with safe_open(str(path), framework="np") as f:
                return all(bool(np.isfinite(f.get_tensor(key).astype("float32")).all()) for key in f.keys())
    except Exception:
        return None


# --------------------------------------------------------------------- loop
class _Stop(Exception):
    def __init__(self, cancelled: bool):
        self.cancelled = cancelled


def _bool_stop(stop_event: threading.Event, cancel_flag: Callable[[], bool]) -> None:
    if stop_event.is_set():
        raise _Stop(cancel_flag())


def _pause(seconds: float, ops: GoalOps, stop_event: threading.Event, cancel_flag: Callable[[], bool]) -> None:
    """Wait in one-second slices so a pause or cancel is honoured promptly."""
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        _bool_stop(stop_event, cancel_flag)
        step = min(1.0, remaining)
        ops.sleep(step)
        remaining -= step


def _wait_for_job(job_id: str, run: TrainingGoalRun, config: TrainingGoalConfig, ops: GoalOps,
                  stop_event: threading.Event, cancel_flag: Callable[[], bool], store: GoalStore,
                  deadline: float) -> Dict[str, Any]:
    """Poll a job to a terminal state, cancelling it on stop or deadline."""
    cancel_sent = False
    while True:
        job = ops.get_job(job_id) or {}
        status = str(job.get("status") or "")
        if status in TERMINAL_JOB_STATUSES:
            return job
        if not cancel_sent and (stop_event.is_set() or ops.now() > deadline):
            ops.cancel_job(job_id)
            cancel_sent = True
            if ops.now() > deadline and not stop_event.is_set():
                run.error = f"The {config.max_hours:g}-hour limit was reached."
        run.last_result = {"job_id": job_id, "job_status": status, "progress_percent": job.get("progress_percent"),
                           "eta_seconds": job.get("eta_seconds")}
        store.save(run)
        ops.sleep(config.poll_seconds)


def run_training_goal(
    config: TrainingGoalConfig,
    ops: GoalOps,
    store: GoalStore,
    *,
    goal_run_id: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    run: Optional[TrainingGoalRun] = None,
) -> TrainingGoalRun:
    """Run (or resume) a training goal to a terminal state and return it."""
    stop = stop_event or threading.Event()
    cancelled = cancel_flag or (lambda: False)
    run = run or TrainingGoalRun(
        goal_run_id=goal_run_id or f"tg-{uuid.uuid4().hex[:12]}", dataset_id=config.dataset_id, status="running",
        started_at=_utc_now(), config=dict(config.config), training_model_id=config.training_model_id or "",
        goal_config=dataclasses.asdict(config),
    )
    run.status = "running"
    run.error = ""
    deadline = ops.now() + float(config.max_hours) * 3600.0
    store.save(run)

    def stage(name: str) -> None:
        run.current_stage = name
        store.save(run)

    def done(name: str) -> None:
        if name not in run.completed_stages:
            run.completed_stages.append(name)
        store.save(run)

    try:
        dataset = ops.get_dataset(config.dataset_id)
        if not dataset:
            raise ValueError(f"Dataset {config.dataset_id} was not found.")

        # ---- probe
        if "probe" not in run.completed_stages:
            _bool_stop(stop, cancelled)
            stage("probe")
            report = ops.probe()
            run.hardware = report.to_dict()
            done("probe")
        else:
            report = ops.probe()

        # ---- plan
        vision = bool(config.vision if config.vision is not None
                      else (dataset.get("image_sample_count") or dataset.get("image_count") or dataset.get("has_images")))
        if "plan" not in run.completed_stages:
            _bool_stop(stop, cancelled)
            stage("plan")
            model_id = config.training_model_id or recommend_base(report, vision=vision).id
            run.training_model_id = model_id
            run.license = license_status(model_id)
            if config.require_permissive_license and run.license["tier"] != "permissive":
                raise ValueError(f"{model_id} is not permissively licensed: {run.license['message']}")
            plan = plan_training(report, lookup(model_id), model_id=model_id, vision=vision,
                                 requested=dict(config.config))
            run.plan = plan.to_dict()
            done("plan")
        plan = _plan_from_dict(run.plan)
        model_id = run.training_model_id

        # ---- train / verify / repair
        if "train" not in run.completed_stages:
            stage("train")
            attempt_number = len(run.attempts)
            boost = 1.0 + 0.5 * run.quality_boosts_used
            while True:
                _bool_stop(stop, cancelled)
                if attempt_number >= config.max_attempts + config.quality_boosts:
                    raise RuntimeError(f"No attempt succeeded in {attempt_number} tries.")
                attempt_number += 1
                job_config = {
                    **plan.to_job_config(),
                    # Settings the plan does not own pass through unchanged.
                    **{key: config.config[key] for key in PASSTHROUGH_KEYS if key in config.config},
                    "training_model_id": model_id,
                    **({"ollama_base_model": config.ollama_base_model} if config.ollama_base_model else {}),
                    "resume": "auto" if plan.resume else "",
                }
                if boost > 1.0:
                    for key in ("max_steps", "epochs"):
                        if key in config.config:
                            job_config[key] = type(config.config[key])(config.config[key] * boost)
                started = ops.start_job(config.dataset_id, config.model_name, config.title, job_config)
                if started.get("status") not in {"started", "success"}:
                    raise RuntimeError(f"Could not start a job: {started.get('message') or started}")
                job_id = str((started.get("job") or {}).get("id") or started.get("job_id") or "")
                attempt: Dict[str, Any] = {"attempt": attempt_number, "job_id": job_id, "plan": plan.to_dict(),
                                           "started_at": _utc_now(), "outcome": "running"}
                run.attempts.append(attempt)
                store.save(run)
                ops.notify(f"Fine-tuning goal {run.goal_run_id}: attempt {attempt_number} started (job {job_id}).", "progress")

                job = _wait_for_job(job_id, run, config, ops, stop, cancelled, store, deadline)
                if ops.now() > deadline and run.error:
                    attempt["outcome"] = "deadline"
                    raise RuntimeError(run.error)
                _bool_stop(stop, cancelled)

                summary = ops.read_summary(job)
                verdict = verify_adapter(job, summary, ops, config.target_eval_loss)
                attempt.update({"finished_at": _utc_now(), "verification": verdict,
                                "return_code": job.get("return_code")})
                if verdict["passed"]:
                    attempt["outcome"] = "passed"
                    run.last_result = {"job_id": job_id, **verdict}
                    done("train")
                    break

                reason = str(summary.get("reason") or "")
                kind = {"out_of_memory": "out_of_memory", "non_finite_loss": "non_finite_loss"}.get(reason) or classify_failure(
                    job.get("return_code"), ops.read_log_tail(job_id, 200))
                attempt.update({"outcome": "failed", "failure_kind": kind})
                if verdict["below_target"] and run.quality_boosts_used < config.quality_boosts and not (verdict["failures"][1:]):
                    run.quality_boosts_used += 1
                    boost = 1.0 + 0.5 * run.quality_boosts_used
                    attempt["failure_kind"] = "quality_below_target"
                    attempt["repair"] = f"retrying with {boost:g}x the training budget"
                    store.save(run)
                    continue
                nxt = repair_plan(plan, kind, attempt=attempt_number, report=report)
                if nxt is None:
                    run.last_result = {"job_id": job_id, **verdict, "failure_kind": kind}
                    hint = FAILURE_HINTS.get(kind, "")
                    raise RuntimeError(f"Attempt {attempt_number} failed ({kind}) and no repair applies: "
                                       + "; ".join(verdict["failures"]) + (f". {hint}" if hint else ""))
                attempt["repair"] = nxt.history[-1] if nxt.history else "plan changed"
                plan = nxt
                run.plan = plan.to_dict()
                if kind == "system_resources" and config.resource_pause_seconds > 0:
                    wait = config.resource_pause_seconds * attempt_number
                    attempt["repair"] += f" ({wait:g}s)"
                    store.save(run)
                    _pause(wait, ops, stop, cancelled)
                store.save(run)

        # ---- install
        if config.install and "install" not in run.completed_stages:
            _bool_stop(stop, cancelled)
            stage("install")
            job_id = str(run.attempts[-1]["job_id"])
            installed = ops.install(job_id)
            run.last_result = {"job_id": job_id, "install": installed}
            if installed.get("status") not in {"success", "installed"}:
                raise RuntimeError(f"Install failed: {installed.get('message') or installed}")
            done("install")
        run.status = "completed"
    except _Stop as stopped:
        run.status = "cancelled" if stopped.cancelled else "paused"
    except Exception as exc:  # the loop reports; it never raises into a caller thread
        run.status = "failed"
        run.error = run.error or str(exc)
    finally:
        run.current_stage = run.current_stage if run.status == "paused" else ""
        run.finished_at = "" if run.status == "paused" else _utc_now()
        try:
            store.write_report(run)
            store.save(run)
        except OSError:
            pass
        ops.notify(f"Fine-tuning goal {run.goal_run_id} {run.status}." + (f" {run.error}" if run.error else ""),
                   "completed" if run.status == "completed" else run.status)
    return run


def _plan_from_dict(data: Mapping[str, Any]) -> TrainingPlan:
    known = {f.name for f in dataclasses.fields(TrainingPlan)}
    values = {k: v for k, v in dict(data).items() if k in known}
    return TrainingPlan(**values)


# ------------------------------------------------------------- run registry
# In-process registry, the same shape goal_loop uses for media goals: a lock, a
# stop event and a cancel flag per run, with the durable truth in the GoalStore.
_LOCK = threading.Lock()
_RUNS: Dict[str, TrainingGoalRun] = {}
_STOP_EVENTS: Dict[str, threading.Event] = {}
_CANCEL_FLAGS: Dict[str, bool] = {}
_THREADS: Dict[str, threading.Thread] = {}


def start_training_goal(config: TrainingGoalConfig, ops: GoalOps, store: GoalStore,
                        *, run: Optional[TrainingGoalRun] = None, blocking: bool = False) -> Dict[str, Any]:
    """Start a goal in a daemon thread (or inline with ``blocking``)."""
    with _LOCK:
        for existing in _RUNS.values():
            if existing.status == "running":
                return {"status": "already_running", "message": f"Goal {existing.goal_run_id} is already running.",
                        "run": existing.to_dict()}
        goal_run_id = run.goal_run_id if run else f"tg-{uuid.uuid4().hex[:12]}"
        stop = threading.Event()
        _STOP_EVENTS[goal_run_id] = stop
        _CANCEL_FLAGS[goal_run_id] = False
        initial = run or TrainingGoalRun(
            goal_run_id=goal_run_id, dataset_id=config.dataset_id, status="queued", started_at=_utc_now(),
            config=dict(config.config), training_model_id=config.training_model_id or "",
            goal_config=dataclasses.asdict(config),
        )
        initial.status = "running"
        _RUNS[goal_run_id] = initial

    def target() -> None:
        try:
            result = run_training_goal(config, ops, store, goal_run_id=goal_run_id, stop_event=stop,
                                       cancel_flag=lambda: _CANCEL_FLAGS.get(goal_run_id, False), run=initial)
        finally:
            with _LOCK:
                _THREADS.pop(goal_run_id, None)
        with _LOCK:
            _RUNS[goal_run_id] = result

    if blocking:
        target()
    else:
        thread = threading.Thread(target=target, daemon=True, name=f"fine-tuning-goal-{goal_run_id}")
        with _LOCK:
            _THREADS[goal_run_id] = thread
        thread.start()
    return {"status": "started", "goal_run_id": goal_run_id,
            "message": f"Training goal {goal_run_id} started for dataset {config.dataset_id}."}


def pause_training_goal(goal_run_id: str, ops: Optional[GoalOps] = None) -> Dict[str, Any]:
    """Stop at the next safe point and keep everything: resume continues the run."""
    with _LOCK:
        event = _STOP_EVENTS.get(goal_run_id)
        _CANCEL_FLAGS[goal_run_id] = False
        if event is not None:
            event.set()
    return {"status": "pausing" if event is not None else "not_found", "goal_run_id": goal_run_id}


def cancel_training_goal(goal_run_id: str) -> Dict[str, Any]:
    with _LOCK:
        event = _STOP_EVENTS.get(goal_run_id)
        _CANCEL_FLAGS[goal_run_id] = True
        if event is not None:
            event.set()
    return {"status": "cancelling" if event is not None else "not_found", "goal_run_id": goal_run_id}


def config_from_run(run: TrainingGoalRun) -> TrainingGoalConfig:
    """Rebuild the goal's configuration from its saved state."""
    known = {f.name for f in dataclasses.fields(TrainingGoalConfig)}
    values = {k: v for k, v in dict(run.goal_config).items() if k in known}
    values.setdefault("dataset_id", run.dataset_id)
    values.setdefault("config", dict(run.config))
    return TrainingGoalConfig(**values)


def resume_training_goal(goal_run_id: str, config: Optional[TrainingGoalConfig], ops: GoalOps, store: GoalStore,
                         *, blocking: bool = False) -> Dict[str, Any]:
    """Continue a paused, interrupted or failed run from its first incomplete stage.

    A run whose last attempt never recorded an outcome - the server restarted or
    the trainer was killed - is treated as a vanished process: the same plan is
    resumed from its newest checkpoint.
    """
    run = store.load(goal_run_id)
    if run is None:
        return {"status": "not_found", "message": f"No saved goal {goal_run_id}."}
    if run.status == "completed":
        return {"status": "already_completed", "run": run.to_dict()}
    if run.attempts and run.attempts[-1].get("outcome") == "running":
        last = run.attempts[-1]
        job = ops.get_job(str(last.get("job_id") or "")) or {}
        if str(job.get("status")) in ACTIVE_JOB_STATUSES:
            return {"status": "job_still_running", "message": "The trainer is still running; wait for it or cancel it first."}
        last["outcome"] = "vanished"
        last["failure_kind"] = "killed"
        plan = _plan_from_dict(run.plan) if run.plan else None
        if plan is not None:
            repaired = repair_plan(plan, "killed", attempt=len(run.attempts))
            if repaired is not None:
                run.plan = repaired.to_dict()
    run.status = "queued"
    return start_training_goal(config or config_from_run(run), ops, store, run=run, blocking=blocking)


def get_training_goal_status(goal_run_id: str, store: GoalStore) -> Dict[str, Any]:
    with _LOCK:
        live = _RUNS.get(goal_run_id)
        if live is not None:
            return live.to_dict()
    run = store.load(goal_run_id)
    return run.to_dict() if run else {"goal_run_id": goal_run_id, "status": "not_found"}


def list_training_goals(store: GoalStore) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {run.goal_run_id: run.to_dict() for run in store.list()}
    with _LOCK:
        for run_id, run in _RUNS.items():
            seen[run_id] = run.to_dict()
    return sorted(seen.values(), key=lambda item: item.get("started_at") or "", reverse=True)


def _reset_registry_for_tests() -> None:
    with _LOCK:
        _RUNS.clear()
        _STOP_EVENTS.clear()
        _CANCEL_FLAGS.clear()
        _THREADS.clear()
