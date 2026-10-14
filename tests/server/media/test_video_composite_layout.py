# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""One video track keeps desktop pixels, thumbnail placement, and input in sync."""

import asyncio
import json
from pathlib import Path

import pytest
import server
from shared import video_call_manager as video


class SyntheticSource:
    def __init__(self, size, color):
        self.output_size = size
        self.color = color
        self.has_live_content = True
        self.monitor_id = 0
        self.captures = 0
        self.enabled = False

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def capture_preview_image(self, *, max_width):
        assert self.enabled
        self.captures += 1
        image = video.Image.new("RGB", self.output_size, self.color)
        # Large corner markers prove that fitting preserves the whole image.
        for box in ((0, 0, 40, 40), (self.output_size[0] - 40, self.output_size[1] - 40,
                                   self.output_size[0], self.output_size[1])):
            image.paste((250, 250, 250), box)
        return image

    def desktop_bounds(self):
        return {"left": 100, "top": 200, "width": 1600, "height": 900}

    def map_output_point_to_desktop(self, x, y):
        return round(100 + x * 1599), round(200 + y * 899)


@pytest.fixture
def sources():
    pytest.importorskip("PIL")
    desktop = SyntheticSource((1600, 900), (180, 20, 20))
    camera = SyntheticSource((640, 480), (20, 180, 20))
    return desktop, camera


def _track(sources, **kwargs):
    desktop, camera = sources
    return video.CompositeVideoStreamTrack(
        [("remote_desktop", desktop), ("camera", camera)], max_width=1280, **kwargs,
    )


