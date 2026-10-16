# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Native game control pins one engine attachment for the entire input lease."""
from shared.session_transport import SessionDenied
import uuid


def validate_game_binding(value):
    if not isinstance(value, dict) or set(value) != {"version", "kind", "engine_id"} or \
            type(value.get("version")) is not int or value["version"] != 1 or \
            not isinstance(value["kind"], str) or value["kind"] not in {"engine", "host"} or not isinstance(value["engine_id"], str):
        raise SessionDenied("native game grant has no selected engine")
    if value["kind"] == "engine":
        try: uuid.UUID(value["engine_id"])
        except (ValueError, TypeError, AttributeError) as error:
            raise SessionDenied("native game grant has an invalid engine identity") from error
    elif value["engine_id"] != "":
        raise SessionDenied("native host game grant changed its target")
    return dict(value)


class NativeGameInputPort:
    def __init__(self, *, hub, session_id, control_id):
        self.hub, self.session_id, self.control_id = hub, session_id, control_id
        self.game_binding = hub.binding()
        if self.game_binding is None:
            raise SessionDenied("native game has no selected engine")
        self.queue = hub.queue_for_binding(self.game_binding)
        self._started = False
        self._closed = False

    def check_current(self):
        if self._closed or self.hub.queue_for_binding(self.game_binding) is not self.queue:
            raise SessionDenied("native game engine changed; request control again")

    def check_payload(self, payload):
        self.check_current()
        if validate_game_binding(payload.get("native_game")) != self.game_binding:
            raise SessionDenied("native game input changed its selected engine")

    async def prepare_async(self):
        self.check_current()
        self._started = True
        self.hub.publish(self.session_id, dict(event="game_input", input_type="session_start", control_id=self.control_id))

    async def apply_async(self, frame, track):
        self.check_current()
        self.hub.publish(self.session_id, frame)
        return True

    async def release_async(self, buttons, keys):
        self.check_current()
        for key in keys:
            self.hub.publish(self.session_id, dict(event="remote_desktop_keyboard", action="key", key=key,
                phase="up", control_id=self.control_id))
        for button in buttons:
            self.hub.publish(self.session_id, dict(event="remote_desktop_input", input_type="button", button=button,
                phase="up", control_id=self.control_id))

    async def close_async(self, buttons, keys):
        self._closed = True
        if self._started:
            self.hub.retire_control(self.queue, self.session_id, self.control_id)


class NativeHostGameInputPort:
    """Explicit host fallback; a later engine attachment requires a new lease."""
    def __init__(self, *, hub, port, control_id):
        self.hub, self.port, self.control_id = hub, port, control_id
        self.game_binding = dict(version=1, kind="host", engine_id="")
        self.scope_check = lambda: None
        self._touch_id = self._point = None
        self._touch_blocked = False
        self._axes = {}
        self._settled = (set(), set(), set(), set())

    def check_current(self):
        if self.hub.binding() is not None:
            raise SessionDenied("native game engine changed; request control again")
        self.scope_check()

    def check_payload(self, payload):
        self.check_current()
        if validate_game_binding(payload.get("native_game")) != self.game_binding:
            raise SessionDenied("native game input changed its selected engine")

    def prepare(self):
        self.check_current()
        prepare = getattr(self.port, "prepare", None)
        if prepare is not None: prepare()
        self.check_current()

    def _plan(self, frame):
        commands, updates = [], {}
        def mouse(**values):
            commands.append(dict(event="remote_desktop_input", control_id=self.control_id, **values))
        def pulse(key):
            commands.append(dict(event="remote_desktop_keyboard", control_id=self.control_id,
                action="key", key=key, phase="press"))
        kind = frame["input_type"]
        if kind == "touch":
            points = frame["points"]
            blocked = len(points) > 1 or self._touch_blocked and bool(points)
            touch = points[0] if len(points) == 1 and not blocked else None
            identifier = touch["id"] if touch else None
            point = (touch["x"], touch["y"]) if touch else None
            if self._touch_id is not None and identifier != self._touch_id:
                mouse(input_type="button", button="left", phase="up")
            if touch is not None:
                if identifier != self._touch_id:
                    mouse(input_type="button", button="left", phase="down", x=point[0], y=point[1])
                elif point != self._point:
                    mouse(input_type="move", coordinate_mode="absolute", x=point[0], y=point[1])
            updates = dict(_touch_id=identifier, _point=point, _touch_blocked=blocked)
        elif kind == "axis" and frame["axis"] in {"left_x", "left_y"}:
            direction = -1 if frame["value"] < -0.55 else 1 if frame["value"] > 0.55 else 0
            if direction != self._axes.get(frame["axis"], 0) and direction:
                keys = ("left", "right") if frame["axis"] == "left_x" else ("up", "down")
                pulse(keys[0 if direction < 0 else 1])
            updates = dict(_axes={**self._axes, frame["axis"]: direction})
        elif kind == "button" and frame["phase"] == "down":
            key = {"action_a": "space", "jump": "space", "action_b": "right",
                "left": "left", "right": "right"}.get(frame["button"])
            if key: pulse(key)
        return commands, updates

    def reserved_inputs(self, frame):
        buttons, keys = set(), set()
        if frame["event"] == "game_input":
            commands, _ = self._plan(frame)
            for command in commands:
                if command["event"] == "remote_desktop_input" and command.get("phase") == "down":
                    buttons.add(command["button"])
                elif command["event"] == "remote_desktop_keyboard":
                    keys.add(command["key"])
        return buttons, keys

    def settled_inputs(self, frame):
        return self._settled if frame["event"] == "game_input" else (set(), set(), set(), set())

    def apply(self, frame, track):
        return self.apply_guarded(frame, track, lambda: None)

    def apply_guarded(self, frame, track, guard):
        self.check_current()
        def dispatch(command):
            guard()
            self.check_current()
            guarded = getattr(self.port, "apply_guarded", None)
            return guarded(command, track, guard) if callable(guarded) else self.port.apply(command, track)
        if frame["event"] != "game_input":
            return dispatch(frame)
        commands, updates = self._plan(frame)
        buttons, keys, released_buttons, released_keys = set(), set(), set(), set()
        for command in commands:
            self.check_current()
            if dispatch(command) is not True:
                return False
            if command["event"] == "remote_desktop_input" and command.get("input_type") == "button":
                button = command["button"]
                if command["phase"] == "down":
                    buttons.add(button); released_buttons.discard(button)
                else:
                    released_buttons.add(button); buttons.discard(button)
            elif command["event"] == "remote_desktop_keyboard":
                released_keys.add(command["key"])
        for name, value in updates.items(): setattr(self, name, value)
        self._settled = buttons, keys, released_buttons, released_keys
        return True

    def release(self, buttons, keys):
        self.port.release(buttons, keys)
