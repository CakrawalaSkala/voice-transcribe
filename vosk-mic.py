import queue
import sys
import sounddevice as sd
import json
import os
from vosk import Model, KaldiRecognizer
import time
import serial

USE_MIC = True  
USE_SERIAL = True
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200

MODEL_PATH = "model/vosk-model-en-us-0.22-lgraph"
SAMPLE_RATE = 16000

AUDIO_BLOCKSIZE = 1000 

ERROR = 0 

ACTION_MAP = {
    "distribute": { "scarlet": 2, "blacky": 3, "fertilizer": 8 },
    "clearance": { "scarlet": 7, "blacky": 9, "fertilizer": 6 },
    "evening": { "scarlet": 4 },
    "selection": { "vehicle": 5 }
}

TRAP_WORDS_LIST = [
    "district", "disturb", "[unk]", "the", "is", "stop"

# # --- 1. TRAPS FOR "DISTRIBUTE" ---
# # Sound-alikes: "Dis-" start or "-bute" end
# "district", "disturb", "dispute", "display", "distinct",
# "attribute", "contribute", "statute",

# "this", "tree", "three", "street", "mute", "but",

# # Variations
# "distribution",

# # -tute endings
# "destitute", "institute", "constitute", "prostitute",

# # Strong "str"
# "street", "straight", "strut", "strip",

# # soft "sh"
# "dish", "fish", "wish",

# # "bute"
# "boot", "root", "shoot",

# # phrases
# "this three", "is three", "tree root",


# # --- 2. TRAPS FOR "SCARLET" ---
# # Sound-alikes: "skar-" or "-let"
#  "charlotte", "harlot", "varlet",

# "scar", "star", "car", "far", "bar",

# "let", "net", "set", "bet", "wet",

# "wallet", "bullet", "skillet", "toilet", "pilot",

# "garlic", "target", "market", "carpet",

# "skeleton", "scatter", "scholar",

# "starlit", "harlot", "varlet",

# "scared", "sacred", "scary", "score",

# # rhymes
# "skillet", "spill it", "kill it", "still it",

# # -erl sound
# "carl", "earl", "pearl", "girl",

# # rhythm traps
# "solid", "salad", "valid", "pallid",

# "cigarrete", "carrot", "carpet", "secret", "circuit",


# # --- 3. GENERAL NOISE / COMMON WORDS ---
# "the", "a", "an", "it", "is", "to", "in", "on", "at", "of",
# "and", "that", "this", "no", "yes", "stop", "go", "up",
# "okay", "hey", "hi", "hello", "right", "left",


# # --- 4. DRONE RELATED WORDS ---
# "battery", "voltage", "signal", "gps", "mode",
# "stable", "hover", "launch", "land", "arm", "disarm",
# "ready", "check", "clear", "prop", "motor",

# # numbers
# "one", "two", "three", "four", "five",
# "six", "seven", "eight", "nine", "ten",

# "error", "fire", "fail", "cancel", "reset",


# # --- 5. TRAPS FOR "FERTILIZER" ---
# "fertile", "fertility", "fertilize", "fertilized",

# "utilizer", "initializer", "stabilizer",
# "neutralizer", "analyzer", "finalizer", "catalyzer",

# "filter", "feature", "future",

# "fertile soil", "fertilizer spread",


# # --- 6. TRAPS FOR "CLEARANCE" ---
# "clear", "cleared", "clearing", "clears",

# "clarence", "clarens", "clarance", "clarity",
# "clearens", "clearancee",

# "appearance", "adherence", "reference",
# "conference", "difference",

# "insurance", "assurance", "tolerance",


# # --- 7. TRAPS FOR "BLACKY" ---
# "black", "blackie", "blaki", "blaccy",

# "blucky", "block", 
# "blank", "bleaky", "bleak",

# "blue", "blink", "blip", "bless", "blade", "blame",

# "blood", "blur",

# "lucky", "rocky", "jackie", "macky", "backy",

# "black key", "black tea", "blacky please",


# # --- 8. TRAPS FOR "EVENING" ---
# "even", "evenin", "evened", "evenly",

# "eveningg",

# "event", "events",

# "evan", "evans", "evin",

# "earning", "heaven",

# "good evening", "evening sir", "evening all", "evening mode",


# # --- 9. TRAPS FOR "SELECTION" ---
# "select", "selected", "selecting", "selections", "selectional",

# "election", "reflection", "collection",
# "direction", "inspection",
# "protection", "connection",

# "section", "sections",

# "session", "sessions",

# "seduction", "solution", "salvation",

# "selection mode", "select one",


# # --- 10. TRAPS FOR "VEHICLE" ---
# "vehical", "vehikle", "vehicl",

# "vertical", "critical", "physical",

# "article", "miracle", "medical", "digital",

# "vega", "vegas", "vigor",

# "visual", "vision",

# "vehicle mode", "vehicle switch",


# # --- UNKNOWN TOKEN ---
# "[unk]"
]

