# Hurtigstart

Komplett guide fra opptak til inferens.

## Forutsetninger

```bash
uv pip install -e .
uv run python -m scripts.record --list-devices
```

## Steg 1: Datainnsamling

### 1a. Positive samples ("Kåre")

```bash
# Standard opptak
uv run python -m scripts.record --label kåre --count 50 --device 0

# Barnevennlig (nedtelling, morsomme prompts)
uv run python -m scripts.record --label kåre --count 25 --device 0 --child-friendly
```

**Variasjon er nøkkelen.** Ta opp i flere runder med ulike forhold:

| Runde | Fokus | Eksempel |
|-------|-------|----------|
| 1 | Tydelig, rett i mikrofon | "Kåre!" |
| 2 | Vanlig samtaletone | "Kåre, kom og spis" |
| 3 | Hvisket / stille | "kåre..." |
| 4 | Ropt fra avstand | "KÅRE!" fra et annet rom |
| 5 | Med bakgrunnsstøy | Si det mens TV-en er på |
| 6 | Ulike stemmer | La alle i husstanden ta opp |
| 7 | Barn | Bruk `--child-friendly` |

Mål: 200-300 opptak. Nåværende: 298.

### 1b. Negative samples (andre ord)

```bash
# Tilfeldige norske ord — scriptet velger ord, du sier dem
uv run python -m scripts.record_negative --count 50 --device 0

# Barnevennlig — enklere ord (tall, dyr, farger)
uv run python -m scripts.record_negative --count 25 --device 0 --child-friendly
```

**Viktige ord å inkludere:**
- Ord som ligner: "klare", "fare", "bære", "være", "tåre", "såre", "nåre"
- Vanlige ord hjemme: navn på familiemedlemmer, "kom", "gå", "hei"
- Andre wake words: "ok google", "hey siri", "alexa"
- Generelle norske ord: scriptet har en innebygd ordliste

Mål: 200-300 opptak. Nåværende: 177.

### 1c. Bakgrunnslyd (ambient)

```bash
# 2 minutter opptak, splittes automatisk til 1.5s klipp
uv run python -m scripts.record_ambient --device 0 --duration 120

# 5 minutter for mer variasjon
uv run python -m scripts.record_ambient --device 0 --duration 300
```

**Kjør flere runder med ulike lydkilder:**

| Runde | Kilde | Varighet |
|-------|-------|----------|
| 1 | TV (nyheter) | 2 min |
| 2 | TV (barne-TV) | 2 min |
| 3 | Radio / musikk | 2 min |
| 4 | Kjøkkenaktivitet (vannkran, koking) | 2 min |
| 5 | Samtale mellom voksne | 2 min |
| 6 | Stille rom (bare bakgrunnsstøy) | 1 min |
| 7 | Støvsuger / vaskemaskin | 1 min |

Klipp med for lite lyd filtreres automatisk bort.
Mål: 200-300 klipp. Nåværende: 163.

## Steg 2: Bygg alt i ett

```bash
./prepare.sh --target mac
```

Dette kjører:
1. Augmentering (3x per sample — pitch, speed, gain, noise)
2. MFCC-ekstraksjon (WAV → 40 MFCC-koeffisienter)
3. Kombinering (rå + augmentert)
4. Trening (Dense-nettverk, EarlyStopping)
5. Eksport til TFLite

### Alternativer

```bash
./prepare.sh --target all        # eksporter for mac + rpi + coral + vim3
./prepare.sh --quick             # hopp over augmentering (raskere)
FACTOR=5 ./prepare.sh            # 5x augmentering i stedet for 3x
```

## Steg 3: Test inferens

```bash
uv run python -m inference.run_coral \
    --model models/wakeword_mac.tflite \
    --device 0 \
    --confidence 0.85
```

Du vil se en live score-bar:
```
  [################    ] 0.82 (kare)
  >> Wake word detected! Confidence: 0.95
```

Si "Kåre" og se om den trigger. Juster `--confidence` etter behov:
- **0.70** — sensitiv (flere treff, men også flere false positives)
- **0.85** — balansert (anbefalt start)
- **0.95** — streng (få false positives, men kan misse noen)

## Steg 4: Deploy til andre enheter

### Eksporter

```bash
uv run python -m training.export_tflite --target coral  # → INT8 for Edge TPU
uv run python -m training.export_tflite --target vim3   # → INT8 for VIM3 NPU
uv run python -m training.export_tflite --target rpi    # → float32 for RPi CPU
```

### Coral Edge TPU (på Linux/RPi)

```bash
# Kompiler for Edge TPU
edgetpu_compiler models/wakeword_coral_int8.tflite

# Kjør inferens
python -m inference.run_coral \
    --model models/wakeword_coral_int8_edgetpu.tflite \
    --device 0
```

### VIM3 NPU

```bash
python -m inference.run_vim3 \
    --model models/wakeword_vim3_int8.tflite \
    --device 0
```

### Raspberry Pi (uten akselerator)

```bash
python -m inference.run_coral \
    --model models/wakeword_rpi.tflite \
    --device 0
```

## Iterativt forbedre

Modellen blir bedre med mer og bedre data. Etter testing:

1. **Identifiser svakheter** — hva trigger feilaktig? Hva misses?
2. **Samle målrettet data** — f.eks. vannkranlyder hvis det gir false positives
3. **Kjør pipeline** — `./prepare.sh --target mac`
4. **Test igjen** — sjekk om det ble bedre

## Feilsøking

| Problem | Løsning |
|---------|---------|
| For mange false positives | Mer bakgrunnsdata med den spesifikke lydkilden |
| Detekterer bare ropte "Kåre" | Flere opptak i vanlig samtaletone |
| Detekterer ikke "Kåre" i det hele tatt | Senk `--confidence`, sjekk riktig `--device` |
| Modellen lærer ikke (stuck ~33%) | Sjekk `from_logits=True` i `training/model.py` |
| Overfitting (train >> val) | Mer data, `FACTOR=5 ./prepare.sh` |
| Invalid number of channels | Bruk `--list-devices`, velg device med input |
| Buffer overflow under opptak | Normalt for macOS, påvirker ikke kvaliteten |
