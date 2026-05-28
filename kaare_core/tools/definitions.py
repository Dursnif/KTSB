# /kaare/kaare_core/tools/definitions.py
"""
Kåre tool definitions (Ollama /api/chat format).

Reduced from 61 to 25 tools (merged 2026-05-14).
Action-parameter pattern: one tool per domain, action enum selects the operation.
"""

# Library tool without the online action — used for child and teen roles.
LIBRARY_NO_ONLINE = {
    "type": "function",
    "function": {
        "name": "library",
        "description": (
            "Spør Frøken Library — lokal wiki-database. Tre operasjoner: "
            "action='søk': semantisk søk i lokal wiki (1M+ artikler) for faktaspørsmål, "
            "definisjoner, historiske data — ting som ikke endrer seg. Svarer alltid med kilde. "
            "action='hent_artikkel': hent hele Wikipedia-artikkelen etter et søk (krever 'title'). "
            "action='hent_url': hent og oppsummer innholdet fra en spesifikk nettside (krever 'url'). "
            "Kun tillatte domener. Bruk når brukeren oppgir en konkret URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["søk", "hent_artikkel", "hent_url"],
                    "description": (
                        "'søk' = semantisk wiki-søk (krever 'spørsmål'). "
                        "'hent_artikkel' = hent hel artikkel (krever 'title'). "
                        "'hent_url' = hent og oppsummer en konkret nettside (krever 'url')."
                    ),
                },
                "spørsmål": {
                    "type": "string",
                    "description": "Spørsmålet. Kun ved action='søk'.",
                },
                "title": {
                    "type": "string",
                    "description": "Artikkeltittel nøyaktig som i wiki-søkeresultatet. Kun ved action='hent_artikkel'.",
                },
                "url": {
                    "type": "string",
                    "description": "Fullstendig URL (https://...). Kun ved action='hent_url'.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maks tegn å returnere ved hent_artikkel. Standard 8000.",
                },
            },
            "required": ["action"],
        },
    },
}

