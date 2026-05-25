# Dokumentasjon

| Dokument | Beskrivelse |
|----------|-------------|
| [QUICKSTART.md](QUICKSTART.md) | Komplett steg-for-steg fra opptak til inferens |
| [../README.md](../README.md) | Prosjektoversikt, datainnsamlingstips, teknisk info |

## Pipeline-oversikt

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Opptak     │     │ Augmentering │     │ Preprocessing│
│              │────>│              │────>│  WAV → MFCC  │
│ record.py    │     │ augment.py   │     │ preprocess.py│
│ record_neg.  │     │ pitch/speed/ │     │ 40 MFCC,     │
│ record_amb.  │     │ gain/noise   │     │ ~151 frames  │
└─────────────┘     └──────────────┘     └──────┬───────┘
                                                 │
                                                 v
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Inferens    │     │   Eksport    │     │   Trening    │
│              │<────│              │<────│              │
│ run_coral.py │     │ export_tfl.  │     │ train.py     │
│ Mikrofon +   │     │ --target:    │     │ Dense-nett   │
│ Live scoring │     │ mac/rpi/     │     │ L2 + dropout │
│              │     │ coral/vim3   │     │ Metal GPU    │
└─────────────┘     └──────────────┘     └──────────────┘
```

## Script-referanse

### Datainnsamling (`scripts/`)

| Script | Flagg | Beskrivelse |
|--------|-------|-------------|
| `record.py` | `--label kåre --count N --device D` | Positive opptak |
| | `--child-friendly` | Nedtelling + morsomme prompts for barn |
| | `--list-devices` | Vis tilgjengelige mikrofoner |
| `record_negative.py` | `--count N --device D` | Negative med tilfeldige norske ord |
| | `--child-friendly` | Enklere ord (tall, dyr, farger) |
| `record_ambient.py` | `--device D --duration S` | Kontinuerlig opptak, auto-split til 1.5s |
| `augment.py` | `--source DIR --output DIR --factor N` | Pitch, speed, gain, noise-varianter |
| `preprocess.py` | `--input DIR --output FILE.npy` | WAV → MFCC-features |

### Trening (`training/`)

| Script | Flagg | Beskrivelse |
|--------|-------|-------------|
| `train.py` | `--positive/--negative/--background .npy` | Tren med EarlyStopping + ReduceLR |
| | `--epochs N --batch-size N` | Juster treningsparametre |
| `export_tflite.py` | `--target mac\|rpi\|coral\|vim3\|all` | Eksporter til TFLite for target |

### Inferens (`inference/`)

| Script | Flagg | Beskrivelse |
|--------|-------|-------------|
| `run_coral.py` | `--model .tflite --device D` | Mac/Coral/RPi med live score-bar |
| | `--confidence F` | Juster terskel (default 0.85) |
| `run_vim3.py` | `--model .tflite --device D` | VIM3 NPU (KSNN eller TFLite) |

### Pipeline (`prepare.sh`)

| Flagg | Beskrivelse |
|-------|-------------|
| (ingen) | Full pipeline: augment → MFCC → train → export (mac) |
| `--target T` | Eksporter for target (mac/rpi/coral/vim3/all) |
| `--quick` | Hopp over augmentering |
| `FACTOR=N` | Augmenteringsfaktor (default 3) |

## Konfigurasjon

All delt audiokonfigurasjon ligger i `scripts/audio_config.py`:
- Sample rate, klipp-lengde, MFCC-parametre
- Label-mapping (0=kåre, 1=negativt, 2=bakgrunn)
- Inference-terskler og debounce

## Eksport-modeller

| Target | Fil | Format | Bruk |
|--------|-----|--------|------|
| mac | `wakeword_mac.tflite` | float32 | Lokal testing |
| rpi | `wakeword_rpi.tflite` | float32 | RPi uten akselerator |
| coral | `wakeword_coral_int8.tflite` | INT8 | Input til `edgetpu_compiler` |
| vim3 | `wakeword_vim3_int8.tflite` | INT8 | VIM3 direkte eller KSNN |
