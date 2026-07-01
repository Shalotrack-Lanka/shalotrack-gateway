# Command System

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

The command system enables the gateway to send instructions from the server to a connected GPS tracker. This is two-way communication: the tracker sends packets to the gateway (location, status, alarms), and the gateway sends commands back to the tracker (request position, reboot, cut/restore engine relay).

This is one of the more technically significant features of the gateway, because GT06/V5 trackers do not accept arbitrary TCP text — commands must be structured as valid GT06 binary packets with the correct header, checksum, and framing, using Protocol `0x80` specifically. An early implementation attempt sent ASCII strings directly over the socket; the tracker silently ignored them. The discovery that Protocol `0x80` binary packets were required — and the implementation and verification of that — is detailed in the Challenges section of the Supervisor Report.

---

## Architecture

```
Operator (console.py)
        │  types: "where 355172106043787"
        ▼
services/command_service.py
        │  send_where(imei)
        ▼
utils/command_builder.py
        │  build_where() → build_command("WHERE#")
        │  builds a Protocol 0x80 binary packet
        ▼
services/device_registry.py
        │  get_socket(imei) → live socket object
        ▼
socket.sendall(packet)
        │
        ▼ (tracker receives and processes)
        │
Tracker replies with Protocol 0x21 (Command Text Response)
        │
        ▼
services/packet_handler.py (protocol "21" branch)
        │  strips framing, decodes ASCII, prints to console
        ▼
Operator sees tracker's response
```

---

## Protocol `0x80` — Command Packet Format

Built by `utils/command_builder.py::build_command(command: str) -> bytes`.

Packet structure (all in `7878` short-frame format):

```
78 78                   Start bytes
<length>                1 byte: total body length (protocol byte onwards, before CRC)
80                      Protocol number
<cmd_length>            1 byte: len(SERVER_FLAG) + len(command_bytes)
00 00 00 01             SERVER_FLAG (fixed, identifies source as server not SMS)
<command_bytes>         ASCII command string, e.g. b"WHERE#"
00 02                   LANGUAGE (fixed)
<serial[0]> <serial[1]> 2-byte incrementing packet serial
<CRC_high> <CRC_low>    CRC16-X25 over (length_byte + body)
0D 0A                   End bytes
```

Example — `WHERE#` command:

```
78 78 0F 80 0A 00 00 00 01 57 48 45 52 45 23 00 02 00 01 [CRC] 0D 0A
                               W  H  E  R  E  #
```

**Serial number:** a module-level counter in `command_builder.py`, starting at 1 and incrementing with each call to `build_command()`. Resets to 1 on process restart. Not persisted, not verified against tracker acknowledgements.

---

## Available Commands

All commands are defined in `utils/command_builder.py`. Those currently exposed through `command_service.py` and the console:

| Console command | `command_service` function | GT06 ASCII command | Effect |
|---|---|---|---|
| `where <imei>` | `send_where(imei)` | `WHERE#` | Tracker reports current GPS location |
| `reset <imei>` | `reboot(imei)` | `RESET#` | Tracker reboots |
| `relay_off <imei>` | `cut_engine(imei)` | `RELAY,0#` | Cuts engine relay (immobilizes vehicle) |
| `relay_on <imei>` | `resume_engine(imei)` | `RELAY,1#` | Restores engine relay |

Commands defined in `command_builder.py` but **not yet exposed** through the console or `command_service.py`:

| Builder function | GT06 ASCII command |
|---|---|
| `build_status()` | `STATUS#` |
| `build_version()` | `VERSION#` |
| `build_imei()` | `IMEI#` |
| `build_params()` | `PARAM#` |

These are one-line additions to `command_service.py` when needed.

---

## The Console (`console.py`)

A background thread (`start_console()`) reads lines from stdin in a `while True` loop. Format:

```
<action> <imei>
```

This runs as a daemon thread (`daemon=True`), so it does not prevent the process from exiting when the main thread (TCP server) stops.

Limitations:
- Console input is blocking. On AWS/ECS or any environment where stdin is not a terminal (e.g. a Docker container with no attached TTY), the console thread blocks indefinitely on `input()` without doing anything useful.
- There is no way to list currently connected devices from the console — you have to know the IMEI.
- No command history or tab completion.

---

## Command Response — Protocol `0x21`

When the tracker executes a command and responds, it sends back a Protocol `0x21` packet carrying an ASCII text payload. The gateway's `21` branch in `packet_handler.py` handles this:

```python
if raw[:2] == b"\x79\x79":
    payload = raw[10:-6]    # 7979 long frame
else:
    payload = raw[9:-6]     # 7878 short frame

text = payload.decode("ascii", errors="ignore")
print("=" * 60)
print(text)
print("=" * 60)
```

Example response to `WHERE#`:
```
============================================================
Lat:6.9147N,Lon:79.8587E,Course:0,Speed:0,DateTime:25-01-10 12:34:56
============================================================
```

The response text is currently only printed to the console — it is not persisted to the database, not parsed into structured fields, and not correlated back to the specific command (by serial number) that triggered it.

---

## Protocol `0x8A` — Command Acknowledgement

Distinct from Protocol `0x21`. This is a binary acknowledgement at the protocol level (the tracker saying "I received the Protocol `0x80` command"), while `0x21` is the human-readable result. The `8a` branch in `packet_handler.py` saves the raw packet but does not decode its fields.

---

## Thread Safety

`command_service.py::send_command()` calls `socket.sendall()` on the socket stored in the device registry. If the command console thread and the device's own receive-loop thread were to write to the same socket simultaneously, a race condition could occur. Under the current single-tracker setup this does not arise in practice (the device thread is blocked in `conn.recv()` while the console calls `sendall()`), but it is not protected with a lock.
