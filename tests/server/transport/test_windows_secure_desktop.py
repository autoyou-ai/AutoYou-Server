# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Protocol, auth, and routing tests for the Windows secure desktop helper.

The Win32 parts (service, desktop switching) only run on Windows; these tests
drive the helper's request handling and the client over real loopback
sockets with a fake pyautogui and mss.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import socket
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

import shared.windows_secure_desktop as sd
from shared.remote_desktop_keep_awake import (
    IN_USE_GRACE_SECONDS,
    RemoteDesktopKeepAwake,
    normalize_keep_awake_mode,
)


class _FakeBackend:
    def __init__(self):
        self.calls = []
        self.FAILSAFE = True
        self.PAUSE = 0.1

    def __getattr__(self, name):
        if name not in sd.ALLOWED_CALLS:
            raise AttributeError(name)

        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "position":
                return (12, 34)
            return None

        return _call


@pytest.fixture
def helper_server(tmp_path, monkeypatch):
    backend = _FakeBackend()
    helper = sd.SecureDesktopHelper("S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(helper, "_backend", lambda: backend)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    stop = threading.Event()

    def _accept():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            threading.Thread(target=helper.handle_connection, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    info_path = tmp_path / "helper.json"
    info_path.write_text(json.dumps({"port": listener.getsockname()[1], "token": helper.token}), encoding="utf-8")
    yield SimpleNamespace(helper=helper, backend=backend, path=info_path)
    stop.set()
    listener.close()


def test_frame_roundtrip_over_socketpair():
    left, right = socket.socketpair()
    try:
        left.sendall(sd.encode_frame({"op": "ping", "n": 1}, b"abc"))
        payload, blob = sd.read_frame(right)
        assert payload == {"op": "ping", "n": 1}
        assert blob == b"abc"
    finally:
        left.close()
        right.close()


def test_read_frame_rejects_oversized_blob():
    left, right = socket.socketpair()
    try:
        left.sendall(sd.encode_frame({"op": "x"}, b"12345"))
        with pytest.raises(ValueError):
            sd.read_frame(right, max_blob=4)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("system", [], {}),
        ("hotkey", ["ctrl", "c"], {}),
        ("moveTo", [1, 2], {"duration": 5}),
        ("write", ["x" * 65], {}),
        ("moveTo", [{"x": 1}], {}),
        ("moveTo", [1, 2, 3, 4, 5], {}),
        ("moveTo", [float("nan"), 1], {}),
    ],
)
def test_validate_call_rejects_unsafe_calls(name, args, kwargs):
    with pytest.raises(ValueError):
        sd.validate_call(name, args, kwargs)


def test_validate_call_accepts_remote_desktop_surface():
    assert sd.validate_call("mouseDown", [], {"button": "left"}) == ("mouseDown", [], {"button": "left"})
    assert sd.validate_call("write", ["a"], None) == ("write", ["a"], {})


def test_helper_rejects_wrong_token(helper_server):
    with socket.create_connection(("127.0.0.1", json.loads(helper_server.path.read_text())["port"]), timeout=2) as sock:
        sock.sendall(sd.encode_frame({"op": "hello", "token": "0" * 64}))
        reply, _ = sd.read_frame(sock)
    assert reply == {"ok": False, "error": "unauthorized"}
    assert helper_server.backend.calls == []


def test_client_forwards_calls_to_helper(helper_server):
    client = sd.SecureDesktopClient(helper_server.path)
    proxy = sd.SecureDesktopInputBackend(client)
    proxy.FAILSAFE = True  # ignored, fixed in the helper

    proxy.moveTo(100, 200)
    proxy.mouseDown(button="left")
    proxy.write("p")
    assert proxy.position() == (12, 34)
    assert helper_server.backend.calls[:3] == [
        ("moveTo", (100, 200), {}),
        ("mouseDown", (), {"button": "left"}),
        ("write", ("p",), {}),
    ]
    with pytest.raises(AttributeError):
        proxy.hotkey  # noqa: B018 - not part of the allowed surface
    assert client.recently_reachable() is True


def test_ctrl_alt_delete_is_sent_as_secure_attention_sequence(helper_server, monkeypatch):
    sent = []
    monkeypatch.setattr(sd, "IS_WINDOWS", True)
    monkeypatch.setattr(sd, "_send_sas", lambda: sent.append(True) or True)
    monkeypatch.setattr(sd, "attach_thread_to_input_desktop", lambda: "Winlogon")
    proxy = sd.SecureDesktopInputBackend(sd.SecureDesktopClient(helper_server.path))

    proxy.keyDown("ctrl")
    proxy.keyDown("alt")
    proxy.press("delete")
    proxy.keyUp("alt")
    proxy.keyUp("ctrl")

    assert sent == [True]
    names = [call[0] + ":" + str(call[1][0]) for call in helper_server.backend.calls]
    assert "press:delete" not in names
    assert names == ["keyDown:ctrl", "keyDown:alt", "keyUp:alt", "keyUp:ctrl"]


