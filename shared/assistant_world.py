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
        if "lights_on" in obj and obj.get("lights_on") is not None:
            if not isinstance(obj.get("lights_on"), bool):
                raise ValueError("World object lights_on must be true or false")
            artwork["lights_on"] = obj["lights_on"]
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


WORLD_BIOMES = (
    {
        "name": "Sakura Twilight Village",
        "sky": "#1b1947",
        "ground": "#2d4c46",
        "house_color": "#ff8ab5",
        "tree_color": "#ff9ec7",
        "sky_body": ("moon", "#fff3b0"),
        "star_color": "#ffe6a7",
        "cloud_color": "#d8c7ff",
        "pond_color": "#4ea8de",
        "flower_color": "#ff70a6",
        "mountain_color": "#3d346b",
    },
    {
        "name": "Starlight Meadow",
        "sky": "#0f2042",
        "ground": "#1f4e45",
        "house_color": "#7bdff2",
        "tree_color": "#57cc99",
        "sky_body": ("moon", "#fdfcdc"),
        "star_color": "#ffd166",
        "cloud_color": "#b8d8fc",
        "pond_color": "#38a3a5",
        "flower_color": "#ffbe0b",
        "mountain_color": "#273c66",
    },
    {
        "name": "Golden Hour Harbor",
        "sky": "#2c1b4d",
        "ground": "#2e5244",
        "house_color": "#ff9f68",
        "tree_color": "#6bcb77",
        "sky_body": ("sun", "#ffd166"),
        "star_color": "#ffe599",
        "cloud_color": "#ffd6ba",
        "pond_color": "#48bfe3",
        "flower_color": "#ff6b6b",
        "mountain_color": "#4a306d",
    },
    {
        "name": "Emerald Sprout Valley",
        "sky": "#143048",
        "ground": "#245749",
        "house_color": "#f4a261",
        "tree_color": "#52b788",
        "sky_body": ("sun", "#ffdd75"),
        "star_color": "#b7efc5",
        "cloud_color": "#d8f3dc",
        "pond_color": "#4cc9f0",
        "flower_color": "#f72585",
        "mountain_color": "#2b4c65",
    },
    {
        "name": "Aurora Cozy Ridge",
        "sky": "#121b3a",
        "ground": "#21424e",
        "house_color": "#a78bfa",
        "tree_color": "#64dfdf",
        "sky_body": ("moon", "#e0fbfc"),
        "star_color": "#80ffdb",
        "cloud_color": "#c4b5fd",
        "pond_color": "#56cfe1",
        "flower_color": "#ff85a1",
        "mountain_color": "#2d3561",
    },
)


def randomize_world(seed: Any = None) -> dict:
    """Create a lively, validated 2D world with an interactive house, sky bodies, and nature."""
    import random

    rng = random.Random(seed)
    biome = rng.choice(WORLD_BIOMES)
    sky_shape, sky_color = biome["sky_body"]
    house_on_left = rng.choice((True, False))
    house_x = rng.randint(18, 28) if house_on_left else rng.randint(72, 82)
    pond_x = rng.randint(68, 82) if house_on_left else rng.randint(18, 32)
    tree_x = rng.randint(74, 86) if house_on_left else rng.randint(14, 26)
    flower_x = rng.randint(34, 44) if house_on_left else rng.randint(56, 66)

    objects = [
        {
            "id": "mountain-bg",
            "shape": "mountain",
            "x": rng.randint(25, 75),
            "y": rng.randint(54, 60),
            "size": rng.randint(26, 36),
            "color": biome["mountain_color"],
            "label": "Peak",
            "animation": "none",
            "reaction": "grow",
            "sound": "none",
        },
        {
            "id": "sky-luminary",
            "shape": sky_shape,
            "x": rng.randint(16, 84),
            "y": rng.randint(16, 26),
            "size": rng.randint(13, 18),
            "color": sky_color,
            "label": "Sky Light",
            "animation": "pulse",
            "reaction": "spin",
            "sound": "bell",
        },
        {
            "id": "cloud-drift",
            "shape": "cloud",
            "x": rng.randint(22, 78),
            "y": rng.randint(20, 32),
            "size": rng.randint(15, 22),
            "color": biome["cloud_color"],
            "label": "Cloud",
            "animation": "float",
            "reaction": "bounce",
            "sound": "pop",
        },
        {
            "id": "star-twinkle",
            "shape": "star",
            "x": rng.randint(14, 86),
            "y": rng.randint(12, 24),
            "size": rng.randint(8, 12),
            "color": biome["star_color"],
            "label": "Wish Star",
            "animation": "pulse",
            "reaction": "spin",
            "sound": "bell",
        },
        {
            "id": "cozy-house",
            "shape": "house",
            "x": house_x,
            "y": rng.randint(64, 70),
            "size": rng.randint(20, 26),
            "color": biome["house_color"],
            "label": "Home",
            "animation": "none",
            "reaction": "color",
            "sound": "bell",
            "lights_on": True,
        },
        {
            "id": "grove-tree",
            "shape": "tree",
            "x": tree_x,
            "y": rng.randint(64, 72),
            "size": rng.randint(18, 24),
            "color": biome["tree_color"],
            "label": "Grove",
            "animation": "sway",
            "reaction": "bounce",
            "sound": "pop",
        },
        {
            "id": "mirror-pond",
            "shape": "pond",
            "x": pond_x,
            "y": rng.randint(78, 86),
            "size": rng.randint(16, 22),
            "color": biome["pond_color"],
            "label": "Pond",
            "animation": "pulse",
            "reaction": "grow",
            "sound": "water",
        },
        {
            "id": "meadow-flower",
            "shape": "flower",
            "x": flower_x,
            "y": rng.randint(74, 83),
            "size": rng.randint(10, 14),
            "color": biome["flower_color"],
            "label": "Bloom",
            "animation": "sway",
            "reaction": "spin",
            "sound": "bell",
        },
    ]

    return validate_world({
        "version": 1,
        "enabled": True,
        "idle_enabled": True,
        "professional": False,
        "wander": True,
        "sound_enabled": False,
        "name": biome["name"],
        "sky": biome["sky"],
        "ground": biome["ground"],
        "robot_size": rng.randint(44, 54),
        "objects": objects,
    })


WORLD_INSTRUCTIONS = """Edit the Assistant's 2D world using assistant_update_world. First read it with assistant_get_world.
Use a complete JSON object: version:1, enabled:true, name, sky and ground (#RRGGBB), idle_enabled,
professional (calm movements), wander, sound_enabled (default false), robot_size (20..80 percent of height),
optional robot_glow (eyes, mouth and reactor) and robot_shell (body) colors in #RRGGBB; omit to use the skin's colors,
objects (at most 96). Each object: unique id, shape (tree/cloud/mountain/flower/star/sun/moon/rock/pond/house/ball/sign/circle/rect/path/symbol/photo),
x/y (0..100 percent), size (2..45 percent), color (#RRGGBB), label (80 chars), animation (none/sway/float/spin/pulse),
reaction (bounce/spin/grow/color/none), sound (none/bell/pop/water). For symbol set glyph to its Unicode character. Optional SVG path coordinates only, no markup.
Keep uploaded photos by their existing object IDs. Set background_photo to null only to remove the uploaded background.
Keep existing objects unless asked to change them. Use a few objects per step for small models. Never claim a change before the tool succeeds."""
