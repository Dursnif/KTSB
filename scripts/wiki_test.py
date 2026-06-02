#!/usr/bin/env python3
"""
Manuell test av wiki_no Qdrant-databasen.
Stiller 25 spørsmål, viser topp-3 treff per spørsmål.
Kjøres: /kaare/venv/bin/python /kaare/scripts/wiki_test.py
"""

import httpx
from qdrant_client import QdrantClient

EMBED_URL   = "http://localhost:11446/api/embed"
EMBED_MODEL = "qwen3-embedding:8b"
QDRANT_URL  = "http://localhost:6333"
COLLECTION  = "wiki_no"
TOP_K       = 3

SPØRSMÅL = [
    # Norsk geografi og natur
    "Hva er Norges høyeste fjell?",
    "Hvilken elv er lengst i Norge?",
    "Hvor mange innbyggere har Oslo?",
    "Hva er Hardangervidda?",
    "Hvilken by er kjent som Norges oljehovedstad?",

    # Norsk historie
    "Når ble Norge selvstendig fra Sverige?",
    "Hvem var Norges første statsminister?",
    "Hva skjedde 9. april 1940?",
    "Hva er Eidsvoll kjent for?",
    "Hvem var Fridtjof Nansen?",

    # Vitenskap og teknologi
    "Hva er fotosyntese?",
    "Hvordan fungerer et svart hull?",
    "Hva er DNA?",
    "Hva er kvantemekanikk?",
    "Hvem oppfant telefonen?",

    # Kunst og kultur
    "Hvem malte Skrik?",
    "Hva er Ibsens mest kjente skuespill?",
    "Hvem var Edvard Grieg?",
    "Hva er Holbergprisen?",
    "Hva handler Kristin Lavransdatter om?",

    # Internasjonalt
    "Hva er FN?",
    "Hva var den kalde krigen?",
    "Hva er klimaendringer?",
    "Hva er den europeiske union?",
    "Hvem var Albert Einstein?",
]


def embed(tekst: str) -> list[float]:
    resp = httpx.post(
        EMBED_URL,
        json={"model": EMBED_MODEL, "input": tekst},
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # Ollama embed API returnerer {"embeddings": [[...]]}
    return data["embeddings"][0]


def søk(spørsmål: str, client: QdrantClient) -> list:
    vektor = embed(spørsmål)
    resultat = client.query_points(
        collection_name=COLLECTION,
        query=vektor,
        limit=TOP_K,
        with_payload=True,
    )
    return resultat.points


def main():
    client = QdrantClient(url=QDRANT_URL)

    print(f"\n{'='*70}")
    print(f"  Wiki-test: {len(SPØRSMÅL)} spørsmål mot {COLLECTION}")
    print(f"  Embedding: {EMBED_MODEL} | Topp {TOP_K} treff per spørsmål")
    print(f"{'='*70}\n")

    for nr, spørsmål in enumerate(SPØRSMÅL, 1):
        print(f"[{nr:02d}/{len(SPØRSMÅL)}] {spørsmål}")
        print("-" * 70)

        try:
            treff = søk(spørsmål, client)
            if not treff:
                print("  (ingen treff)\n")
                continue

            for i, t in enumerate(treff, 1):
                tittel  = t.payload.get("title", "ukjent")
                tekst   = t.payload.get("text", "")
                score   = t.score
                avsnitt = tekst[:300].replace("\n", " ")
                print(f"  #{i} [{score:.3f}] {tittel}")
                print(f"      {avsnitt}...")
                print()

        except Exception as e:
            print(f"  FEIL: {e}\n")

    print("=" * 70)
    print("Test ferdig.")


if __name__ == "__main__":
    main()