def test_client_capture_returns_image(helper_server, monkeypatch):
    pytest.importorskip("PIL")

    class _Shot:
        size = (4, 2)
        bgra = bytes([10, 20, 30, 255] * 8)

    class _Sct:
        monitors = [{"left": 0, "top": 0, "width": 4, "height": 2}]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, monitor):
            return _Shot()

    fake_mss = ModuleType("mss")
    fake_mss.mss = _Sct
    monkeypatch.setitem(sys.modules, "mss", fake_mss)

    image = sd.SecureDesktopClient(helper_server.path).capture(0)

    assert image.size == (4, 2)
    assert image.getpixel((0, 0)) == (30, 20, 10)


def test_missing_helper_reports_not_installed(tmp_path):
    client = sd.SecureDesktopClient(tmp_path / "missing.json")
    assert client.installed() is False
    assert client.available() is False
    assert client.last_error == "not_installed"
    assert client.recently_reachable() is False


def test_routing_is_inactive_off_windows(monkeypatch):
    monkeypatch.setattr(sd, "IS_WINDOWS", False)
    assert sd.secure_input_backend_if_needed() is None
    assert sd.capture_secure_desktop_image_if_needed(0) is None
    assert sd.secure_desktop_unavailable_hint() == ""
    assert sd.secure_desktop_status()["supported"] is False


def test_locked_screen_without_helper_raises_with_install_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "IS_WINDOWS", True)
    monkeypatch.setattr(sd, "secure_desktop_state", lambda **_: {"secure": True, "locked": True})
    monkeypatch.setattr(sd, "_CLIENT", sd.SecureDesktopClient(tmp_path / "missing.json"))

    with pytest.raises(sd.SecureDesktopError) as excinfo:
        sd.secure_input_backend_if_needed()
    assert "Install the AutoYou Secure Desktop Helper" in str(excinfo.value)


def test_unlocked_screen_keeps_normal_input(monkeypatch):
    monkeypatch.setattr(sd, "IS_WINDOWS", True)
    monkeypatch.setattr(sd, "secure_desktop_state", lambda **_: {"secure": False, "locked": False})
    assert sd.secure_input_backend_if_needed() is None


def test_keyboard_input_reports_failure_when_locked_without_helper(monkeypatch):
    import shared.remote_desktop_keyboard as keyboard

    def _locked():
        raise sd.SecureDesktopError("locked")

    monkeypatch.setattr(keyboard, "load_remote_input_backend", _locked)
    assert keyboard.execute_remote_desktop_keyboard(
        {"event": "remote_desktop_keyboard", "control_id": "c1", "action": "key", "key": "a", "phase": "press"}
    ) is False


def test_service_command_reenters_launcher_with_sid(monkeypatch):
    monkeypatch.setattr(sd, "launcher_command", lambda: [r"C:\Program Files\AutoYou\AutoYou.exe"])
    command = sd.service_command("S-1-5-21-1-2-3-1001")
    assert command == '"C:\\Program Files\\AutoYou\\AutoYou.exe" --secure-desktop service --allow-sid S-1-5-21-1-2-3-1001'


def test_sid_validation_and_sddl():
    assert sd._is_valid_sid("S-1-5-21-1-2-3-1001")
    assert not sd._is_valid_sid("S-1-5-21-1-2-3-1001)(A;;FA;;;WD")
    assert not sd._is_valid_sid("WD")
    sddl = sd._secure_sddl("S-1-5-21-1-2-3-1001", inherit=False)
    assert sddl == "O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;S-1-5-21-1-2-3-1001)"


def test_keep_awake_modes():
    assert normalize_keep_awake_mode(None) == "off"
    assert normalize_keep_awake_mode(True) == "in_use"
    assert normalize_keep_awake_mode("Always") == "always"
    assert normalize_keep_awake_mode("while-in-use") == "in_use"
    assert normalize_keep_awake_mode("bogus") == "off"


def test_keep_awake_in_use_follows_activity(monkeypatch):
    import shared.remote_desktop_keep_awake as keep_awake

    monkeypatch.setattr(keep_awake, "keep_awake_supported", lambda platform_name=None: False)
    now = [1000.0]
    controller = RemoteDesktopKeepAwake(clock=lambda: now[0])
    controller.configure("in_use")
    assert controller.should_be_active() is False
    controller.touch()
    assert controller.should_be_active() is True
    now[0] += IN_USE_GRACE_SECONDS + 1
    assert controller.should_be_active() is False
    controller.configure("always")
    assert controller.should_be_active() is True
    controller.configure("off")
    assert controller.should_be_active() is False
    assert controller.status()["mode"] == "off"
