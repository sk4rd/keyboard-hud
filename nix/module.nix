{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.programs.keyboard-hud;

  package = cfg.package.override {
    inherit (cfg) theme extraArgs;
  };

  # kglobalaccel picks up shortcut .desktop files from share/kglobalaccel. A
  # second launch closes the running HUD, so the shortcut toggles it.
  shortcut = pkgs.writeTextDir "share/kglobalaccel/keyboard-hud.desktop" ''
    [Desktop Entry]
    Type=Application
    Name=Keyboard HUD
    Exec=${lib.getExe package}
    Icon=input-keyboard
    Terminal=false
    StartupNotify=false
    X-KDE-Shortcuts=${cfg.shortcut}
  '';

  # --move toggles move mode on the running HUD instead of closing it.
  moveShortcutFile = pkgs.writeTextDir "share/kglobalaccel/keyboard-hud-move.desktop" ''
    [Desktop Entry]
    Type=Application
    Name=Keyboard HUD Move
    Exec=${lib.getExe package} --move
    Icon=transform-move
    Terminal=false
    StartupNotify=false
    X-KDE-Shortcuts=${cfg.moveShortcut}
  '';
in
{
  options.programs.keyboard-hud = {
    enable = lib.mkEnableOption "the keyboard-hud overlay for Argos/QMK keyboards";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "keyboard-hud package; overridden with theme and extraArgs.";
    };

    theme = lib.mkOption {
      type = lib.types.nullOr (lib.types.attrsOf lib.types.anything);
      default = null;
      example = lib.literalExpression ''
        {
          background = "#000000";
          foreground = "#c8c8c8";
          palette = { red = "#e74669"; green = "#89d287"; yellow = "#dab853"; blue = "#449fd0"; };
        }
      '';
      description = "Colours in terminal-theme terms; unset entries use the built-in defaults.";
    };

    extraArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "--opacity"
        "0.5"
      ];
      description = "Command-line flags passed to every launch.";
    };

    shortcut = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = "Meta+Alt+K";
      description = "Plasma global shortcut that toggles the HUD, or null for none.";
    };

    moveShortcut = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "Meta+Alt+Shift+K";
      description = "Plasma global shortcut that toggles move mode, or null for none.";
    };

    hidrawAccess = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Let logged-in users open the keyboard's hidraw nodes (USB vendor a8f8,
        Bastard Keyboards). Disable if another rule already grants it.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      package
    ]
    ++ lib.optional (cfg.shortcut != null) shortcut
    ++ lib.optional (cfg.moveShortcut != null) moveShortcutFile;
    environment.pathsToLink = lib.mkIf (cfg.shortcut != null || cfg.moveShortcut != null) [
      "/share/kglobalaccel"
    ];

    services.udev.extraRules = lib.mkIf cfg.hidrawAccess ''
      KERNEL=="hidraw*", ATTRS{idVendor}=="a8f8", MODE="0660", GROUP="users", TAG+="uaccess"
    '';
  };
}
