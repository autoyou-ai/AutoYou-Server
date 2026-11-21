# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""The training goal loop, driven by a scripted job runner.

No trainer, GPU or Ollama is involved: ``FakeOps`` plays the job supervisor and
each scripted attempt says how that attempt ends. What is under test is the
loop's judgement - what it verifies, how it classifies a failure, what it
changes before trying again, and what it leaves on disk.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoyou_agents.fine_tuning_agent import hardware, planner, training_goal as tg

GIB = hardware.GIB


def _strix_report() -> hardware.HardwareReport:
    device = hardware.DeviceInfo(index=0, name="AMD Radeon(TM) 8060S Graphics", backend="rocm",
                                 total_bytes=int(107.9 * GIB), free_bytes=int(107 * GIB), arch="gfx1151",
                                 capability=(11, 5), bf16=True)
    return hardware.HardwareReport("Windows", "x86_64", 32, 32 * GIB, 13 * GIB, "rocm", (device,), "strix_halo")


class FakeOps:
    """Scripted supervisor. ``script`` is a list of attempt descriptions."""

    def __init__(self, tmp_path: Path, script, *, dataset=None, report=None, clock=None):
        self.tmp = tmp_path
        self.script = list(script)
        self.dataset = {"id": "ds_1", "sample_count": 100} if dataset is None else dataset
        self.report = report or _strix_report()
        self.jobs: dict = {}
        self.started: list = []
        self.cancelled: list = []
        self.installed: list = []
        self.notifications: list = []
        self.probes = 0
        self.clock = clock
        self.polls_before_done = 0

    # -- GoalOps surface
    def probe(self):
        self.probes += 1
        return self.report

    def get_dataset(self, dataset_id):
        return self.dataset if dataset_id == "ds_1" else None

    def start_job(self, dataset_id, model_name, title, config):
        index = len(self.started)
        spec = self.script[index] if index < len(self.script) else self.script[-1]
        job_id = f"job_{index + 1}"
        out = self.tmp / job_id / "adapter"
        out.mkdir(parents=True, exist_ok=True)
        if spec.get("weights", True):
            (out / "adapter_model.safetensors").write_bytes(b"x" * 64)
            (out / "adapter_config.json").write_text("{}", encoding="utf-8")
        self.started.append({"job_id": job_id, "config": dict(config), "spec": spec})
        self.jobs[job_id] = {
            "id": job_id, "status": "running", "output_dir": str(out), "return_code": None,
            "progress_percent": 10.0, "spec": spec, "polls": 0,
        }
        return {"status": "started", "job": {"id": job_id}}

    def get_job(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job["polls"] += 1
        spec = job["spec"]
        if spec.get("hang"):
            return dict(job)
        if job["polls"] <= self.polls_before_done:
            return dict(job)
        if job_id in self.cancelled:
            job.update(status="cancelled", return_code=1)
        else:
            job.update(status=spec.get("status", "completed"), return_code=spec.get("return_code", 0))
        return dict(job)

    def cancel_job(self, job_id):
        self.cancelled.append(job_id)
        return {"status": "cancelling"}

    def read_log_tail(self, job_id, lines):
        return self.jobs[job_id]["spec"].get("log", "")

    def read_summary(self, job):
        return dict(job["spec"].get("summary", {"status": "completed", "metrics": {"train_loss": 1.0, "eval_loss": 1.2}}))

    def install(self, job_id):
        self.installed.append(job_id)
        return self.install_result if hasattr(self, "install_result") else {"status": "success"}

    # -- plumbing
    def sleep(self, seconds):
        if self.clock is not None:
            self.clock["now"] += seconds

    def now(self):
        return self.clock["now"] if self.clock is not None else 0.0

    def ops(self, **overrides):
        values = dict(
            probe=self.probe, get_dataset=self.get_dataset, start_job=self.start_job, get_job=self.get_job,
            cancel_job=self.cancel_job, read_log_tail=self.read_log_tail, read_summary=self.read_summary,
            install=self.install, adapter_is_finite=lambda path: True, sleep=self.sleep, now=self.now,
            notify=lambda message, status: self.notifications.append((status, message)),
        )
        values.update(overrides)
        return tg.GoalOps(**values)


GOOD = {"status": "completed", "return_code": 0}
OOM = {"status": "failed", "return_code": planner.EXIT_OUT_OF_MEMORY,
       "summary": {"status": "failed", "reason": "out_of_memory"}, "log": "OUT_OF_MEMORY HIP out of memory"}


@pytest.fixture
def store(tmp_path):
    return tg.GoalStore(tmp_path / "goals")


@pytest.fixture(autouse=True)
def _clean_registry():
    tg._reset_registry_for_tests()
    yield
    tg._reset_registry_for_tests()


def _config(**kwargs):
    kwargs.setdefault("poll_seconds", 0.0)
    return tg.TrainingGoalConfig(dataset_id="ds_1", **kwargs)


# --------------------------------------------------------------- happy path
def test_a_clean_run_probes_plans_trains_and_reports(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(_config(), fake.ops(), store)
    assert run.status == "completed" and run.completed_stages == ["probe", "plan", "train"]
    assert len(run.attempts) == 1 and run.attempts[0]["outcome"] == "passed"
    # the plan came from the hardware: Strix Halo, so a 9B base with eager attention
    assert run.training_model_id == "Qwen/Qwen3.5-9B" and run.license["tier"] == "permissive"
    assert run.hardware["family"] == "strix_halo" and run.plan["attn_implementation"] == "eager"
    config = fake.started[0]["config"]
    assert config["training_model_id"] == "Qwen/Qwen3.5-9B" and config["precision"] == "bf16" and config["resume"] == ""
    assert run.report_path and Path(run.report_path).is_file()
    assert json.loads(Path(run.report_path).read_text(encoding="utf-8"))["goal_run_id"] == run.goal_run_id
    assert fake.notifications[-1][0] == "completed"


def test_state_is_persisted_and_listable(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(_config(), fake.ops(), store)
    loaded = store.load(run.goal_run_id)
    assert loaded.status == "completed" and loaded.attempts[0]["job_id"] == "job_1"
    assert [r.goal_run_id for r in store.list()] == [run.goal_run_id]
    assert store.load("missing") is None
    (store.directory / "goal-garbage.json").write_text("not json", encoding="utf-8")
    assert len(store.list()) == 1


def test_a_missing_dataset_fails_cleanly(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(tg.TrainingGoalConfig(dataset_id="nope", poll_seconds=0.0), fake.ops(), store)
    assert run.status == "failed" and "was not found" in run.error and not fake.started


def test_an_image_dataset_gets_a_vision_capable_recommendation(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD], dataset={"id": "ds_1", "image_sample_count": 12})
    run = tg.run_training_goal(_config(), fake.ops(), store)
    assert lookup_modality(run.training_model_id) == "vision"


def lookup_modality(model_id):
    from autoyou_agents.fine_tuning_agent import model_registry

    return model_registry.lookup(model_id).modality


# ------------------------------------------------------------------ licence
def test_a_non_commercial_base_is_refused_when_the_goal_requires_a_permissive_licence(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(
        _config(training_model_id="Qwen/Qwen2.5-3B-Instruct", require_permissive_license=True), fake.ops(), store)
    assert run.status == "failed" and "not permissively licensed" in run.error and not fake.started
    assert run.license["tier"] == "non_commercial"
    # without the requirement the operator may proceed; the licence is still recorded
    again = tg.run_training_goal(_config(training_model_id="Qwen/Qwen2.5-3B-Instruct"), FakeOps(tmp_path, [GOOD]).ops(), store)
    assert again.status == "completed" and again.license["ok_to_distribute"] is False


# ------------------------------------------------------------------- repair
def test_out_of_memory_shrinks_the_plan_and_tries_again(tmp_path, store):
    fake = FakeOps(tmp_path, [OOM, GOOD])
    run = tg.run_training_goal(_config(config={"batch_size": 2, "gradient_checkpointing": False}), fake.ops(), store)
    assert run.status == "completed" and len(run.attempts) == 2
    first, second = run.attempts
    assert first["outcome"] == "failed" and first["failure_kind"] == "out_of_memory"
    assert "checkpointing" in first["repair"]
    assert fake.started[0]["config"]["gradient_checkpointing"] is False
    assert fake.started[1]["config"]["gradient_checkpointing"] is True
    assert second["outcome"] == "passed"


def test_a_vanished_process_resumes_the_same_plan(tmp_path, store):
    vanished = {"status": "failed", "return_code": 1, "log": "step 10 loss 1.2\nstep 20 loss 1.1\n",
                "summary": {}}
    fake = FakeOps(tmp_path, [vanished, GOOD])
    run = tg.run_training_goal(_config(), fake.ops(), store)
    assert run.status == "completed" and run.attempts[0]["failure_kind"] == "killed"
    first, second = (s["config"] for s in fake.started)
    assert first["resume"] == "" and second["resume"] == "auto"
    assert {k: v for k, v in first.items() if k != "resume"} == {k: v for k, v in second.items() if k != "resume"}


def test_a_traceback_is_not_mistaken_for_a_kill(tmp_path, store):
    crash = {"status": "failed", "return_code": 1, "log": "Traceback (most recent call last):\nValueError: bad row",
             "summary": {}}
    run = tg.run_training_goal(_config(), FakeOps(tmp_path, [crash]).ops(), store)
    assert run.status == "failed" and run.attempts[0]["failure_kind"] == "unknown"
    assert len(run.attempts) == 1  # nothing to repair, so nothing was retried


def test_non_finite_loss_lowers_the_learning_rate_then_gives_up(tmp_path, store):
    nan = {"status": "failed", "return_code": planner.EXIT_NON_FINITE_LOSS,
           "summary": {"status": "failed", "reason": "non_finite_loss"}, "log": "NON_FINITE loss=nan"}
    fake = FakeOps(tmp_path, [nan, nan, nan, nan])
    run = tg.run_training_goal(_config(max_attempts=4), fake.ops(), store)
    assert run.status == "failed" and "no repair applies" in run.error
    rates = [s["config"]["learning_rate"] for s in fake.started]
    assert rates[1] == rates[0] * 0.5 and fake.started[2]["config"]["precision"] == "fp32"
    assert len(fake.started) == 3  # the ladder ends after fp32


def test_attempts_are_capped(tmp_path, store):
    fake = FakeOps(tmp_path, [OOM])
    run = tg.run_training_goal(_config(max_attempts=2, quality_boosts=0), fake.ops(), store)
    assert run.status == "failed" and len(fake.started) == 2 and "No attempt succeeded" in run.error


# ------------------------------------------------------------- verification
def test_a_completed_job_with_no_weights_does_not_pass(tmp_path, store):
    fake = FakeOps(tmp_path, [{"status": "completed", "return_code": 0, "weights": False}])
    run = tg.run_training_goal(_config(max_attempts=1, quality_boosts=0), fake.ops(), store)
    assert run.status == "failed"
    assert any("missing or empty" in f for f in run.attempts[0]["verification"]["failures"])


def test_nan_weights_fail_even_when_the_job_says_completed(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(_config(max_attempts=1, quality_boosts=0),
                               fake.ops(adapter_is_finite=lambda path: False), store)
    assert run.status == "failed" and "NaN or infinity" in " ".join(run.attempts[0]["verification"]["failures"])


def test_a_non_finite_metric_fails_verification(tmp_path, store):
    summary = {"status": "completed", "metrics": {"train_loss": float("nan"), "eval_loss": 1.0}}
    fake = FakeOps(tmp_path, [{"status": "completed", "return_code": 0, "summary": summary}])
    run = tg.run_training_goal(_config(max_attempts=1, quality_boosts=0), fake.ops(), store)
    assert run.status == "failed" and "train_loss is not finite" in " ".join(run.attempts[0]["verification"]["failures"])


def test_a_target_loss_is_enforced_and_a_longer_run_is_tried_once(tmp_path, store):
    weak = {"status": "completed", "return_code": 0,
            "summary": {"status": "completed", "metrics": {"train_loss": 1.9, "eval_loss": 2.0}}}
    strong = {"status": "completed", "return_code": 0,
              "summary": {"status": "completed", "metrics": {"train_loss": 1.0, "eval_loss": 1.2}}}
    fake = FakeOps(tmp_path, [weak, strong])
    run = tg.run_training_goal(
        _config(target_eval_loss=1.5, quality_boosts=1, config={"max_steps": 100}), fake.ops(), store)
    assert run.status == "completed" and run.quality_boosts_used == 1
    assert run.attempts[0]["failure_kind"] == "quality_below_target"
    assert fake.started[0]["config"]["max_steps"] == 100
    assert fake.started[1]["config"]["max_steps"] == 150  # 1.5x budget


def test_a_target_loss_that_is_never_met_fails(tmp_path, store):
    weak = {"status": "completed", "return_code": 0,
            "summary": {"status": "completed", "metrics": {"train_loss": 1.9, "eval_loss": 2.0}}}
    run = tg.run_training_goal(_config(target_eval_loss=1.0, quality_boosts=1, config={"max_steps": 10}),
                               FakeOps(tmp_path, [weak]).ops(), store)
    assert run.status == "failed" and run.quality_boosts_used == 1


# ------------------------------------------------------------------ install
def test_install_runs_after_a_verified_job_and_a_failure_is_reported(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    run = tg.run_training_goal(_config(install=True), fake.ops(), store)
    assert run.status == "completed" and fake.installed == ["job_1"] and run.completed_stages[-1] == "install"

    bad = FakeOps(tmp_path / "b", [GOOD])
    bad.install_result = {"status": "error", "message": "ollama create failed"}
    failed = tg.run_training_goal(_config(install=True), bad.ops(), store)
    assert failed.status == "failed" and "ollama create failed" in failed.error


def test_nothing_is_installed_unless_asked(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    tg.run_training_goal(_config(), fake.ops(), store)
    assert fake.installed == []


# ----------------------------------------------------------- stop / deadline
def test_cancel_stops_the_running_job_and_marks_the_goal_cancelled(tmp_path, store):
    fake = FakeOps(tmp_path, [{"hang": True}])
    stop = threading.Event()
    original_sleep = fake.sleep

    def sleep_then_stop(seconds):
        stop.set()
        original_sleep(seconds)

    ops = fake.ops(sleep=sleep_then_stop)
    # make the job finish once the cancel has been sent
    original_get = fake.get_job

    def get_job(job_id):
        if fake.cancelled:
            fake.jobs[job_id]["spec"] = {"status": "cancelled", "return_code": 1}
        return original_get(job_id)

    ops.get_job = get_job
    run = tg.run_training_goal(_config(), ops, store, stop_event=stop, cancel_flag=lambda: True)
    assert run.status == "cancelled" and fake.cancelled == ["job_1"] and run.finished_at


def test_pause_keeps_the_run_resumable(tmp_path, store):
    fake = FakeOps(tmp_path, [{"hang": True}, GOOD])
    stop = threading.Event()
    original_get = fake.get_job

    def get_job(job_id):
        if fake.cancelled:
            fake.jobs[job_id]["spec"] = {"status": "cancelled", "return_code": 1}
        return original_get(job_id)

    def sleep_then_stop(seconds):
        stop.set()

    ops = fake.ops(sleep=sleep_then_stop)
    ops.get_job = get_job
    run = tg.run_training_goal(_config(), ops, store, stop_event=stop, cancel_flag=lambda: False)
    assert run.status == "paused" and not run.finished_at and run.completed_stages == ["probe", "plan"]

    # Resume: probe and plan are not repeated, and the interrupted attempt is treated as a vanished process.
    fake2 = FakeOps(tmp_path / "r", [GOOD])
    result = tg.resume_training_goal(run.goal_run_id, _config(), fake2.ops(), store, blocking=True)
    assert result["status"] == "started"
    resumed = store.load(run.goal_run_id)
    assert resumed.status == "completed" and resumed.completed_stages == ["probe", "plan", "train"]
    assert fake2.started[0]["config"]["resume"] == "auto"
    assert resumed.attempts[0]["outcome"] == "vanished"


def test_the_deadline_cancels_a_job_that_never_finishes(tmp_path, store):
    clock = {"now": 0.0}
    fake = FakeOps(tmp_path, [{"hang": True}], clock=clock)
    original_get = fake.get_job

    def get_job(job_id):
        if fake.cancelled:
            fake.jobs[job_id]["spec"] = {"status": "cancelled", "return_code": 1}
        return original_get(job_id)

    ops = fake.ops()
    ops.get_job = get_job
    run = tg.run_training_goal(_config(max_hours=1.0, poll_seconds=1800.0), ops, store)
    assert run.status == "failed" and "1-hour limit" in run.error and fake.cancelled == ["job_1"]
    assert run.attempts[0]["outcome"] == "deadline"


# ------------------------------------------------------------ resume / API
def test_resuming_an_unknown_or_finished_goal_is_reported(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    assert tg.resume_training_goal("nope", _config(), fake.ops(), store)["status"] == "not_found"
    run = tg.run_training_goal(_config(), fake.ops(), store)
    assert tg.resume_training_goal(run.goal_run_id, _config(), fake.ops(), store)["status"] == "already_completed"


def test_resume_refuses_while_the_trainer_is_still_running(tmp_path, store):
    fake = FakeOps(tmp_path, [{"hang": True}])
    run = tg.TrainingGoalRun(goal_run_id="tg-live", dataset_id="ds_1", status="paused", completed_stages=["probe", "plan"],
                             attempts=[{"attempt": 1, "job_id": "job_1", "outcome": "running"}])
    fake.start_job("ds_1", None, None, {})
    store.save(run)
    assert tg.resume_training_goal("tg-live", _config(), fake.ops(), store)["status"] == "job_still_running"


def test_start_blocking_registers_and_reports(tmp_path, store):
    fake = FakeOps(tmp_path, [GOOD])
    started = tg.start_training_goal(_config(), fake.ops(), store, blocking=True)
    assert started["status"] == "started"
    status = tg.get_training_goal_status(started["goal_run_id"], store)
    assert status["status"] == "completed"
    assert tg.list_training_goals(store)[0]["goal_run_id"] == started["goal_run_id"]
    assert tg.get_training_goal_status("zzz", store)["status"] == "not_found"


def test_only_one_goal_runs_at_a_time(tmp_path, store):
    release = threading.Event()
    fake = FakeOps(tmp_path, [{"hang": True}])

    def sleep(seconds):
        release.wait(timeout=5)

    original_get = fake.get_job

    def get_job(job_id):
        if release.is_set() and fake.cancelled:
            fake.jobs[job_id]["spec"] = {"status": "cancelled", "return_code": 1}
        return original_get(job_id)

    ops = fake.ops(sleep=sleep)
    ops.get_job = get_job
    first = tg.start_training_goal(_config(), ops, store)
    assert first["status"] == "started"
    for _ in range(200):
        if fake.started:
            break
        threading.Event().wait(0.01)
    second = tg.start_training_goal(_config(), fake.ops(), store)
    assert second["status"] == "already_running"
    cancelled = tg.cancel_training_goal(first["goal_run_id"])
    assert cancelled["status"] == "cancelling"
    release.set()
    thread = tg._THREADS.get(first["goal_run_id"])
    if thread:
        thread.join(timeout=10)
    assert tg.pause_training_goal("unknown")["status"] == "not_found"


# ---------------------------------------------------------------- adapters
def test_adapter_finiteness_check_reads_real_tensors(tmp_path):
    np = pytest.importorskip("numpy")
    st = pytest.importorskip("safetensors.numpy")
    good, bad = tmp_path / "good.safetensors", tmp_path / "bad.safetensors"
    st.save_file({"a": np.ones((2, 2), dtype="float32")}, str(good))
    st.save_file({"a": np.array([[1.0, float("nan")]], dtype="float32")}, str(bad))
    assert tg.check_adapter_finite(good) is True
    assert tg.check_adapter_finite(bad) is False
    assert tg.check_adapter_finite(tmp_path / "missing.safetensors") is None