@pytest.mark.parametrize("layout", ["below", "beside"])
@pytest.mark.parametrize("size_percent", [10, 25, 50])
@pytest.mark.parametrize("secondary_size", [(640, 480), (480, 640), (1600, 900)])
def test_desktop_is_larger_and_secondary_is_complete(sources, layout, size_percent, secondary_size):
    desktop, camera = sources
    camera.output_size = secondary_size
    track = _track(sources, layout=layout, secondary_size_percent=size_percent)
    track.enable()
    image = track._compose_images([source.capture_preview_image(max_width=1280) for source in sources])
    mapping = track.remote_desktop_mapping()
    _names, _height, rects = track._current_layout()
    dx, dy, dw, dh = rects[0]
    cx, cy, cw, ch = rects[1]

    assert image.size == track.output_size == (mapping["frame_width"], mapping["frame_height"])
    assert image.width % 2 == image.height % 2 == 0
    assert dw > 800  # The old equal tile gives the desktop only 636 pixels.
    assert cw <= dw * size_percent / 100 + 1
    assert ch <= dh * size_percent / 100 + 1
    assert abs(cw / ch - secondary_size[0] / secondary_size[1]) < 0.04
    assert image.getpixel((dx + dw // 2, dy + dh // 2)) == desktop.color
    assert image.getpixel((cx + cw // 2, cy + ch // 2)) == camera.color
    assert image.getpixel((cx + 1, cy + 1)) == (250, 250, 250)
    assert image.getpixel((cx + cw - 2, cy + ch - 2)) == (250, 250, 250)
    assert track.map_output_point_to_desktop((cx + cw / 2) / image.width, (cy + ch / 2) / image.height) is None
    assert track.map_output_point_to_desktop((dx + dw / 2) / image.width, (dy + dh / 2) / image.height) == (900, 650)
    if layout == "below":
        assert (dx, dy, dw, dh) == (0, 0, 1280, 720)
        assert cy >= dh + 4
        assert image.height == 720 + 4 + ch + ch % 2
    else:
        assert cx >= dx + dw + 2
        assert image.height <= 720
    assert track.map_output_point_to_desktop(1.0, 1.0) is None


@pytest.mark.parametrize("layout", ["below", "beside"])
def test_layout_uses_screen_as_primary_regardless_of_selection_order(sources, layout):
    desktop, camera = sources
    track = video.CompositeVideoStreamTrack([("video_file", camera), ("remote_desktop", desktop)], layout=layout)
    track.enable()
    image = track._compose_images([source.capture_preview_image(max_width=1280) for source in (camera, desktop)])
    assert image.getpixel((100, 100)) == desktop.color
    assert track.remote_desktop_mapping()["source_index"] == 1
    assert track.remote_desktop_mapping()["content_rect"]["width"] > 0.8


@pytest.mark.parametrize("layout", ["below", "beside"])
def test_multiple_secondary_sources_fit_outside_ultrawide_desktop(sources, layout):
    desktop, camera = sources
    desktop.output_size = (2400, 900)
    file_source = SyntheticSource((480, 640), (20, 20, 180))
    api_source = SyntheticSource((1600, 900), (180, 180, 20))
    selected = [("api", api_source), ("camera", camera), ("remote_desktop", desktop), ("video_file", file_source)]
    track = video.CompositeVideoStreamTrack(selected, layout=layout, secondary_size_percent=50)
    track.enable()
    image = track._compose_images([source.capture_preview_image(max_width=1280) for _name, source in selected])
    _sources, _height, rects = track._current_layout()
    dx, dy, dw, dh = rects[2]
    for index, (_name, source) in enumerate(selected):
        x, y, w, h = rects[index]
        assert x >= 0 and y >= 0 and x + w <= image.width and y + h <= image.height
        assert image.getpixel((x + w // 2, y + h // 2)) == source.color
        if index != 2:
            assert x >= dx + dw or y >= dy + dh
    if layout == "below":
        assert (dw, dh) == (1280, 480)


def test_preview_does_not_replace_emitted_frame_mapping_and_disabled_never_captures(sources):
    track = _track(sources, layout="below")
    assert track.capture_preview_image(max_width=640).getbbox() is None
    assert all(source.captures == 0 for source in sources)
    track.enable()
    image = track._compose_images([source.capture_preview_image(max_width=1280) for source in sources])
    mapping = track.remote_desktop_mapping()
    preview = track.capture_preview_image(max_width=640)
    assert preview.size == (640, 454)  # Half-sized desktop plus a 4-pixel gap and thumbnail.
    assert track.remote_desktop_mapping() == mapping
    assert track.output_size == image.size == (1280, 904)
    track.disable()
    captures = [source.captures for source in sources]
    assert track.capture_preview_image(max_width=640).getbbox() is None
    assert [source.captures for source in sources] == captures


@pytest.mark.parametrize("layout", ["below", "beside"])
def test_missing_camera_and_game_mode_restore_full_screen(sources, layout):
    track = _track(sources, layout=layout)
    track.enable()
    sources[1].has_live_content = False
    assert track.capture_preview_image(max_width=640).size == (640, 360)
    assert track.remote_desktop_mapping()["content_rect"]["width"] == 1.0
    sources[1].has_live_content = True
    track.set_game_mode(True)
    assert not sources[1].enabled
    assert track.remote_desktop_mapping()["source_count"] == 1
    assert track.output_size == (1280, 720)
    track.set_game_mode(False)
    assert sources[1].enabled
    assert track.remote_desktop_mapping()["source_count"] == 2
    track.set_remote_desktop_enabled(False)
    assert track.remote_desktop_mapping() == {}
    assert track.capture_preview_image(max_width=640).size == (640, 360)


def test_single_track_updates_layout_and_size_in_active_recv(sources):
    pytest.importorskip("av")

    async def run():
        track = _track(sources, fps=30)
        track.enable()
        first = await track.recv()
        track.apply_composite_layout(layout="below", secondary_size_percent=25)
        second = await track.recv()
        mapping = track.remote_desktop_mapping()
        track.apply_composite_layout(layout="below", secondary_size_percent=10)
        third = await track.recv()
        assert (first.width, first.height) == (1280, 720)
        assert (second.width, second.height) == (1280, 904)
        assert (third.width, third.height) == (1280, 796)
        assert first.pts < second.pts < third.pts
        assert mapping["frame_height"] == second.height
        assert track.remote_desktop_mapping()["frame_height"] == third.height
        track.disable()

    asyncio.run(run())


@pytest.mark.parametrize("codec", ["h264", "vp8"])
def test_existing_webrtc_encoder_decodes_changing_layout_dimensions(sources, codec):
    av = pytest.importorskip("av")
    pytest.importorskip("aiortc")
    from aiortc.codecs.h264 import H264Encoder, h264_depayload
    from aiortc.codecs.vpx import Vp8Encoder, vp8_depayload
    from fractions import Fraction

    encoder, depayload = (H264Encoder(), h264_depayload) if codec == "h264" else (Vp8Encoder(), vp8_depayload)
    decoder = av.CodecContext.create(codec, "r")
    track = _track(sources)
    track.enable()
    for timestamp, (layout, size) in enumerate((("grid", 25), ("below", 25), ("below", 10), ("beside", 25)), 1):
        track.apply_composite_layout(layout=layout, secondary_size_percent=size)
        image = track._compose_images([source.capture_preview_image(max_width=1280) for source in sources])
        frame = av.VideoFrame.from_image(image)
        frame.pts, frame.time_base = timestamp * 9000, Fraction(1, 90000)
        packets, _timestamp = encoder.encode(frame, force_keyframe=True)
        decoded = decoder.decode(av.Packet(b"".join(depayload(packet) for packet in packets)))
        assert decoded and (decoded[-1].width, decoded[-1].height) == image.size
    track.disable()


def test_settings_persist_partial_updates_and_default_to_existing_layout():
    cfg = server._default_config()
    assert cfg["video_call"]["composite"] == {"layout": "grid", "secondary_size_percent": 25}
    cfg, touched, _theme = server._apply_admin_ui_config_patch(cfg, {
        "video_call": {"composite": {"layout": "BELOW", "secondary_size_percent": "20"}},
    })
    assert "video_call" in touched
    assert cfg["video_call"]["composite"] == {"layout": "below", "secondary_size_percent": 20}
    cfg, _touched, _theme = server._apply_admin_ui_config_patch(cfg, {
        "video_call": {"composite": {"secondary_size_percent": 5}},
    })
    assert cfg["video_call"]["composite"] == {"layout": "below", "secondary_size_percent": 10}
    cfg, _touched, _theme = server._apply_admin_ui_config_patch(cfg, {
        "video_call": {"composite": {"secondary_size_percent": 99}},
    })
    assert cfg["video_call"]["composite"]["secondary_size_percent"] == 50
    assert server._normalize_video_call_config(cfg["video_call"])["composite"] == cfg["video_call"]["composite"]


@pytest.mark.parametrize("composite", [{"layout": "overlay"}, {"secondary_size_percent": "bad"}, "below"])
def test_bad_layout_settings_are_rejected(composite):
    with pytest.raises(ValueError, match="Video layout|Camera / file size"):
        server._apply_admin_ui_config_patch(server._default_config(), {"video_call": {"composite": composite}})


def test_configured_track_receives_layout_and_live_save_updates_once(monkeypatch, sources):
    cfg = server._default_config()
    cfg["video_call"]["outbound_sources"] = ["remote_desktop", "camera"]
    cfg["video_call"]["composite"] = {"layout": "below", "secondary_size_percent": 20}
    monkeypatch.setattr(server, "_get_available_video_outbound_sources", lambda **_kwargs: ["remote_desktop", "camera"])
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", lambda **_kwargs: sources[0])
    monkeypatch.setattr(server, "create_outbound_video_track", lambda kind, **kwargs:
                        video.CompositeVideoStreamTrack(**kwargs) if kind == "composite" else sources[1])
    track = server._create_configured_outbound_video_track(cfg=cfg)
    assert track.layout == "below" and track.secondary_size_percent == 20
    updated = []
    original_apply = track.apply_composite_layout

    def record_update(**kwargs):
        updated.append(kwargs)
        original_apply(**kwargs)

    monkeypatch.setattr(track, "apply_composite_layout", record_update)
    cfg["video_call"]["composite"] = {"layout": "beside", "secondary_size_percent": 10}
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server, "_get_outbound_video_available", lambda **_kwargs: True)
    manager = server.WebRTCManager()
    manager.desktop_video_tracks["synthetic-layout-session"] = track
    manager.desktop_video_tracks["synthetic-layout-alias"] = track
    asyncio.run(manager.apply_video_call_settings())
    assert manager.desktop_video_tracks["synthetic-layout-session"] is track
    assert updated == [{"layout": "beside", "secondary_size_percent": 10}]
    assert track.layout == "beside" and track.secondary_size_percent == 10


def test_video_settings_ui_saves_and_reloads_layout(monkeypatch, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(server.STATE, "config", server._default_config())
    bootstrap = asyncio.run(server._build_admin_ui_bootstrap_payload())
    assets = Path(server.__file__).resolve().parent / "assets"
    config_posts = []

    def handle(route):
        path = route.request.url.split("admin.test", 1)[-1].split("?", 1)[0]
        if path == "/":
            return route.fulfill(content_type="text/html", body=(
                '<!DOCTYPE html><html><head><link rel="stylesheet" href="/assets/admin-ui.css"></head>'
                '<body><div id="autoyou-admin-root"></div><script src="/assets/admin-ui.js"></script></body></html>'
            ))
        if path in {"/assets/admin-ui.js", "/assets/admin-ui.css"}:
            return route.fulfill(content_type="text/javascript" if path.endswith(".js") else "text/css",
                                 body=(assets / path.rsplit("/", 1)[1]).read_text(encoding="utf-8"))
        if path == "/api/admin/config" and route.request.method == "POST":
            payload = json.loads(route.request.post_data)
            config_posts.append(payload)
            bootstrap["config"], _touched, _theme = server._apply_admin_ui_config_patch(bootstrap["config"], payload)
            return route.fulfill(content_type="application/json", body=json.dumps(bootstrap, default=str))
        if path == "/api/admin/bootstrap":
            return route.fulfill(content_type="application/json", body=json.dumps(bootstrap, default=str))
        return route.fulfill(content_type="application/json", body="{}")

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1600})
            page.route("**/*", handle)
            page.goto("http://admin.test/")
            page.get_by_role("button", name="Video & Calls", exact=True).click()
            placement = page.locator('[data-bind="videoCall.composite.layout"]')
            size = page.locator('[data-bind="videoCall.composite.secondary_size_percent"]')
            assert placement.input_value() == "grid"
            assert size.input_value() == "25"
            placement.select_option("below")
            size.fill("15")
            page.get_by_role("button", name="Save system settings", exact=True).click()
            page.wait_for_function("document.querySelector('[data-bind=\"videoCall.composite.layout\"]').value === 'below'")
            assert len(config_posts) == 1
            assert bootstrap["config"]["video_call"]["composite"] == {"layout": "below", "secondary_size_percent": 15}
            page.reload()
            page.get_by_role("button", name="Video & Calls", exact=True).click()
            assert placement.input_value() == "below"
            assert size.input_value() == "15"
            assert size.get_attribute("min") == "10"
            assert size.get_attribute("max") == "50"
            page.screenshot(path=str(tmp_path / "video-layout-settings.png"), full_page=True)
        finally:
            browser.close()
