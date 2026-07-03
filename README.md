# Voice-Controlled Command System (Vosk + Serial → ESP32 PPM)

A Python-based offline voice recognition system used to control drone mechanisms — including drone switching and camera switching — by voice. Recognized commands are sent over a serial link to an ESP32, which is expected to convert them into PPM signals so they can be read by the RC transmitter.

---

## 1. Voice Recognition Engine

Speech recognition runs fully offline using **Vosk** (`vosk-model-en-us-0.22-lgraph`) with **Kaldi**'s recognizer. Audio is captured live from the microphone via `sounddevice.RawInputStream` at 16 kHz, mono, 16-bit, with a small block size (1000 samples) chosen to minimize recognition latency.

Rather than using free-form/open-vocabulary recognition, the recognizer is constrained to a **custom grammar** — a fixed JSON list of allowed words built from three sources:

1. **Command words** — the keys of `ACTION_MAP` (e.g. `distribute`, `clearance`, `evening`, `selection`)
2. **Key words** — the second-stage words nested under each command in `ACTION_MAP`
3. **Trap words** — a large list of decoy/distractor words

Restricting recognition to this closed grammar (instead of a general language model) significantly improves accuracy and speed, since the engine only ever has to choose among a small, known set of words. `SetWords(False)` is also set, since word-level timing metadata isn't needed by this script's logic.

---

## 2. Command Structure (Two-Stage: Command → Key)

Commands are not single words — they require a **command word** followed by a **valid key word**, and each valid (command, key) pair maps to a unique serial ID:

| Command | Valid keys | Serial ID | Meaning |
|---|---|---|---|
| `distribute` | `scarlet` | 2 | Trigger payload reload |
| `distribute` | `blacky` | 3 | Rotate drone heading on the PRS landing dock |
| `distribute` | `fertilizer` | 8 | Reset PRS |
| `clearance` | `scarlet` | 7 | Drop the right-side servo section |
| `clearance` | `blacky` | 9 | Drop the left-side servo section |
| `clearance` | `fertilizer` | 6 | Reset all dropper servos to position 1 |
| `evening` | `scarlet` | 4 | Switch camera |
| `selection` | `vehicle` | 5 | Switch drone |

This two-stage structure means the same key word can map to different serial IDs depending on which command preceded it (e.g. `scarlet` is ID 2 under `distribute` but ID 7 under `clearance`), and a command alone does nothing until a valid key word follows it.

These meanings (rightmost column) are not defined anywhere in the code itself — `scarlet`, `blacky`, `fertilizer`, and `vehicle` are arbitrary voice-recognition-friendly code names chosen for grammar reliability. The functional mapping above reflects how each (command, key) pair is actually used in the connected system: `distribute` operates the Payload Reloader System (PRS) — reload, drone heading rotation on the landing dock, and PRS reset — while `clearance` operates the dropper servo mechanism — right-side drop, left-side drop, and resetting all dropper servos back to position 1. `evening` and `selection` are single-key commands used for camera switching and drone switching, respectively.

---

## 3. Trap Words (False-Positive Rejection)

A large list of **trap words** (`TRAP_WORDS_LIST`) is included in the grammar specifically to catch sound-alike words that the recognizer might otherwise misrecognize as an actual command or key. Per the inline comments in the source, traps are grouped by which real word they're designed to intercept — for example:

- Traps for `distribute`: words like *district, disturb, dispute, institute, street*, etc.
- Traps for `scarlet`: words like *scar, star, let, wallet, skillet, carl*, etc.
- Traps for `fertilizer`, `clearance`, `blacky`, `evening`, `selection`, `vehicle`: similarly grouped sound-alikes.
- General noise/common words (*the, a, is, okay, yes, stop*, etc.) and drone-related chatter (*battery, hover, launch, arm, disarm*, etc.) are also included so they don't get swallowed into a nearby real command/key by the recognizer.
- The Vosk `[unk]` (unknown) token is included as well.

Any recognized word matching `[unk]` or appearing in the trap set is logged as `[ERR]` and ignored — it does not affect the command buffer or get sent over serial.

---

## 4. Command Processing Logic (`process_data`)

For each chunk of recognized text, words are processed one at a time with the following state machine, backed by a single-slot buffer (`buffer["cmd"]`):

1. **Trap/unknown word** → logged as `[ERR]`, skipped.
2. **Command word recognized** (e.g. `distribute`) → stored into `buffer["cmd"]`, the buffer timestamp (`last_time`) is refreshed, and the valid key options for that command are printed.
3. **Key word recognized**:
   - If no command is currently buffered → the key is ignored (`[ERR] Ignored key '...' (No command armed)`).
   - If a command is buffered and the key is valid for it → the corresponding serial ID is looked up and sent over serial; the buffer and recognizer state are reset, and the audio queue is cleared so any leftover audio doesn't bleed into the next command.
   - If a command is buffered but the key is **not** valid for that specific command → the buffer is reset (the attempt fails; the user must say the command again).
4. **5-second timeout**: if a command is buffered but no valid key follows within `TIMEOUT_SEC = 5.0` seconds, the buffer is automatically cleared and the recognizer is reset, requiring the command to be repeated.

---

## 5. Serial Communication to ESP32

When a valid (command, key) pair completes, `send_serial()` builds a simple 3-byte packet and writes it to the serial port:

```
[ 0xAA (header) ] [ cmd_id ] [ checksum ]
```

The checksum is computed as `cmd_id & 0xFF` — since `cmd_id` is already a small integer (2–9), this checksum byte ends up identical to `cmd_id` itself rather than being an independently-computed checksum.

- Port: `/dev/ttyUSB1`, baud rate: `115200`
- `serial_conn.flush()` is called after every write to push the packet out immediately rather than waiting in the OS write buffer.
- A `write_timeout=0.1` is set on the serial connection so a full buffer doesn't cause the script to hang.

On the receiving end, the ESP32 is expected to interpret this serial packet and convert it into a PPM signal so the resulting command becomes readable by the RC transmitter — enabling voice-triggered switching of mechanisms such as the drone and camera selection.

---

## 6. Notable Code Detail

`send_error()` is defined to send a fixed `ERROR = 0` value over serial, but it is **never called** anywhere else in the script — it currently exists as dead code with no active call site.

---

## 7. Runtime Flow Summary

```
Microphone → sounddevice RawInputStream → audio queue
   → Vosk/Kaldi recognizer (constrained grammar)
      → text → word-by-word processing
         → trap/unknown words discarded
         → command word buffered (waiting for key)
         → valid key word completes the pair → serial packet sent
         → invalid key / timeout → buffer reset
   → ESP32 (serial) → PPM signal → RC transmitter
```