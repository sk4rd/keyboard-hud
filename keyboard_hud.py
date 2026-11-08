#!/usr/bin/env python3
"""Live HUD of an Argos (QMK/VIA) layout on a Bastard Keyboards Charybdis 4x6.

With the keyboard_hud firmware module (see firmware/), the keyboard streams its
switch matrix, layer state and modifiers over raw HID, and the HUD draws them
against the keymap exported from Argos. Stock firmware cannot report any of
this; the HUD then falls back to VIA's matrix query (answered only by firmware
built with VIA_INSECURE) and replays it through a model of QMK's layer,
tap-hold and auto-mouse logic. The typed-key strip always comes from the
keyboard's real HID output.

Running it while an instance is already up closes that instance, so a single
global shortcut toggles the overlay.
"""

import argparse
import colorsys
import glob
import json
import os
import select
import sys
import threading
import time
from collections import deque

# ── Keycodes ────────────────────────────────────────────────────────────────
# Numeric ranges follow QMK's keycodes spec 0.0.x, as used by Argos exports.

TRNS, NO = 0x01, 0x00
MOD_CTL, MOD_SFT, MOD_ALT, MOD_GUI, MOD_RIGHT = 0x01, 0x02, 0x04, 0x08, 0x10

BASIC = {
    0x28: "⏎", 0x29: "Esc", 0x2A: "⌫", 0x2B: "Tab", 0x2C: "␣", 0x2D: "-",
    0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x32: "#", 0x33: ";",
    0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/", 0x39: "Caps",
    0x46: "PrtSc", 0x47: "ScrLk", 0x48: "Pause", 0x49: "Ins", 0x4A: "Home",
    0x4B: "PgUp", 0x4C: "Del", 0x4D: "End", 0x4E: "PgDn", 0x4F: "→",
    0x50: "←", 0x51: "↓", 0x52: "↑", 0x53: "NumLk", 0x54: "/", 0x55: "*",
    0x56: "-", 0x57: "+", 0x58: "⏎", 0x63: ".", 0x64: "\\", 0x65: "Menu",
    0x67: "=",
    # System and consumer
    0xA5: "Power", 0xA6: "Sleep", 0xA7: "Wake", 0xA8: "Mute", 0xA9: "Vol+",
    0xAA: "Vol-", 0xAB: "Next", 0xAC: "Prev", 0xAD: "Stop", 0xAE: "Play",
    0xAF: "Media", 0xB0: "Eject", 0xB1: "Mail", 0xB2: "Calc", 0xB3: "Files",
    0xB4: "Search", 0xB5: "WHome", 0xB6: "Back", 0xB7: "Fwd", 0xB8: "WStop",
    0xB9: "Reload", 0xBA: "Fav", 0xBB: "FFwd", 0xBC: "Rew", 0xBD: "Bri+",
    0xBE: "Bri-", 0xBF: "Ctrl Pnl", 0xC0: "Assist", 0xC1: "Mission",
    0xC2: "Launch",
    # Mouse keys
    0xCD: "M↑", 0xCE: "M↓", 0xCF: "M←", 0xD0: "M→", 0xD1: "LClk",
    0xD2: "RClk", 0xD3: "MClk", 0xD4: "Btn4", 0xD5: "Btn5", 0xD6: "Btn6",
    0xD7: "Btn7", 0xD8: "Btn8", 0xD9: "W↑", 0xDA: "W↓", 0xDB: "W←",
    0xDC: "W→", 0xDD: "Acc0", 0xDE: "Acc1", 0xDF: "Acc2",
    # Modifiers
    0xE0: "Ctrl", 0xE1: "Shift", 0xE2: "Alt", 0xE3: "Super", 0xE4: "Ctrl",
    0xE5: "Shift", 0xE6: "AltGr", 0xE7: "Super",
}
for i in range(26):
    BASIC[0x04 + i] = chr(ord("a") + i)
for i, ch in enumerate("1234567890"):
    BASIC[0x1E + i] = ch
for i in range(12):
    BASIC[0x3A + i] = f"F{i + 1}"
    BASIC[0x68 + i] = f"F{i + 13}"
for i, ch in enumerate("1234567890"):
    BASIC[0x59 + i] = ch

SHIFTED = dict(zip(
    [0x1E + i for i in range(10)] + [0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38],
    list("!@#$%^&*()") + list("_+{}|:\"~<>?"),
))

MOD_KEYS = {0xE0: MOD_CTL, 0xE1: MOD_SFT, 0xE2: MOD_ALT, 0xE3: MOD_GUI,
            0xE4: MOD_CTL | MOD_RIGHT, 0xE5: MOD_SFT | MOD_RIGHT,
            0xE6: MOD_ALT | MOD_RIGHT, 0xE7: MOD_GUI | MOD_RIGHT}

SHORTCUTS = {(MOD_CTL, 0x1D): "Undo", (MOD_CTL, 0x1B): "Cut", (MOD_CTL, 0x06): "Copy",
             (MOD_CTL, 0x19): "Paste", (MOD_CTL, 0x1C): "Redo",
             (MOD_CTL | MOD_SFT, 0x1D): "Redo", (MOD_CTL, 0x04): "SelAll",
             (MOD_CTL, 0x16): "Save", (MOD_CTL, 0x09): "Find"}

QUANTUM = {0x7C00: "Boot", 0x7C01: "Reboot", 0x7C03: "EEClr", 0x7C73: "CapsWd",
           0x7C79: "Rep", 0x7C7A: "AltRep", 0x7C58: "Lead"}

LIGHTING = {0x7800: "BL on", 0x7801: "BL off", 0x7802: "BL tog", 0x7820: "UG tog",
            0x7821: "UG ›", 0x7822: "UG ‹", 0x7840: "RGB on", 0x7841: "RGB off",
            0x7842: "RGB tog", 0x7843: "RGB ›", 0x7844: "RGB ‹", 0x7845: "Hue+",
            0x7846: "Hue-", 0x7847: "Sat+", 0x7848: "Sat-", 0x7849: "Bri+",
            0x784A: "Bri-", 0x784B: "Spd+", 0x784C: "Spd-"}

# Argos pointer-mode keys (QK_KB range). Argos documents the modes but not their
# keycodes; the order below matches its documentation, and the dpi table in the
# export (mode 1 at precision dpi) supports it. Even codes are taken to be hold
# keys and odd codes toggles.
POINTER_MODES = ["Precision", "Drag-scroll", "Cursor", "Brightness", "Zoom",
                 "Volume", "Tab switch", "History", "Custom 1", "Custom 2",
                 "Custom 3", "Custom 4", "Custom 5"]
POINTER_SHORT = ["Prec", "Drag", "Curs", "Brite", "Zoom", "Vol", "Tabs",
                 "Hist", "Cust1", "Cust2", "Cust3", "Cust4", "Cust5"]


CONSUMER = {0xE2: "Mute", 0xE9: "Vol+", 0xEA: "Vol-", 0xCD: "Play", 0xB7: "Stop",
            0xB5: "Next", 0xB6: "Prev", 0xB3: "FFwd", 0xB4: "Rew", 0x6F: "Bri+",
            0x70: "Bri-", 0x192: "Calc", 0x194: "Files", 0x221: "Search",
            0x223: "WHome", 0x224: "Back", 0x225: "Fwd", 0x227: "Reload"}


def mod_prefix(mods):
    return "".join(c for bit, c in ((MOD_CTL, "C-"), (MOD_ALT, "A-"), (MOD_GUI, "G-"), (MOD_SFT, "S-")) if mods & bit)


def mod_name(mods):
    if mods == MOD_ALT | MOD_RIGHT:
        return "altgr"
    names = [n for bit, n in ((MOD_CTL, "ctl"), (MOD_SFT, "sft"), (MOD_ALT, "alt"), (MOD_GUI, "gui")) if mods & bit]
    return "+".join(names) if len(names) < 4 else "hyper"


