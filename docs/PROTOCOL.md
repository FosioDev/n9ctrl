# AKG N9 Hybrid — USB dongle protocol notes

These notes describe what N9Ctrl sends to the headset through the 2.4 GHz USB dongle.
They were worked out by observation and testing on a single unit (dongle VID `0x0ECB`,
PID `0x20D9`). Nothing here is official; firmware updates may change behaviour.

## Transport

The dongle exposes several HID collections. The control channel is the one with
**usage page `0xFF13`, collection `Col02`**.

| Direction | HID report | Layout |
|-----------|-----------|--------|
| Host → dongle | output report `0x06`, 62 bytes, zero-padded | `06 <len+1> <dest> <RACE frame>` |
| Dongle → host | `GET_REPORT 0x07`, 62 bytes, polled | `07 <len> <dest> <len bytes of stream>` |

`dest` is `0x80` for the headset and `0x00` for the dongle itself. Incoming data is a byte
stream: frames can span or share reports, so buffer per `dest` and split on frame length.

## RACE frames (Airoha)

```
05 <type> <length u16 LE> <id u16 LE> <payload>
```

`length` counts the id and the payload. Types: `5A` request, `5B` response,
`5C`/`5D` indication (sent by the headset on its own, e.g. when a setting changes).
In responses, byte 6 is usually the status (`00` = OK) and data starts at byte 7.
Frames with id `0x0F92` are firmware debug logs and can be ignored.

## Commands used by N9Ctrl

All values were verified by setting, reading back and restoring.

### Noise control

| Feature | Read | Write | Notes |
|--------|------|-------|-------|
| Mode (ANC / Ambient / Off) | `0x0901` `05 00` → `f[9]` filter (1 ANC, 10 ambient, 0 off), `f[10..11]` level | `0x0E06` ANC `00 0A 01 00`, Ambient `00 0A 0A 04`, Off `00 0B` | headset sends `0x0901` indications on change |
| ANC / ambient level (1–7) | as above, or `0x0901` `07 00` → `f[9..10]` ambient level | `0x0E06` `00 1E <level u16 LE>` | applies to the active filter |
| Adaptive ANC | `0x0E17` `03 02` → `f[8]` | `0x0E17` `03 <0/1>` | |
| Auto compensation | `0x0E26` `00 0D 00 00` → `f[14]` (0 = **on**) | `0x0E13` `05 <0 = on, 1 = off>` | inverted value |
| L/R balance | `0x0037` → `f[7]` on, `f[8]` level | `0x0036` `<on> <level>` | level 84…116, 100 = centre |

### Audio

| Feature | Read | Write | Notes |
|--------|------|-------|-------|
| Hi-Res audio | `0x0075` → `f[7]` | `0x0076` `<0/1>` | **headset restarts** |
| Spatial sound | `0x0013` → `f[7]` on, `f[8]` type (1 music, 2 movie, 3 game) | `0x0012` `<on> <type>` | official app hides it with Hi-Res on; headset still accepts |
| Low-volume dynamic EQ | `0x0058` | `0x0059` `<0/1>` | as above |
| Max volume limit (Safe Sound) | `0x0014` | `0x0015` `<0/1>` | |
| Sound level optimizer (call mode) | `0x0051` | `0x0050` `<0/1>` | |
| Audio profile / call sound / call voice | `0x2C83` `13 00` → `f[11]` profile, `f[10]` sound, `f[9]` voice | `0x2C82` `13 00 <value>` | profile 8 entertainment, 9 call; sound 5 natural, 6 treble+, 7 bass+; voice 1 natural, 2 powerful, 3 bright |
| EQ preset index | `0x0901` `00 00` → `f[9]` | `0x0900` `00 00 <index>` | tentative, not in the app yet; index 0 = EQ off |

### System and voice

| Feature | Read | Write | Notes |
|--------|------|-------|-------|
| Battery | `0x0CD6` `00` → indication `5D`, `f[8]` % | — | |
| Auto power-off | `0x2C83` `01 00` → enabled u16, timeout u16 (seconds) | `0x2C82` `01 00 <on u16> <seconds u16>` | |
| Voice prompts | `0x0901` `01 00` → `f[9]` | `0x0900` `01 00 <0/1>` | |
| Smart Talk | `0x0060` → on, delay | `0x0061` `<on> <delay>` | official app only offers it over Bluetooth |
| Chat optimizer | `0x0053` | `0x0052` `<0/1>` | not shown in the official app |
| Audio source | `0x0055` | — | read only in N9Ctrl (switching would drop the dongle link) |

## Not implemented (yet)

- **Custom 10-band EQ** — the official app computes filter coefficients with a native library
  and writes them to the headset's non-volatile memory.
- **Gesture mapping** (left button cycle: ANC / Ambient / Off) — stored as a key map in NVKEY
  `0xF2D6`; read via `0x0A0C`, written via `0x0A0D`.
- **Auto play/pause, Smart Audio & Video** — Bluetooth-only features in the official app.
- Microphone gain (`0x0064`/`0x0065`) — writes are acknowledged but have no effect.

## Safety

Everything above was tested by setting a value, reading it back and restoring it. Commands
that write to non-volatile memory (gestures, custom EQ), pairing, factory reset and firmware
update are deliberately **not** used by N9Ctrl.
