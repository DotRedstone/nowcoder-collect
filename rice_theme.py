"""Read wallpaper-derived colors without executing pywal/matugen commands.

Optional sources:
  NOWCODER_THEME_FILE=/path/to/colors.json
  ~/.cache/wal/colors.json (pywal)
  ~/.cache/matugen/colors.json or ~/.config/matugen/colors.json
Matugen users should point NOWCODER_THEME_FILE to their actual JSON template.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from textual.theme import Theme

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if _HEX.fullmatch(value):
        return value.lower()
    return None


def _matugen_color(value: object) -> str | None:
    """Support flattened, v2 dark/light, and v3/v4 default.color formats."""
    if isinstance(value, str):
        return _hex(value)
    if not isinstance(value, dict):
        return None
    for key in ("dark", "default", "color", "hex"):
        result = _matugen_color(value[key]) if key in value else None
        if result:
            return result
    return None


def theme_from_dict(data: dict, name: str = "wallpaper") -> Theme | None:
    """Return a validated Theme or None; never trust arbitrary CSS from JSON."""
    if not isinstance(data, dict):
        return None
    if "special" in data and "colors" in data:  # pywal colors.json
        special, colors = data["special"], data["colors"]
        if not isinstance(special, dict) or not isinstance(colors, dict):
            return None
        bg, fg = _hex(special.get("background")), _hex(special.get("foreground"))
        primary = _hex(colors.get("color4")) or _hex(colors.get("color6"))
        if not bg or not fg or not primary:
            return None
        return Theme(
            name=name,
            primary=primary, accent=_hex(colors.get("color5")) or primary,
            secondary=_hex(colors.get("color6")) or primary,
            background=bg, foreground=fg,
            surface=_hex(colors.get("color0")) or bg,
            panel=_hex(colors.get("color8")) or bg,
            error=_hex(colors.get("color1")) or "#ff6b76",
            warning=_hex(colors.get("color3")) or "#f6c177",
            success=_hex(colors.get("color2")) or "#a3be8c",
            dark=True,
        )
    colors = data.get("colors", data)
    if not isinstance(colors, dict):
        return None
    # matugen --json hex returns {colors: {dark: {...}, light: {...}}}.
    # A template may instead flatten the colors, or embed default.color objects.
    palette = colors.get("dark", colors)
    if not isinstance(palette, dict):
        return None
    get = lambda key: _matugen_color(palette.get(key))
    bg = get("background") or get("surface")
    fg = get("on_background") or get("on_surface")
    primary = get("primary")
    if not bg or not fg or not primary:
        return None
    return Theme(
        name=name, primary=primary, accent=get("tertiary") or primary,
        secondary=get("secondary") or primary,
        background=bg, foreground=fg,
        surface=get("surface_container") or get("surface") or bg,
        panel=get("surface_container_high") or get("surface_variant") or bg,
        error=get("error") or "#ff6b76",
        warning=get("warning") or "#f6c177",
        success=get("success") or "#a3be8c",
        dark=True,
    )


def candidate_paths() -> list[Path]:
    override = os.environ.get("NOWCODER_THEME_FILE")
    if override:
        return [Path(override).expanduser()]
    home = Path.home()
    return [
        home / ".cache/wal/colors.json",
        home / ".cache/matugen/colors.json",
        home / ".config/matugen/colors.json",
    ]


def load_wallpaper_theme(paths: list[Path] | None = None) -> tuple[Theme | None, str]:
    for path in paths if paths is not None else candidate_paths():
        try:
            if not path.is_file() or path.stat().st_size > 256 * 1024:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            theme = theme_from_dict(raw)
            if theme:
                return theme, str(path)
        except (OSError, UnicodeError, ValueError):
            continue
    return None, ""