TRAP_WORDS = set(TRAP_WORDS_LIST)

command_words = list(ACTION_MAP.keys())
key_words = []
for cmd in ACTION_MAP:
    key_words.extend(list(ACTION_MAP[cmd].keys()))

grammar_list = command_words + key_words + list(TRAP_WORDS)
grammar_json = json.dumps(grammar_list)

if not os.path.exists(MODEL_PATH):
    print(f"Model not found at {MODEL_PATH}")
    sys.exit()

def send_error(ser):
    if USE_SERIAL:
        send_serial(ser, ERROR)

model = Model(MODEL_PATH)
rec = KaldiRecognizer(model, SAMPLE_RATE, grammar_json)

# Optimasi: Matikan SetWords karena tidak digunakan di logika Anda (.split() sudah cukup)
# Ini menghemat CPU dan mempercepat proses pengenalan
rec.SetWords(False) 

q = queue.Queue()

buffer = {"cmd": None, "key": None}
last_time = 0.0
TIMEOUT_SEC = 5.0

def audio_callback(indata, frames, time, status):
    if status: print(status, file=sys.stderr)
    q.put(bytes(indata))

def send_serial(serial_conn, cmd_id):
    if not USE_SERIAL or serial_conn is None:
        return
    try:
        header = 0xAA
        checksum = (cmd_id) & 0xFF
        packet = bytearray([header, cmd_id, checksum])
        
        serial_conn.write(packet)
        
        # OPTIMASI: flush() memastikan data langsung dikirim ke hardware serial
        serial_conn.flush() 
        
        print(f"-> [SERIAL TX] ID: {cmd_id} | HEX: {packet.hex()}")
    except Exception as e:
        print(f"Serial Error: {e}")

def process_data(data, active_ser):
    global last_time, buffer

    if buffer['cmd'] and (time.time() - last_time > TIMEOUT_SEC):
        print("\n[TIMEOUT] Buffer cleared.")
        buffer['cmd'] = None
        rec.Reset()

    detected_text = ""
    if rec.AcceptWaveform(data):
        res = json.loads(rec.Result())    
        detected_text = res.get("text", "")
    else:
        res = json.loads(rec.PartialResult())
        detected_text = res.get("partial", "")

    if not detected_text: return

    words = detected_text.split()
    
    for word in words:
        # Optimasi: Sekarang menggunakan SET (TRAP_WORDS) yang sudah dibuat di atas
        if word == "[unk]" or word in TRAP_WORDS:
            print(f"[ERR] {word}")
            continue

        if word in ACTION_MAP.keys():
            if buffer["cmd"] != word:
                buffer["cmd"] = word
                last_time = time.time()
                valid_options = list(ACTION_MAP[word].keys())
                print(f"[CMD] {word.upper()} -> WAITING FOR: {valid_options}")

        elif word in key_words:
            if buffer["cmd"] is None:
                print(f"[ERR] Ignored key '{word}' (No command armed)")
                return 

            valid_keys_map = ACTION_MAP[buffer["cmd"]] 

            if word in valid_keys_map:
                serial_id = valid_keys_map[word]
                print(f"[KEY] {word.upper()} ACCEPTED!")
                print(f"!!! EXECUTING: {buffer['cmd'].upper()} {word.upper()} (ID: {serial_id}) !!!")
                
                send_serial(active_ser, serial_id)
                
                buffer["cmd"] = None
                rec.Reset()
                if USE_MIC:
                    with q.mutex: q.queue.clear()
                return
            
            else:
                print(f"[ERR] Key '{word}' invalid for command '{buffer['cmd']}'")
                buffer["cmd"] = None
                rec.Reset()
                return

def run():
    ser = None
    if USE_SERIAL:
        try:
            # Optimasi: Tambah write_timeout agar write tidak menggantung jika buffer penuh
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1, write_timeout=0.1)
            time.sleep(2)
            print(f"Serial Connected: {ser.name}")
        except Exception as e:
            print(f"Serial Error: {e}")

    if USE_MIC:
        print(f"Listening... Valid Commands: {list(ACTION_MAP.keys())}")
        # Optimasi Utama: blocksize dari 4000 jadi 1000 (atau 800)
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=AUDIO_BLOCKSIZE, dtype='int16',
                               channels=1, callback=audio_callback):
            while True:
                data = q.get()
                process_data(data, ser)

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nExiting...")