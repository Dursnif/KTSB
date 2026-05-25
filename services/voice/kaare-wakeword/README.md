# Home-Kaare: Norsk Wake Word "Kåre"

Lokal wake word-detektor for "Kåre" med trening og inferens for:
- **Mac** (testing via TFLite + Metal GPU-trening)
- **Google Coral Edge TPU** (USB, via Raspberry Pi)
- **Khadas VIM3** (NPU-akselerert)
- **Raspberry Pi** (ren TFLite uten akselerator)

## Installasjon

Krever Python 3.10+ og [uv](https://docs.astral.sh/uv/):

```bash
uv pip install -e .
```

## Hurtigstart

```bash
# Finn mikrofon
uv run python -m scripts.record --list-devices

# Ta opp data
uv run python -m scripts.record --label kåre --count 50 --device 0
uv run python -m scripts.record_negative --count 50 --device 0
uv run python -m scripts.record_ambient --device 0 --duration 120

# Bygg og tren alt i ett
./prepare.sh --target mac
```

Se [docs/QUICKSTART.md](docs/QUICKSTART.md) for komplett guide.

## Datainnsamling

God data er viktigere enn modellarkitektur. Mål for et robust datasett:

### Positive samples ("Kåre")

```bash
# Standard opptak — si "Kåre" ved hvert pip
uv run python -m scripts.record --label kåre --count 50 --device 0

# Barnevennlig — nedtelling, morsomme prompts, hjelper barn holde fokus
uv run python -m scripts.record --label kåre --count 25 --device 0 --child-friendly
```

For gode positive samples, varier:
- **Hvem** som sier det — voksne, barn, ulike stemmer
- **Hvordan** — ropt, hvisket, vanlig samtale, syngende
- **Kontekst** — alene ("Kåre!"), i setning ("hei Kåre, kom hit"), som spørsmål ("Kåre?")
- **Avstand** — rett foran mikrofon, fra andre siden av rommet, fra et annet rom
- **Bakgrunn** — stille, med TV på, mens noen snakker

### Negative samples (andre ord)

```bash
# Tilfeldige norske ord — scriptet viser ordene, du sier dem
uv run python -m scripts.record_negative --count 50 --device 0

# Barnevennlig — enklere ord (tall, dyr, farger)
uv run python -m scripts.record_negative --count 25 --device 0 --child-friendly
```

Negative samples lærer modellen å _ikke_ trigge. Viktige ord å inkludere:
- **Lignende ord** — "klare", "fare", "bære", "være", "tåre", "såre"
- **Vanlige norske ord** — "hallo", "takk", "ja", "nei", "kom", "gå"
- **Navn** — andre navn som brukes hjemme
- **Kommandoer** — "ok google", "hey siri", "alexa" (hindrer krysstrigger)

### Bakgrunnslyd (ambient)

```bash
# Kontinuerlig opptak — splittes automatisk til 1.5s klipp
uv run python -m scripts.record_ambient --device 0 --duration 120

# Lengre sesjon (5 min)
uv run python -m scripts.record_ambient --device 0 --duration 300
```

Bakgrunnsdata hindrer false positives. Varier kildene:
- **TV/radio** — nyheter, barne-TV, film, musikk
- **Kjøkken** — vannkran, oppvaskmaskin, kaffetrakter, matlaging
- **Husholdning** — støvsuger, vaskemaskin, dører, skritt
- **Samtaler** — folk som prater uten å si "Kåre"
- **Stillhet** — tomt rom med bare bakgrunnsstøy (vifte, kjøleskap)
- **Utendørs** — vind, trafikk, fugler (hvis relevant)

### Datasett-balanse

Mål for et balansert datasett:

| Klasse | Minimum | Anbefalt | Nåværende |
|--------|---------|----------|-----------|
| Positive (kåre) | 100 | 300+ | 298 |
| Negative (andre ord) | 100 | 300+ | 177 |
| Bakgrunn (ambient) | 100 | 300+ | 163 |

Med augmentering (3x) gir 300 rå samples ~1200 treningssamples per klasse.

## Pipeline

### Alt-i-ett

```bash
./prepare.sh                     # augment + MFCC + train + export (mac)
./prepare.sh --target coral      # eksporter for Coral Edge TPU
./prepare.sh --target all        # eksporter for alle targets
./prepare.sh --quick             # hopp over augmentering
FACTOR=5 ./prepare.sh            # 5x augmentering
```

### Steg for steg

```bash
# 1. Augmenter (lager varianter med pitch/speed/gain/noise)
uv run python -m scripts.augment --source data/positive/   --output data/augmented/            --factor 3
uv run python -m scripts.augment --source data/negative/   --output data/augmented_negative/   --factor 3
uv run python -m scripts.augment --source data/background/ --output data/augmented_background/ --factor 3

# 2. Konverter WAV → MFCC
uv run python -m scripts.preprocess --input data/positive/   --output data/mfcc_positive_raw.npy
uv run python -m scripts.preprocess --input data/negative/   --output data/mfcc_negative_raw.npy
uv run python -m scripts.preprocess --input data/background/ --output data/mfcc_background_raw.npy

# 3. Tren
uv run python -m training.train \
    --positive data/mfcc_positive_all.npy \
    --negative data/mfcc_negative_all.npy \
    --background data/mfcc_background_all.npy

# 4. Eksporter
uv run python -m training.export_tflite --target mac
```

### Eksport-targets

```bash
uv run python -m training.export_tflite --target mac    # → wakeword_mac.tflite (float32)
uv run python -m training.export_tflite --target rpi    # → wakeword_rpi.tflite (float32)
uv run python -m training.export_tflite --target coral  # → wakeword_coral_int8.tflite (INT8)
uv run python -m training.export_tflite --target vim3   # → wakeword_vim3_int8.tflite (INT8)
uv run python -m training.export_tflite --target all    # alle fire
```

Coral og VIM3 krever ett ekstra steg etter eksport:
```bash
# Coral (på Linux): edgetpu_compiler models/wakeword_coral_int8.tflite
# VIM3: Konverter til .nb med Khadas SDK, eller bruk TFLite direkte
```

## Inferens

```bash
# List mikrofoner
uv run python -m inference.run_coral --list-devices

# Mac (float32)
uv run python -m inference.run_coral --model models/wakeword_mac.tflite --device 0

# RPi + Coral (Edge TPU)
python -m inference.run_coral --model models/wakeword_coral_int8_edgetpu.tflite --device 0

# VIM3
python -m inference.run_vim3 --model models/wakeword_vim3_int8.tflite --device 0

# Juster confidence-terskel (default 0.85)
uv run python -m inference.run_coral --model models/wakeword_mac.tflite --device 0 --confidence 0.90
```

Live score-bar vises under inferens:
```
  [################    ] 0.82 (kare)
  >> Wake word detected! Confidence: 0.95
```

## Prosjektstruktur

```
home-kaare/
├── scripts/                    # Datainnsamling og preprocessing
│   ├── audio_config.py         # Delte konstanter (16kHz, 1.5s, 40 MFCC)
│   ├── record.py               # Positive opptak (--child-friendly, --count, --device)
│   ├── record_negative.py      # Negative opptak (tilfeldige norske ord)
│   ├── record_ambient.py       # Bulk bakgrunnsopptak (--duration, auto-split)
│   ├── augment.py              # Augmentering (--factor, pitch/speed/gain/noise)
│   ├── preprocess.py           # WAV → MFCC (.npy)
│   ├── prepare.py              # Full pipeline som Python-modul
│   ├── generate_tts.py         # Syntetiske samples via TTS
│   └── analyze_data.py         # Datasett-analyse
├── training/                   # Modelltrening
│   ├── model.py                # Dense-nettverk med L2-regularisering
│   ├── dataset.py              # Dataset-loader med normalisering og split
│   ├── train.py                # Treningsloop (EarlyStopping, ReduceLR)
│   └── export_tflite.py        # Eksport (--target mac/rpi/coral/vim3/all)
├── inference/                  # Sanntids-inferens
│   ├── common.py               # MFCC-ekstraksjon, debounce, device-listing
│   ├── run_coral.py            # Mac/Coral/RPi-inferens med live score-bar
│   └── run_vim3.py             # VIM3 NPU-inferens (KSNN / TFLite)
├── prepare.sh                  # Alt-i-ett pipeline-script
├── data/                       # Audio-data (ikke i git)
│   ├── positive/               # "Kåre"-opptak (.wav)
│   ├── negative/               # Andre ord (.wav)
│   ├── background/             # Bakgrunnslyd (.wav)
│   ├── augmented*/             # Augmenterte varianter (.wav)
│   └── mfcc_*_all.npy          # Kombinerte MFCC-features
├── models/                     # Trenede modeller
│   ├── wakeword_model.keras    # Keras-modell
│   ├── wakeword_mac.tflite     # Mac (float32)
│   ├── wakeword_rpi.tflite     # RPi (float32)
│   ├── wakeword_coral_int8.tflite  # Coral input (INT8)
│   └── wakeword_vim3_int8.tflite   # VIM3 input (INT8)
└── docs/                       # Dokumentasjon
```

## Teknisk oversikt

| Parameter | Verdi |
|-----------|-------|
| Sample rate | 16 kHz |
| Klipp-lengde | 1.5 sekunder (24000 samples) |
| MFCC-koeffisienter | 40 |
| Tidsrammer | ~151 per klipp |
| FFT-vindu | 1024 |
| Hop-lengde | 160 (10ms) |
| Vinduslengde | 400 (25ms) |
| Modell | Flatten → Dense(64, ReLU, L2) → Dense(32, ReLU, L2) → Dense(3) |
| Loss | SparseCategoricalCrossentropy (from_logits=True) |
| Klasser | 0=kåre, 1=negativt, 2=bakgrunn |
| Kvantisering | Post-training INT8 (for Edge TPU / VIM3) |
| GPU-trening | Apple Metal (M1/M2/M3) via tensorflow-metal |

## Feilsøking

| Problem | Løsning |
|---------|---------|
| For mange false positives | Mer bakgrunnsdata, hev `--confidence` |
| Detekterer ikke "Kåre" | Flere positive i samtaletone, senk `--confidence` |
| Detekterer bare ropte "Kåre" | Ta opp positive i vanlig samtalevolum |
| Modellen lærer ikke (stuck ~33%) | Sjekk `from_logits=True` i loss |
| Overfitting (train >> val) | Mer data, mer augmentering, enklere modell |
| Invalid number of channels | Bruk `--list-devices` og velg en input-enhet |
| Buffer overflow under opptak | Normalt for macOS, påvirker ikke kvaliteten |

## Lisens

Apache 2.0
