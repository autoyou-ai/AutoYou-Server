"""Portable, declarative Assistant worlds. Packs never contain executable code or URLs."""
from __future__ import annotations
import json
import re
import base64
import io
from typing import Any, Mapping

SHAPES = ("tree", "cloud", "mountain", "flower", "star", "sun", "moon", "rock", "pond", "house", "ball", "sign", "circle", "rect", "path", "symbol", "photo")
ANIMATIONS = ("none", "sway", "float", "spin", "pulse")
REACTIONS = ("bounce", "spin", "grow", "color", "none")
SOUNDS = ("none", "bell", "pop", "water")


def _number(value, default, low, high):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not low <= value <= high:
        raise ValueError(f"World coordinates must be numbers between {low} and {high}")
    return value


def _text(value, default, limit):
    value = default if value is None else value
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"World text must be at most {limit} characters")
    return value


def _color(value, default):
    value = default if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("World colors must use #RRGGBB")
    return value


def _choice(value, choices, default):
    value = default if value is None else value
    if value not in choices:
        raise ValueError("Unsupported world shape, animation, reaction or sound")
    return value


def _photo(value):
    from PIL import Image
    prefix = "data:image/jpeg;base64,"
    try:
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ValueError
        data = base64.b64decode(value[len(prefix):], validate=True)
        if len(data) > 65536:
            raise ValueError
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as image:
            if max(image.size) > 1536:
                raise ValueError
            image.verify()
    except (ValueError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid world photo") from error
    return value


def validate_world(raw: Any) -> dict:
    source = {} if raw is None else raw
    if not isinstance(source, Mapping) or len(json.dumps(source).encode("utf-8")) > 131072:
        raise ValueError("World must be a JSON object under 128 KiB")
    if type(source.get("version", 1)) is not int or source.get("version", 1) != 1:
        raise ValueError("Unsupported world version")
    flags = {"enabled": False, "idle_enabled": True, "professional": False, "wander": True, "sound_enabled": False}
    for key, default in flags.items():
        flags[key] = source.get(key, default)
        if not isinstance(flags[key], bool):
            raise ValueError("World switches must be true or false")
    objects = source.get("objects", [])
    if not isinstance(objects, list) or len(objects) > 96:
        raise ValueError("Worlds support up to 96 objects")
    cleaned, used = [], set()
    for index, obj in enumerate(objects):
        if not isinstance(obj, Mapping):
            raise ValueError("World objects must be JSON objects")
        identifier = _text(obj.get("id"), f"object-{index}", 48)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", identifier) or identifier in used:
            raise ValueError("World object IDs must be unique letters, numbers, hyphens or underscores")
        used.add(identifier)
        path = _text(obj.get("path"), "", 4096)
        if path and not re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9.,+eE\s-]+", path):
            raise ValueError("Only SVG path coordinates are supported")
        artwork = {}
        if obj.get("photo") is not None or obj.get("shape") == "photo":
            artwork["photo"] = _photo(obj.get("photo"))
        if obj.get("glyph") is not None or obj.get("shape") == "symbol":
            glyph = obj.get("glyph")
            if not isinstance(glyph, str) or not 1 <= len(glyph.encode()) <= 32:
                raise ValueError("Invalid world symbol")
            artwork["glyph"] = glyph
        cleaned.append({
            **artwork, "id": identifier, "shape": _choice(obj.get("shape"), SHAPES, "circle"),
            "x": _number(obj.get("x"), 50, 0, 100), "y": _number(obj.get("y"), 65, 0, 100),
            "size": _number(obj.get("size"), 12, 2, 45),
            "color": _color(obj.get("color"), "#57b894"),
            "label": _text(obj.get("label"), "", 80), "path": path,
            "animation": _choice(obj.get("animation"), ANIMATIONS, "none"),
            "reaction": _choice(obj.get("reaction"), REACTIONS, "bounce"),
            "sound": _choice(obj.get("sound"), SOUNDS, "none"),
        })
    return {
        "version": 1, **flags,
        **({"background_photo": _photo(source["background_photo"])} if source.get("background_photo") is not None else {}),
        **{key: _color(source[key], "") for key in ("robot_glow", "robot_shell") if key in source and source[key] is not None},
        "name": _text(source.get("name"), "My Assistant world", 80),
        "sky": _color(source.get("sky"), "#142449"),
        "ground": _color(source.get("ground"), "#244a51"),
        "robot_size": _number(source.get("robot_size"), 48, 20, 80),
        "objects": cleaned,
    }

WORLD_INSTRUCTIONS = """Edit the Assistant's 2D world using assistant_update_world. First read it with assistant_get_world.
Use a complete JSON object: version:1, enabled:true, name, sky and ground (#RRGGBB), idle_enabled,
professional (calm movements), wander, sound_enabled (default false), robot_size (20..80 percent of height),
optional robot_glow (eyes, mouth and reactor) and robot_shell (body) colors in #RRGGBB; omit to use the skin's colors,
objects (at most 96). Each object: unique id, shape (tree/cloud/mountain/flower/star/sun/moon/rock/pond/house/ball/sign/circle/rect/path/symbol/photo),
x/y (0..100 percent), size (2..45 percent), color (#RRGGBB), label (80 chars), animation (none/sway/float/spin/pulse),
reaction (bounce/spin/grow/color/none), sound (none/bell/pop/water). For symbol set glyph to its Unicode character. Optional SVG path coordinates only, no markup.
Keep uploaded photos by their existing object IDs. Set background_photo to null only to remove the uploaded background.
Keep existing objects unless asked to change them. Use a few objects per step for small models. Never claim a change before the tool succeeds."""
