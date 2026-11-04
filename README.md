# keyboard-hud

A live, see-through overlay of an [Argos](https://docs.bastardkb.com/fw/argos.html)
layout on a Bastard Keyboards Charybdis 4x6. It lights the keys you press,
switches layers when the keyboard does, and lists what you just typed. Use it
to learn a new layout.

It runs on Wayland compositors with layer-shell (Plasma, Sway, Hyprland). On
other desktops it falls back to a plain window. On Plasma it also adds a
tray icon with a close entry.

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

## NixOS

```nix
{
  inputs.keyboard-hud.url = "github:sk4rd/keyboard-hud";

  # in a NixOS configuration
  imports = [ inputs.keyboard-hud.nixosModules.default ];
  programs.keyboard-hud = {
    enable = true;
    layout = ./argos-layout.json;   # Argos → export
    # theme = { background = "#000000"; palette = { blue = "#449fd0"; }; };
    # extraArgs = [ "--opacity" "0.5" ];
    # shortcut = "Meta+Alt+K";      # Plasma global shortcut; null to skip
  };
}
```

The module installs the HUD, a launcher and the shortcut. It also adds a
udev rule so users can open the keyboard's hidraw nodes; set
`hidrawAccess = false` if another rule already does that.

## Usage

`keyboard-hud` toggles the overlay: running it again closes the open HUD.

- `--interactive`: click a layer pill to preview it; right-click to close.
- `--position`, `--margin`, `--scale`, `--opacity`: placement and look.
- `--snapshot out.png [--snapshot-layer N]`: render one frame without a keyboard.

The HUD draws key labels from the layout's keycodes and colours each key with
its LED colour from Argos. That way the overlay and the board share one colour
scheme.
