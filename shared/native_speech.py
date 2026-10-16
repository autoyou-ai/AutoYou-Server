# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Generation ownership for native-call speech workers and recorder replacement."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time

from shared.audio_workers import AudioWorkerOwner


class SpeechGeneration:
    def __init__(self, number, *, signal_recorder, release_recorder, timeout):
        self.number = number
        self.workers = AudioWorkerOwner(max_workers=8)
        self._signal = signal_recorder
        self._release = release_recorder
        self._timeout = timeout
        self._condition = threading.Condition()
        self._recorders = []
        self._users = 0
        self.complete = False
        self.failure = None

    def own(self, recorder, *, initialized=False):
        with self._condition:
            slot = next((s for s in self._recorders if s[0] is recorder), None)
            if slot is None:
                if len(self._recorders) >= 4:
                    raise RuntimeError("native recorder attempt capacity exhausted")
                slot = [recorder, initialized]
                self._recorders.append(slot)
            elif initialized:
                slot[1] = True
            stopped = self.workers.stopped.is_set()
        if stopped:
            self._signal(recorder)

    def discard_failed_attempt(self, recorder):
        # Called only after its constructor has unwound. Backend workers still
        # belong to the slot until the release callback proves physical exit.
        self._signal(recorder)
        self._release(recorder, initialized=False)
        with self._condition:
            self._recorders = [s for s in self._recorders if s[0] is not recorder]

    @contextmanager
    def use(self, recorder):
        with self._condition:
            admitted = not self.workers.stopped.is_set() and any(
                s[0] is recorder and s[1] for s in self._recorders)
            if admitted:
                self._users += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self._condition:
                    self._users -= 1
                    self._condition.notify_all()

    def fence(self):
        self.workers.fence()

    def close(self):
        if self.failure is not None:
            raise self.failure
        if self.complete:
            return
        self.fence()
        try:
            with self._condition:
                recorders = tuple(self._recorders)
            failures = []
            for recorder, _ in recorders:
                try:
                    self._signal(recorder)
                except BaseException as exc:
                    failures.append(exc)
            try:
                self.workers.join(timeout=self._timeout())
            except BaseException as exc:
                failures.append(exc)
            deadline = time.monotonic() + self._timeout()
            with self._condition:
                while self._users:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        failures.append(RuntimeError("native recorder consumer cleanup did not finish"))
                        break
                    self._condition.wait(remaining)
                # A constructor may have registered or finished a recorder after
                # the first signal. The initializer must have exited before any
                # of these models can be released.
                recorders = tuple(self._recorders)
            if failures:
                raise failures[0]
            for recorder, initialized in recorders:
                self._signal(recorder)
                self._release(recorder, initialized=initialized)
                with self._condition:
                    self._recorders = [s for s in self._recorders if s[0] is not recorder]
            self.complete = True
        except BaseException as exc:
            self.failure = exc
            raise


class NativeSpeechLifecycle:
    def __init__(self, *, on_request, start_generation, signal_recorder, release_recorder,
                 on_failure, timeout):
        self._on_request = on_request
        self._start_generation = start_generation
        self._signal = signal_recorder
        self._release = release_recorder
        self._on_failure = on_failure
        self._timeout = timeout
        self._condition = threading.Condition()
        self._coordinator = AudioWorkerOwner(max_workers=1)
        self._active = None
        self._pending = None  # One latest settings request, never an unbounded queue.
        self._requested = 0
        self._accepting = False
        self._closing = False
        self.failure = None
        self.cleanup_failure = None
        self._coordinator.start(self._run, name="Native-Speech-Replacement")

    def request(self, settings, *, expected_generation=None):
        with self._condition:
            if self._closing or self.failure is not None:
                return False
            if expected_generation is not None and expected_generation != self._requested:
                return False
            self._requested += 1
            self._accepting = settings is not None
            if self._active is not None:
                self._active.fence()
            self._on_request(self._requested)
            self._pending = (self._requested, settings)
            self._condition.notify_all()
            return True

    def suspend(self):
        # An idle request still advances the fence immediately. The coordinator
        # joins the previous model, but never allocates a replacement while idle.
        return self.request(None)

    def generation(self, number):
        with self._condition:
            owner = self._active
            return owner if owner is not None and owner.number == number else None

    def current(self, number):
        with self._condition:
            return (not self._closing and self.failure is None and self._accepting
                    and self._requested == number)

    def admit(self, number, callback):
        # Warm-up input may precede the model allocation. Its generation and
        # consent must be checked atomically with the bounded queue write.
        with self._condition:
            if not self.current(number):
                return False
            callback()
            return True

    def publish(self, number, callback):
        with self._condition:
            if not self.current(number):
                return False
            owner = self._active
            if owner is None or owner.number != number or owner.workers.stopped.is_set():
                return False
            callback()
            return True

    @contextmanager
    def use(self, recorder):
        with self._condition:
            owner = self._active
            current = (not self._closing and self.failure is None and self._accepting and owner is not None
                       and owner.number == self._requested)
        if not current:
            yield False
            return
        with owner.use(recorder) as admitted:
            yield admitted

    def fence(self):
        with self._condition:
            self._closing = True
            self._pending = None
            if self._active is not None:
                self._active.fence()
            self._coordinator.fence()
            self._condition.notify_all()

    def close(self):
        self.fence()
        self._coordinator.join(timeout=4 * self._timeout())
        if self.failure is not None:
            raise self.failure
        owner = self._active
        if owner is not None:
            owner.close()

    def _run(self):
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closing:
                        self._condition.wait()
                    owner = self._active
                    closing = self._closing
                if owner is not None:
                    owner.close()
                if closing:
                    return
                with self._condition:
                    if self._closing:
                        return
                    number, settings = self._pending
                    self._pending = None
                    if settings is None:
                        self._active = None
                        continue
                    owner = SpeechGeneration(number, signal_recorder=self._signal,
                        release_recorder=self._release, timeout=self._timeout)
                    self._active = owner
                self._start_generation(owner, settings)
        except BaseException as exc:
            with self._condition:
                self.failure = exc
                self._pending = None
                owner = self._active
                if owner is not None:
                    owner.fence()
                self._condition.notify_all()
            if owner is not None:
                try:
                    owner.close()
                except BaseException as cleanup_error:
                    self.cleanup_failure = cleanup_error
            self._on_failure(exc)
