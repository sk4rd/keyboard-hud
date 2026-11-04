# Bastard Keyboards' vendor (Argos) firmware for the Charybdis 4x6 splinktegrated
# rev1, plus the keyboard_hud module that streams key and layer state to the HUD.
{
  lib,
  stdenvNoCC,
  fetchFromGitHub,
  python3,
  qmk,
}:

let
  qmkFirmware = fetchFromGitHub {
    owner = "bastardkb";
    repo = "bastardkb-qmk";
    rev = "bc50f889c80773e96bfcb1249f137b676cf0eb5f";
    fetchSubmodules = true;
    hash = "sha256-CpH5R/KvSlxdVrcRZmEbUssl2UlKmQ8TmJYSwRPeF78=";
  };

  # Holds the vendor keymaps and, as a submodule, the Argos and pointing-device modules.
  userspace = fetchFromGitHub {
    owner = "Bastardkb";
    repo = "qmk_userspace";
    rev = "80d06aa14cdf8bb5067069c7918e02662322781d";
    fetchSubmodules = true;
    hash = "sha256-mKxR1s5rSfnSnLHbOKUPOZQS/HsP83FImTUI9oaluIk=";
  };

  keyboard = "bastardkb/charybdis/4x6/splinktegrated_rev1";
  keymap = "miko-hud";
in
stdenvNoCC.mkDerivation {
  pname = "charybdis-hud-firmware";
  version = "0-unstable-2026-11-03";

  dontUnpack = true;
  nativeBuildInputs = [
    python3
    qmk
  ];

  buildPhase = ''
    runHook preBuild
    export HOME=$TMPDIR

    cp -r ${qmkFirmware} qmk_firmware
    cp -r ${userspace} userspace
    chmod -R u+w qmk_firmware userspace
    patchShebangs qmk_firmware/util
    mkdir -p userspace/modules/miko
    cp -r ${../firmware/keyboard_hud} userspace/modules/miko/keyboard_hud
    cp -r ${../firmware/keymap} userspace/keyboards/bastardkb/charybdis/4x6/keymaps/${keymap}
    chmod -R u+w userspace

    # SKIP_VERSION pins QMK_BUILDDATE. VIA wipes the EEPROM keymap whenever the
    # build date changes, so a fixed date keeps the Argos layout across reflashes.
    QMK_HOME=$PWD/qmk_firmware QMK_USERSPACE=$PWD/userspace \
      qmk compile -kb ${keyboard} -km ${keymap} -e SKIP_VERSION=yes
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 userspace/*.uf2 -t $out
    runHook postInstall
  '';

  meta.description = "Charybdis 4x6 Argos firmware with key/layer reporting for keyboard-hud";
}
