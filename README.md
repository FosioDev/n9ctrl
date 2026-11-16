# N9Ctrl (WIP)

**Control the AKG N9 Hybrid headphones from Linux, through the 2.4 GHz USB dongle.**

The official AKG app only runs on phones. This is a small Linux tool that talks
to the headset through the USB dongle it ships with, so you can change noise control
and other settings without picking up your phone.

> **Unofficial project.** Not affiliated with, endorsed by or supported by AKG or HARMAN.
> "AKG" and "N9 Hybrid" are trademarks of their owners and are used only to describe
> compatibility. Use at your own risk.

## Features

- Noise control: ANC, Ambient and Off, plus ANC / ambient level sliders
- Adaptive ANC and auto compensation
- Left/right balance
- Hi-Res audio, spatial sound, low-volume dynamic EQ, max volume limit
- Call mode settings: profile, sound settings, voice settings, sound level optimizer
- Auto power-off, voice prompts, Smart Talk
- Battery level in the tray icon, low-battery notification
- Stays in sync: changes made with the headset buttons or the phone app show up within seconds
- Some features that the official app hides while Hi-Res is on (spatial sound, low-volume EQ) can be toggled — the headset accepts them

## Requirements

- AKG N9 Hybrid with its USB dongle plugged in (the headset must be connected to the dongle)

## Install

WIP

## How it works

The dongle exposes a vendor HID channel that carries Airoha **RACE** commands to the headset.
The commands are documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Safety

N9Ctrl only uses commands that were tested by setting a value, reading it back and restoring it.
It does **not** touch pairing, factory reset, firmware updates, gesture mapping or custom EQ
(those write to the headset's non-volatile memory). Changing Hi-Res audio restarts the headset;
the app asks before doing it.

## Not supported (yet)

- Custom 10-band EQ and EQ presets
- Left button cycle customization (gestures)
- Charging while listening — the headset turns audio off while charging; this is a firmware
  decision and is not controllable over the dongle
- Bluetooth-only features of the official app (auto play/pause, Smart Audio & Video)

## License

[MIT](LICENSE).