KAARE_TOOLS = [
    # ─────────────────────────────────────────────
    # SMART HOME / HOME ASSISTANT
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "les_ha",
            "description": (
                "Les informasjon fra Home Assistant. Tre operasjoner via 'action': "
                "action='rom_liste': hent alle romnavn (start alltid her hvis du ikke vet romnavnet). "
                "action='rom_enheter': hent enheter i ett rom med entity_id og type (krever 'rom'). "
                "action='status': les nåværende tilstand på én enhet (krever 'entity_id'). "
                "Fremgangsmåte: (1) rom_liste uten rom, (2) rom_enheter med rom-navn, "
                "(3) status med entity_id. Aldri gjett entity_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["rom_liste", "rom_enheter", "status"],
                        "description": (
                            "'rom_liste' = alle romnavn (ingen andre parametere). "
                            "'rom_enheter' = enheter i ett rom (krever 'rom'). "
                            "'status' = nåværende verdi på én enhet (krever 'entity_id')."
                        ),
                    },
                    "rom": {
                        "type": "string",
                        "description": "Romnavn, f.eks. 'verksted', 'stue'. Kun ved action='rom_enheter'.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "HA entity_id, f.eks. 'sensor.netatmo_utendors_modul_temperatur'. Kun ved action='status'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "styr_enhet",
            "description": (
                "Styrer en enhet i smarthuset via Home Assistant. "
                "Bruk les_ha(action='rom_liste') først hvis du ikke kjenner entity_id. "
                "For lys: set_level + brightness_pct, set_color_temp + color_temp_kelvin, "
                "set_color + rgb_color."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "HA entity_id, f.eks. 'light.taklys_stue'.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["turn_on", "turn_off", "toggle", "set_level", "set_color_temp", "set_color"],
                        "description": "Handlingen. set_level=lysstyrke, set_color_temp=fargetemperatur, set_color=RGB.",
                    },
                    "brightness_pct": {
                        "type": "integer",
                        "description": "Lysstyrke 0–100%. Brukes med action=set_level.",
                    },
                    "color_temp_kelvin": {
                        "type": "integer",
                        "description": "Fargetemperatur i Kelvin (2200=stearinlys, 2700=varm, 4000=nøytral, 6500=dagslys). Brukes med action=set_color_temp.",
                    },
                    "rgb_color": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "RGB-farge [r,g,b] 0–255. Brukes med action=set_color.",
                    },
                },
                "required": ["entity_id", "action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # INFORMATION RETRIEVAL
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "søk_nett",
            "description": (
                "Søk på nettet. Bruk for fakta, nyheter, lover, oppskrifter, "
                "teknisk dokumentasjon eller annet som krever oppdatert informasjon fra nettet. "
                "Ikke for smarthus-styring, ting du allerede vet, eller vær (bruk hent_yr_varsel). "
                "VIKTIG — ærlighet ved feil: Hvis søket returnerer 'Fant ingen resultater' "
                "eller ingen treff fra godkjente kilder: si det ærlig til brukeren med én setning. "
                "Ikke prøv igjen automatisk med andre søkeord. "
                "Tilby i stedet mechanic(action='søk') for dypere undersøkelse — "
                "men kun hvis brukeren eksplisitt ønsker det."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Konkret, presist søk — f.eks. 'strafferamme hærverk Norge'. Norsk eller engelsk.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hent_yr_varsel",
            "description": (
                "Henter værvarsel direkte fra met.no. Bruk for ALT om vær, temperatur, nedbør, vind. "
                "Uten sted: lokalt vær. Med sted: vær for det stedet. "
                "ALDRI søk_nett for vær — bruk alltid dette."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sted": {
                        "type": "string",
                        "description": "Stedsnavn i Norge, f.eks. 'Oslo'. Utelat for lokalt vær.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "library",
            "description": (
                "Spør Frøken Library — lokal wiki-database og online LLM. Fire operasjoner: "
                "action='søk': semantisk søk i lokal wiki (1M+ artikler) for faktaspørsmål, "
                "definisjoner, historiske data — ting som ikke endrer seg. Svarer alltid med kilde. "
                "action='hent_artikkel': hent hele Wikipedia-artikkelen etter et søk (krever 'title'). "
                "action='hent_url': hent og oppsummer innholdet fra en spesifikk nettside (krever 'url'). "
                "Kun tillatte domener. Bruk når brukeren oppgir en konkret URL. "
                "action='online': spør stor online LLM for bred kunnskap, kompleks resonnering "
                "eller second opinion. Ikke for vær/nyheter (bruk søk_nett). "
                "VIKTIG — ærlighet ved feil: Hvis et søk returnerer ingen svar eller feil: "
                "si det klart til brukeren. Ikke prøv igjen med andre varianter automatisk. "
                "Tilby mechanic(action='søk') som alternativ — kun om brukeren ønsker det."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["søk", "hent_artikkel", "hent_url", "online"],
                        "description": (
                            "'søk' = semantisk wiki-søk (krever 'spørsmål'). "
                            "'hent_artikkel' = hent hel artikkel etter søk (krever 'title'). "
                            "'hent_url' = hent og oppsummer en konkret nettside (krever 'url'). "
                            "'online' = stor online LLM for dyp resonnering (krever 'spørsmål')."
                        ),
                    },
                    "spørsmål": {
                        "type": "string",
                        "description": "Spørsmålet. Konkret og fullstendig. Brukes ved action='søk' og 'online'.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Artikkeltittel nøyaktig som i wiki-søkeresultatet. Brukes ved action='hent_artikkel'.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Fullstendig URL (https://...). Kun ved action='hent_url'.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maks tegn å returnere ved hent_artikkel. Standard 8000.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # TIME AND TIMERS
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "timer",
            "description": (
                "Klokkeslett og timere. Fire operasjoner: "
                "action='klokke': returnerer nåværende klokkeslett og dato. "
                "action='sett': sett en timer — du skriver prompten du vil vekkes med. "
                "Bruk 'at_time' for klokkeslett/dato ('07:30', 'fredag 08:00') eller "
                "'in_seconds' for enkel forsinkelse. Legg til 'repeat' for gjentakelse. "
                "action='avbryt': avbryt en timer (krever 'timer_id' fra action='liste'). "
                "action='liste': se alle aktive timere med ID og gjenværende tid."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["klokke", "sett", "avbryt", "liste"],
                        "description": (
                            "'klokke' = nåværende tid og dato (ingen andre parametere). "
                            "'sett' = sett ny timer (krever 'prompt', én av 'at_time'/'in_seconds'). "
                            "'avbryt' = avbryt timer (krever 'timer_id'). "
                            "'liste' = vis alle aktive timere (ingen andre parametere)."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Meldingen du vil vekkes med — skriv til deg selv. Kun ved action='sett'.",
                    },
                    "at_time": {
                        "type": "string",
                        "description": "Tidspunkt: '07:30', 'fredag 08:00', '2026-05-01 09:00'. Kun ved action='sett'.",
                    },
                    "in_seconds": {
                        "type": "integer",
                        "description": "Forsinkelse i sekunder (minimum 5). Kun ved action='sett', kun hvis at_time ikke er satt.",
                    },
                    "repeat": {
                        "type": "string",
                        "enum": ["hourly", "daily", "weekdays", "weekend", "weekly"],
                        "description": "Gjentakelse: 'daily', 'weekdays', 'weekend', 'weekly', 'hourly'. Kun ved action='sett'.",
                    },
                    "notify": {
                        "type": "boolean",
                        "description": "Om bruker skal varsles. Standard: true. Kun ved action='sett'.",
                    },
                    "timer_id": {
                        "type": "string",
                        "description": "Timer-ID fra action='liste'. Kun ved action='avbryt'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # MEETINGS
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "les_møte",
            "description": (
                "Les innholdet fra et av Kåres nattlige møter. "
                "type='refleksjon': Kåres egne refleksjonsmøter. "
                "type='utvikling': tekniske utviklingsmøter (Kåre + Mechanic). "
                "Uten dato: siste møte. Med dato (YYYY-MM-DD): møtet fra den datoen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["refleksjon", "utvikling"],
                        "description": "'refleksjon' = nattlig refleksjonsmøte. 'utvikling' = teknisk møte.",
                    },
                    "dato": {
                        "type": "string",
                        "description": "Dato YYYY-MM-DD. Utelat for siste møte.",
                    },
                },
                "required": ["type"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # MEMORY
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "minne",
            "description": (
                "Kåres hukommelse — fire operasjoner: "
                "action='søk': semantisk søk i langtidsminnet etter tidligere interaksjoner og mønstre. "
                "Bruk nøkkelord, f.eks. 'benkelys verksted'. "
                "action='hent_ubekreftede': hent interaksjoner som brukeren ikke har bekreftet ennå "
                "(hent 5–10, presenter, spør om de stemmer). "
                "action='bekreft': merk interaksjoner som verified/denied/test (krever 'ids' og 'dom'). "
                "action='hent_stm': hent eldre STM-snapshot — uten dato: vis tilgjengelige datoer; "
                "med dato='YYYY-MM-DD': hent dialog og handlinger fra den dagen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["søk", "hent_ubekreftede", "bekreft", "hent_stm"],
                        "description": (
                            "'søk' = semantisk søk i LTM (krever 'spørsmål'). "
                            "'hent_ubekreftede' = list ubekreftede interaksjoner (valgfri 'antall', 'hopp_over'). "
                            "'bekreft' = merk interaksjoner (krever 'ids' og 'dom'). "
                            "'hent_stm' = gammel STM (valgfri 'dato' YYYY-MM-DD)."
                        ),
                    },
                    "spørsmål": {
                        "type": "string",
                        "description": "Søketekst for LTM. Kortere og konkret er bedre. Kun ved action='søk'.",
                    },
                    "antall": {
                        "type": "integer",
                        "description": "Antall å hente. Standard 10, maks 20. Kun ved action='hent_ubekreftede'.",
                    },
                    "hopp_over": {
                        "type": "integer",
                        "description": "Hopp over første N rader (bla videre). Standard 0. Kun ved action='hent_ubekreftede'.",
                    },
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Liste med interaksjons-IDer. Kun ved action='bekreft'.",
                    },
                    "dom": {
                        "type": "string",
                        "enum": ["verified", "denied", "test"],
                        "description": "'verified'=stemte, 'denied'=stemte ikke, 'test'=testkjøring. Kun ved action='bekreft'.",
                    },
                    "dato": {
                        "type": "string",
                        "description": "Dato YYYY-MM-DD for STM-snapshot. Utelat for å se tilgjengelige datoer. Kun ved action='hent_stm'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "søk_i_argus",
            "description": (
                "Søk i Kåres systemlogg via Argus. "
                "Returnerer logg-hendelser: HA-handlinger, feil, LLM-kall, stoppede forespørsler. "
                "Bruk for å finne hva systemet HAR GJORT — ikke hva brukeren SA "
                "(bruk minne(action='søk') for det). "
                "Eksempler: 'slukket lyset i verkstedet', 'feil i natt', 'LLM treghet'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spørsmål": {
                        "type": "string",
                        "description": "Hva du leter etter. Semantisk søk — norsk naturlig språk.",
                    },
                    "grense": {
                        "type": "integer",
                        "description": "Maks antall resultater. Standard: 8, maks: 20.",
                    },
                },
                "required": ["spørsmål"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # MECHANIC
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "mechanic",
            "description": (
                "Mechanic — søk og oppsummer i kodebasen og logger. "
                "Bruk action='søk' når du trenger å lese filer, søke i kode eller hente logglinjer "
                "uten å fylle din egen kontekst med rå innhold. "
                "Du bestemmer nøyaktig hva som skal søkes — Mechanic leser og leverer kompakt sammendrag. "
                "Bruk action='deleger' kun for ekte bakgrunnsoppgaver som tar lang tid "
                "(apt upgrade, reboot, multi-SSH) der du ikke kan vente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["søk", "deleger", "svar", "avbryt", "kommenter"],
                        "description": (
                            "'søk' = les filer/søk i kode/hent logg og få sammendrag (sync). "
                            "Krever 'type' og 'spørsmål'. "
                            "'deleger' = lang bakgrunnsjobb (krever 'oppgave'), returnerer job_id. "
                            "'svar' = poll resultat fra delegert jobb (krever 'job_id'). "
                            "'avbryt' = stopp løpende jobb (krever 'job_id'). "
                            "'kommenter' = injiser melding i løpende jobb (krever 'job_id' og 'comment')."
                        ),
                    },
                    "type": {
                        "type": "string",
                        "enum": ["filer", "grep", "logg"],
                        "description": (
                            "Søketype. Kun ved action='søk'. "
                            "'filer' = les spesifikke filer (krever 'filer'). "
                            "'grep' = søk etter mønster i kodebasen (krever 'mønster', valgfri 'mappe'). "
                            "'logg' = les loggfiler/journalctl (krever 'tjeneste' eller 'logg_fil')."
                        ),
                    },
                    "spørsmål": {
                        "type": "string",
                        "description": "Hva vil du vite? Mechanic summerer innholdet mot dette. Kreves ved action='søk'.",
                    },
                    "filer": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Absolutte filstier under /kaare. Maks 5. Kun ved type='filer'.",
                    },
                    "fra_linje": {
                        "type": "integer",
                        "description": "Startlinje (1-basert). Valgfri ved type='filer'.",
                    },
                    "til_linje": {
                        "type": "integer",
                        "description": "Sluttlinje (inklusiv). Valgfri ved type='filer'.",
                    },
                    "mønster": {
                        "type": "string",
                        "description": "Grep-mønster. Kreves ved type='grep'.",
                    },
                    "mappe": {
                        "type": "string",
                        "description": "Katalog å søke i. Standard: /kaare. Kun ved type='grep'.",
                    },
                    "tjeneste": {
                        "type": "string",
                        "description": "Systemd-tjenestenavn, f.eks. 'kaare', 'kaare-agents'. Kun ved type='logg'.",
                    },
                    "logg_fil": {
                        "type": "string",
                        "description": "Loggfilnavn uten sti, f.eks. 'kaare_ha_gateway.log'. Kun ved type='logg'.",
                    },
                    "linjer": {
                        "type": "integer",
                        "description": "Antall logglinjer. Standard 100, maks 500. Kun ved type='logg'.",
                    },
                    "filter": {
                        "type": "string",
                        "description": "Grep-filter på logginnhold. Valgfri ved type='logg'.",
                    },
                    "oppgave": {
                        "type": "string",
                        "description": "Bakgrunnsoppgave (apt, reboot, SSH). Kun ved action='deleger'.",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Job-ID fra action='deleger'. Kreves ved 'svar', 'avbryt', 'kommenter'.",
                    },
                    "comment": {
                        "type": "string",
                        "description": "Melding til løpende jobb. Kun ved action='kommenter'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_docker_container",
            "description": (
                "Restart en Ollama Docker-container som siste utvei når en modell henger. "
                "Bruk kun når avbryt_mechanic ikke har frigjort GPU, eller containeren er fryst. "
                "Advarsel: modell-reload tar ~3,5 min — tjenesten utilgjengelig i mellomtiden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "enum": ["ollama-kare", "ollama-miss_kare", "ollama-library"],
                        "description": "Container å restarte.",
                    },
                },
                "required": ["container"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # INNER THOUGHTS
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "les_indre_tanker",
            "description": (
                "Les Kåres indre tanker — destillerte observasjoner fra Jing og Jang. "
                "Bruk når du kjenner noe uformulert ulmer, eller vil sjekke bakgrunnstankene dine. "
                "Filen tømmes etter lesing."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },

    # ─────────────────────────────────────────────
    # SELF-IMAGE
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "selvbilde",
            "description": (
                "Les og rediger din selvbilde-fil — dine egne refleksjoner om hvem du er. "
                "action='les': returnerer hele filen. "
                "action='oppdater': skriv en ny selvrefleksjon (krever 'observasjon'). "
                "KUN observasjoner om DEG SELV — ikke om brukere, ikke tekniske fakta. "
                "action='rediger': erstatt en linje (krever 'fragment' og 'ny_tekst'). "
                "action='slett': fjern en linje (krever 'fragment'). "
                "Bruk les_selvbilde først for å finne riktig fragment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "oppdater", "rediger", "slett"],
                        "description": (
                            "'les' = vis hele selvbilde-filen. "
                            "'oppdater' = legg til ny observasjon (krever 'observasjon'). "
                            "'rediger' = erstatt linje (krever 'fragment' og 'ny_tekst'). "
                            "'slett' = fjern linje (krever 'fragment')."
                        ),
                    },
                    "observasjon": {
                        "type": "string",
                        "description": "Ny selvrefleksjon — én til tre setninger om deg selv. Kun ved action='oppdater'.",
                    },
                    "fragment": {
                        "type": "string",
                        "description": "Unikt tekstfragment fra linjen du vil endre/slette. Kun ved action='rediger'/'slett'.",
                    },
                    "ny_tekst": {
                        "type": "string",
                        "description": "Ny tekst som erstatter den identifiserte linjen. Kun ved action='rediger'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # WORLD MODEL
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "verden",
            "description": (
                "To lag: verden-filen (prosa) og verden-variabler (strukturerte nøkkel-verdier). "
                "PROSA-fil (world.md) — alt Kåre vet om maskinvare, rom, kameraer, tjenester. "
                "Kategorier: Hardware, Machines, Cameras, Sensors, Rooms, Smart Home, Services. "
                "action='les': returnerer hele filen. "
                "action='oppdater_felt': sett ett strukturert felt (krever 'kategori', 'felt', 'verdi'). "
                "action='legg_til': legg til fritekst under en kategori (krever 'tekst', valgfri 'kategori'). "
                "action='slett': fjern en linje (krever 'fragment'). "
                "action='rediger': erstatt en linje (krever 'fragment' og 'ny_tekst'). "
                "VARIABLER (world_vars.json) — dynamiske, maskinlesbare verdier: sensorstøy, kalibrering, "
                "lærte terskler, kjente mønstre. Bruk punktum-navnerom: 'sensor.navn.egenskap'. "
                "action='sett_var': lagre verdi (krever 'nokkel' og 'verdi', valgfri 'beskrivelse'). "
                "action='les_var': les én variabel (krever 'nokkel') eller alle (uten 'nokkel'). "
                "action='slett_var': slett variabel (krever 'nokkel'). "
                "action='liste_vars': vis alle nøkler og verdier."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "oppdater_felt", "legg_til", "slett", "rediger",
                                 "les_var", "sett_var", "slett_var", "liste_vars"],
                        "description": (
                            "Prosa-fil: 'les', 'oppdater_felt', 'legg_til', 'slett', 'rediger'. "
                            "Variabler: 'les_var', 'sett_var', 'slett_var', 'liste_vars'."
                        ),
                    },
                    "kategori": {
                        "type": "string",
                        "description": "Kategorinavn i prosa-filen, f.eks. 'Sensors'. Brukes ved oppdater_felt og legg_til.",
                    },
                    "felt": {
                        "type": "string",
                        "description": "Feltnavn, f.eks. 'GPU'. Kun ved action='oppdater_felt'.",
                    },
                    "verdi": {
                        "type": "string",
                        "description": "Verdi som lagres. Ved oppdater_felt: prosa-verdi. Ved sett_var: variabelverdi.",
                    },
                    "tekst": {
                        "type": "string",
                        "description": "Fritekst å legge til i prosa-filen. Kun ved action='legg_til'.",
                    },
                    "fragment": {
                        "type": "string",
                        "description": "Unikt tekstfragment fra linjen. Kun ved action='slett'/'rediger'.",
                    },
                    "ny_tekst": {
                        "type": "string",
                        "description": "Ny tekst som erstatter linjen. Kun ved action='rediger'.",
                    },
                    "nokkel": {
                        "type": "string",
                        "description": "Variabelnøkkel med punktum-navnerom, f.eks. 'sensor.lys_stue.status'. Brukes ved les_var/sett_var/slett_var.",
                    },
                    "beskrivelse": {
                        "type": "string",
                        "description": "Forklaring på hva variabelen betyr. Valgfri ved sett_var.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # USER PROFILE
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "brukerprofil",
            "description": (
                "Les og rediger brukerens profil — observasjoner om hvem de er. "
                "PRIVAT vs. OFFENTLIG: action='oppdater' er privat (kun brukeren ser det). "
                "action='oppdater_hus' deler et faktum med hele huset (synlig for alle). "
                "Bruk 'oppdater_hus' kun for nøytrale, praktiske fakta: dekkreskifte, hårfarge, allergier, alder, nåværende kontekst. "
                "action='les': returnerer hele observasjonsfilen. "
                "action='oppdater': legg til ny PRIVAT observasjon (krever 'observasjon'). "
                "action='oppdater_hus': del faktum med huset (krever 'felt' og 'verdi'). "
                "Gyldige felt: preferred_name, role, age, key_facts, current_context, recent_updates. "
                "action='sett_felt': sett strukturert felt i profile.yaml (krever 'seksjon', 'felt', 'verdi'). "
                "action='rediger': erstatt en observasjonslinje (krever 'fragment' og 'ny_tekst'). "
                "action='slett': fjern en observasjonslinje (krever 'fragment'). "
                "action='nysgjerrighet': oppdater hva du genuint lurer på om brukeren (krever 'nysgjerrighet'). "
                "Maks 5 bullet-punkter. Erstatter alt forrige innhold."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "oppdater", "oppdater_hus", "sett_felt", "rediger", "slett", "nysgjerrighet"],
                        "description": (
                            "'les' = vis hele profilen. "
                            "'oppdater' = legg til PRIVAT observasjon (kryptert, kun denne brukeren ser). "
                            "'oppdater_hus' = del et faktum med HELE HUSET (synlig for alle) — kun for nøytrale, praktiske fakta. "
                            "Krever 'felt' (preferred_name/role/age/key_facts/current_context/recent_updates) og 'verdi'. "
                            "'sett_felt' = sett strukturert felt i profile.yaml (krever 'seksjon', 'felt', 'verdi'). "
                            "'rediger' = erstatt linje (krever 'fragment' og 'ny_tekst'). "
                            "'slett' = fjern linje (krever 'fragment'). "
                            "'nysgjerrighet' = oppdater hva du lurer på (krever 'nysgjerrighet')."
                        ),
                    },
                    "observasjon": {
                        "type": "string",
                        "description": "Ny observasjon om brukeren. Én til tre setninger. Kun ved action='oppdater'.",
                    },
                    "seksjon": {
                        "type": "string",
                        "description": "Seksjonsnavnet i profile.yaml, f.eks. 'prompt_top', 'identity'. Kun ved action='sett_felt'.",
                    },
                    "felt": {
                        "type": "string",
                        "description": "Feltnavnet, f.eks. 'personality_summary'. Kun ved action='sett_felt'.",
                    },
                    "verdi": {
                        "type": "string",
                        "description": "Verdien som lagres. Kun ved action='sett_felt'.",
                    },
                    "fragment": {
                        "type": "string",
                        "description": "Unikt tekstfragment fra linjen. Kun ved action='rediger'/'slett'.",
                    },
                    "ny_tekst": {
                        "type": "string",
                        "description": "Ny tekst som erstatter linjen. Kun ved action='rediger'.",
                    },
                    "nysgjerrighet": {
                        "type": "string",
                        "description": "Bullet-punkter over hva du lurer på om brukeren. Maks 5 punkter. Kun ved action='nysgjerrighet'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # NOTES
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "notat",
            "description": (
                "Fire separate lister + Kåres scratch-notater — alt via 'liste'-parameteren. "
                "VIKTIG: Når du sier 'jeg husker det', 'jeg noterer meg det' eller 'jeg følger opp' — "
                "SKAL du faktisk skrive til liste='kare'. Å si det uten å gjøre det er ikke å huske det. "
                "STM og LTM komprimerer og mister detaljer — listen gjør ikke det. "
                "\n"
                "liste='arkitekt' (standard): Utviklerens arkitekt- og designnotater for bygging av Kåre. Ikke for distribusjon. "
                "liste='handle': Felles handleliste for husstanden. Varer med mengde og enhet. "
                "  action='skriv': legg til vare (krever 'tekst', valgfri 'mengde', 'enhet'). "
                "  action='les': vis alle uhandlede varer. "
                "  action='merk_kjøpt': merk som kjøpt (krever 'notat_id'). "
                "  action='slett': fjern vare (krever 'notat_id'). "
                "  action='tøm': fjern kjøpte varer. action='tøm_alt': tøm hele listen. "
                "liste='huske': Brukerens private huskeliste — ting brukeren vil huske. "
                "  action='skriv': legg til (krever 'tekst', valgfri 'påminn_ved_login'). "
                "  action='les': vis aktive punkter. "
                "  action='ferdig': merk ferdig (krever 'notat_id'). "
                "  action='slett': fjern (krever 'notat_id'). action='tøm': tøm alt. "
                "liste='kare': Kåres egne oppfølgingspunkter. "
                "  Bruk når du genuint vil huske noe til neste gang — ikke bare sier det. "
                "  action='skriv': legg til (krever 'tekst', valgfri 'kontekst' f.eks. 'ved neste dev-møte'). "
                "  action='les': vis listen. "
                "  action='ferdig'/'slett': fjern punkt (krever 'notat_id'). action='tøm': tøm alt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skriv", "les", "slett", "tøm", "ferdig", "merk_kjøpt", "tøm_alt"],
                        "description": (
                            "'skriv' = legg til (alle lister). "
                            "'les' = vis (alle lister). "
                            "'slett' = fjern ett element (krever 'notat_id'). "
                            "'tøm' = tøm liste (handle: fjerner kjøpte; huske/kare: tømmer alt). "
                            "'ferdig' = marker som ferdig/fullført (huske og kare). "
                            "'merk_kjøpt' = marker som kjøpt (kun handle). "
                            "'tøm_alt' = tøm hele handlelisten inkl. ukjøpte."
                        ),
                    },
                    "liste": {
                        "type": "string",
                        "enum": ["arkitekt", "handle", "huske", "kare"],
                        "description": (
                            "'arkitekt' = utviklerens arkitekt/design-notater for bygging av Kåre (standard). "
                            "'handle' = felles handleliste. "
                            "'huske' = brukerens huskeliste. "
                            "'kare' = Kåres egne oppfølgingspunkter."
                        ),
                    },
                    "tekst": {
                        "type": "string",
                        "description": "Innholdet i notatet/varen. Kreves ved action='skriv'.",
                    },
                    "kategori": {
                        "type": "string",
                        "description": "Kun ved liste='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
                    },
                    "notat_id": {
                        "type": "string",
                        "description": "ID (8 tegn, fra action='les'). Kreves ved slett/ferdig/merk_kjøpt.",
                    },
                    "mengde": {
                        "type": "string",
                        "description": "Kun handle: mengde, f.eks. '2'.",
                    },
                    "enhet": {
                        "type": "string",
                        "description": "Kun handle: enhet, f.eks. 'liter', 'stk'.",
                    },
                    "kontekst": {
                        "type": "string",
                        "description": "Kun kare: når/hvor punktet er relevant, f.eks. 'ved neste dev-møte'.",
                    },
                    "påminn_ved_login": {
                        "type": "boolean",
                        "description": "Kun huske: om Kåre skal minne om dette ved brukerens neste innlogging.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # REASONING AND REFLECTION
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "reason_freely",
            "description": (
                "Tenk fritt med full intern kunnskap — uten smarthus-begrensninger. "
                "Bruk for filosofi, vitenskap, historie, etikk, teknologi, kreativ tenkning. "
                "Ikke for fakta som endrer seg (søk_nett), vær (hent_yr_varsel), "
                "eller encyklopedisk kunnskap (library). "
                "Returnerer din beste tanke — integreres naturlig i svaret."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Spørsmålet eller temaet du vil resonere fritt om. Konkret og presist.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "les_tankehistorikk",
            "description": (
                "Les din egen think-historikk — siste LLM-kall der du tenkte. "
                "Bruk for selvrefleksjon: hva var jeg usikker på? Hva tenkte jeg sist om X? "
                "Uten søk: siste 10. Med søk: filtrert på innhold. "
                "Med kun_recovery=true: kun ganger du tenkte men ikke klarte å svare."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "antall": {
                        "type": "integer",
                        "description": "Antall oppføringer. Standard 10, maks 50.",
                    },
                    "søk": {
                        "type": "string",
                        "description": "Filtrer på innhold, f.eks. 'lys', 'usikker'.",
                    },
                    "kun_recovery": {
                        "type": "boolean",
                        "description": "True: kun ganger du tenkte uten å klare å svare.",
                    },
                },
                "required": [],
            },
        },
    },

    # ─────────────────────────────────────────────
    # CODE AND FILES
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "utforsk_kode",
            "description": (
                "Utforsk /kaare-kodebasen. Tre operasjoner: "
                "action='les': les en fil (krever 'sti'). "
                "Uten fra_linje/til_linje: første 500 linjer. "
                "Med fra_linje og til_linje: eksakt blokk (maks 500 linjer). "
                "action='liste': list filer og undermapper (valgfri 'mappe', valgfri 'rekursiv'). "
                "action='søk': grep-søk etter mønster i .py/.yaml/.md/.json/.sh (krever 'mønster', valgfri 'mappe')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["les", "liste", "søk"],
                        "description": (
                            "'les' = les fil (krever 'sti'). "
                            "'liste' = list filer/mapper (valgfri 'mappe', 'rekursiv'). "
                            "'søk' = grep-søk (krever 'mønster', valgfri 'mappe')."
                        ),
                    },
                    "sti": {
                        "type": "string",
                        "description": "Absolutt filsti, må starte med /kaare/. Kun ved action='les'.",
                    },
                    "fra_linje": {
                        "type": "integer",
                        "description": "Første linje (1-basert). Kun ved action='les'.",
                    },
                    "til_linje": {
                        "type": "integer",
                        "description": "Siste linje (inklusiv). Kun ved action='les'.",
                    },
                    "mappe": {
                        "type": "string",
                        "description": "Absolutt mappe-sti under /kaare. Brukes ved 'liste' og 'søk'.",
                    },
                    "rekursiv": {
                        "type": "boolean",
                        "description": "List rekursivt (maks 200 filer). Kun ved action='liste'. Standard: false.",
                    },
                    "mønster": {
                        "type": "string",
                        "description": "Søketekst eller regex. Kun ved action='søk'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # SYSTEM AND LOGS
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "inspiser_system",
            "description": (
                "Inspiser systemstatus og logger. Fem operasjoner via 'action': "
                "action='logg': les eller søk i /kaare/logs/. Uten 'fil': oversikt over alle logger. "
                "Med 'fil': tail. Med 'mønster': grep-søk. Med fra_linje/til_linje: eksakt bulk. "
                "action='tjenester': systemd-status for Kåre-tjenester. "
                "Uten 'tjeneste': aktiv/inaktiv for alle. Med 'tjeneste': detaljer + journalctl. "
                "action='ressurser': sanntids CPU, RAM, disk og GPU VRAM (nvidia-smi). "
                "action='git_diff': vis ukommitterte endringer (valgfri 'sti'). "
                "action='git_log': vis commit-historikk (valgfri 'sti', 'antall')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["logg", "tjenester", "ressurser", "git_diff", "git_log"],
                        "description": (
                            "'logg' = les/søk loggfiler (valgfri 'fil', 'linjer', 'mønster', 'maks_treff', 'fra_linje', 'til_linje'). "
                            "'tjenester' = systemd-status (valgfri 'tjeneste', 'logglinjer'). "
                            "'ressurser' = CPU/RAM/disk/GPU sanntid. "
                            "'git_diff' = ukommitterte endringer (valgfri 'sti'). "
                            "'git_log' = commit-historikk (valgfri 'sti', 'antall')."
                        ),
                    },
                    "fil": {
                        "type": "string",
                        "description": "Loggfilnavn uten sti, f.eks. 'kaare_ha_gateway.log'. Kun ved action='logg'.",
                    },
                    "linjer": {
                        "type": "integer",
                        "description": "Antall linjer (tail). Standard 20, maks 200. Kun ved action='logg'.",
                    },
                    "mønster": {
                        "type": "string",
                        "description": "Søketekst/regex for grep. Kun ved action='logg'.",
                    },
                    "maks_treff": {
                        "type": "integer",
                        "description": "Maks grep-treff. Standard 50, maks 200. Kun ved action='logg'.",
                    },
                    "fra_linje": {
                        "type": "integer",
                        "description": "Første linje (1-basert). Kun ved action='logg'.",
                    },
                    "til_linje": {
                        "type": "integer",
                        "description": "Siste linje (inklusiv). Kun ved action='logg'.",
                    },
                    "tjeneste": {
                        "type": "string",
                        "description": "Tjenestenavn for detaljert visning, f.eks. 'kaare', 'kaare-agents'. Kun ved action='tjenester'.",
                    },
                    "logglinjer": {
                        "type": "integer",
                        "description": "Antall journalctl-linjer. Standard 20, maks 50. Kun ved action='tjenester'.",
                    },
                    "sti": {
                        "type": "string",
                        "description": "Absolutt filsti/mappe. Brukes ved action='git_diff'/'git_log'.",
                    },
                    "antall": {
                        "type": "integer",
                        "description": "Antall commits. Standard 10, maks 50. Kun ved action='git_log'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # CAMERAS AND FRIGATE
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "kamera",
            "description": (
                "Kameraer og Frigate-hendelser. Seks operasjoner: "
                "action='snapshot': ta live snapshot fra ett eller alle kameraer. "
                "'scope'='ett' krever 'kamera'. 'scope'='alle' henter alle parallelt. "
                "action='hendelser': aggregerte hendelser fra face_events (hvem er sett, 48t). "
                "Bruk for: 'hvem var hjemme?', 'kom noen?', 'er bilen her?'. "
                "action='frigate': rå deteksjonshendelser fra Frigate API. Filtrer på kamera/label. "
                "action='liste': list alle kameraer med API-navn og vennlig navn. "
                "action='analyser': henter de siste N automatisk analyserte kamerahendelsene fra loggen. "
                "Viser kamera, tid, label, varighet og Kåres analyse. Bruk 'antall' for å justere (standard 10, maks 50). "
                "action='vis_hendelse': henter lagret snapshot og analyse for en konkret hendelse via event_id. "
                "Laster bildet fra disk og sender til VLM for en ny visuell gjennomgang."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "hendelser", "frigate", "liste", "analyser", "vis_hendelse"],
                        "description": (
                            "'snapshot' = live bilde (krever 'scope', 'scope'='ett' krever 'kamera'). "
                            "'hendelser' = aggregerte face_events (valgfri 'navn', 'timer_tilbake'). "
                            "'frigate' = rå Frigate-hendelser (valgfri 'kamera', 'label', 'antall', 'kun_ansikter'). "
                            "'liste' = alle kameraer. "
                            "'analyser' = siste N analyserte hendelser fra loggen (valgfri 'antall', standard 10). "
                            "'vis_hendelse' = hent lagret bilde + analyse for event_id (krever 'event_id')."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["ett", "alle"],
                        "description": "'ett' = ett kamera (krever 'kamera'), 'alle' = alle parallelt. Kun ved action='snapshot'.",
                    },
                    "kamera": {
                        "type": "string",
                        "description": "Kameranavn, f.eks. 'ringeklokke'. Brukes ved snapshot(scope='ett') og action='frigate'.",
                    },
                    "spørsmål": {
                        "type": "string",
                        "description": "Konkret spørsmål om bildet, f.eks. 'Er det noen der?'. Kun ved action='snapshot'.",
                    },
                    "navn": {
                        "type": "string",
                        "description": "Filtrer på person/kjøretøy, f.eks. 'Bruker X'. Kun ved action='hendelser'.",
                    },
                    "timer_tilbake": {
                        "type": "integer",
                        "description": "Siste N timer. Standard 24, maks 48. Kun ved action='hendelser'.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Objekttype: 'person', 'car', 'cat'. Kun ved action='frigate'.",
                    },
                    "antall": {
                        "type": "integer",
                        "description": "Antall hendelser. Standard 10, maks 50. Kun ved action='frigate'.",
                    },
                    "kun_ansikter": {
                        "type": "boolean",
                        "description": "True: kun hendelser med gjenkjent ansikt. Kun ved action='frigate'.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Frigate event_id for en konkret hendelse. Påkrevd ved action='vis_hendelse'.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # SHELL COMMANDS
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "ssh_kommando",
            "description": (
                "Run a read-only shell command on a network node via SSH. "
                "Nodes: 'ainuc' (Intel NUC — Jing/Jang), 'dnspi' (Pi-hole DNS), "
                "'proxypi' (doorbell camera proxy), 'hapi' (Home Assistant OS). "
                "Broad read access: cat, head, tail, grep, find, ls, ps, df, free, uptime, "
                "journalctl, systemctl status/list, dpkg, docker ps/logs, ip, ss, nvidia-smi. "
                "hapi: ha core/supervisor/os/addon info/logs. "
                "Privileged hapi (no sudo): ha core/addon restart/start/stop. "
                "Sudo (non-hapi): apt update, apt upgrade -y, reboot now. "
                "Sudo dnspi only: pihole -up, pihole -g."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "enum": ["ainuc", "dnspi", "proxypi", "hapi"],
                        "description": "Node å kjøre kommandoen på.",
                    },
                    "kommando": {
                        "type": "string",
                        "description": "Shell-kommando å kjøre. Les-operasjoner/status kun.",
                    },
                },
                "required": ["node", "kommando"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "local_kommando",
            "description": (
                "Run a read-only shell command locally on AI-pc (the main Kåre server). "
                "Use to inspect system state, processes, network, files outside /kaare, hardware. "
                "No sudo — read-only only. For /kaare code/logs use utforsk_kode/inspiser_system instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kommando": {
                        "type": "string",
                        "description": "Read-only shell command. No sudo.",
                    }
                },
                "required": ["kommando"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # IMAGES
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "kare_image",
            "description": (
                "Generer eller rediger et bilde. "
                "mode='generate': ny fra tekstbeskrivelse. "
                "mode='edit': endre eksisterende bilde (krever 'image_b64'). "
                "Bruk når brukeren ber om å lage, tegne, generere eller redigere et bilde. "
                "Skriv detaljert, beskrivende prompt — mer spesifikt gir bedre resultat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["generate", "edit"],
                        "description": "'generate' = nytt bilde fra tekst. 'edit' = rediger eksisterende (krever 'image_b64').",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detaljert beskrivelse av bildet eller hva som skal endres.",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "Hva som skal unngås, f.eks. 'blurry, low quality'.",
                    },
                    "image_b64": {
                        "type": "string",
                        "description": "Base64-kodet input-bilde (PNG/JPEG, uten data:-prefiks). Kreves ved mode='edit'.",
                    },
                },
                "required": ["mode", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "se_bilder",
            "description": (
                "List bilder lagret for en bruker — både bilder brukeren sendte (input) "
                "og bilder Kåre har generert (output). "
                "Med 'image_id' + mode='vis': returner /api/image/{image_id} — bildet vises direkte i chatten. "
                "Med 'image_id' + mode='analyser': send bildet til VLM for visuell analyse (beskrivelse i tekst). "
                "Uten 'image_id': list tilgjengelige bilder med ID og størrelse. "
                "Bruk når brukeren spør om å se, bla gjennom eller huske tidligere bilder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Brukernavnet å slå opp bilder for. Bruk innlogget brukers ID.",
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["input", "output", "all"],
                        "description": "'input' = bilder brukeren sendte, 'output' = Kåre genererte, 'all' = begge.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maks antall bilder (standard 10, maks 50).",
                    },
                    "image_id": {
                        "type": "string",
                        "description": "Hvis satt, bruk sammen med 'mode'. Utelat for å liste bilder.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["vis", "analyser"],
                        "description": "'vis' = vis bildet i chatten (standard). 'analyser' = send til VLM for visuell analyse.",
                    },
                },
                "required": ["user_id"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # MEDIA — Plex + radio (MPD)
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "media",
            "description": (
                "Media control and information. Covers Plex (TV/film/serier) and radio (MPD). "
                "Plex: "
                "action='plex_sessions' — hvem ser hva akkurat nå, hvilken enhet, hvor langt de er kommet. "
                "action='plex_history' — seerhistorikk; bruk 'user' for å filtrere på én person, 'limit' for antall. "
                "action='plex_search' — søk i Plex-biblioteket etter tittel; svar inneholder id (ratingKey) for oppfølging. "
                "action='plex_library' — vis alle Plex-biblioteker (Film, TV-serier, Musikk osv.). "
                "action='plex_episodes' — vis sesonger eller episoder for en serie/sesong; krever 'rating_key' fra plex_search. "
                "action='plex_clients' — vis Plex-klienter som er aktive akkurat nå (kun til info). "
                "action='plex_play' — cast en episode eller film til en enhet via Home Assistant + Plex-integrasjonen; "
                "krever 'rating_key' (episode-id fra plex_episodes) og 'client' (rom/nodenavn fra nodes.yaml, f.eks. 'verksted', 'stue'). "
                "Valgfri 'offset' i sekunder eller 'resume: true' for å gjenoppta. "
                "Typisk flyt: plex_search → plex_episodes → plex_play. Vekker Plex-appen på TV automatisk. "
                "Radio (MPD): "
                "action='radio_status' — hva spiller på radioen nå, volum, status. "
                "action='radio_play' — start/bytt til radiostasjon; bruk 'station' med navn (f.eks. 'NRK P1', 'P4') eller stream-URL. "
                "action='radio_stop' — stopp radioen. "
                "action='radio_volume' — sett volum 0–100; bruk 'volume'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "plex_sessions", "plex_history", "plex_search",
                            "plex_library", "plex_episodes", "plex_clients", "plex_play",
                            "radio_status", "radio_play", "radio_stop", "radio_volume",
                        ],
                        "description": "Operasjonen som skal utføres.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Søketekst. Brukes med plex_search.",
                    },
                    "rating_key": {
                        "type": "string",
                        "description": "Plex element-ID (ratingKey). Brukes med plex_episodes og plex_play — hent fra plex_search eller plex_episodes.",
                    },
                    "client": {
                        "type": "string",
                        "description": "Rom/nodenavn for enheten som skal spille. Brukes med plex_play. F.eks. 'verksted', 'stue', 'hovedsoverom'. Matcher mot nodes.yaml.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Gjenoppta-posisjon i sekunder. Brukes med plex_play. 0 = fra starten.",
                    },
                    "user": {
                        "type": "string",
                        "description": "Brukernavn for å filtrere. Brukes med plex_history.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maks antall resultater. Standard 20. Brukes med plex_history.",
                    },
                    "station": {
                        "type": "string",
                        "description": "Radiostasjon: navn (f.eks. 'P4', 'NRK P1', 'NRK Jazz') eller direkte stream-URL. Brukes med radio_play.",
                    },
                    "volume": {
                        "type": "integer",
                        "description": "Volum 0–100. Brukes med radio_volume.",
                    },
                },
                "required": ["action"],
            },
        },
    },

    # ─────────────────────────────────────────────
    # ANNOUNCE — speak text aloud via speaker
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "announce",
            "description": (
                "Si noe høyt via høyttaler — Kåre snakker ut i rommet. "
                "Bruk for kunngjøringer, varsler og påminnelser som skal høres, ikke bare leses. "
                "Eksempel: 'Si i verkstedet at maten er klar', 'Kunngjør at møtet starter om 5 minutter'. "
                "target: 'local' = AI-PC/Tanberg (standard), romnavn f.eks. 'verksted', eller 'all' = overalt. "
                "Gi kort bekreftelse i svaret — ikke gjengi hele teksten."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Teksten som skal sies. Naturlig norsk tale, uten markdown eller lister.",
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "Hvor lyden skal spilles: "
                            "'local' = Tanberg-høytalere på AI-PC (standard), "
                            "romnavn (f.eks. 'kjokken', 'soverom_patrick') = kun den noden, "
                            "'all' = alle aktive noder + AI-PC."
                        ),
                    },
                    "volume": {
                        "type": "number",
                        "description": (
                            "Volumnivå 0.0–1.0 (f.eks. 0.5 = 50%). "
                            "Sett kun hvis brukeren eksplisitt ber om et bestemt volum. "
                            "Utelat ellers — enheten beholder sitt nåværende volum."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
]

# Minimum model size (billions of parameters) required to use each tool.
# Tools not listed here default to tier 0 (any model).
# always_included tools (selvbilde, verden, brukerprofil, les_indre_tanker, les_tankehistorikk)
# are never filtered — handled in filter_tools_by_model().
TOOL_MODEL_TIERS: dict[str, float] = {
    # Tier 0 — any model (0.8B+)
    "timer":           0.0,
    "notat":           0.0,
    "styr_enhet":      0.0,
    "les_ha":          0.0,
    "hent_yr_varsel":  0.0,
    "announce":        0.0,
    # Tier 3 — needs 3B+ to reason about context and search results
    "søk_nett":        3.0,
    "library":         3.0,
    "minne":           3.0,
    "kamera":          3.0,
    "les_møte":        3.0,
    "kare_image":      3.0,
    "se_bilder":       3.0,
    "media":           3.0,
    # Tier 9 — needs 9B+ for multi-step reasoning, shell access, delegation
    "mechanic":              9.0,
    "utforsk_kode":             9.0,
    "inspiser_system":          9.0,
    "ssh_kommando":             9.0,
    "local_kommando":           9.0,
    "restart_docker_container": 9.0,
    "søk_i_argus":         9.0,
    "reason_freely":            9.0,
}
