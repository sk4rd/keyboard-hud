{
  lib,
  python3Packages,
  wrapGAppsHook4,
  gobject-introspection,
  gtk4,
  gtk4-layer-shell,
  writeText,
  makeDesktopItem,
  # { background, foreground, palette = { red, green, ... }; } in terminal-theme terms.
  theme ? null,
  # Extra command-line flags baked into the wrapper, e.g. [ "--opacity" "0.5" ].
  extraArgs ? [ ],
}:

python3Packages.buildPythonApplication {
  pname = "keyboard-hud";
  version = "0.1.0";
  format = "other";

  src = lib.fileset.toSource {
    root = ../.;
    fileset = ../keyboard_hud.py;
  };

  nativeBuildInputs = [
    wrapGAppsHook4
    gobject-introspection
  ];

  buildInputs = [
    gtk4
    gtk4-layer-shell
  ];

  dependencies = with python3Packages; [
    pygobject3
    pycairo
  ];

  desktopItem = makeDesktopItem {
    name = "keyboard-hud";
    desktopName = "Keyboard HUD";
    comment = "Toggle the live Charybdis layout overlay";
    exec = "keyboard-hud";
    icon = "input-keyboard";
    categories = [ "Utility" ];
  };

  installPhase = ''
    runHook preInstall
    install -Dm755 keyboard_hud.py $out/bin/keyboard-hud
    install -Dm644 $desktopItem/share/applications/keyboard-hud.desktop -t $out/share/applications
    runHook postInstall
  '';

  dontWrapGApps = true;

  # gtk4-layer-shell has to be linked before libwayland-client.
  makeWrapperArgs = [
    "--prefix"
    "LD_PRELOAD"
    ":"
    "${gtk4-layer-shell}/lib/libgtk4-layer-shell.so"
  ]
  ++ lib.optionals (extraArgs != [ ]) [
    "--add-flags"
    (lib.escapeShellArgs extraArgs)
  ]
  ++ lib.optionals (theme != null) [
    "--set-default"
    "KEYBOARD_HUD_THEME"
    "${writeText "keyboard-hud-theme.json" (builtins.toJSON theme)}"
  ];

  preFixup = ''
    makeWrapperArgs+=("''${gappsWrapperArgs[@]}")
  '';

  meta = {
    description = "Live overlay of an Argos keyboard layout on a Charybdis 4x6";
    mainProgram = "keyboard-hud";
  };
}