def basic_label(code, mods=0):
    m = mods & 0x0F
    if m in (MOD_SFT,) and code in SHIFTED:
        return SHIFTED[code]
    if (m, code) in SHORTCUTS:
        return SHORTCUTS[(m, code)]
    name = BASIC.get(code, f"0x{code:02X}")
    if m == MOD_ALT and mods & MOD_RIGHT:
        return "AltGr-" + name  # altgr-intl: the OS turns these into accented characters
    if m & MOD_SFT and len(name) == 1 and name.isalpha():
        name, m = name.upper(), m & ~MOD_SFT
    elif len(name) == 1 and name.isalpha() and m:
        name = name.upper()
    return mod_prefix(m) + name


def shifted_label(key):
    """The label a key shows while shift is held, or None if it does not change."""
    if key.kind not in ("key", "mt", "lt") or key.tap is None:
        return None
    if key.kind == "key":
        if key.mods & MOD_SFT:
            return None  # the keycode already applies shift
        if (key.mods & 0x0F, key.tap) in SHORTCUTS:
            return None  # the label is a shortcut name, not a character
    name = BASIC.get(key.tap, f"0x{key.tap:02X}")
    label = SHIFTED.get(key.tap)
    if label is None:
        if len(name) != 1 or not name.isalpha():
            return None
        label = name.upper()
    if key.kind in ("mt", "lt"):
        return label  # key.mods describes the hold, not the tap
    if key.mods & MOD_ALT and key.mods & MOD_RIGHT:
        return "AltGr-" + label
    return mod_prefix(key.mods & ~MOD_SFT) + label


class Key:
    """A decoded keycode: what it does on tap, on hold, and how to draw it."""

    __slots__ = ("code", "kind", "label", "hold", "layer", "mods", "tap", "arg")

    def __init__(self, code, kind, label="", hold="", layer=None, mods=0, tap=None, arg=None):
        self.code, self.kind, self.label, self.hold = code, kind, label, hold
        self.layer, self.mods, self.tap, self.arg = layer, mods, tap, arg


def decode(code, layer_names):
    def lname(n):
        return layer_names[n] if n < len(layer_names) else f"L{n}"

    if code == NO:
        return Key(code, "none")
    if code == TRNS:
        return Key(code, "trns", "▽")
    if code in MOD_KEYS:
        return Key(code, "mod", BASIC[code], mods=MOD_KEYS[code])
    if code <= 0xFF:
        kind = "mouse" if 0xCD <= code <= 0xDF else "key"
        return Key(code, kind, basic_label(code), tap=code)
    if code <= 0x1FFF:
        mods = (code >> 8) & 0x1F
        return Key(code, "key", basic_label(code & 0xFF, mods), mods=mods, tap=code & 0xFF)
    if code <= 0x3FFF:
        mods = (code >> 8) & 0x1F
        return Key(code, "mt", basic_label(code & 0xFF), mod_name(mods), mods=mods, tap=code & 0xFF)
    if code <= 0x4FFF:
        n = (code >> 8) & 0x0F
        return Key(code, "lt", basic_label(code & 0xFF), lname(n), layer=n, tap=code & 0xFF)
    if code <= 0x51FF:
        n, mods = (code >> 5) & 0x0F, code & 0x1F
        return Key(code, "lm", lname(n), mod_name(mods), layer=n, mods=mods)
    if 0x5200 <= code <= 0x52FF:
        n = code & 0x1F
        kinds = {0x5200: ("to", "TO "), 0x5220: ("mo", ""), 0x5240: ("df", "DF "),
                 0x5260: ("tg", "TG "), 0x5280: ("osl", "OS "), 0x52C0: ("tt", "TT "),
                 0x52E0: ("df", "PDF ")}
        base = code & 0xFFE0
        if base == 0x52A0:
            return Key(code, "osm", "OS " + mod_name(n), mods=n)
        kind, prefix = kinds.get(base, ("other", "?"))
        return Key(code, kind, prefix + lname(n), layer=n)
    if 0x5700 <= code <= 0x57FF:
        return Key(code, "other", f"TD{code & 0xFF}")
    if 0x7700 <= code <= 0x777F:
        return Key(code, "other", f"M{code & 0x7F}")
    if code in LIGHTING:
        return Key(code, "other", LIGHTING[code])
    if code in QUANTUM:
        return Key(code, "other", QUANTUM[code])
    if 0x7E00 <= code <= 0x7E3F:
        i, toggle = divmod(code - 0x7E00, 2)
        if i < len(POINTER_MODES):
            return Key(code, "pmode", POINTER_SHORT[i] + ("⇄" if toggle else ""), "tog" if toggle else "", arg=(i, bool(toggle)))
        return Key(code, "other", f"KB{code - 0x7E00}")
    if 0x7E40 <= code <= 0x7FFF:
        return Key(code, "other", f"U{code - 0x7E40}")
    return Key(code, "other", f"{code:04X}")


# ── Physical layout ─────────────────────────────────────────────────────────
# (matrix row, matrix col) -> (centre x, centre y, rotation degrees), in key
# units. Column 0 is the outer pinky column on both halves; the right half's
# matrix rows are 5-9. Positions match the QMK LAYOUT of the 4x6, with column
# stagger and an angled thumb arc to resemble the real board.

STAGGER = [0.35, 0.35, 0.12, 0.0, 0.12, 0.22]
RIGHT_X = 9.2


def build_geometry():
    geo = {}
    for r in range(4):
        for c in range(6):
            geo[(r, c)] = (c + 0.5, r + 0.5 + STAGGER[c], 0.0)
            geo[(5 + r, c)] = (RIGHT_X + (5 - c) + 0.5, r + 0.5 + STAGGER[c], 0.0)
    left_thumbs = {(4, 3): (4.25, 4.85, 8), (4, 4): (5.35, 5.02, 16), (4, 1): (6.42, 5.32, 24),
                   (4, 5): (5.62, 6.1, 16), (4, 2): (6.72, 6.42, 24)}
    geo.update(left_thumbs)
    mirror = {(4, 1): (9, 1), (4, 4): (9, 3), (4, 5): (9, 5)}
    for src, dst in mirror.items():
        x, y, a = left_thumbs[src]
        geo[dst] = (RIGHT_X + (6 - x), y, -a)
    return geo


GEOMETRY = build_geometry()
TRACKBALL = (RIGHT_X + 1.95, 5.45, 0.72)
BOARD_W, BOARD_H = RIGHT_X + 6, 7.1


# ── Firmware model ──────────────────────────────────────────────────────────

class Hold:
    """A physically held key and the role it ended up with."""

    __slots__ = ("pos", "key", "layer", "t", "role")

    def __init__(self, pos, key, layer, t, role):
        self.pos, self.key, self.layer, self.t, self.role = pos, key, layer, t, role


