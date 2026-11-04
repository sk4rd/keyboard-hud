// SPDX-License-Identifier: GPL-2.0-or-later
//
// Streams the switch matrix, layer state and modifiers to the keyboard-hud
// overlay over raw HID. The firmware stays silent until a host subscribes via
// VIA's custom-value command on HUD_CHANNEL; the subscription lapses after
// HUD_SUBSCRIPTION_MS without renewal, so configurators such as Argos never
// receive these reports unless the HUD is running at the same time.
//
// Subscribe:   [0x07 id_custom_set_value, HUD_CHANNEL, 0x01, on]
// Report:      [HUD_REPORT_ID, version, rows, layer_state (u32 LE),
//               default_layer_state (u32 LE), mods, weak mods, oneshot mods,
//               row 0 .. row N-1]

#include QMK_KEYBOARD_H
#include <string.h>
#include "raw_hid.h"
#include "via.h"

ASSERT_COMMUNITY_MODULES_MIN_API_VERSION(1, 0, 0);

#define HUD_CHANNEL 0x4B
#define HUD_VALUE_SUBSCRIBE 0x01
#define HUD_REPORT_ID 0xA5
#define HUD_PROTOCOL_VERSION 1
#define HUD_SUBSCRIPTION_MS 3000
#define HUD_HEADER_SIZE 14
#define HUD_REPORT_SIZE 32 // raw HID endpoint size

_Static_assert(MATRIX_COLS <= 8, "keyboard_hud packs one byte per matrix row");
_Static_assert(HUD_HEADER_SIZE + MATRIX_ROWS <= HUD_REPORT_SIZE, "matrix does not fit a raw HID report");

static bool     hud_subscribed = false;
static bool     hud_dirty      = false;
static uint32_t hud_renewed_at = 0;
static uint8_t  hud_last[HUD_REPORT_SIZE];

void via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    // data = [ command_id, channel_id, value_id, value_data ]
    if (data[1] == HUD_CHANNEL && data[0] == id_custom_set_value && data[2] == HUD_VALUE_SUBSCRIBE) {
        hud_subscribed = data[3] != 0;
        hud_renewed_at = timer_read32();
        if (data[3] == 2) {
            hud_dirty = true; // host asks for a fresh snapshot
        }
        data[4] = HUD_PROTOCOL_VERSION;
        return;
    }
    data[0] = id_unhandled;
}

static void hud_put32(uint8_t *out, uint32_t value) {
    out[0] = value & 0xFF;
    out[1] = (value >> 8) & 0xFF;
    out[2] = (value >> 16) & 0xFF;
    out[3] = (value >> 24) & 0xFF;
}

void housekeeping_task_keyboard_hud(void) {
    if (!hud_subscribed) {
        return;
    }
    if (timer_elapsed32(hud_renewed_at) > HUD_SUBSCRIPTION_MS) {
        hud_subscribed = false;
        return;
    }

    uint8_t report[HUD_REPORT_SIZE] = {0};
    report[0] = HUD_REPORT_ID;
    report[1] = HUD_PROTOCOL_VERSION;
    report[2] = MATRIX_ROWS;
    hud_put32(&report[3], (uint32_t)layer_state);
    hud_put32(&report[7], (uint32_t)default_layer_state);
    report[11] = get_mods();
    report[12] = get_weak_mods();
#ifndef NO_ACTION_ONESHOT
    report[13] = get_oneshot_mods();
#endif
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        report[HUD_HEADER_SIZE + row] = (uint8_t)matrix_get_row(row);
    }

    if (hud_dirty || memcmp(report, hud_last, sizeof(report)) != 0) {
        memcpy(hud_last, report, sizeof(report));
        hud_dirty = false;
        raw_hid_send(report, sizeof(report));
    }
}
