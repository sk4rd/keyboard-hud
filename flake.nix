{
  description = "Live overlay of an Argos (QMK/VIA) layout on a Bastard Keyboards Charybdis 4x6";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.keyboard-hud;
        keyboard-hud = pkgs.callPackage ./nix/package.nix { };
        # Argos vendor firmware plus the keyboard_hud module; copy the .uf2 onto each half.
        charybdis-firmware = pkgs.callPackage ./nix/firmware.nix { };
      });

      nixosModules.default = ./nix/module.nix;

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
