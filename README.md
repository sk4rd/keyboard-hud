# keyboard-hud

A live, see-through overlay of an [Argos](https://docs.bastardkb.com/fw/argos.html)
layout on a Bastard Keyboards Charybdis 4x6. It lights the keys you press,
switches layers when the keyboard does, and lists what you just typed. Use it
to learn a new layout.

It runs on Wayland compositors with layer-shell (Plasma, Sway, Hyprland). On
other desktops it falls back to a plain window. On Plasma it also adds a
tray icon with close and move entries.

## Firmware

Stock Argos firmware can't report pressed keys or layers to the host. Its VIA
matrix query is compiled locked. `firmware/` holds a small QMK community
module, `keyboard_hud`, built into Bastard KB's vendor firmware. Once the HUD
subscribes, the module streams the switch matrix, layer state and modifiers
over raw HID. It goes quiet 3 s after the HUD stops.

```sh
nix build .#charybdis-firmware   # result/*.uf2, for splinktegrated rev1
```

Flash both halves. Double-tap the reset button so the `RPI-RP2` drive
appears, then copy the `.uf2` onto it. The first flash resets the VIA keymap,
so re-import your layout in Argos afterwards. The build date is pinned, so
later rebuilds keep it.

Without the module, the HUD shows "stock firmware: no key or layer data".

## Layout

Export your layout from Argos and put it at
`~/.config/keyboard-hud/layout.json`. A symlink works too, so the file can
live wherever you keep it. The HUD reads it each time it starts, so a new
layout needs no rebuild.

`--layout` or `KEYBOARD_HUD_LAYOUT` overrides that path.

## NixOS

```nix
{
  inputs.keyboard-hud.url = "github:sk4rd/keyboard-hud";

  # in a NixOS configuration
  imports = [ inputs.keyboard-hud.nixosModules.default ];
  programs.keyboard-hud = {
    enable = true;
    # theme = { background = "#000000"; palette = { blue = "#449fd0"; }; };
    # extraArgs = [ "--opacity" "0.5" ];
    # shortcut = "Meta+Alt+K";           # toggle the HUD; null to skip
    # moveShortcut = "Meta+Alt+Shift+K"; # toggle move mode; null to skip
  };
}
```

The module installs the HUD, a launcher and the shortcut. It also adds a
udev rule so users can open the keyboard's hidraw nodes; set
`hidrawAccess = false` if another rule already does that.

## Usage

`keyboard-hud` toggles the overlay: running it again closes the open HUD.

- `--interactive`: click a layer pill to preview it; right-click to close.
- `--move`: start in move mode, or toggle move mode of a running HUD.
- `--position`, `--monitor`, `--margin`, `--scale`, `--opacity`: placement and look.
- `--reset-position`: forget the saved position and opacity, and use `--position` again.
- `--snapshot out.png [--snapshot-layer N]`: render one frame without a keyboard.

### Moving the HUD

The HUD is click-through by default. To move it, run `keyboard-hud --move` or
pick **Move HUD** from the tray menu. The HUD then accepts pointer input: drag
it anywhere on its monitor, then right-click, press Escape, pick **Lock HUD**
from the tray, or run `keyboard-hud --move` again to lock it in place. Scroll
the wheel over the HUD to change its opacity. The position and opacity are
saved to `~/.config/keyboard-hud/position.json` and restored on the next
start. Dragging moves the HUD within its monitor; use `--monitor`
(e.g. `--monitor DP-2`) to choose another monitor.

The HUD draws key labels from the layout's keycodes and colours each key with
its LED colour from Argos. That way the overlay and the board share one colour
scheme.