class Model:
    def __init__(self, layout, auto_mouse_layer=None):
        self.rows, self.cols = layout["rows"], layout["cols"]
        self.names = list(layout.get("layerNames") or [])
        raw = layout["keycodes"]
        # Per-key LED colours from Argos ("layer:row:col" -> rgb), for lit keys only.
        self.leds = {tuple(int(v) for v in k.split(":")): (c["r"], c["g"], c["b"])
                     for k, c in (layout.get("rgbMatrix") or {}).items() if c.get("on")}
        self.keymap = [[decode(code, self.names) for code in layer] for layer in raw]
        self.used_layers = [i for i, layer in enumerate(raw) if i == 0 or any(c not in (TRNS,) for c in layer)]
        self.tapping_term = layout.get("tappingTerm", 200) / 1000
        self.flow_tap_term = layout.get("flowTapTerm", 0) / 1000
        self.quick_tap_term = layout.get("quickTapTerm", self.tapping_term * 1000) / 1000
        self.auto_mouse = bool(layout.get("autoMouseLayerEnabled"))
        self.auto_mouse_time = layout.get("autoMouseTime", 650) / 1000
        if auto_mouse_layer is None:
            auto_mouse_layer = self.names.index("pointer") if "pointer" in self.names else 3
        self.mouse_layer = auto_mouse_layer

        self.default_layer = 0
        self.layers = set()
        self.oneshot_layer = None
        self.oneshot_mods = 0
        self.matrix = set()
        self.held = {}
        self.pending = None
        self.buffer = []
        self.auto_until = 0.0
        self.last_motion = -1.0
        self.last_mouse_key = -1.0
        self.last_press = (-1.0, None)
        self.last_tap = (-1.0, None)
        self.pointer_mode = None
        self.pointer_toggle = None
        self.history = deque(maxlen=24)
        self.connected = False
        self.status = "searching for keyboard…"
        self.rev = 0  # bumped on every state change, so the UI redraws only when needed
        self.fw = False  # True once the firmware streams its own state
        self.fw_active = {0}
        self.fw_mods = 0
        self.hid_keys = set()
        self.hid_consumer = 0

    # Layer state ----------------------------------------------------------

    def auto_active(self, t):
        if self.fw:
            return self.mouse_layer in self.fw_active
        if not self.auto_mouse:
            return False
        if t < self.auto_until:
            return True
        return self.auto_until > 0 and any(h.layer == self.mouse_layer for h in self.held.values())

    def active_layers(self, t):
        if self.fw:
            return set(self.fw_active)
        active = {self.default_layer} | self.layers
        if self.auto_active(t):
            active.add(self.mouse_layer)
        return active

    def resolve(self, pos, active):
        idx = pos[0] * self.cols + pos[1]
        for layer in sorted(active, reverse=True):
            key = self.keymap[layer][idx] if layer < len(self.keymap) else None
            if key is not None and key.kind != "trns":
                return key, layer
        return self.keymap[0][idx], 0

    def mods(self):
        if self.fw:
            return self.fw_mods
        m = self.oneshot_mods
        for h in self.held.values():
            if h.role in ("mod", "hold") and h.key.kind in ("mod", "mt", "lm"):
                m |= h.key.mods
        return m

    # Input ----------------------------------------------------------------

    def firmware_state(self, pressed, layer_state, default_state, mods8, t):
        """Apply a report from the keyboard_hud firmware module."""
        self.fw = True
        self.rev += 1
        active = self.fw_active
        for pos in sorted(self.matrix - pressed):
            h = self.held.pop(pos, None)
            if h and h.key.kind == "pmode" and not h.key.arg[1] and self.pointer_mode == h.key.arg[0]:
                self.pointer_mode = None
        for pos in sorted(pressed - self.matrix):
            # Keys resolve against the layer state from before their own press, as in QMK.
            key, layer = self.resolve(pos, active)
            self.held[pos] = Hold(pos, key, layer, t, "key")
            if key.kind == "pmode":
                idx, toggle = key.arg
                if toggle:
                    self.pointer_toggle = None if self.pointer_toggle == idx else idx
                else:
                    self.pointer_mode = idx
        self.matrix = set(pressed)
        bits = layer_state | default_state
        self.fw_active = {n for n in range(32) if bits >> n & 1} or {0}
        self.default_layer = max((n for n in range(32) if default_state >> n & 1), default=0)
        right = MOD_RIGHT if mods8 & 0xF0 else 0
        self.fw_mods = ((mods8 | mods8 >> 4) & 0x0F) | right

    def role(self, h):
        """How a held key is acting right now: 'hold', 'layer', or 'key'."""
        if not self.fw:
            return h.role
        k = h.key
        if k.kind in ("mt", "mod", "lm") and self.fw_mods & k.mods & 0x0F:
            return "hold"
        if k.kind in ("mo", "lt", "tt", "lm") and k.layer in self.fw_active:
            return "layer"
        return "key"

    def hid_keyboard(self, report, t):
        """Record keys from the boot-keyboard report the host receives."""
        if len(report) < 8:
            return
        mods8 = report[0]
        mods = ((mods8 | mods8 >> 4) & 0x0F) | (MOD_RIGHT if mods8 & 0xF0 else 0)
        keys = {c for c in report[2:8] if c > 0x03}
        for code in sorted(keys - self.hid_keys):
            self.history.append((t, basic_label(code, mods), False))
        self.hid_keys = keys
        self.rev += 1

    def hid_consumer_usage(self, usage, t):
        if usage and usage != self.hid_consumer:
            self.history.append((t, CONSUMER.get(usage, f"0x{usage:X}"), True))
        self.hid_consumer = usage
        self.rev += 1

    def set_matrix(self, pressed, t):
        released, new = self.matrix - pressed, pressed - self.matrix
        self.matrix = set(pressed)
        self.rev += 1
        for pos in sorted(released):
            self.event(("up", pos, t))
        for pos in sorted(new):
            self.event(("down", pos, t))

    def motion(self, t):
        self.last_motion = t
        self.rev += 1
        if not self.auto_mouse:
            return
        mouse_key_held = any(h.key.kind == "mouse" for h in self.held.values())
        if mouse_key_held or t - self.last_mouse_key < 0.05:
            return
        self.auto_until = t + self.auto_mouse_time

    def tick(self, t):
        if self.pending and t - self.pending.t >= self.tapping_term:
            self.decide(hold=True, t=t)
            self.rev += 1
        if self.auto_mouse and self.auto_until and not self.auto_active(t):
            self.auto_until = 0.0
            self.rev += 1

    def event(self, ev):
        if self.pending:
            self.buffer.append(ev)
            kind, pos, t = ev
            if kind == "up" and pos == self.pending.pos:
                self.buffer.pop()
                self.decide(hold=False, t=t, release=True)
            elif kind == "up" and any(e[0] == "down" and e[1] == pos for e in self.buffer):
                # Permissive hold: another key was tapped inside the hold.
                self.decide(hold=True, t=t)
            return
        kind, pos, t = ev
        if kind == "down":
            self.press(pos, t)
        else:
            self.release(pos, t)

    def decide(self, hold, t, release=False):
        h = self.pending
        self.pending = None
        if hold:
            h.role = "hold"
            if h.key.kind == "lt":
                self.layers.add(h.key.layer)
        else:
            self.emit(h.key.tap, h.key, t)
            self.last_tap = (t, h.pos)
            if release:
                self.held.pop(h.pos, None)
            else:
                h.role = "tap"
        buffered, self.buffer = self.buffer, []
        for ev in buffered:
            self.event(ev)

    def press(self, pos, t):
        key, layer = self.resolve(pos, self.active_layers(t))
        h = Hold(pos, key, layer, t, "key")
        self.held[pos] = h
        k = key.kind
        if self.auto_active(t) and layer != self.mouse_layer and k in ("key", "mt", "lt") and not self.layers & {self.mouse_layer}:
            self.auto_until = 0.0
        if k == "mouse":
            self.last_mouse_key = t
            if self.auto_active(t):
                self.auto_until = max(self.auto_until, t + self.auto_mouse_time)
        if k == "mo":
            self.layers.add(key.layer)
            h.role = "layer"
        elif k == "lm":
            self.layers.add(key.layer)
            h.role = "mod"
        elif k == "tg":
            self.layers ^= {key.layer}
        elif k == "to":
            self.layers = {key.layer}
        elif k == "df":
            self.default_layer = key.layer
        elif k == "osl":
            self.layers.add(key.layer)
            self.oneshot_layer = key.layer
        elif k == "tt":
            self.layers.add(key.layer)
            h.role = "layer"
        elif k == "osm":
            self.oneshot_mods |= key.mods
        elif k == "mod":
            h.role = "mod"
        elif k == "pmode":
            idx, toggle = key.arg
            if toggle:
                self.pointer_toggle = None if self.pointer_toggle == idx else idx
            else:
                self.pointer_mode = idx
        elif k in ("mt", "lt"):
            prev_t, prev_key = self.last_press
            flow = (self.flow_tap_term and prev_key is not None and t - prev_t < self.flow_tap_term
                    and prev_key.kind == "key" and prev_key.tap is not None and 0x04 <= prev_key.tap <= 0x38)
            quick = self.last_tap[1] == pos and t - self.last_tap[0] < self.quick_tap_term
            if flow or quick:
                self.emit(key.tap, key, t)
                h.role = "tap"
            else:
                h.role = "pending"
                self.pending = h
        elif k in ("key", "mouse", "other"):
            self.emit(key.tap, key, t)
        self.last_press = (t, key)

    def release(self, pos, t):
        h = self.held.pop(pos, None)
        if h is None:
            return
        k = h.key.kind
        if k in ("mo", "lm", "tt") or (k == "lt" and h.role == "hold"):
            self.layers.discard(h.key.layer)
        elif k == "pmode" and not h.key.arg[1] and self.pointer_mode == h.key.arg[0]:
            self.pointer_mode = None
        elif k == "mouse":
            self.last_mouse_key = t
        if h.layer == self.mouse_layer and self.auto_until:
            self.auto_until = max(self.auto_until, t + self.auto_mouse_time)

    def emit(self, code, key, t):
        """A key fired in the fallback model. History comes from real HID output instead."""
        if code is None:
            return
        self.oneshot_mods = 0
        if self.oneshot_layer is not None:
            self.layers.discard(self.oneshot_layer)
            self.oneshot_layer = None


