"""Interactive negative word recording with random word prompts.

Shows random Norwegian words, user presses SPACE to record.
Child-friendly mode available (simple words, easy to read).

Usage:
    python -m scripts.record_negative --count 100 --device 0 --child-friendly
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from scripts.audio_config import CLIP_SAMPLES, SAMPLE_RATE
from scripts.record import list_devices


# Child-friendly simple Norwegian words (easy to pronounce)
CHILD_FRIENDLY_WORDS = [
    "Hei", "Nei", "Ja", "Takk", "Hva", "Hvor", "Når", "Hvem",
    "Gå", "Kom", "Se", "Vær", "Vær så god", "Gjør det",
    "En", "To", "Tre", "Fire", "Fem", "Seks", "Syv", "Åtte",
    "Ni", "Ti", "Hundre", "Tusen",
    "Vann", "Brød", "Egg", "Melk", "Bolle", "Kake", "Kjeks",
    "Hund", "Katt", "Hest", "Fugl", "Fisk", "Ku", "Sau", "Geit",
    "Hus", "Bil", "Båt", "Fly", "Sykkel", "Buss", "Tog",
    "Bok", "Kål", "Bær", "Eple", "Pære", "Banan", "Appelsin",
    "Rød", "Blå", "Grønn", "Gul", "Sort", "Hvit",
    "Sol", "Måne", "Stjerne", "Sky", "Regn", "Snø", "Vind",
    "Vinter", "Sommer", "Høst", "Vår",
    "Dag", "Natt", "Morgen", "Kveld",
    "Mamma", "Pappa", "Barn", "Bestemor", "Bestefar",
    "Tante", "Onkel", "Kusine", "Søsken", "Niese", "Nevø",
    "Lekse", "Skole", "Lærer", "Venn", "Leke",
    "Ball", "Dukke", "Bil", "Lego", "Tegning",
    "Hopp", "Løp", "Svøm", "Sitt", "Ligg", "Sov",
    "Sulten", "Ørst", "Søvnig", "Glad", "Trist",
    "Vær så snill", "Unnskyld", "Takk sist",
    "God natt", "God morgen", "Ha det bra", "Vi sees",
    "Hei på deg", "God dag",
]

# All negative Norwegian words (more variety)
ALL_WORDS = [
    *CHILD_FRIENDLY_WORDS,
    "Hei", "Nei", "Ja", "Takk", "Vær så god",
    "Hva", "Hvorfor", "Hvordan", "Når", "Hvem", "Hva heter du",
    "Kom hit", "Gå bort", "Stopp", "Vent", "Gå",
    "Vær så snill", "Unnskyld", "Beklager",
    "Hører du meg", "Ser du meg", "Forstår du",
    "God morgen", "God dag", "God kveld", "God natt",
    "Ha det bra", "Vi sees", "Farvel",
    "Hjelp meg", "Redd meg", "Vent på meg",
    "Er du klar", "Er du der", "Kommer du",
    "Vær så vennlig", "Tusen takk", "Jeg er ferdig",
    "Ikke i dag", "Kanskje senere", "En annen gang",
    "Hvordan går det", "Hva skjer", "Hva er dette",
    "Hvor er den", "Hvor er du", "Hvor skal vi",
    "Hvem er det", "Hvem er du", "Hvem er de",
    "Dette er bra", "Dette er dårlig", "Jeg vet ikke",
    "Jeg forstår ikke", "Jeg vet", "Jeg trodde",
    "La oss gå", "La oss se", "La oss leke",
    "Vær forsiktig", "Vær snill", "Vær rolig",
    "Det er greit", "Det går fint", "Ingen problem",
    "Se der", "Se her", "Hør dette", "Lytt til meg",
    "Si det igjen", "Gjør det igjen", "Prøv igjen",
    "Ikke nå", "Ikke i dag", "Kanskje neste uke",
    "Først", "Så", "Etterpå", "Til slutt",
    "Nå", "Da", "Senere", "Før",
    "I dag", "I morgen", "I går", "I kveld",
    "I dette rommet", "Der borte", "Her inne",
    "På bordet", "På gulvet", "I taket", "Under sengen",
    "I huset", "I hagen", "På gaten",
    "Vær så grei", "Takk for hjelpen", "Du er snill",
    "Glem ikke", "Husk å", "Ikke glem",
    "Er du ferdig", "Har du gjort det", "Er det klart",
    "Ta en pause", "Hvil litt", "Vær så god",
    "En liten stund", "Noen minutter", "Et øyeblikk",
    "Så mye", "Så lite", "Noe mer", "Noe mindre",
    "Bare litt", "Bare nok", "Bare dette",
    "Ikke noe mer", "Ingenting igjen", "Det var alt",
    "Er det alt", "Er det nok", "Er det ferdig",
    "Stopp nå", "Avslutt her", "Det er greit",
    "Si det", "Hva sa du", "Gjenta det",
    "Vær så greit", "Det går bra", "Ingen bekymring",
    "Jeg tror det", "Jeg mener", "Jeg synes",
    "Det er viktig", "Det er nødvendig", "Vi må gjøre det",
    "La meg se", "La meg prøve", "La meg tenke",
    "Du kan gjøre det", "Du klarer det", "Du er god",
    "Bra jobbet", "Godt gjort", "Utmerket",
    "Fantastisk", "Super", "Veldig bra",
    "Enkelt nok", "Ikke så vanskelig", "Det er lett",
    "For vanskelig", "For komplisert", "Jeg klarer ikke",
    "Det er umulig", "Det er for vanskelig",
    "La være", "Ikke gjør det", "Glem det",
    "Hva betyr det", "Hva mener du", "Hva sier du",
    "Jeg forstår", "Jeg vet", "Jeg kan",
    "Lær meg", "Vis meg", "Fortell meg",
    "Hvordan fungerer det", "Hvordan gjør man det",
    "Trinn for trinn", "Del for del", "Litt etter litt",
    "Sånn", "Bort fra", "Mot", "Over", "Under",
    "Igjennom", "Rundt", "Forbi", "Bak",
    "Opp", "Ned", "Inne", "Ute",
    "Framme", "Bakover", "Til høyre", "Til venstre",
    "Rett opp", "Rett ned", "Hit", "Dit",
    "Her og der", "Overalt", "Ingen steder", "Alle steder",
    "Noen steder", "Mange steder", "Få steder",
    "Nærmere", "Lengre bort", "Ikke så nær",
    "Kom hit", "Gå dit", "Vær så vennlig å komme",
    "Velkommen", "Velkommen tilbake", "Ha det bra",
    "Farvel og lykke til", "Vi sees snart", "Ha en fin dag",
]


def record_word(
    word: str,
    output_dir: Path,
    device: int = 0,
    prefix: str = "neg",
) -> bool:
    """Record a single word - press ENTER to start.

    Returns:
        True if recording was successful
    """
    try:
        # Prepare output path — find highest existing number to avoid overwriting
        max_idx = -1
        for f in output_dir.glob("*.wav"):
            m = re.search(r"(\d+)", f.stem)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        output_path = output_dir / f"{prefix}_{max_idx + 1:04d}.wav"

        print(f"\n{'='*60}")
        print(f"Si dette ordet:")
        print(f"  >>> {word.upper()} <<<")
        print(f"{'='*60}")
        print("Trykk ENTER for å starte opptak...")

        # Wait for ENTER before opening the stream
        try:
            input("")
        except EOFError:
            print("\nHoppet over")
            return False

        # Start stream RIGHT before recording to avoid buffer overflow
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            device=device,
            dtype=np.float32,
            blocksize=1024,
        )
        stream.start()

        print("Tar opp...")
        data, overflow = stream.read(CLIP_SAMPLES)

        stream.stop()
        stream.close()

        # Check audio level
        peak = np.max(np.abs(data))
        if peak < 0.01:
            print(f"Advarsel: For stille! Peak: {peak:.4f}")
            print("Prøv igjen")
            return False

        # Save
        sf.write(str(output_path), data, SAMPLE_RATE, subtype="PCM_16")
        print(f"✓ Lagret: {output_path.name} (Peak: {peak:.4f})")

        return True

    except Exception as exc:
        print(f"Feil under opptak: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Record negative words with random prompts")
    parser.add_argument(
        "--count",
        type=int,
        default=200,
        help="Number of recordings (default: 200 - balansert med positive)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="Audio input device index (use --list-devices to see options)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--child-friendly",
        action="store_true",
        help="Use child-friendly simple words only",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/negative/"),
        help="Output directory (default: data/negative/)",
    )
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    # Setup output directory
    args.output.mkdir(parents=True, exist_ok=True)

    # Choose word list
    if args.child_friendly:
        word_list = CHILD_FRIENDLY_WORDS
        print(f"Child-friendly mode: {len(word_list)} ord")
    else:
        word_list = ALL_WORDS
        print(f"Full ordliste: {len(word_list)} ord")

    # Shuffle words
    random.shuffle(word_list)
    random.seed(42)

    # Record
    successful = 0
    skipped = 0

    print("\n" + "="*60)
    print(f"Starter negative opptak - {args.count} opptak")
    print(f"Mål: {args.output}/")
    print("="*60 + "\n")

    try:
        for i in range(args.count):
            # Pick random word
            word = random.choice(word_list)

            if record_word(word, args.output, args.device, prefix="neg"):
                successful += 1
            else:
                skipped += 1

            # Progress
            remaining = args.count - (i + 1)
            if (i + 1) % 10 == 0:
                print(f"\nProgres: {i + 1}/{args.count} ferdig ({successful} vellykket, {skipped} hoppet over)")

    except KeyboardInterrupt:
        print("\n\nAvbrutt av bruker")
    finally:
        print("\n" + "="*60)
        print(f"Opptak fullført!")
        print(f"  Vellykket: {successful}")
        print(f"  Hoppet over: {skipped}")
        print(f"  Totalt: {successful + skipped}")
        print(f"  Output: {args.output}/")
        print("="*60)


if __name__ == "__main__":
    main()