# ── Device I/O ──────────────────────────────────────────────────────────────

RAW_USAGE = bytes([0x06, 0x60, 0xFF])          # usage page 0xFF60 (VIA raw HID)
KEYBOARD_USAGE = bytes([0x05, 0x01, 0x09, 0x06])  # generic desktop / keyboard
SHARED_USAGE = bytes([0x05, 0x01, 0x09, 0x02])  # mouse + system + consumer, with report ids

# keyboard_hud firmware module protocol (firmware/keyboard_hud/keyboard_hud.c)
HUD_CHANNEL = 0x4B
HUD_REPORT_ID = 0xA5
HUD_HEADER_SIZE = 14
VIA_CUSTOM_SET_VALUE, VIA_UNHANDLED = 0x07, 0xFF


def find_hidraw(vid, pid):
    found = {}
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(node + "/device/uevent") as f:
                uevent = f.read().upper()
            with open(node + "/device/report_descriptor", "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if f"{vid:08X}:{pid:08X}" not in uevent:
            continue
        dev = "/dev/" + os.path.basename(node)
        for name, usage in (("raw", RAW_USAGE), ("keyboard", KEYBOARD_USAGE), ("shared", SHARED_USAGE)):
            if desc.startswith(usage):
                found[name] = dev
    return found


class Device(threading.Thread):
    """Talks to the keyboard and calls back on the GLib main loop.

    Subscribes to the keyboard_hud firmware stream; if the firmware does not
    know the command, polls VIA's switch-matrix query instead. Also forwards
    the boot-keyboard and consumer reports (the typed output) and trackball
    motion from the shared mouse interface."""

    def __init__(self, rows, cols, vid, pid, hz, post):
        super().__init__(daemon=True)
        self.rows, self.cols, self.vid, self.pid = rows, cols, vid, pid
        self.interval = 1.0 / hz
        self.post = post
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            nodes = find_hidraw(self.vid, self.pid)
            if "raw" not in nodes:
                self.post("status", False, "keyboard not found")
                self.stop.wait(2)
                continue
            try:
                self.session(nodes)
            except OSError as e:
                self.post("status", False, f"{os.path.basename(nodes['raw'])}: {e.strerror or e}")
                self.post("reset")
                self.stop.wait(2)

    @staticmethod
    def subscribe(fd, value):
        os.write(fd, bytes([0x00, VIA_CUSTOM_SET_VALUE, HUD_CHANNEL, 0x01, value]) + bytes(28))

    def session(self, nodes):
        raw = os.open(nodes["raw"], os.O_RDWR)
        fds = {raw: "raw"}
        for name in ("keyboard", "shared"):
            if name in nodes:
                try:
                    fds[os.open(nodes[name], os.O_RDONLY | os.O_NONBLOCK)] = name
                except OSError:
                    pass
        query = bytes([0x00, 0x02, 0x03, 0x00]) + bytes(29)
        mode, started = None, time.monotonic()  # mode: "firmware" | "matrix"
        renew, sent, waiting, last = 0.0, 0.0, False, None
        try:
            self.subscribe(raw, 2)  # 2: subscribe and send a snapshot now
            while not self.stop.is_set():
                now = time.monotonic()
                if mode == "firmware" and now >= renew:
                    self.subscribe(raw, 1)
                    renew = now + 1.0
                elif mode is None and now - started > 1.0:
                    mode = "matrix"  # no answer at all; try the VIA query
                    self.post("status", True, "stock firmware: no key or layer data")
                if mode == "matrix" and (not waiting or now - sent > 0.25):
                    os.write(raw, query)
                    sent, waiting = now, True
                timeout = self.interval if mode == "matrix" else 0.5
                ready, _, _ = select.select(list(fds), [], [], timeout)
                for fd in ready:
                    data = os.read(fd, 64)
                    kind = fds[fd]
                    if kind == "keyboard":
                        self.post("hid", bytes(data))
                    elif kind == "shared":
                        if len(data) >= 4 and data[0] == 2 and (data[2] or data[3]):
                            self.post("motion")
                        elif len(data) >= 3 and data[0] == 4:
                            self.post("consumer", data[1] | data[2] << 8)
                    elif len(data) >= HUD_HEADER_SIZE + self.rows and data[0] == HUD_REPORT_ID:
                        if mode != "firmware":
                            mode, renew = "firmware", now + 1.0
                            self.post("status", True, "connected")
                        pressed = frozenset((r, c) for r in range(self.rows) for c in range(self.cols)
                                            if data[HUD_HEADER_SIZE + r] >> c & 1)
                        layers = int.from_bytes(data[3:7], "little")
                        default = int.from_bytes(data[7:11], "little")
                        mods = data[11] | data[12] | data[13]
                        self.post("firmware", set(pressed), layers, default, mods)
                    elif len(data) >= 2 and data[1] == HUD_CHANNEL and data[0] == VIA_UNHANDLED and mode is None:
                        mode = "matrix"
                        self.post("status", True, "stock firmware: no key or layer data")
                    elif mode == "matrix" and len(data) >= 3 + self.rows and data[:3] == bytes([0x02, 0x03, 0x00]):
                        waiting = False
                        pressed = frozenset((r, c) for r in range(self.rows) for c in range(self.cols)
                                            if data[3 + r] >> c & 1)
                        if pressed != last:
                            last = pressed
                            self.post("matrix", set(pressed))
                if mode == "matrix" and not waiting:
                    delay = self.interval - (time.monotonic() - sent)
                    if delay > 0:
                        self.stop.wait(delay)
            if mode == "firmware":
                self.subscribe(raw, 0)
        finally:
            for fd in fds:
                os.close(fd)


# ── Drawing ─────────────────────────────────────────────────────────────────

DEFAULT_THEME = {
    "background": "#000000", "foreground": "#c8c8c8",
    "palette": {"black": "#353535", "red": "#e74669", "green": "#89d287", "yellow": "#dab853",
                "blue": "#449fd0", "magenta": "#da62dc", "cyan": "#65aaaf", "white": "#b4b4b4",
                "brightBlack": "#535353", "brightBlue": "#6fbce2", "brightMagenta": "#e586e7"},
}


def pango():
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    return Pango, PangoCairo


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def mix(a, b, f):
    return tuple(x + (y - x) * f for x, y in zip(a, b))


class Painter:
    def __init__(self, model, theme, scale=1.0, opacity=0.6, font="BlexMono Nerd Font"):
        self.m, self.u, self.opacity, self.font = model, 44 * scale, opacity, font
        p = {**DEFAULT_THEME["palette"], **theme.get("palette", {})}
        self.bg = rgb(theme.get("background", DEFAULT_THEME["background"]))
        self.fg = rgb(theme.get("foreground", DEFAULT_THEME["foreground"]))
        self.dim = rgb(p["brightBlack"])
        self.edge = rgb(p["black"])
        self.key_bg = mix(self.bg, self.fg, 0.07)
        # Idle keys sit a little above the panel; labels and pressed keys stay opaque.
        self.key_alpha = min(1.0, opacity + 0.15)
        self.mod_color = rgb(p["cyan"])
        self.ok_color, self.bad_color = rgb(p["green"]), rgb(p["red"])
        # Same hue per layer as the keyboard's lighting: numbers blue, symbols green,
        # navigation yellow. Unnamed layers fall back to the leftover palette colours.
        by_name = {"base": "white", "sym": "green", "num": "blue", "nav": "yellow",
                   "pointer": "magenta", "game": "red",
                   # Argos sometimes exports generic names; keep their colours stable.
                   "lower": "green", "raise": "brightMagenta",
                   "layer 4": "yellow", "layer 5": "brightGreen",
                   "layer 6": "brightYellow", "layer 7": "brightRed"}
        spare = iter(["brightBlue", "brightMagenta", "brightCyan", "brightGreen", "brightYellow", "brightRed"])
        self.layer_colors = [rgb(p.get(by_name.get(name) or next(spare, "white"), p["white"]))
                             for name in (model.names + [""] * 8)[:max(8, len(model.names))]]
        self.pad = 0.32 * self.u
        self.header_h = 0.62 * self.u
        self.history_h = 0.6 * self.u
        self.width = int(BOARD_W * self.u + 2 * self.pad)
        self.height = int(self.header_h + BOARD_H * self.u + self.history_h + 2 * self.pad)
        self.preview = None
        self.moving = False

    def set_opacity(self, value):
        self.opacity = max(0.1, min(1.0, float(value)))
        self.key_alpha = min(1.0, self.opacity + 0.15)

    def layer_color(self, n):
        return self.layer_colors[n % len(self.layer_colors)]

    def led_tint(self, layer, pos):
        """A label colour from the key's LED, lightened to read on a dark HUD."""
        led = self.m.leds.get((layer, *pos))
        if led is None:
            return None
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in led))
        if v < 0.3:  # the dim "nothing here" shade
            return None
        return colorsys.hsv_to_rgb(h, s * 0.6, 0.92)

    def text(self, cr, s, x, y, size, color, max_w=None, bold=False, align="center"):
        Pango, PangoCairo = pango()
        layout = PangoCairo.create_layout(cr)
        while True:
            fd = Pango.FontDescription.from_string(self.font)
            fd.set_absolute_size(size * Pango.SCALE)
            if bold:
                fd.set_weight(Pango.Weight.BOLD)
            layout.set_font_description(fd)
            layout.set_text(s, -1)
            w, h = layout.get_pixel_size()
            if max_w is None or w <= max_w or size < 7:
                break
            size *= 0.88
        cr.set_source_rgb(*color)
        ox = {"center": w / 2, "left": 0, "right": w}[align]
        cr.move_to(x - ox, y - h / 2)
        PangoCairo.show_layout(cr, layout)
        return w

    def text_width(self, cr, s, size):
        Pango, PangoCairo = pango()
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(self.font)
        fd.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(s, -1)
        return layout.get_pixel_size()[0]

    def pill_label(self, n):
        name = self.m.names[n] if n < len(self.m.names) else f"L{n}"
        return name + (" ●" if n == self.m.default_layer and n != 0 else "")

    def layer_pills(self, cr):
        """Yield (layer, x, width) for the header's layer pills."""
        x = self.pad
        for n in self.m.used_layers:
            w = self.text_width(cr, self.pill_label(n), 0.3 * self.u) + 0.4 * self.u
            yield n, x, w
            x += w + 0.14 * self.u

    def animating(self, t):
        """'fast' while something moves, 'slow' while history fades, else None."""
        m = self.m
        if (m.last_motion >= 0 and t - m.last_motion < 0.4) or t < m.auto_until or m.pending:
            return "fast"
        if m.history and t - m.history[-1][0] < 8:
            return "slow"
        return None

    @staticmethod
    def rounded(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def draw(self, cr, t):
        m, u = self.m, self.u
        cr.set_operator(0)  # CLEAR
        cr.paint()
        cr.set_operator(2)  # OVER
        self.rounded(cr, 0, 0, self.width, self.height, 0.3 * u)
        cr.set_source_rgba(*self.bg, self.opacity)
        cr.fill()

        active = m.active_layers(t) if self.preview is None else {m.default_layer, self.preview}
        top = max(active)
        shift = bool(m.mods() & MOD_SFT)
        self.draw_header(cr, t, active, top)
        cr.save()
        cr.translate(self.pad, self.pad + self.header_h)
        self.draw_trackball(cr, t)
        for pos, (cx, cy, angle) in GEOMETRY.items():
            self.draw_key(cr, pos, cx, cy, angle, active, t, shift)
        cr.restore()
        self.draw_history(cr, t)
        if self.moving:
            self.draw_move_hint(cr)

    def draw_move_hint(self, cr):
        """A border and hint while the HUD accepts pointer input for dragging."""
        u = self.u
        self.rounded(cr, 1, 1, self.width - 2, self.height - 2, 0.3 * u)
        cr.set_source_rgb(*self.mod_color)
        cr.set_line_width(2)
        cr.stroke()
        label = "drag to move · right-click to lock"
        size = 0.26 * u
        w = self.text_width(cr, label, size)
        pad = 0.18 * u
        x = (self.width - w) / 2 - pad
        y = self.height - self.pad - self.history_h / 2 + 0.05 * u
        self.rounded(cr, x, y - 0.17 * u, w + 2 * pad, 0.34 * u, 0.17 * u)
        cr.set_source_rgba(*self.key_bg, 0.95)
        cr.fill()
        self.text(cr, label, self.width / 2, y, size, self.mod_color)

    def draw_header(self, cr, t, active, top):
        m, u = self.m, self.u
        y = self.pad + self.header_h * 0.42
        size = 0.3 * u
        # Backing strip so dim labels stay legible over bright windows.
        self.rounded(cr, self.pad - 0.08 * u, y - 0.3 * u, self.width - 2 * self.pad + 0.16 * u, 0.6 * u, 0.3 * u)
        cr.set_source_rgba(*self.key_bg, self.key_alpha)
        cr.fill()
        x = self.pad
        for n, x, w in list(self.layer_pills(cr)):
            color = self.layer_color(n)
            on = n in active
            label = self.pill_label(n)
            self.rounded(cr, x, y - 0.24 * u, w, 0.48 * u, 0.24 * u)
            if n == top:
                cr.set_source_rgb(*color)
                cr.fill()
                self.text(cr, label, x + w / 2, y, size, self.bg, bold=True)
            elif on:
                cr.set_source_rgb(*color)
                cr.set_line_width(1.5)
                cr.stroke()
                self.text(cr, label, x + w / 2, y, size, color)
            else:
                cr.new_path()
                self.text(cr, label, x + w / 2, y, size, self.dim)
        x += w + 0.14 * u if m.used_layers else 0
        if self.preview is not None:
            self.text(cr, "preview", x + 0.1 * u, y, size * 0.85, self.dim, align="left")

        rx = self.width - self.pad
        degraded = m.connected and not m.fw and m.status != "connected"
        status_color = self.bad_color if not m.connected or degraded else self.ok_color
        cr.new_path()
        cr.arc(rx - 0.1 * u, y, 0.09 * u, 0, 6.2832)
        cr.set_source_rgb(*status_color)
        cr.fill()
        rx -= 0.32 * u
        if not m.connected or degraded:
            self.text(cr, m.status, rx, y, size * 0.9, self.dim, align="right")
            return
        mods = m.mods()
        for bit, name in ((MOD_GUI, "gui"), (MOD_ALT, "alt"), (MOD_SFT, "sft"), (MOD_CTL, "ctl")):
            on = bool(mods & bit)
            if bit == MOD_ALT and mods & MOD_RIGHT and on:
                name = "altgr"
            w = self.text(cr, name, rx, y, size, self.mod_color if on else self.dim, bold=on, align="right")
            rx -= w + 0.22 * u
        pmode = m.pointer_mode if m.pointer_mode is not None else m.pointer_toggle
        if pmode is not None:
            self.text(cr, "◉ " + POINTER_MODES[pmode], rx, y, size, self.layer_color(m.mouse_layer), align="right")

    def draw_trackball(self, cr, t):
        u = self.u
        cx, cy, r = TRACKBALL
        glow = max(0.0, 1 - (t - self.m.last_motion) / 0.4) if self.m.last_motion >= 0 else 0
        color = self.layer_color(self.m.mouse_layer)
        cr.new_path()
        cr.arc(cx * u, cy * u, r * u, 0, 6.2832)
        cr.set_source_rgba(*mix(self.key_bg, color, 0.15 + 0.6 * glow), self.key_alpha + (1 - self.key_alpha) * glow)
        cr.fill_preserve()
        cr.set_source_rgb(*mix(self.edge, color, 0.3 + 0.7 * glow))
        cr.set_line_width(1.5)
        cr.stroke()
        if self.m.auto_active(t):
            remaining = max(0.0, self.m.auto_until - t) / self.m.auto_mouse_time
            if remaining <= 0:
                remaining = 1.0  # layer is on, but not from a timeout we can see
            cr.new_path()
            cr.arc(cx * u, cy * u, (r + 0.1) * u, -1.5708, -1.5708 + 6.2832 * min(1.0, remaining))
            cr.set_source_rgb(*color)
            cr.set_line_width(2.5)
            cr.stroke()

    def draw_key(self, cr, pos, cx, cy, angle, active, t, shift=False):
        m, u = self.m, self.u
        hold = m.held.get(pos) if self.preview is None else None
        if hold is not None:
            key, layer = hold.key, hold.layer
        else:
            key, layer = m.resolve(pos, active)
        size = 0.94 * u
        cr.save()
        cr.translate(cx * u, cy * u)
        cr.rotate(angle * 3.14159265 / 180)
        self.rounded(cr, -size / 2, -size / 2, size, size, 0.16 * u)
        accent = self.layer_color(layer)
        target = self.layer_color(key.layer) if key.layer is not None else None
        pressed = pos in m.matrix and self.preview is None
        if pressed:
            role = m.role(hold) if hold else None
            fill = self.mod_color if role in ("hold", "mod") and key.mods else accent
            if role == "layer" and target:
                fill = target
            cr.set_source_rgb(*fill)
            cr.fill()
            text_color = self.bg
        else:
            cr.set_source_rgba(*self.key_bg, self.key_alpha)
            cr.fill_preserve()
            cr.set_source_rgb(*(mix(self.edge, target, 0.65) if target else self.edge))
            cr.set_line_width(1.2)
            cr.stroke()
            text_color = self.fg if layer == max(active) or layer == 0 else mix(self.fg, self.dim, 0.5)
            if layer != 0 and layer == max(active):
                text_color = self.led_tint(layer, pos) or mix(self.fg, accent, 0.55)
        if key.kind == "none":
            cr.restore()
            return
        hold_label = key.hold
        if key.kind in ("mo", "tg", "to", "df", "osl", "tt", "lt", "lm") and target and not pressed:
            cr.rectangle(-size / 2 + 0.16 * u, size / 2 - 0.07 * u, size - 0.32 * u, 0.045 * u)
            cr.set_source_rgb(*target)
            cr.fill()
            if key.kind in ("mo", "tt"):
                text_color = target
        label = (shifted_label(key) if shift else None) or key.label
        main_size = 0.34 * u if len(label) <= 2 else 0.25 * u
        main_y = -0.08 * u if hold_label else 0
        self.text(cr, label, 0, main_y, main_size, text_color, max_w=size - 0.12 * u, bold=pressed)
        if hold_label:
            hc = self.bg if pressed else (target if key.kind == "lt" and target else self.mod_color)
            self.text(cr, hold_label, 0, 0.27 * u, 0.18 * u, hc, max_w=size - 0.1 * u)
        cr.restore()

    def draw_history(self, cr, t):
        u = self.u
        y = self.height - self.pad - self.history_h / 2 + 0.05 * u
        x = self.width - self.pad
        size = 0.27 * u
        for stamp, label, special in reversed(self.m.history):
            age = t - stamp
            if age > 8:
                break
            fade = max(0.25, 1 - age / 8)
            color = mix(self.bg, self.mod_color if special or len(label) > 1 else self.fg, fade)
            w = self.text(cr, label, x, y, size, color, align="right")
            x -= w + 0.22 * u
            if x < self.pad + 2 * u:
                break


# ── System tray ─────────────────────────────────────────────────────────────

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  </interface>
</node>"""


class Tray:
    """A StatusNotifierItem (the protocol behind KDE's system tray) whose menu
    closes the HUD or toggles move mode. Re-registers when plasmashell restarts;
    does nothing on desktops without a StatusNotifierWatcher."""

    SNI_PATH, MENU_PATH = "/StatusNotifierItem", "/MenuBar"
    CLOSE_ID, MOVE_ID = 1, 2

    def __init__(self, connection, on_close, on_move, is_moving):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.conn, self.on_close = connection, on_close
        self.on_move, self.is_moving = on_move, is_moving
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.ids = [
            connection.register_object_with_closures2(self.SNI_PATH, sni, self.sni_call, self.sni_prop, None),
            connection.register_object_with_closures2(self.MENU_PATH, menu, self.menu_call, self.menu_prop, None),
        ]
        self.owner = Gio.bus_own_name_on_connection(connection, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self.watch = Gio.bus_watch_name_on_connection(
            connection, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE, self.register, None)

    def register(self, conn, _name, _owner):
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                  "RegisterStatusNotifierItem", self.GLib.Variant("(s)", (self.name,)), None,
                  self.Gio.DBusCallFlags.NONE, -1, None, None)

    def close(self):
        self.Gio.bus_unwatch_name(self.watch)
        self.Gio.bus_unown_name(self.owner)
        for reg in self.ids:
            self.conn.unregister_object(reg)

    # org.kde.StatusNotifierItem

    def sni_prop(self, _conn, _sender, _path, _iface, prop):
        V = self.GLib.Variant
        return {
            "Category": V("s", "Hardware"),
            "Id": V("s", "keyboard-hud"),
            "Title": V("s", "Keyboard HUD"),
            "Status": V("s", "Active"),
            "IconName": V("s", "input-keyboard"),
            "ToolTip": V("(sa(iiay)ss)", ("input-keyboard", [], "Keyboard HUD", "Click to close; use the menu to move")),
            "ItemIsMenu": V("b", True),
            "Menu": V("o", self.MENU_PATH),
        }.get(prop)

    def sni_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        # With ItemIsMenu the tray shows the menu itself; a direct activation closes the HUD.
        if method in ("Activate", "SecondaryActivate"):
            self.GLib.idle_add(self.on_close)
        invocation.return_value(None)

    # com.canonical.dbusmenu

    def item(self, item_id):
        V = self.GLib.Variant
        if item_id == 0:
            return (0, {"children-display": V("s", "submenu")},
                    [V("(ia{sv}av)", self.item(self.MOVE_ID)), V("(ia{sv}av)", self.item(self.CLOSE_ID))])
        if item_id == self.MOVE_ID:
            moving = self.is_moving()
            return (self.MOVE_ID, {"label": V("s", "Lock HUD" if moving else "Move HUD"),
                                   "icon-name": V("s", "changes-prevent" if moving else "transform-move")}, [])
        return (self.CLOSE_ID, {"label": V("s", "Close keyboard HUD"), "icon-name": V("s", "window-close")}, [])

    def menu_prop(self, _conn, _sender, _path, _iface, prop):
        V = self.GLib.Variant
        return {"Version": V("u", 3), "TextDirection": V("s", "ltr"), "Status": V("s", "normal"),
                "IconThemePath": V("as", [])}.get(prop)

    def menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        V = self.GLib.Variant
        args = params.unpack()
        if method == "GetLayout":
            invocation.return_value(V("(u(ia{sv}av))", (1, self.item(0))))
        elif method == "GetGroupProperties":
            ids = [i for i in args[0] if i in (0, self.CLOSE_ID, self.MOVE_ID)] or [0, self.MOVE_ID, self.CLOSE_ID]
            invocation.return_value(V("(a(ia{sv}))", ([self.item(i)[:2] for i in ids],)))
        elif method == "GetProperty":
            props = self.item(args[0])[1]
            invocation.return_value(V("(v)", (props.get(args[1], V("s", "")),)))
        elif method == "Event":
            if args[1] == "clicked":
                if args[0] == self.CLOSE_ID:
                    self.GLib.idle_add(self.on_close)
                elif args[0] == self.MOVE_ID:
                    self.GLib.idle_add(self.on_move)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event, _data, _ts in args[0]:
                if event == "clicked":
                    if item_id == self.CLOSE_ID:
                        self.GLib.idle_add(self.on_close)
                    elif item_id == self.MOVE_ID:
                        self.GLib.idle_add(self.on_move)
            invocation.return_value(V("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(V("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(V("(aiai)", ([], [])))
        else:
            invocation.return_value(None)


# ── App ─────────────────────────────────────────────────────────────────────

APP_ID = "den.miko.KeyboardHud"


def config_dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "keyboard-hud")


def default_layout():
    return os.path.join(config_dir(), "layout.json")


def position_path():
    return os.path.join(config_dir(), "position.json")


def load_position():
    """The last locked position and opacity, or None if missing or malformed."""
    try:
        with open(position_path()) as f:
            data = json.load(f)
        monitor = data.get("monitor")
        opacity = data.get("opacity")
        if isinstance(opacity, (int, float)):
            opacity = max(0.1, min(1.0, float(opacity)))
        else:
            opacity = None
        return {"x": int(data["x"]), "y": int(data["y"]),
                "monitor": monitor if isinstance(monitor, str) else None,
                "opacity": opacity}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_position(x, y, monitor, opacity):
    path = position_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"x": int(x), "y": int(y), "monitor": monitor,
                       "opacity": round(float(opacity), 3)}, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"keyboard-hud: cannot save position: {e}", file=sys.stderr)


def clear_position():
    try:
        os.unlink(position_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"keyboard-hud: cannot remove position: {e}", file=sys.stderr)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--layout", default=os.environ.get("KEYBOARD_HUD_LAYOUT") or default_layout(),
                    help="Argos layout export (JSON); defaults to $KEYBOARD_HUD_LAYOUT, then ~/.config/keyboard-hud/layout.json")
    ap.add_argument("--theme", default=os.environ.get("KEYBOARD_HUD_THEME"), help="theme JSON (background, foreground, palette)")
    ap.add_argument("--vid", type=lambda s: int(s, 16), default=0xA8F8, help="USB vendor id (hex)")
    ap.add_argument("--pid", type=lambda s: int(s, 16), default=0x1833, help="USB product id (hex)")
    ap.add_argument("--poll-hz", type=float, default=200, help="matrix polling rate")
    ap.add_argument("--auto-mouse-layer", type=int, help="layer the trackball activates (default: 'pointer')")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--opacity", type=float, default=None,
                    help="initial background opacity, 0-1; scroll in move mode to adjust")
    ap.add_argument("--position", choices=["bottom", "top", "bottom-left", "bottom-right", "top-left", "top-right"],
                    help="initial placement; defaults to the saved position, then bottom")
    ap.add_argument("--monitor", help="monitor connector name (e.g. DP-1); defaults to the saved monitor, then the first")
    ap.add_argument("--margin", type=int, default=24)
    ap.add_argument("--move", action="store_true",
                    help="start in move mode, or toggle move mode of a running HUD")
    ap.add_argument("--reset-position", action="store_true",
                    help="forget the saved position and opacity, and use --position (default: bottom)")
    ap.add_argument("--interactive", action="store_true",
                    help="accept clicks: click a layer to preview it, right-click to close")
    ap.add_argument("--no-tray", action="store_true", help="do not show a system tray icon")
    ap.add_argument("--snapshot", metavar="PNG", help="render one frame to a PNG and exit (no keyboard needed)")
    ap.add_argument("--snapshot-layer", type=int, help="layer to preview in --snapshot")
    ap.add_argument("--snapshot-press", nargs="*", default=[], metavar="ROW,COL", help="matrix keys held in --snapshot")
    return ap.parse_args(argv)


def load(args):
    if not os.path.exists(args.layout):
        sys.exit(f"keyboard-hud: no layout at {args.layout}; put an Argos export there or pass --layout")
    with open(args.layout) as f:
        layout = json.load(f)
    theme = {}
    if args.theme:
        with open(args.theme) as f:
            theme = json.load(f)
    model = Model(layout, args.auto_mouse_layer)
    opacity = 0.6 if args.opacity is None else args.opacity
    return model, Painter(model, theme, args.scale, opacity)


def snapshot(args):
    import cairo
    model, painter = load(args)
    model.connected, model.status = True, "connected"
    t = time.monotonic()
    if args.snapshot_layer is not None:
        painter.preview = args.snapshot_layer
    model.set_matrix({tuple(int(v) for v in p.split(",")) for p in args.snapshot_press}, t)
    model.tick(t + 1)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, painter.width, painter.height)
    painter.draw(cairo.Context(surface), t + 1)
    surface.write_to_png(args.snapshot)


def run(args):
    import gi
    gi.require_version("Gtk", "4.0")
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell
    except (ValueError, ImportError):
        LayerShell = None
    from gi.repository import Gdk, Gio, GLib, Gtk
    import cairo

    model, painter = load(args)
    state = {
        "win": None, "device": None, "tray": None, "area": None,
        "moving": False, "pos": [0, 0], "monitor": None, "move_cache": None,
        "drag": None, "left_click": None, "scroll": None,
    }

    def post(kind, *payload):
        GLib.idle_add(handle, kind, payload, time.monotonic())

    def handle(kind, payload, t):
        if kind == "firmware":
            model.firmware_state(*payload, t)
        elif kind == "matrix":
            model.set_matrix(payload[0], t)
        elif kind == "reset":
            model.fw = False
            model.set_matrix(set(), t)
        elif kind == "hid":
            model.hid_keyboard(payload[0], t)
        elif kind == "consumer":
            model.hid_consumer_usage(payload[0], t)
        elif kind == "motion":
            model.motion(t)
        elif kind == "status":
            model.connected, model.status = payload
            model.rev += 1
        return False

    def monitor_name(monitor):
        if monitor is None:
            return None
        return monitor.get_connector() or monitor.get_model()

    def choose_monitor(display, saved):
        if display is None:
            return None
        monitors = display.get_monitors()
        wanted = args.monitor or (saved.get("monitor") if saved else None)
        if wanted:
            for i in range(monitors.get_n_items()):
                monitor = monitors.get_item(i)
                if monitor_name(monitor) == wanted:
                    return monitor
        return monitors.get_item(0) if monitors.get_n_items() else None

    def clamp_position(x, y):
        monitor = state["monitor"]
        if monitor is None:
            return [int(x), int(y)]
        geometry = monitor.get_geometry()
        max_x = max(0, geometry.width - painter.width)
        max_y = max(0, geometry.height - painter.height)
        return [max(0, min(int(x), max_x)), max(0, min(int(y), max_y))]

    def initial_position(monitor, saved):
        if monitor is None:
            return [0, 0]
        geometry = monitor.get_geometry()
        if saved is not None and args.position is None:
            return clamp_position(saved["x"], saved["y"])
        place = args.position or "bottom"
        margin = args.margin
        x = (geometry.width - painter.width) // 2
        y = geometry.height - painter.height - margin
        if "left" in place:
            x = margin
        elif "right" in place:
            x = geometry.width - painter.width - margin
        if place.startswith("top"):
            y = margin
        return clamp_position(x, y)

    def save_current_position():
        save_position(state["pos"][0], state["pos"][1], monitor_name(state["monitor"]), painter.opacity)

    def apply_input_region():
        win = state["win"]
        if win is None:
            return
        surface = win.get_surface()
        if surface is None:
            return
        if state["moving"] or args.interactive:
            region = cairo.Region(cairo.RectangleInt(0, 0, painter.width, painter.height))
        else:
            region = cairo.Region()
        surface.set_input_region(region)

    def set_position(win, pos):
        if LayerShell is None or not LayerShell.is_supported():
            return
        LayerShell.set_margin(win, LayerShell.Edge.LEFT, int(pos[0]))
        LayerShell.set_margin(win, LayerShell.Edge.TOP, int(pos[1]))

    def set_normal_geometry(win, pos):
        if LayerShell is None or not LayerShell.is_supported():
            return
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, False)
        LayerShell.set_anchor(win, LayerShell.Edge.RIGHT, False)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
        LayerShell.set_margin(win, LayerShell.Edge.RIGHT, 0)
        LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, 0)
        set_position(win, pos)
        win.set_default_size(painter.width, painter.height)

    def move_to(x, y):
        state["pos"] = clamp_position(x, y)
        if state["win"] is not None:
            set_position(state["win"], state["pos"])

    def set_gestures(moving):
        # Only the gesture that makes sense in the current mode receives events.
        if state["drag"] is not None:
            state["drag"].set_propagation_phase(Gtk.PropagationPhase.BUBBLE if moving else Gtk.PropagationPhase.NONE)
        if state["left_click"] is not None:
            state["left_click"].set_propagation_phase(Gtk.PropagationPhase.NONE if moving else Gtk.PropagationPhase.BUBBLE)
        if state["scroll"] is not None:
            state["scroll"].set_propagation_phase(Gtk.PropagationPhase.BUBBLE if moving else Gtk.PropagationPhase.NONE)

    def render_move_cache():
        # The HUD content is static during a drag; blitting this image keeps the
        # frame clock at the monitor refresh rate. Re-render it when opacity changes.
        scale = state["monitor"].get_scale_factor() if state["monitor"] is not None else 1
        cache = cairo.ImageSurface(cairo.FORMAT_ARGB32, painter.width * scale, painter.height * scale)
        cache_cr = cairo.Context(cache)
        cache_cr.scale(scale, scale)
        painter.draw(cache_cr, time.monotonic())
        state["move_cache"] = (cache, scale)

    def enter_move():
        if state["moving"] or state["win"] is None:
            return
        state["moving"] = True
        painter.moving = True
        set_gestures(True)
        render_move_cache()
        win = state["win"]
        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.ON_DEMAND)
        if state["area"] is not None:
            state["area"].set_cursor(Gdk.Cursor.new_from_name("move", None))
            state["area"].queue_draw()
        apply_input_region()

    def exit_move():
        if not state["moving"]:
            return
        state["moving"] = False
        painter.moving = False
        state["move_cache"] = None
        set_gestures(False)
        win = state["win"]
        if LayerShell is not None and LayerShell.is_supported():
            save_current_position()
            if win is not None:
                set_position(win, state["pos"])
                LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        if state["area"] is not None:
            state["area"].set_cursor(None)
            state["area"].queue_draw()
        apply_input_region()

    def toggle_move():
        if state["moving"]:
            exit_move()
        else:
            enter_move()

    def quit_app():
        if state["moving"]:
            exit_move()
        app.quit()
        return False

    def build(app, start_moving=False):
        win = Gtk.ApplicationWindow(application=app, title="Keyboard HUD", decorated=False, resizable=False)
        css = Gtk.CssProvider()
        css.load_from_data(b"window, .background { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        area = Gtk.DrawingArea(content_width=painter.width, content_height=painter.height)

        def draw_func(_area, cr, _w, _h):
            cache = state["move_cache"]
            if state["moving"] and cache is not None:
                surface, scale = cache
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.paint()
                cr.set_operator(cairo.OPERATOR_OVER)
                cr.save()
                cr.scale(1 / scale, 1 / scale)
                cr.set_source_surface(surface, 0, 0)
                cr.paint()
                cr.restore()
            else:
                painter.draw(cr, time.monotonic())
        area.set_draw_func(draw_func)
        win.set_child(area)
        state["area"] = area

        if args.reset_position:
            clear_position()
        saved = load_position()
        if args.opacity is None and saved is not None and saved.get("opacity") is not None:
            painter.set_opacity(saved["opacity"])
        monitor = choose_monitor(Gdk.Display.get_default(), saved)
        state["monitor"] = monitor
        state["pos"] = initial_position(monitor, saved)

        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.init_for_window(win)
            LayerShell.set_namespace(win, "keyboard-hud")
            LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
            LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(win, -1)
            if monitor is not None:
                LayerShell.set_monitor(win, monitor)
            set_normal_geometry(win, state["pos"])
        else:
            print("keyboard-hud: layer-shell unavailable, using a plain window", file=sys.stderr)

        left_click = Gtk.GestureClick(button=1)

        def on_left_click(_gesture, _n, x, y):
            if not args.interactive or state["moving"]:
                return
            hit = layer_at(x, y)
            painter.preview = None if hit is None or hit == painter.preview else hit
        left_click.connect("pressed", on_left_click)
        area.add_controller(left_click)
        state["left_click"] = left_click

        right_click = Gtk.GestureClick(button=3)

        def on_right_click(_gesture, _n, _x, _y):
            if state["moving"]:
                exit_move()
            elif args.interactive:
                quit_app()
        right_click.connect("pressed", on_right_click)
        area.add_controller(right_click)

        drag = Gtk.GestureDrag(button=1)

        def on_drag_begin(gesture, start_x, start_y):
            if not state["moving"]:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
            if LayerShell is None or not LayerShell.is_supported():
                surface = win.get_surface()
                device = gesture.get_current_event_device()
                timestamp = gesture.get_current_event_time()
                if isinstance(surface, Gdk.Toplevel) and device is not None:
                    surface.begin_move(device, 1, start_x, start_y, timestamp)

        def on_drag_update(_gesture, offset_x, offset_y):
            if not state["moving"]:
                return
            if LayerShell is None or not LayerShell.is_supported():
                return
            # The gesture offset is relative to the moving window, so adding it to the
            # current position keeps the drag going even when the pointer leaves the HUD.
            move_to(state["pos"][0] + offset_x, state["pos"][1] + offset_y)

        def on_drag_end(_gesture, _offset_x, _offset_y):
            if state["moving"] and LayerShell is not None and LayerShell.is_supported():
                save_current_position()

        drag.connect("drag-begin", on_drag_begin)
        drag.connect("drag-update", on_drag_update)
        drag.connect("drag-end", on_drag_end)
        area.add_controller(drag)
        state["drag"] = drag

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)

        def on_scroll(_controller, _dx, dy):
            if not state["moving"]:
                return False
            painter.set_opacity(painter.opacity - dy * 0.05)
            render_move_cache()
            if state["area"] is not None:
                state["area"].queue_draw()
            return True
        scroll.connect("scroll", on_scroll)
        area.add_controller(scroll)
        state["scroll"] = scroll
        set_gestures(state["moving"])

        key = Gtk.EventControllerKey()

        def on_key(_controller, keyval, _keycode, _mods):
            if state["moving"] and keyval == Gdk.KEY_Escape:
                exit_move()
                return True
            return False
        key.connect("key-pressed", on_key)
        win.add_controller(key)

        win.connect("map", lambda _w: apply_input_region())

        drawn = {"rev": None, "t": 0.0, "preview": None}

        def frame(_widget, _clock):
            t = time.monotonic()
            model.tick(t)
            anim = painter.animating(t)
            changed = drawn["rev"] != model.rev or drawn["preview"] != painter.preview
            # Keep the frame clock at the monitor refresh rate while dragging. The draw
            # function only blits the cached HUD image, so this stays cheap.
            if state["moving"] or changed or anim == "fast" or (anim == "slow" and t - drawn["t"] > 0.25):
                drawn.update(rev=model.rev, t=t, preview=painter.preview)
                area.queue_draw()
            return True
        area.add_tick_callback(frame)

        state["win"] = win
        device = Device(model.rows, model.cols, args.vid, args.pid, args.poll_hz, post)
        device.start()
        state["device"] = device
        if not args.no_tray and app.get_dbus_connection() is not None:
            try:
                state["tray"] = Tray(app.get_dbus_connection(), quit_app, toggle_move, lambda: state["moving"])
            except Exception as e:  # the overlay works without a tray icon
                print(f"keyboard-hud: no tray icon: {e}", file=sys.stderr)
        if start_moving:
            enter_move()
        win.present()

    def layer_at(x, y):
        if not (painter.pad <= y <= painter.pad + painter.header_h * 0.85):
            return None
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        for n, left, w in painter.layer_pills(cr):
            if left <= x <= left + w:
                return n
        return None

    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    app.add_main_option("move", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                        "Toggle move mode", None)

    def on_command_line(_app, command_line):
        move = command_line.get_options_dict().lookup_value("move", None) is not None
        if state["win"] is None:
            build(_app, start_moving=move)
        elif move:
            toggle_move()
        else:
            quit_app()  # a second launch toggles the HUD off
        return 0

    app.connect("command-line", on_command_line)

    def on_activate(app):
        if state["win"] is None:
            build(app)
        else:
            quit_app()
    app.connect("activate", on_activate)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        add_signal = GLibUnix.signal_add
    except (ValueError, ImportError):
        add_signal = GLib.unix_signal_add
    for signum in (2, 15):  # SIGINT, SIGTERM
        add_signal(GLib.PRIORITY_DEFAULT, signum, lambda: quit_app() or False)
    status = app.run([sys.argv[0]] + (["--move"] if args.move else []))
    if state["tray"]:
        state["tray"].close()
    if state["device"]:
        state["device"].stop.set()
    return status


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.snapshot:
        snapshot(args)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
