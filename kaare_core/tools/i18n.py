from pathlib import Path

import yaml

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def get_user_lang_override(user_id: str = "global") -> str | None:
    """The user's own language choice (profile `prompt_top.language`, nb/en/de), or None when the user
    follows the installation's language."""
    if not user_id or user_id == "global":
        return None
    try:
        profile_path = Path(f"/kaare/state/users/{user_id}/profile.yaml")
        if profile_path.exists():
            profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
            lang = (profile.get("prompt_top") or {}).get("language")
            if lang in ("nb", "en", "de"):
                return lang
    except Exception:
        pass
    return None


def get_lang(user_id: str = "global") -> str:
    override = get_user_lang_override(user_id)
    if override:
        return override
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        lang = cfg.get("kare_language", "nb")
        if lang in ("nb", "en", "de"):
            return lang
    except Exception:
        pass
    # kare_language may be set to any of 16 GUI-selectable languages (sv/da/fr/...),
    # but the internal pipe (system-prompt scaffolding, t()-text) only has nb/en/de
    # translations. English is the safer fallback for anything else — Norwegian-only
    # scaffold text bleeding into e.g. a Swedish household's prompt is worse than English.
    return "en"


def get_speech_lang(user_id: str = "global") -> str:
    """Resolve the raw kare_language value for STT/TTS — uncollapsed, all 16 GUI values.

    Unlike get_lang() (pipe-scaffold text, which collapses to nb/en/de), speech IS the chat
    itself — the user's spoken words are chat input, Kåre's spoken reply is chat output — so
    it follows the active kare_language selection directly (P101). `user_id` is accepted for
    call-site symmetry with get_lang() and reserved for a possible future per-user override,
    but is not consulted yet — global kare_language only (P101 Decision 1).
    """
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        return cfg.get("kare_language", "nb")
    except Exception:
        # Settings unreadable entirely — no real value to fall back to, so use the same
        # safe universal default as get_lang()'s final fallback, not a Norwegian assumption.
        return "en"


def t(msg_key: str, lang: str = "nb", **kwargs) -> str:
    s = _T.get(msg_key, {}).get(lang) or _T.get(msg_key, {}).get("nb") or msg_key
    return s.format(**kwargs) if kwargs else s


_T: dict = {
    # ── HA ───────────────────────────────────────────────────────────────────
    "ha_no_devices": {
        "nb": "Kjenner ikke til noen enheter ennå.",
        "en": "No devices known yet.",
        "de": "Noch keine Geräte bekannt.",
    },
    "ha_known_in_room": {
        "nb": "Kjenner til følgende i {room}:",
        "en": "Known devices in {room}:",
        "de": "Bekannte Geräte in {room}:",
    },
    "ha_room_not_found": {
        "nb": "Kjenner ikke til noe rom som heter '{room}'.",
        "en": "No room named '{room}' found.",
        "de": "Kein Raum namens '{room}' gefunden.",
    },
    "ha_known_rooms": {
        "nb": "Kjenner til følgende rom i huset:",
        "en": "Known rooms in the house:",
        "de": "Bekannte Räume im Haus:",
    },
    "ha_room_hint": {
        "nb": "Kall les_alias_lista med rom='<romnavn>' for å se enheter i et rom.",
        "en": "Call les_alias_lista with rom='<room_name>' to see devices in a room.",
        "de": "Rufe les_alias_lista mit rom='<Raumname>' auf, um Geräte in einem Raum zu sehen.",
    },
    "ha_room_example": {
        "nb": "Eksempel: les_alias_lista(rom='ute') for uteenheter.",
        "en": "Example: les_alias_lista(rom='ute') for outdoor devices.",
        "de": "Beispiel: les_alias_lista(rom='ute') für Außengeräte.",
    },
    "ha_entity_action_required": {
        "nb": "Feil: entity_id og action er påkrevd.",
        "en": "Error: entity_id and action are required.",
        "de": "Fehler: entity_id und action sind erforderlich.",
    },
    "ha_turned_on": {
        "nb": "'{entity_id}' skrudd på.",
        "en": "'{entity_id}' turned on.",
        "de": "'{entity_id}' eingeschaltet.",
    },
    "ha_turned_off": {
        "nb": "'{entity_id}' skrudd av.",
        "en": "'{entity_id}' turned off.",
        "de": "'{entity_id}' ausgeschaltet.",
    },
    "ha_brightness_set": {
        "nb": "'{entity_id}' lysstyrke satt til {brightness_pct}%.",
        "en": "'{entity_id}' brightness set to {brightness_pct}%.",
        "de": "'{entity_id}' Helligkeit auf {brightness_pct}% gesetzt.",
    },
    "ha_color_temp_set": {
        "nb": "'{entity_id}' fargetemperatur satt til {color_temp_kelvin}K.",
        "en": "'{entity_id}' color temperature set to {color_temp_kelvin}K.",
        "de": "'{entity_id}' Farbtemperatur auf {color_temp_kelvin}K gesetzt.",
    },
    "ha_color_set": {
        "nb": "'{entity_id}' farge satt.",
        "en": "'{entity_id}' color set.",
        "de": "'{entity_id}' Farbe gesetzt.",
    },
    "ha_action_done": {
        "nb": "'{entity_id}' {action} utført.",
        "en": "'{entity_id}' {action} executed.",
        "de": "'{entity_id}' {action} ausgeführt.",
    },
    "ha_status_response": {
        "nb": "HA svarte med status: {status}.",
        "en": "HA responded with status: {status}.",
        "de": "HA antwortete mit Status: {status}.",
    },
    "ha_call_error": {
        "nb": "Feil ved HA-kall: {error}",
        "en": "Error calling HA: {error}",
        "de": "Fehler beim HA-Aufruf: {error}",
    },
    "ha_entity_id_required": {
        "nb": "Feil: entity_id er påkrevd.",
        "en": "Error: entity_id is required.",
        "de": "Fehler: entity_id ist erforderlich.",
    },
    "ha_entity_not_found": {
        "nb": "Entitet '{entity_id}' ikke funnet i Home Assistant.",
        "en": "Entity '{entity_id}' not found in Home Assistant.",
        "de": "Entität '{entity_id}' nicht in Home Assistant gefunden.",
    },
    "ha_read_error": {
        "nb": "Feil ved lesing av '{entity_id}': {error}",
        "en": "Error reading '{entity_id}': {error}",
        "de": "Fehler beim Lesen von '{entity_id}': {error}",
    },
    "ha_blocked_external": {
        "nb": "Smarthus-kontroll er ikke tilgjengelig eksternt for din bruker.",
        "en": "Smart home control is not available externally for your user.",
        "de": "Smart-Home-Steuerung ist für deinen Benutzer extern nicht verfügbar.",
    },
    "ha_history_no_entity": {
        "nb": "entity_id er påkrevd for ha_history.",
        "en": "entity_id is required for ha_history.",
        "de": "entity_id ist für ha_history erforderlich.",
    },
    "ha_history_not_configured": {
        "nb": "HA-tilkobling ikke konfigurert (mangler URL eller token).",
        "en": "HA connection not configured (missing URL or token).",
        "de": "HA-Verbindung nicht konfiguriert (URL oder Token fehlt).",
    },
    "ha_history_error": {
        "nb": "Feil ved henting av historikk for {entity_id}: {error}",
        "en": "Error fetching history for {entity_id}: {error}",
        "de": "Fehler beim Abrufen des Verlaufs für {entity_id}: {error}",
    },
    "ha_history_no_changes": {
        "nb": "Ingen registrert historikk for {entity_id} de siste {days} dagene.",
        "en": "No recorded history for {entity_id} in the last {days} days.",
        "de": "Kein aufgezeichneter Verlauf für {entity_id} in den letzten {days} Tagen.",
    },
    "ha_history_changes_header": {
        "nb": "Tilstandsendringer — {entity_id} (siste {days} dager). Entiteten har ingen langtidsstatistikk i HA, så dette er rå historikk, bare så langt tilbake som HA lagrer den:",
        "en": "State changes — {entity_id} (last {days} days). The entity has no long-term statistics in HA, so this is raw history, only as far back as HA keeps it:",
        "de": "Zustandsänderungen — {entity_id} (letzte {days} Tage). Die Entität hat keine Langzeitstatistik in HA, daher ist dies der Rohverlauf, nur so weit zurück, wie HA ihn speichert:",
    },
    "ha_history_changes_truncated": {
        "nb": "(viser de siste {shown} av {total} endringer)",
        "en": "(showing the last {shown} of {total} changes)",
        "de": "(zeigt die letzten {shown} von {total} Änderungen)",
    },
    "ha_history_header": {
        "nb": "Sensorhistorikk — {entity_id} (siste {days} dager, per {period}):",
        "en": "Sensor history — {entity_id} (last {days} days, per {period}):",
        "de": "Sensorverlauf — {entity_id} (letzte {days} Tage, pro {period}):",
    },
    "ha_history_period_day": {"nb": "dag", "en": "day", "de": "Tag"},
    "ha_history_period_week": {"nb": "uke", "en": "week", "de": "Woche"},
    "ha_history_period_month": {"nb": "måned", "en": "month", "de": "Monat"},
    "ha_history_summary": {
        "nb": "Oppsummering over perioden: maks {max_val:.2f}{unit}, snitt {mean_val:.2f}{unit}, lavest {min_val:.2f}{unit}",
        "en": "Summary over period: max {max_val:.2f}{unit}, avg {mean_val:.2f}{unit}, min {min_val:.2f}{unit}",
        "de": "Zusammenfassung: max {max_val:.2f}{unit}, Durchschnitt {mean_val:.2f}{unit}, min {min_val:.2f}{unit}",
    },
    "ha_history_total": {
        "nb": "Akkumulert totalt: {total:.2f}{unit}",
        "en": "Accumulated total: {total:.2f}{unit}",
        "de": "Kumulierter Gesamtwert: {total:.2f}{unit}",
    },
    # ── Camera ───────────────────────────────────────────────────────────────
    "cam_no_cameras": {
        "nb": "Ingen kameraer funnet i Frigate.",
        "en": "No cameras found in Frigate.",
        "de": "Keine Kameras in Frigate gefunden.",
    },
    "cam_fetch_failed": {
        "nb": "Klarte ikke hente bilder fra noen kameraer ({ts}).",
        "en": "Could not fetch images from any cameras ({ts}).",
        "de": "Konnte keine Bilder von Kameras abrufen ({ts}).",
    },
    "cam_empty_vlm": {
        "nb": "Fikk tomt svar fra bildeanalyse.",
        "en": "Received empty response from image analysis.",
        "de": "Leere Antwort von der Bildanalyse erhalten.",
    },
    "cam_analysis_failed": {
        "nb": "Bilder hentet ({ts}), men analyse feilet: {error}",
        "en": "Images fetched ({ts}), but analysis failed: {error}",
        "de": "Bilder abgerufen ({ts}), aber Analyse fehlgeschlagen: {error}",
    },
    "cam_specify_name": {
        "nb": "Oppgi kameranavn. Tilgjengelige kameraer: {cam_list}",
        "en": "Please specify a camera name. Available cameras: {cam_list}",
        "de": "Bitte Kameraname angeben. Verfügbare Kameras: {cam_list}",
    },
    "cam_not_found": {
        "nb": "Kamera ikke funnet: {error}\nTilgjengelige: {cam_list}",
        "en": "Camera not found: {error}\nAvailable: {cam_list}",
        "de": "Kamera nicht gefunden: {error}\nVerfügbar: {cam_list}",
    },
    "cam_snapshot_error": {
        "nb": "Kunne ikke hente snapshot fra '{camera}': {error}",
        "en": "Could not fetch snapshot from '{camera}': {error}",
        "de": "Snapshot von '{camera}' konnte nicht abgerufen werden: {error}",
    },
    "cam_snapshot_analysis_failed": {
        "nb": "Snapshot hentet ({ts}), men bildeanalyse feilet: {error}",
        "en": "Snapshot fetched ({ts}), but image analysis failed: {error}",
        "de": "Snapshot abgerufen ({ts}), aber Bildanalyse fehlgeschlagen: {error}",
    },
    "cam_no_events": {
        "nb": "Ingen kamerahendelser registrert ennå.",
        "en": "No camera events recorded yet.",
        "de": "Noch keine Kameraereignisse aufgezeichnet.",
    },
    "cam_events_read_error": {
        "nb": "Kunne ikke lese kamerahendelser: {error}",
        "en": "Could not read camera events: {error}",
        "de": "Kameraereignisse konnten nicht gelesen werden: {error}",
    },
    "cam_no_events_filtered": {
        "nb": "Ingen kamerahendelser{label} funnet siste {hours} timer.",
        "en": "No camera events{label} found in the last {hours} hours.",
        "de": "Keine Kameraereignisse{label} in den letzten {hours} Stunden gefunden.",
    },
    "cam_events_header": {
        "nb": "Kamerahendelser siste {hours} timer ({count} hendelse{suffix}):",
        "en": "Camera events last {hours} hours ({count} event{suffix}):",
        "de": "Kameraereignisse der letzten {hours} Stunden ({count} Ereignis{suffix}):",
    },
    "cam_frigate_error": {
        "nb": "Kunne ikke hente Frigate-hendelser: {error}",
        "en": "Could not fetch Frigate events: {error}",
        "de": "Frigate-Ereignisse konnten nicht abgerufen werden: {error}",
    },
    "cam_no_frigate_events": {
        "nb": "Ingen hendelser funnet.",
        "en": "No events found.",
        "de": "Keine Ereignisse gefunden.",
    },
    "cam_list_error": {
        "nb": "Kunne ikke hente kameraliste fra Frigate: {error}",
        "en": "Could not fetch camera list from Frigate: {error}",
        "de": "Kameraliste von Frigate konnte nicht abgerufen werden: {error}",
    },
    "cam_list_header": {
        "nb": "Kameraer ({count}):",
        "en": "Cameras ({count}):",
        "de": "Kameras ({count}):",
    },
    "cam_no_analysis": {
        "nb": "Ingen analyserte kamerahendelser funnet ennå.",
        "en": "No analyzed camera events found yet.",
        "de": "Noch keine analysierten Kameraereignisse gefunden.",
    },
    "cam_analysis_log_error": {
        "nb": "Kunne ikke lese analyse-logg: {error}",
        "en": "Could not read analysis log: {error}",
        "de": "Analyse-Protokoll konnte nicht gelesen werden: {error}",
    },
    "cam_no_analysis_in_log": {
        "nb": "Ingen analyserte hendelser funnet i loggen.",
        "en": "No analyzed events found in the log.",
        "de": "Keine analysierten Ereignisse im Protokoll gefunden.",
    },
    "cam_event_id_required": {
        "nb": "vis_hendelse krever 'event_id'. Bruk action='analyze' for å se tilgjengelige event_id-er.",
        "en": "vis_hendelse requires 'event_id'. Use action='analyze' to see available event_ids.",
        "de": "vis_hendelse erfordert 'event_id'. Verwende action='analyze', um verfügbare event_ids zu sehen.",
    },
    "cam_snapshot_not_found": {
        "nb": "Fant ikke bilde for event_id '{event_id}' — verken i lokal cache eller live fra Frigate. Hendelsen er kanskje for gammel eller utløpt.",
        "en": "No image found for event_id '{event_id}' — neither in local cache nor live from Frigate. The event may be too old or expired.",
        "de": "Kein Bild für event_id '{event_id}' gefunden — weder im lokalen Cache noch live von Frigate. Das Ereignis ist möglicherweise zu alt oder abgelaufen.",
    },
    "cam_snapshot_read_error": {
        "nb": "Kunne ikke lese snapshot: {error}",
        "en": "Could not read snapshot: {error}",
        "de": "Snapshot konnte nicht gelesen werden: {error}",
    },
    "cam_empty_vlm_response": {
        "nb": "Fikk tomt svar fra VLM.",
        "en": "Received empty response from VLM.",
        "de": "Leere Antwort von VLM erhalten.",
    },
    "cam_vlm_error": {
        "nb": "VLM-analyse feilet: {error}",
        "en": "VLM analysis failed: {error}",
        "de": "VLM-Analyse fehlgeschlagen: {error}",
    },
    "cam_prompt_all": {
        "nb": (
            "Du ser bilder fra flere overvåkningskameraer. "
            "Beskriv hvert bilde kort: hva skjer, er det personer, kjøretøy eller hendelser? "
            "Angi kameranavnet i svaret."
        ),
        "en": (
            "You are looking at images from multiple surveillance cameras. "
            "Briefly describe each image: what is happening, are there people, vehicles or events? "
            "Include the camera name in your answer."
        ),
        "de": (
            "Du siehst Bilder von mehreren Überwachungskameras. "
            "Beschreibe jedes Bild kurz: Was passiert, gibt es Personen, Fahrzeuge oder Ereignisse? "
            "Nenne den Kameranamen in deiner Antwort."
        ),
    },
    "cam_prefix_all": {
        "nb": "Kameraer: {cam_list}.\n",
        "en": "Cameras: {cam_list}.\n",
        "de": "Kameras: {cam_list}.\n",
    },
    "cam_prompt_single": {
        "nb": "Beskriv hva du ser på bildet. Nevn personer, kjøretøy, dyr og eventuelle hendelser.",
        "en": "Describe what you see in the image. Mention any people, vehicles, animals and events.",
        "de": "Beschreibe, was du auf dem Bild siehst. Nenne Personen, Fahrzeuge, Tiere und Ereignisse.",
    },
    "cam_prompt_show_event": {
        "nb": "Du ser et lagret Frigate-kameraopptak (event_id: {event_id}).",
        "en": "You are looking at a stored Frigate camera recording (event_id: {event_id}).",
        "de": "Du siehst eine gespeicherte Frigate-Kameraaufnahme (event_id: {event_id}).",
    },
    "cam_prompt_show_event_prior_analysis": {
        "nb": "\n\nDen automatiske analysen sa:\n{analysis}",
        "en": "\n\nThe automatic analysis said:\n{analysis}",
        "de": "\n\nDie automatische Analyse ergab:\n{analysis}",
    },
    "cam_prompt_show_event_question": {
        "nb": "\n\nBeskriv hva du ser på bildet og om den lagrede analysen stemmer. Svar på norsk.",
        "en": "\n\nDescribe what you see in the image and whether the stored analysis is accurate.",
        "de": "\n\nBeschreibe, was du auf dem Bild siehst, und ob die gespeicherte Analyse korrekt ist.",
    },
    # ── Memory ───────────────────────────────────────────────────────────────
    "mem_no_stm_history": {
        "nb": "Ingen STM-historikk funnet ennå.",
        "en": "No STM history found yet.",
        "de": "Noch keine STM-Geschichte gefunden.",
    },
    "mem_no_stm_snapshots": {
        "nb": "Ingen daglige STM-snapshots lagret ennå.",
        "en": "No daily STM snapshots saved yet.",
        "de": "Noch keine täglichen STM-Snapshots gespeichert.",
    },
    "mem_stm_dates": {
        "nb": "Tilgjengelige STM-datoer:",
        "en": "Available STM dates:",
        "de": "Verfügbare STM-Daten:",
    },
    "mem_no_stm_for_date": {
        "nb": "Ingen STM-snapshot for {date}. Tilgjengelige: {tip}",
        "en": "No STM snapshot for {date}. Available: {tip}",
        "de": "Kein STM-Snapshot für {date}. Verfügbar: {tip}",
    },
    "mem_stm_read_error": {
        "nb": "Kunne ikke lese STM-snapshot for {date}: {error}",
        "en": "Could not read STM snapshot for {date}: {error}",
        "de": "STM-Snapshot für {date} konnte nicht gelesen werden: {error}",
    },
    "mem_admin_only_tool": {
        "nb": "Tankehistorikken og de indre tankene kan inneholde andre brukeres samtaler, så bare en administrator kan lese dem.",
        "en": "The thought history and inner thoughts can contain other users' conversations, so only an administrator can read them.",
        "de": "Der Gedankenverlauf und die inneren Gedanken können Gespräche anderer Nutzer enthalten, daher kann nur ein Administrator sie lesen.",
    },
    "mem_no_inner_thoughts": {
        "nb": "Ingen indre tanker akkumulert ennå.",
        "en": "No inner thoughts accumulated yet.",
        "de": "Noch keine inneren Gedanken angesammelt.",
    },
    "mem_inner_thoughts_error": {
        "nb": "Kunne ikke lese indre tanker: {error}",
        "en": "Could not read inner thoughts: {error}",
        "de": "Innere Gedanken konnten nicht gelesen werden: {error}",
    },
    "mem_no_reflection": {
        "nb": "Fant ingen refleksjon for {date}. Tilgjengelige datoer: {dates}.",
        "en": "No reflection found for {date}. Available dates: {dates}.",
        "de": "Keine Reflexion für {date} gefunden. Verfügbare Daten: {dates}.",
    },
    "mem_no_reflection_file": {
        "nb": "Ingen refleksjonsfil funnet.",
        "en": "No reflection file found.",
        "de": "Keine Reflexionsdatei gefunden.",
    },
    "mem_reflection_error": {
        "nb": "Kunne ikke lese refleksjonsfil: {error}",
        "en": "Could not read reflection file: {error}",
        "de": "Reflexionsdatei konnte nicht gelesen werden: {error}",
    },
    "mem_no_dev_meeting": {
        "nb": "Fant ingen utviklingsmøte for {date}. Tilgjengelige datoer: {dates}.",
        "en": "No developer meeting found for {date}. Available dates: {dates}.",
        "de": "Kein Entwicklertreffen für {date} gefunden. Verfügbare Daten: {dates}.",
    },
    "mem_no_dev_meeting_file": {
        "nb": "Ingen utviklingsmøtefil funnet.",
        "en": "No developer meeting file found.",
        "de": "Keine Entwicklertreffen-Datei gefunden.",
    },
    "mem_dev_meeting_error": {
        "nb": "Kunne ikke lese utviklingsmøtefil: {error}",
        "en": "Could not read developer meeting file: {error}",
        "de": "Entwicklertreffen-Datei konnte nicht gelesen werden: {error}",
    },
    "mem_empty_query": {
        "nb": "Feil: spørsmål kan ikke være tomt.",
        "en": "Error: query cannot be empty.",
        "de": "Fehler: Anfrage darf nicht leer sein.",
    },
    "mem_no_results": {
        "nb": "Fant ingenting i minnet om '{query}'.",
        "en": "Nothing found in memory about '{query}'.",
        "de": "Nichts im Gedächtnis zu '{query}' gefunden.",
    },
    "mem_search_results": {
        "nb": "Samtaler i langtidsminnet — viser {count} av {total} treff (flest søkeord først, så nyeste):",
        "en": "Conversations in long-term memory — showing {count} of {total} hits (most keywords first, then newest):",
        "de": "Gespräche im Langzeitgedächtnis — zeige {count} von {total} Treffern (meiste Suchwörter zuerst, dann neueste):",
    },
    "mem_episode_header": {
        "nb": "Episoder fra episodeminnet (sammendrag, {count} nærmeste i betydning):",
        "en": "Episodes from episodic memory (summaries, {count} closest in meaning):",
        "de": "Episoden aus dem Episodengedächtnis (Zusammenfassungen, {count} bedeutungsnächste):",
    },
    "mem_unverified_user": {
        "nb": "Bruker",
        "en": "User",
        "de": "Nutzer",
    },
    "mem_no_ids": {
        "nb": "Ingen IDer oppgitt.",
        "en": "No IDs provided.",
        "de": "Keine IDs angegeben.",
    },
    "mem_mark_error": {
        "nb": "Kunne ikke merke interaksjoner: {error}",
        "en": "Could not mark interactions: {error}",
        "de": "Interaktionen konnten nicht markiert werden: {error}",
    },
    "mem_no_unverified": {
        "nb": "Ingen ubekreftede interaksjoner funnet — du er à jour!",
        "en": "No unverified interactions found — you are up to date!",
        "de": "Keine unbestätigten Interaktionen gefunden — du bist auf dem neuesten Stand!",
    },
    "mem_unverified_header": {
        "nb": "Ubekreftede interaksjoner — viser {start}–{end} av {total}, nyeste først (de ubekreftede spenner {first} til {last}):",
        "en": "Unverified interactions — showing {start}–{end} of {total}, newest first (the unverified span {first} to {last}):",
        "de": "Unbestätigte Interaktionen — zeige {start}–{end} von {total}, neueste zuerst (die unbestätigten reichen von {first} bis {last}):",
    },
    "mem_unverified_error": {
        "nb": "Kunne ikke hente ubekreftede interaksjoner: {error}",
        "en": "Could not fetch unverified interactions: {error}",
        "de": "Unbestätigte Interaktionen konnten nicht abgerufen werden: {error}",
    },
    # ── Agents ───────────────────────────────────────────────────────────────
    "agent_empty_query": {
        "nb": "Feil: spørsmål kan ikke være tomt.",
        "en": "Error: query cannot be empty.",
        "de": "Fehler: Anfrage darf nicht leer sein.",
    },
    "agent_argus_embedding_off": {
        "nb": "Argus-søk er av på denne installasjonen: det trenger embedding-tjenesten (BGE-M3), som ikke er slått på. Den slås på under Innstillinger → LLM/Modeller → «Embedding (BGE-M3)».",
        "en": "Argus search is off on this install: it needs the embedding service (BGE-M3), which is not switched on. Switch it on under Settings → LLM / Models → «Embedding (BGE-M3)».",
        "de": "Die Argus-Suche ist auf dieser Installation aus: sie braucht den Embedding-Dienst (BGE-M3), der nicht eingeschaltet ist. Einschalten unter Einstellungen → LLM / Modelle → «Embedding (BGE-M3)».",
    },
    "agent_argus_unavailable": {
        "nb": "Argus utilgjengelig: {error}",
        "en": "Argus unavailable: {error}",
        "de": "Argus nicht verfügbar: {error}",
    },
    "agent_no_argus_events": {
        "nb": "Fant ingen hendelser for '{query}' i systemloggen.",
        "en": "No events found for '{query}' in the system log.",
        "de": "Keine Ereignisse für '{query}' im Systemprotokoll gefunden.",
    },
    "agent_argus_results": {
        "nb": "Fant {count} hendelser i systemloggen:",
        "en": "Found {count} events in the system log:",
        "de": "{count} Ereignisse im Systemprotokoll gefunden:",
    },
    "agent_mechanic_disabled": {
        "nb": "Mechanic er deaktivert. Aktiver den under Innstillinger → LLM/Modeller.",
        "en": "Mechanic is disabled. Enable it under Settings → LLM/Models.",
        "de": "Mechanic ist deaktiviert. Aktiviere es unter Einstellungen → LLM/Modelle.",
    },
    "agent_job_done_notification": {
        "nb": "Mechanic er ferdig med oppgaven. Her er hva den fant:\n\n{result}",
        "en": "Mechanic has finished the task. Here's what it found:\n\n{result}",
        "de": "Mechanic hat die Aufgabe abgeschlossen. Hier ist das Ergebnis:\n\n{result}",
    },
    "agent_job_error_notification": {
        "nb": "Mechanic støtte på en feil under oppgaven:\n\n{result}",
        "en": "Mechanic ran into an error during the task:\n\n{result}",
        "de": "Mechanic ist bei der Aufgabe auf einen Fehler gestoßen:\n\n{result}",
    },
    "agent_delegate_started": {
        "nb": (
            "Jobb startet, job_id={job_id}. Mechanic jobber i bakgrunnen. Du blir varslet "
            "proaktivt i chatten straks den er ferdig — ikke bare ved neste samtalerunde. "
            "Bekreft kort til brukeren, i din egen naturlige stil, at du følger opp og "
            "kommer tilbake med svaret — ikke beskriv selve varslingsmekanismen. Husk at "
            "dette er oppgaven brukeren nettopp ba om. (Manuell poll fortsatt mulig: "
            "mechanic(action='result', job_id='{job_id}'))."
        ),
        "en": (
            "Job started, job_id={job_id}. Mechanic is working in the background. You'll be "
            "notified proactively in the chat as soon as it's done — not just at the next "
            "turn. Briefly acknowledge to the user, in your own natural voice, that you're "
            "following up and will come back with the answer — don't describe the "
            "notification mechanism itself. Remember this is the task the user just asked "
            "about. (Manual poll still available: mechanic(action='result', "
            "job_id='{job_id}'))."
        ),
        "de": (
            "Job gestartet, job_id={job_id}. Mechanic arbeitet im Hintergrund. Du wirst "
            "proaktiv im Chat benachrichtigt, sobald es fertig ist — nicht erst bei der "
            "nächsten Runde. Bestätige dem Nutzer kurz, in deiner eigenen natürlichen Art, "
            "dass du dranbleibst und mit der Antwort zurückkommst — beschreibe nicht den "
            "Benachrichtigungsmechanismus selbst. Denk daran, dass dies die Aufgabe ist, "
            "um die der Nutzer gerade gebeten hat. (Manuelles Abfragen weiterhin möglich: "
            "mechanic(action='result', job_id='{job_id}'))."
        ),
    },
    "agent_mechanic_nothing": {
        "nb": "Mechanic fant ingenting.",
        "en": "Mechanic found nothing.",
        "de": "Mechanic hat nichts gefunden.",
    },
    "agent_mechanic_unavailable": {
        "nb": "Mechanic ikke tilgjengelig: {error}",
        "en": "Mechanic not available: {error}",
        "de": "Mechanic nicht verfügbar: {error}",
    },
    "agent_mechanic_search_failed": {
        "nb": "Mechanic søk feilet: {error}",
        "en": "Mechanic search failed: {error}",
        "de": "Mechanic-Suche fehlgeschlagen: {error}",
    },
    # ── Personality ──────────────────────────────────────────────────────────
    "pers_empty_observation": {
        "nb": "Feil: observasjon kan ikke være tom.",
        "en": "Error: observation cannot be empty.",
        "de": "Fehler: Beobachtung darf nicht leer sein.",
    },
    "pers_noted": {
        "nb": "Notert.",
        "en": "Noted.",
        "de": "Notiert.",
    },
    "pers_self_write_error": {
        "nb": "Kunne ikke skrive til selvbilde: {error}",
        "en": "Could not write to self-image: {error}",
        "de": "Selbstbild konnte nicht geschrieben werden: {error}",
    },
    "pers_fragment_not_found": {
        "nb": "Fant ingen linjer med '{fragment}'.",
        "en": "No lines found containing '{fragment}'.",
        "de": "Keine Zeilen mit '{fragment}' gefunden.",
    },
    "pers_lines_deleted": {
        "nb": "{count} linje(r) slettet.",
        "en": "{count} line(s) deleted.",
        "de": "{count} Zeile(n) gelöscht.",
    },
    "pers_delete_error": {
        "nb": "Kunne ikke slette fra selvbilde: {error}",
        "en": "Could not delete from self-image: {error}",
        "de": "Aus Selbstbild konnte nicht gelöscht werden: {error}",
    },
    "pers_fragment_new_required": {
        "nb": "Feil: både fragment og ny_tekst må fylles ut.",
        "en": "Error: both fragment and ny_tekst must be provided.",
        "de": "Fehler: sowohl fragment als auch ny_tekst müssen angegeben werden.",
    },
    "pers_lines_updated": {
        "nb": "{count} linje(r) oppdatert.",
        "en": "{count} line(s) updated.",
        "de": "{count} Zeile(n) aktualisiert.",
    },
    "pers_edit_error": {
        "nb": "Kunne ikke redigere selvbilde: {error}",
        "en": "Could not edit self-image: {error}",
        "de": "Selbstbild konnte nicht bearbeitet werden: {error}",
    },
    "pers_self_empty": {
        "nb": "Selvbilde-filen er tom.",
        "en": "The self-image file is empty.",
        "de": "Die Selbstbild-Datei ist leer.",
    },
    "pers_self_read_error": {
        "nb": "Kunne ikke lese selvbilde-filen: {error}",
        "en": "Could not read self-image file: {error}",
        "de": "Selbstbild-Datei konnte nicht gelesen werden: {error}",
    },
    "pers_no_user": {
        "nb": "Ingen innlogget bruker.",
        "en": "No user logged in.",
        "de": "Kein Benutzer angemeldet.",
    },
    "pers_empty_curiosity": {
        "nb": "Feil: nysgjerrighet kan ikke være tom.",
        "en": "Error: curiosity cannot be empty.",
        "de": "Fehler: Neugier darf nicht leer sein.",
    },
    "pers_curiosity_updated": {
        "nb": "Nysgjerrighet oppdatert.",
        "en": "Curiosity updated.",
        "de": "Neugier aktualisiert.",
    },
    "pers_curiosity_error": {
        "nb": "Kunne ikke oppdatere nysgjerrighet: {error}",
        "en": "Could not update curiosity: {error}",
        "de": "Neugier konnte nicht aktualisiert werden: {error}",
    },
    "pers_no_user_observation": {
        "nb": "Ingen innlogget bruker — kan ikke lagre brukerobservasjon.",
        "en": "No user logged in — cannot save user observation.",
        "de": "Kein Benutzer angemeldet — Benutzerbeobachtung kann nicht gespeichert werden.",
    },
    "pers_profile_write_error": {
        "nb": "Kunne ikke skrive til brukerprofil: {error}",
        "en": "Could not write to user profile: {error}",
        "de": "Benutzerprofil konnte nicht geschrieben werden: {error}",
    },
    "pers_no_user_profile": {
        "nb": "Ingen innlogget bruker — kan ikke oppdatere profil.",
        "en": "No user logged in — cannot update profile.",
        "de": "Kein Benutzer angemeldet — Profil kann nicht aktualisiert werden.",
    },
    "pers_section_field_required": {
        "nb": "Feil: seksjon og felt må fylles ut.",
        "en": "Error: section and field must be provided.",
        "de": "Fehler: Abschnitt und Feld müssen angegeben werden.",
    },
    "pers_profile_field_error": {
        "nb": "Kunne ikke oppdatere profilfelt: {error}",
        "en": "Could not update profile field: {error}",
        "de": "Profilfeld konnte nicht aktualisiert werden: {error}",
    },
    "pers_no_profile_data": {
        "nb": "Ingen profildata registrert ennå.",
        "en": "No profile data registered yet.",
        "de": "Noch keine Profildaten registriert.",
    },
    "pers_no_observations_to_delete": {
        "nb": "Ingen observasjoner å slette fra.",
        "en": "No observations to delete from.",
        "de": "Keine Beobachtungen zum Löschen vorhanden.",
    },
    "pers_profile_delete_error": {
        "nb": "Kunne ikke slette fra brukerprofil: {error}",
        "en": "Could not delete from user profile: {error}",
        "de": "Aus Benutzerprofil konnte nicht gelöscht werden: {error}",
    },
    "pers_no_observations_to_edit": {
        "nb": "Ingen observasjoner å redigere.",
        "en": "No observations to edit.",
        "de": "Keine Beobachtungen zum Bearbeiten vorhanden.",
    },
    "pers_profile_edit_error": {
        "nb": "Kunne ikke redigere brukerprofil: {error}",
        "en": "Could not edit user profile: {error}",
        "de": "Benutzerprofil konnte nicht bearbeitet werden: {error}",
    },
    "pers_house_update_required": {
        "nb": "update_house krever 'field' og 'value'.",
        "en": "update_house requires 'field' and 'value'.",
        "de": "update_house erfordert 'field' und 'value'.",
    },
    "pers_house_update_error": {
        "nb": "Feil ved oppdatering av hus-profil: {error}",
        "en": "Error updating house profile: {error}",
        "de": "Fehler beim Aktualisieren des Hausprofils: {error}",
    },
    # ── World ────────────────────────────────────────────────────────────────
    "world_no_vars": {
        "nb": "Ingen variabler lagret ennå.",
        "en": "No variables saved yet.",
        "de": "Noch keine Variablen gespeichert.",
    },
    "world_var_not_found": {
        "nb": "Ingen variabel med nøkkel '{key}'.",
        "en": "No variable with key '{key}'.",
        "de": "Keine Variable mit Schlüssel '{key}'.",
    },
    "world_all_vars": {
        "nb": "Alle variabler:",
        "en": "All variables:",
        "de": "Alle Variablen:",
    },
    "world_key_value_required": {
        "nb": "Feil: nokkel og verdi må fylles ut.",
        "en": "Error: key and value must be provided.",
        "de": "Fehler: Schlüssel und Wert müssen angegeben werden.",
    },
    "world_var_set": {
        "nb": "Variabel satt: {key} = {value}",
        "en": "Variable set: {key} = {value}",
        "de": "Variable gesetzt: {key} = {value}",
    },
    "world_key_required": {
        "nb": "Feil: nokkel mangler.",
        "en": "Error: key is missing.",
        "de": "Fehler: Schlüssel fehlt.",
    },
    "world_var_key_not_found": {
        "nb": "Fant ingen variabel med nøkkel '{key}'.",
        "en": "No variable found with key '{key}'.",
        "de": "Keine Variable mit Schlüssel '{key}' gefunden.",
    },
    "world_var_deleted": {
        "nb": "Variabel '{key}' slettet.",
        "en": "Variable '{key}' deleted.",
        "de": "Variable '{key}' gelöscht.",
    },
    "world_vars_header": {
        "nb": "Variabler ({count}):",
        "en": "Variables ({count}):",
        "de": "Variablen ({count}):",
    },
    "world_file_empty": {
        "nb": "Verden-filen er tom.",
        "en": "The world file is empty.",
        "de": "Die Weltdatei ist leer.",
    },
    "world_read_error": {
        "nb": "Kunne ikke lese verden-filen: {error}",
        "en": "Could not read world file: {error}",
        "de": "Weltdatei konnte nicht gelesen werden: {error}",
    },
    "world_cat_field_required": {
        "nb": "Feil: kategori og felt må fylles ut.",
        "en": "Error: category and field must be provided.",
        "de": "Fehler: Kategorie und Feld müssen angegeben werden.",
    },
    "world_category_created": {
        "nb": "Ny kategori '{category}' opprettet med felt '{field}'.",
        "en": "New category '{category}' created with field '{field}'.",
        "de": "Neue Kategorie '{category}' mit Feld '{field}' erstellt.",
    },
    "world_field_updated": {
        "nb": "Oppdatert: {field} = {value}",
        "en": "Updated: {field} = {value}",
        "de": "Aktualisiert: {field} = {value}",
    },
    "world_update_error": {
        "nb": "Kunne ikke oppdatere verden-filen: {error}",
        "en": "Could not update world file: {error}",
        "de": "Weltdatei konnte nicht aktualisiert werden: {error}",
    },
    "world_empty_text": {
        "nb": "Feil: tekst kan ikke være tom.",
        "en": "Error: text cannot be empty.",
        "de": "Fehler: Text darf nicht leer sein.",
    },
    "world_added": {
        "nb": "Lagt til.",
        "en": "Added.",
        "de": "Hinzugefügt.",
    },
    "world_write_error": {
        "nb": "Kunne ikke skrive til verden-filen: {error}",
        "en": "Could not write to world file: {error}",
        "de": "Weltdatei konnte nicht geschrieben werden: {error}",
    },
    "world_empty_fragment": {
        "nb": "Feil: fragment kan ikke være tomt.",
        "en": "Error: fragment cannot be empty.",
        "de": "Fehler: Fragment darf nicht leer sein.",
    },
    "world_fragment_not_found": {
        "nb": "Fant ingen linjer med '{fragment}'.",
        "en": "No lines found containing '{fragment}'.",
        "de": "Keine Zeilen mit '{fragment}' gefunden.",
    },
    "world_lines_deleted": {
        "nb": "{count} linje(r) slettet.",
        "en": "{count} line(s) deleted.",
        "de": "{count} Zeile(n) gelöscht.",
    },
    "world_delete_error": {
        "nb": "Kunne ikke slette fra verden-filen: {error}",
        "en": "Could not delete from world file: {error}",
        "de": "Aus Weltdatei konnte nicht gelöscht werden: {error}",
    },
    "world_fragment_new_required": {
        "nb": "Feil: både fragment og ny_tekst må fylles ut.",
        "en": "Error: both fragment and ny_tekst must be provided.",
        "de": "Fehler: sowohl fragment als auch ny_tekst müssen angegeben werden.",
    },
    "world_lines_updated": {
        "nb": "{count} linje(r) oppdatert.",
        "en": "{count} line(s) updated.",
        "de": "{count} Zeile(n) aktualisiert.",
    },
    "world_edit_error": {
        "nb": "Kunne ikke redigere verden-filen: {error}",
        "en": "Could not edit world file: {error}",
        "de": "Weltdatei konnte nicht bearbeitet werden: {error}",
    },
    # ── Library ──────────────────────────────────────────────────────────────
    "lib_empty_query": {
        "nb": "Feil: spørsmål kan ikke være tomt.",
        "en": "Error: query cannot be empty.",
        "de": "Fehler: Anfrage darf nicht leer sein.",
    },
    "lib_online_disabled": {
        "nb": "Online LLM er deaktivert. Aktiver den under Innstillinger → LLM → Sky-modell.",
        "en": "Online LLM is disabled. Enable it under Settings → LLM → Cloud model.",
        "de": "Online-LLM ist deaktiviert. Aktiviere es unter Einstellungen → LLM → Cloud-Modell.",
    },
    "lib_library_disabled": {
        "nb": "Miss Library er deaktivert. Aktiver den under Innstillinger → LLM/Modeller.",
        "en": "Miss Library is disabled. Enable it under Settings → LLM/Models.",
        "de": "Fräulein Library ist deaktiviert. Aktiviere sie unter Einstellungen → LLM/Modelle.",
    },
    "lib_online_no_answer": {
        "nb": "Miss Library Online fant ingen svar.",
        "en": "Miss Library Online found no answer.",
        "de": "Fräulein Library Online hat keine Antwort gefunden.",
    },
    "lib_online_unavailable": {
        "nb": "Miss Library Online ikke tilgjengelig: {error}",
        "en": "Miss Library Online not available: {error}",
        "de": "Fräulein Library Online nicht verfügbar: {error}",
    },
    "lib_no_answer": {
        "nb": "Miss Library fant ingenting.",
        "en": "Miss Library found nothing.",
        "de": "Fräulein Library hat nichts gefunden.",
    },
    "lib_unavailable": {
        "nb": "Miss Library ikke tilgjengelig: {error}",
        "en": "Miss Library not available: {error}",
        "de": "Fräulein Library nicht verfügbar: {error}",
    },
    "lib_empty_title": {
        "nb": "Feil: tittel kan ikke være tom.",
        "en": "Error: title cannot be empty.",
        "de": "Fehler: Titel darf nicht leer sein.",
    },
    "lib_article_not_found": {
        "nb": "Ingen artikkel funnet med tittelen «{title}».",
        "en": "No article found with the title «{title}».",
        "de": "Kein Artikel mit dem Titel «{title}» gefunden.",
    },
    "lib_article_error": {
        "nb": "Kunne ikke hente artikkel: {error}",
        "en": "Could not fetch article: {error}",
        "de": "Artikel konnte nicht abgerufen werden: {error}",
    },
    "lib_empty_url": {
        "nb": "Feil: url kan ikke være tom.",
        "en": "Error: url cannot be empty.",
        "de": "Fehler: URL darf nicht leer sein.",
    },
    "lib_url_no_answer": {
        "nb": "Miss Library fant ingenting på den adressen.",
        "en": "Miss Library found nothing at that address.",
        "de": "Fräulein Library hat an dieser Adresse nichts gefunden.",
    },
    "lib_url_error": {
        "nb": "Kunne ikke hente URL: {error}",
        "en": "Could not fetch URL: {error}",
        "de": "URL konnte nicht abgerufen werden: {error}",
    },
    "lib_url_not_trusted": {
        "nb": "Domenet i '{url}' er ikke på listen over vitlistede nettsider.",
        "en": "The domain in '{url}' is not on the whitelisted websites list.",
        "de": "Die Domain in '{url}' steht nicht auf der Liste der Whitelist-Webseiten.",
    },
    "lib_url_direct_no_content": {
        "nb": "Klarte ikke å hente innhold fra adressen (tom respons).",
        "en": "Could not retrieve content from the address (empty response).",
        "de": "Kein Inhalt von der Adresse abrufbar (leere Antwort).",
    },
    "lib_empty_rf_query": {
        "nb": "Feil: query kan ikke være tomt.",
        "en": "Error: query cannot be empty.",
        "de": "Fehler: Anfrage darf nicht leer sein.",
    },
    "lib_empty_model_answer": {
        "nb": "Fikk tomt svar fra modellen.",
        "en": "Received empty response from the model.",
        "de": "Leere Antwort vom Modell erhalten.",
    },
    "lib_rf_error": {
        "nb": "reason_freely feilet: {error}",
        "en": "reason_freely failed: {error}",
        "de": "reason_freely fehlgeschlagen: {error}",
    },
    # ── trace_reader / self-monitoring ──────────────────────────────────────
    "rid_note": {
        "nb": "[System: Din nåværende request-ID er **{rid}**. Bruk inspect_system(action='fetch_trace', rid='{rid}') for å inspisere en tidligere tur. Merk: inspiserer du DENNE pågående turen (samme rid), vil traceen være ufullstendig siden turen ikke er ferdig ennå.]",
        "en": "[System: Your current request ID is **{rid}**. Use inspect_system(action='fetch_trace', rid='{rid}') to inspect a past turn. Note: inspecting THIS in-progress turn (same rid) returns an incomplete trace, since the turn hasn't finished yet.]",
        "de": "[System: Deine aktuelle Request-ID ist **{rid}**. Verwende inspect_system(action='fetch_trace', rid='{rid}') um eine vergangene Runde zu inspizieren. Hinweis: Inspizierst du DIESE laufende Runde (gleiche rid), ist die Trace unvollständig, da die Runde noch nicht abgeschlossen ist.]",
    },
    "inspiser_system_hent_trace_mangler_rid": {
        "nb": "Mangler 'rid'. Bruk din nåværende request-ID (tilgjengelig i konteksten din).",
        "en": "Missing 'rid'. Use your current request ID (available in your context).",
        "de": "Fehlende 'rid'. Verwende deine aktuelle Request-ID (in deinem Kontext verfügbar).",
    },
    "inspiser_system_hent_trace_ikke_funnet": {
        "nb": "Ingen trace funnet for rid '{rid}'.",
        "en": "No trace found for rid '{rid}'.",
        "de": "Keine Trace für rid '{rid}' gefunden.",
    },
    "inspiser_system_trace_mønstre_ingen": {
        "nb": "Ingen traces funnet i loggfilene.",
        "en": "No traces found in log files.",
        "de": "Keine Traces in den Logdateien gefunden.",
    },
    # ── System / shared_tools ────────────────────────────────────────────────
    "sys_invalid_path": {
        "nb": "Mangler eller ugyldig filsti. Oppgi absolutt sti under /kaare/, f.eks. les_fil(sti='/kaare/kaare_api.py'). Bruk liste_filer() for å se hva som finnes.",
        "en": "Missing or invalid file path. Provide an absolute path under /kaare/, e.g. les_fil(sti='/kaare/kaare_api.py'). Use liste_filer() to see what is available.",
        "de": "Fehlender oder ungültiger Dateipfad. Gib einen absoluten Pfad unter /kaare/ an, z.B. les_fil(sti='/kaare/kaare_api.py'). Verwende liste_filer(), um zu sehen was verfügbar ist.",
    },
    "sys_sensitive_file": {
        "nb": "[Feil: filen er sensitiv og kan ikke leses]",
        "en": "[Error: file is sensitive and cannot be read]",
        "de": "[Fehler: Datei ist sensibel und kann nicht gelesen werden]",
    },
    "sys_file_not_found": {
        "nb": "[Finner ikke: {path}]",
        "en": "[Not found: {path}]",
        "de": "[Nicht gefunden: {path}]",
    },
    "sys_path_not_allowed": {
        "nb": "[Feil: kun mapper under /kaare er tillatt]",
        "en": "[Error: only directories under /kaare are allowed]",
        "de": "[Fehler: nur Verzeichnisse unter /kaare sind erlaubt]",
    },
    "sys_no_grep_results": {
        "nb": "[Ingen treff på '{pattern}' i {path}]",
        "en": "[No matches for '{pattern}' in {path}]",
        "de": "[Keine Treffer für '{pattern}' in {path}]",
    },
    "sys_log_not_found": {
        "nb": "Finner ikke: /kaare/logs/{filename}\nTilgjengelige filer:\n{files}",
        "en": "Not found: /kaare/logs/{filename}\nAvailable files:\n{files}",
        "de": "Nicht gefunden: /kaare/logs/{filename}\nVerfügbare Dateien:\n{files}",
    },
    "sys_empty_log": {
        "nb": "[Tom logg]",
        "en": "[Empty log]",
        "de": "[Leeres Protokoll]",
    },
    "sys_file_header": {
        "nb": "[{path} — linjer {from_line}–{to_line} av {total}]\n",
        "en": "[{path} — lines {from_line}–{to_line} of {total}]\n",
        "de": "[{path} — Zeilen {from_line}–{to_line} von {total}]\n",
    },
    "sys_more_lines": {
        "nb": "\n... ({count} linjer gjenstår — bruk from_line={next_line})",
        "en": "\n... ({count} more lines — use from_line={next_line})",
        "de": "\n... ({count} weitere Zeilen — verwende from_line={next_line})",
    },
    "sys_unknown_service": {
        "nb": "[Feil: '{service}' er ikke en kjent Kåre-tjeneste. Tillatte: {allowed}]",
        "en": "[Error: '{service}' is not a known Kåre service. Allowed: {allowed}]",
        "de": "[Fehler: '{service}' ist kein bekannter Kåre-Dienst. Erlaubt: {allowed}]",
    },
    "sys_dev_tools_disabled": {
        "nb": "[Utviklerverktøy er deaktivert. Shell-kommandoer er ikke tilgjengelig. Admin kan aktivere dette under Innstillinger → Sikkerhet i GUI-et.]",
        "en": "[Developer tools are disabled. Shell commands are not available. An admin can enable this under Settings → Security in the GUI.]",
        "de": "[Entwicklerwerkzeuge sind deaktiviert. Shell-Befehle sind nicht verfügbar. Ein Admin kann dies unter Einstellungen → Sicherheit in der GUI aktivieren.]",
    },
    "sys_empty_command": {
        "nb": "[Tom kommando]",
        "en": "[Empty command]",
        "de": "[Leerer Befehl]",
    },
    "sys_no_output": {
        "nb": "[Ingen output]",
        "en": "[No output]",
        "de": "[Keine Ausgabe]",
    },
    "sys_no_git_changes": {
        "nb": "[Ingen ukommitterte endringer]",
        "en": "[No uncommitted changes]",
        "de": "[Keine uncommitteten Änderungen]",
    },
    "sys_no_git_commits": {
        "nb": "[Ingen commits funnet]",
        "en": "[No commits found]",
        "de": "[Keine Commits gefunden]",
    },
    "sys_gpu_unavailable": {
        "nb": "GPU: [nvidia-smi ikke tilgjengelig]",
        "en": "GPU: [nvidia-smi not available]",
        "de": "GPU: [nvidia-smi nicht verfügbar]",
    },
    "sys_res_cpu": {
        "nb": "CPU: last {l1} / {l5} / {l15} (1/5/15 min) på {cores} kjerner",
        "en": "CPU: load {l1} / {l5} / {l15} (1/5/15 min) on {cores} cores",
        "de": "CPU: Last {l1} / {l5} / {l15} (1/5/15 Min.) auf {cores} Kernen",
    },
    "sys_res_ram": {
        "nb": "RAM: brukt {used} GiB av {total} GiB, ledig {free} GiB ({pct_free} % ledig)",
        "en": "RAM: used {used} GiB of {total} GiB, free {free} GiB ({pct_free} % free)",
        "de": "RAM: belegt {used} GiB von {total} GiB, frei {free} GiB ({pct_free} % frei)",
    },
    "sys_res_disk": {
        "nb": "Disk ({path}): brukt {used} GiB av {total} GiB, ledig {free} GiB ({pct_free} % ledig)",
        "en": "Disk ({path}): used {used} GiB of {total} GiB, free {free} GiB ({pct_free} % free)",
        "de": "Festplatte ({path}): belegt {used} GiB von {total} GiB, frei {free} GiB ({pct_free} % frei)",
    },
    "sys_res_gpu": {
        "nb": "GPU {index} ({name}): brukt {used} MiB av {total} MiB, ledig {free} MiB ({pct_free} % ledig), belastning {util} %",
        "en": "GPU {index} ({name}): used {used} MiB of {total} MiB, free {free} MiB ({pct_free} % free), utilisation {util} %",
        "de": "GPU {index} ({name}): belegt {used} MiB von {total} MiB, frei {free} MiB ({pct_free} % frei), Auslastung {util} %",
    },
    "sys_res_gpu_vllm": {
        "nb": "  ↳ vLLM kjører på dette kortet og reserverer minnet på forhånd — et nesten fullt kort er normalt, ikke et problem.",
        "en": "  ↳ vLLM runs on this card and reserves its memory up front — a nearly full card is normal, not a problem.",
        "de": "  ↳ vLLM läuft auf dieser Karte und reserviert den Speicher im Voraus — eine fast volle Karte ist normal, kein Problem.",
    },
    # ── Timer ────────────────────────────────────────────────────────────────
    "timer_empty_prompt": {
        "nb": "Feil: prompt kan ikke være tom.",
        "en": "Error: prompt cannot be empty.",
        "de": "Fehler: Prompt darf nicht leer sein.",
    },
    "timer_invalid_repeat": {
        "nb": "Feil: ugyldig repeat-verdi '{repeat}'. Gyldige: {valid}.",
        "en": "Error: invalid repeat value '{repeat}'. Valid: {valid}.",
        "de": "Fehler: ungültiger repeat-Wert '{repeat}'. Gültig: {valid}.",
    },
    "timer_parse_error": {
        "nb": "Feil: kunne ikke tolke '{at_time}'. Bruk f.eks. '07:30', 'fredag 08:00' eller '2026-05-01 09:00'.",
        "en": "Error: could not parse '{at_time}'. Use e.g. '07:30', 'fredag 08:00' or '2026-05-01 09:00'.",
        "de": "Fehler: '{at_time}' konnte nicht geparst werden. Verwende z.B. '07:30', 'fredag 08:00' oder '2026-05-01 09:00'.",
    },
    "timer_min_seconds": {
        "nb": "Feil: minimum 5 sekunder.",
        "en": "Error: minimum 5 seconds.",
        "de": "Fehler: mindestens 5 Sekunden.",
    },
    "timer_max_one_year": {
        "nb": "Feil: engangs-timer kan ikke settes mer enn ett år frem.",
        "en": "Error: one-time timer cannot be set more than one year ahead.",
        "de": "Fehler: Einmal-Timer kann nicht mehr als ein Jahr im Voraus gesetzt werden.",
    },
    "timer_set": {
        "nb": "Timer satt [{timer_id}]: om {delay} ({local_time}){repeat_str}. Prompt: «{prompt_preview}»",
        "en": "Timer set [{timer_id}]: in {delay} ({local_time}){repeat_str}. Prompt: «{prompt_preview}»",
        "de": "Timer gesetzt [{timer_id}]: in {delay} ({local_time}){repeat_str}. Prompt: «{prompt_preview}»",
    },
    "timer_repeats": {
        "nb": " — gjentar {label}",
        "en": " — repeats {label}",
        "de": " — wiederholt {label}",
    },
    "timer_not_found": {
        "nb": "Ingen aktiv timer med ID '{timer_id}'.",
        "en": "No active timer with ID '{timer_id}'.",
        "de": "Kein aktiver Timer mit ID '{timer_id}'.",
    },
    "timer_cancelled": {
        "nb": "Timer {timer_id} avbrutt{repeat_str}.",
        "en": "Timer {timer_id} cancelled{repeat_str}.",
        "de": "Timer {timer_id} abgebrochen{repeat_str}.",
    },
    "timer_repeat_was": {
        "nb": " (var {label})",
        "en": " (was {label})",
        "de": " (war {label})",
    },
    "timer_none_active": {
        "nb": "Ingen aktive timere.",
        "en": "No active timers.",
        "de": "Keine aktiven Timer.",
    },
    "timer_list_header": {
        "nb": "Aktive timere ({count}):",
        "en": "Active timers ({count}):",
        "de": "Aktive Timer ({count}):",
    },
    "timer_repeating_header": {
        "nb": "  [Gjentakende]",
        "en": "  [Repeating]",
        "de": "  [Wiederholend]",
    },
    "timer_one_time_header": {
        "nb": "  [Engang]",
        "en": "  [One-time]",
        "de": "  [Einmalig]",
    },
    "timer_repeat_hourly": {
        "nb": "hver time",
        "en": "every hour",
        "de": "jede Stunde",
    },
    "timer_repeat_daily": {
        "nb": "daglig",
        "en": "daily",
        "de": "täglich",
    },
    "timer_repeat_weekdays": {
        "nb": "hverdager",
        "en": "weekdays",
        "de": "Wochentage",
    },
    "timer_repeat_weekend": {
        "nb": "helg",
        "en": "weekend",
        "de": "Wochenende",
    },
    "timer_repeat_weekly": {
        "nb": "ukentlig",
        "en": "weekly",
        "de": "wöchentlich",
    },
    "timer_clock": {
        "nb": "Klokka er {time}. Dato: {date}.",
        "en": "The time is {time}. Date: {date}.",
        "de": "Es ist {time} Uhr. Datum: {date}.",
    },
    # ── Notisblokk ───────────────────────────────────────────────────────────
    "nota_empty_text": {
        "nb": "Feil: tekst kan ikke være tom.",
        "en": "Error: text cannot be empty.",
        "de": "Fehler: Text darf nicht leer sein.",
    },
    "nota_noted": {
        "nb": "Notert [{note_id}]: {text}",
        "en": "Noted [{note_id}]: {text}",
        "de": "Notiert [{note_id}]: {text}",
    },
    "nota_write_error": {
        "nb": "Kunne ikke skrive notat: {error}",
        "en": "Could not write note: {error}",
        "de": "Notiz konnte nicht geschrieben werden: {error}",
    },
    "nota_empty": {
        "nb": "Notisblokken er tom.",
        "en": "The notepad is empty.",
        "de": "Der Notizblock ist leer.",
    },
    "nota_no_category": {
        "nb": "Ingen notater i kategori '{category}'.",
        "en": "No notes in category '{category}'.",
        "de": "Keine Notizen in der Kategorie '{category}'.",
    },
    "nota_header": {
        "nb": "Notisblokk ({count} notat{suffix}):",
        "en": "Notepad ({count} note{suffix}):",
        "de": "Notizblock ({count} Notiz{suffix}):",
    },
    "nota_empty_id": {
        "nb": "Feil: notat_id kan ikke være tom.",
        "en": "Error: notat_id cannot be empty.",
        "de": "Fehler: notat_id darf nicht leer sein.",
    },
    "nota_id_not_found": {
        "nb": "Fant ingen notat med id '{note_id}'.",
        "en": "No note found with id '{note_id}'.",
        "de": "Keine Notiz mit id '{note_id}' gefunden.",
    },
    "nota_deleted": {
        "nb": "Notat '{note_id}' slettet.",
        "en": "Note '{note_id}' deleted.",
        "de": "Notiz '{note_id}' gelöscht.",
    },
    "nota_delete_error": {
        "nb": "Kunne ikke slette notat: {error}",
        "en": "Could not delete note: {error}",
        "de": "Notiz konnte nicht gelöscht werden: {error}",
    },
    "nota_already_empty": {
        "nb": "Notisblokken er allerede tom.",
        "en": "The notepad is already empty.",
        "de": "Der Notizblock ist bereits leer.",
    },
    "nota_category_cleared": {
        "nb": "Slettet {count} notat(er) fra kategori '{category}'.",
        "en": "Deleted {count} note(s) from category '{category}'.",
        "de": "{count} Notiz(en) aus der Kategorie '{category}' gelöscht.",
    },
    "nota_category_clear_error": {
        "nb": "Kunne ikke tømme kategori: {error}",
        "en": "Could not clear category: {error}",
        "de": "Kategorie konnte nicht geleert werden: {error}",
    },
    "nota_cleared": {
        "nb": "Notisblokken tømt ({count} notat(er) slettet).",
        "en": "Notepad cleared ({count} note(s) deleted).",
        "de": "Notizblock geleert ({count} Notiz(en) gelöscht).",
    },
    "nota_clear_error": {
        "nb": "Kunne ikke tømme notisblokken: {error}",
        "en": "Could not clear notepad: {error}",
        "de": "Notizblock konnte nicht geleert werden: {error}",
    },
    "nota_marked_important": {
        "nb": "Notat '{note_id}' markert som viktig — tas opp i neste refleksjonsmøte.",
        "en": "Note '{note_id}' marked important — will be picked up at the next reflection meeting.",
        "de": "Notiz '{note_id}' als wichtig markiert — wird beim nächsten Reflexionstreffen behandelt.",
    },
    "nota_unmarked_important": {
        "nb": "Notat '{note_id}' er ikke lenger markert som viktig.",
        "en": "Note '{note_id}' is no longer marked important.",
        "de": "Notiz '{note_id}' ist nicht mehr als wichtig markiert.",
    },
    # ── executor.py (misc) ───────────────────────────────────────────────────
    "exec_announce_sent": {
        "nb": "Kunngjøring sendt til {target}{vol_label}.",
        "en": "Announcement sent to {target}{vol_label}.",
        "de": "Ankündigung an {target}{vol_label} gesendet.",
    },
    "exec_announce_all_rooms": {
        "nb": "alle rom",
        "en": "all rooms",
        "de": "alle Räume",
    },
    "exec_announce_volume_label": {
        "nb": " (volum {pct}%)",
        "en": " (volume {pct}%)",
        "de": " (Lautstärke {pct}%)",
    },
    "exec_image_disabled": {
        "nb": "Bilderedigering er deaktivert. Aktiver den under Innstillinger → LLM/Modeller.",
        "en": "Image editing is disabled. Enable it under Settings → LLM/Models.",
        "de": "Bildbearbeitung ist deaktiviert. Aktiviere sie unter Einstellungen → LLM/Modelle.",
    },
    "exec_image_no_prompt": {
        "nb": "Oppgi en beskrivelse av bildet du vil lage.",
        "en": "Please provide a description of the image you want to create.",
        "de": "Bitte gib eine Beschreibung des Bildes an, das du erstellen möchtest.",
    },
    "exec_image_no_input": {
        "nb": "Edit-modus krever et input-bilde (image_b64).",
        "en": "Edit mode requires an input image (image_b64).",
        "de": "Bearbeitungsmodus erfordert ein Eingabebild (image_b64).",
    },
    "exec_image_failed": {
        "nb": "Bildegenerering feilet: {error}.",
        "en": "Image generation failed: {error}.",
        "de": "Bildgenerierung fehlgeschlagen: {error}.",
    },
    "exec_image_ready": {
        "nb": "Bildet er klart: /api/image/{image_id}",
        "en": "Image is ready: /api/image/{image_id}",
        "de": "Bild ist fertig: /api/image/{image_id}",
    },
    "exec_image_not_found": {
        "nb": "Bilde '{image_id}' ikke funnet.",
        "en": "Image '{image_id}' not found.",
        "de": "Bild '{image_id}' nicht gefunden.",
    },
    "exec_no_images": {
        "nb": "Ingen bilder funnet for {uid} ({folder}).",
        "en": "No images found for {uid} ({folder}).",
        "de": "Keine Bilder für {uid} ({folder}) gefunden.",
    },
    "exec_images_list": {
        "nb": "Bilder for {uid}:",
        "en": "Images for {uid}:",
        "de": "Bilder für {uid}:",
    },
    # ── Media ────────────────────────────────────────────────────────────────
    "media_no_query": {
        "nb": "Mangler søketekst (query).",
        "en": "Missing search text (query).",
        "de": "Suchtext (query) fehlt.",
    },
    "media_no_rating_key": {
        "nb": "Mangler rating_key. Søk med plex_search først for å finne id.",
        "en": "Missing rating_key. Search with plex_search first to find the id.",
        "de": "rating_key fehlt. Suche zuerst mit plex_search, um die id zu finden.",
    },
    "media_no_client": {
        "nb": "Mangler 'client' — oppgi rom/enhetsnavn (f.eks. 'verksted'). Sjekk nodes.yaml for gyldige navn.",
        "en": "Missing 'client' — provide room/device name (e.g. 'verksted'). Check nodes.yaml for valid names.",
        "de": "'client' fehlt — gib Raum-/Gerätename an (z.B. 'verksted'). Prüfe nodes.yaml für gültige Namen.",
    },
    "media_no_rating_key_play": {
        "nb": "Mangler 'rating_key' — hent episode-id med plex_episodes først.",
        "en": "Missing 'rating_key' — fetch episode id with plex_episodes first.",
        "de": "'rating_key' fehlt — hole die Episode-id zuerst mit plex_episodes.",
    },
    "media_node_not_found": {
        "nb": "Fant ingen node/enhet som matcher «{client}» i nodes.yaml.",
        "en": "No node/device matching «{client}» found in nodes.yaml.",
        "de": "Kein Knoten/Gerät mit «{client}» in nodes.yaml gefunden.",
    },
    "media_plex_meta_error": {
        "nb": "Kunne ikke hente Plex-metadata for id {key}: {error}",
        "en": "Could not fetch Plex metadata for id {key}: {error}",
        "de": "Plex-Metadaten für id {key} konnten nicht abgerufen werden: {error}",
    },
    "media_no_plex_meta": {
        "nb": "Ingen Plex-metadata funnet for ratingKey {key}.",
        "en": "No Plex metadata found for ratingKey {key}.",
        "de": "Keine Plex-Metadaten für ratingKey {key} gefunden.",
    },
    "media_unsupported_type": {
        "nb": "Støtter ikke medietypen «{media_type}» ennå (kun episode og movie).",
        "en": "Media type «{media_type}» not yet supported (only episode and movie).",
        "de": "Medientyp «{media_type}» noch nicht unterstützt (nur episode und movie).",
    },
    "media_cast_error": {
        "nb": "Feil ved HA cast: {error}",
        "en": "Error during HA cast: {error}",
        "de": "Fehler beim HA-Cast: {error}",
    },
    "media_casting": {
        "nb": "▶ Caster «{label}» til {entity_id} ✅",
        "en": "▶ Casting «{label}» to {entity_id} ✅",
        "de": "▶ Überträgt «{label}» auf {entity_id} ✅",
    },
    "media_ha_response": {
        "nb": "HA svarte: {result}",
        "en": "HA responded: {result}",
        "de": "HA antwortete: {result}",
    },
    "media_mpd_not_running": {
        "nb": "MPD svarer ikke — radioen er sannsynligvis ikke i gang.",
        "en": "MPD not responding — the radio is probably not running.",
        "de": "MPD antwortet nicht — das Radio läuft wahrscheinlich nicht.",
    },
    "media_no_station": {
        "nb": "Mangler 'station' — oppgi navn (f.eks. 'P4', 'NRK P1') eller stream-URL.",
        "en": "Missing 'station' — provide name (e.g. 'P4', 'NRK P1') or stream URL.",
        "de": "'station' fehlt — gib Namen (z.B. 'P4', 'NRK P1') oder Stream-URL an.",
    },
    "media_unknown_station": {
        "nb": "Ukjent stasjon «{station}». Kjente stasjoner: {known}.",
        "en": "Unknown station «{station}». Known stations: {known}.",
        "de": "Unbekannte Station «{station}». Bekannte Stationen: {known}.",
    },
    "media_playing": {
        "nb": "Spiller nå: {station}",
        "en": "Now playing: {station}",
        "de": "Spielt jetzt: {station}",
    },
    "media_start_failed": {
        "nb": "Klarte ikke å starte {station}: {error}",
        "en": "Could not start {station}: {error}",
        "de": "{station} konnte nicht gestartet werden: {error}",
    },
    "media_radio_stopped": {
        "nb": "Radioen er stoppet.",
        "en": "The radio has been stopped.",
        "de": "Das Radio wurde gestoppt.",
    },
    "media_no_volume": {
        "nb": "Mangler 'volume' (0–100).",
        "en": "Missing 'volume' (0–100).",
        "de": "'volume' fehlt (0–100).",
    },
    "media_volume_set": {
        "nb": "Radiovolum satt til {vol}%.",
        "en": "Radio volume set to {vol}%.",
        "de": "Radiolautstärke auf {vol}% gesetzt.",
    },
    "media_unknown_action": {
        "nb": "Ukjent media-action: '{action}'.",
        "en": "Unknown media action: '{action}'.",
        "de": "Unbekannte Media-Aktion: '{action}'.",
    },
    "media_radio_status_error": {
        "nb": "Feil ved lesing av radio-status: {error}",
        "en": "Error reading radio status: {error}",
        "de": "Fehler beim Lesen des Radio-Status: {error}",
    },
    "media_radio_volume_error": {
        "nb": "Feil ved endring av volum: {error}",
        "en": "Error changing volume: {error}",
        "de": "Fehler beim Ändern der Lautstärke: {error}",
    },
    "media_radio_stop_error": {
        "nb": "Feil ved stopp av radio: {error}",
        "en": "Error stopping radio: {error}",
        "de": "Fehler beim Stoppen des Radios: {error}",
    },
    "media_radio_start_error": {
        "nb": "Feil ved oppstart av radio: {error}",
        "en": "Error starting radio: {error}",
        "de": "Fehler beim Starten des Radios: {error}",
    },
    # ── Plex ─────────────────────────────────────────────────────────────────
    "plex_unreachable": {
        "nb": "Kunne ikke nå Plex ({error}).",
        "en": "Could not reach Plex ({error}).",
        "de": "Plex nicht erreichbar ({error}).",
    },
    "plex_nothing_playing": {
        "nb": "Ingen spiller noe på Plex akkurat nå.",
        "en": "Nothing is playing on Plex right now.",
        "de": "Auf Plex wird gerade nichts abgespielt.",
    },
    "plex_search_failed": {
        "nb": "Søk feilet ({error}).",
        "en": "Search failed ({error}).",
        "de": "Suche fehlgeschlagen ({error}).",
    },
    "plex_search_header": {
        "nb": "Plex-søk: «{query}»",
        "en": 'Plex search: "{query}"',
        "de": "Plex-Suche: „{query}“",
    },
    "plex_no_clients": {
        "nb": "Ingen Plex-klienter funnet. Åpne Plex-appen på enheten og prøv igjen.",
        "en": "No Plex clients found. Open the Plex app on the device and try again.",
        "de": "Keine Plex-Clients gefunden. Öffne die Plex-App auf dem Gerät und versuche es erneut.",
    },
    "plex_no_clients_hint": {
        "nb": "ingen (åpne Plex-appen på enheten)",
        "en": "none (open the Plex app on the device)",
        "de": "keine (Plex-App auf dem Gerät öffnen)",
    },
    "plex_playing_on": {
        "nb": "▶ Starter avspilling{offset} på «{client}» ✅",
        "en": '▶ Starting playback{offset} on "{client}" ✅',
        "de": "▶ Starte Wiedergabe{offset} auf „{client}“ ✅",
    },
    "plex_session_line": {
        "nb": "  • {user} på {device}: {content}{progress} ({state})",
        "en": "  • {user} on {device}: {content}{progress} ({state})",
        "de": "  • {user} auf {device}: {content}{progress} ({state})",
    },
    # ── Web search ────────────────────────────────────────────────────────────
    "search_empty_query": {
        "nb": "Ingen søketekst oppgitt.",
        "en": "No search query provided.",
        "de": "Kein Suchbegriff angegeben.",
    },
    "search_no_results_any": {
        "nb": "Søket ga ingen brukbare resultater (verken fra godkjente kilder eller åpent nett).",
        "en": "The search gave no usable results (neither from trusted sources nor the open web).",
        "de": "Die Suche ergab keine brauchbaren Ergebnisse (weder aus vertrauenswürdigen Quellen noch aus dem offenen Web).",
    },
    "lib_fetch_source": {
        "nb": "Kilde",
        "en": "Source",
        "de": "Quelle",
    },
    "lib_quote_translate_yourself": {
        "nb": "(originalspråk {src_lang} — oversett selv, og ikke legg til tall eller detaljer som ikke står der)",
        "en": "(original language {src_lang} — translate it yourself, and don't add numbers or details that aren't there)",
        "de": "(Originalsprache {src_lang} — selbst übersetzen, und keine Zahlen oder Details hinzufügen, die nicht dastehen)",
    },
    "lib_retry_hint": {
        "nb": "Du kan sende søket ut igjen med bedre instruks: andre eller flere `keywords` (også på kildens språk), et snevrere `task`, et riktigere `subject` (navnet slik det står på Wikipedia), eller en kortere `query`. Gjør det bare hvis det er verdt det for brukeren — ellers si ærlig hva som mangler.",
        "en": "You can send the search out again with a better instruction: other or more `keywords` (also in the source's language), a narrower `task`, a more accurate `subject` (the name as on Wikipedia), or a shorter `query`. Only do it if it's worth it for the user — otherwise say honestly what is missing.",
        "de": "Du kannst die Suche mit besserer Anweisung erneut losschicken: andere oder mehr `keywords` (auch in der Sprache der Quelle), eine engere `task`, ein genaueres `subject` (der Name wie auf Wikipedia) oder eine kürzere `query`. Tu es nur, wenn es sich für den Nutzer lohnt — sonst sage ehrlich, was fehlt.",
    },
    "lib_assessment_header": {
        "nb": "Hvor sannsynlig (regnet ut av koden fra hvor kildene kommer fra — ikke sjekket av noen; bruk disse ordene, aldri «vet»):",
        "en": "How likely (computed by code from where the sources come from — checked by no one; use these words, never «know»):",
        "de": "Wie wahrscheinlich (vom Code aus der Herkunft der Quellen berechnet — von niemandem geprüft; nutze diese Wörter, nie «weiß»):",
    },
    "lib_conflict": {"nb": "MOTSTRID om", "en": "CONFLICT on", "de": "WIDERSPRUCH zu"},
    "lib_n_sources": {"nb": "{n} kilde(r)", "en": "{n} source(s)", "de": "{n} Quelle(n)"},
    "prob_highly_likely": {
        "nb": "høyst sannsynlig",
        "en": "highly likely",
        "de": "höchst wahrscheinlich",
    },
    "prob_likely": {"nb": "sannsynlig", "en": "likely", "de": "wahrscheinlich"},
    "prob_uncertain": {"nb": "usikkert", "en": "uncertain", "de": "unsicher"},
    "prob_unlikely": {"nb": "lite sannsynlig", "en": "unlikely", "de": "unwahrscheinlich"},
    "tool_stakes_desc": {
        "nb": "Hva står på spill for brukeren? trivial = hverdagslig (oppskrift, trivia); normal = vanlig faktaspørsmål; high = helse, sikkerhet, penger eller juss; extraordinary = en påstand som strider mot etablert kunnskap eller er uavklart i fagmiljøet. Styrer hvor forsiktig svaret må være.",
        "en": "What is at stake for the user? trivial = everyday (recipe, trivia); normal = ordinary factual question; high = health, safety, money or law; extraordinary = a claim against established knowledge or unresolved among experts. Controls how careful the answer must be.",
        "de": "Was steht für den Nutzer auf dem Spiel? trivial = alltäglich (Rezept, Trivia); normal = gewöhnliche Sachfrage; high = Gesundheit, Sicherheit, Geld oder Recht; extraordinary = eine Behauptung gegen gesichertes Wissen oder in der Fachwelt ungeklärt. Steuert, wie vorsichtig die Antwort sein muss.",
    },
    "gen_correction_stakes": {
        "nb": "[SYSTEM: web_search ble IKKE kjørt — stakes mangler. Kall web_search på nytt med stakes = trivial, normal, high (helse, sikkerhet, penger, juss) eller extraordinary (strider mot etablert kunnskap).]",
        "en": "[SYSTEM: web_search was NOT run — stakes is missing. Call web_search again with stakes = trivial, normal, high (health, safety, money, law) or extraordinary (against established knowledge).]",
        "de": "[SYSTEM: web_search wurde NICHT ausgeführt — stakes fehlt. Rufe web_search erneut mit stakes = trivial, normal, high (Gesundheit, Sicherheit, Geld, Recht) oder extraordinary (gegen gesichertes Wissen) auf.]",
    },
    "lib_stakes_high": {
        "nb": "Mye står på spill her: gi bare råd der vurderingen er «høyst sannsynlig». Ellers si hva kildene skriver og hvor sikkert det er, og pek brukeren dit de kan sjekke selv (oppgi kilden).",
        "en": "A lot is at stake here: only give advice where the assessment is «highly likely». Otherwise say what the sources write and how certain it is, and point the user to where they can check themselves (name the source).",
        "de": "Hier steht viel auf dem Spiel: gib nur dort Rat, wo die Einschätzung «höchst wahrscheinlich» ist. Sonst sage, was die Quellen schreiben und wie sicher es ist, und zeige dem Nutzer, wo er selbst prüfen kann (Quelle nennen).",
    },
    "lib_stakes_extraordinary": {
        "nb": "Dette er en ekstraordinær påstand: den regnes som uavklart uansett hvor mange sider som sier det. Si at den er uavklart og hvor påstanden kommer fra.",
        "en": "This is an extraordinary claim: it counts as unresolved however many pages say it. Say it is unresolved and where the claim comes from.",
        "de": "Dies ist eine außergewöhnliche Behauptung: sie gilt als ungeklärt, egal wie viele Seiten sie aufstellen. Sage, dass sie ungeklärt ist und woher die Behauptung stammt.",
    },
    "prob_unresolved": {"nb": "uavklart", "en": "unresolved", "de": "ungeklärt"},
    "tool_library_keywords_desc": {
        "nb": "Ord som trolig står i kildeteksten — for 'law': lovens egne, ofte formelle begreper — f.eks. 'skadeverk' (ikke 'hærverk'), 'øvingskjøring' (ikke 'øvelseskjøring'), 'tre- eller firehjuls moped' / 'klasse AM 147' (ikke 'mopedbil'), 'oppsigelse'. Ta med førerkortklasse, paragraf eller lovnavn når du kjenner dem.",
        "en": "Words likely to appear in the source text — for 'law': the law's own, often formal Norwegian terms — e.g. 'skadeverk' (not 'hærverk'), 'øvingskjøring' (not 'øvelseskjøring'), 'tre- eller firehjuls moped' / 'klasse AM 147' (not 'mopedbil'), 'oppsigelse'. Add the licence class, section or law name when you know it.",
        "de": "Wörter, die wahrscheinlich im Quelltext stehen — für 'law': die eigenen, oft formellen norwegischen Begriffe des Gesetzes — z.B. 'skadeverk' (nicht 'hærverk'), 'øvingskjøring' (nicht 'øvelseskjøring'), 'tre- eller firehjuls moped' / 'klasse AM 147' (nicht 'mopedbil'), 'oppsigelse'. Führerscheinklasse, Paragraph oder Gesetzesname angeben, wenn bekannt.",
    },
    "law_not_enabled": {
        "nb": "Norsk lov (Lovdata) er ikke slått på eller ikke lastet ned ennå. En admin kan slå det på under Innstillinger → Nettsøk/Vær → Norsk lov (Lovdata).",
        "en": "Norwegian law (Lovdata) is not switched on or not downloaded yet. An admin can switch it on under Settings → Web Search & Weather → Norwegian law (Lovdata).",
        "de": "Norwegisches Recht (Lovdata) ist nicht eingeschaltet oder noch nicht heruntergeladen. Ein Admin kann es unter Einstellungen → Websuche & Wetter → Norwegisches Recht (Lovdata) einschalten.",
    },
    "law_no_hits": {
        "nb": "Fant ingen paragrafer i lovene for dette søket.",
        "en": "Found no paragraphs in the laws for this search.",
        "de": "Keine Paragraphen in den Gesetzen für diese Suche gefunden.",
    },
    "lib_law_find_task": {
        "nb": "Din jobb er bare å FINNE: siter ordrett de HELE setningene i paragrafene som trengs for å svare (også unntak og vilkår), én påstand per setning. Ikke trekk konklusjoner og ikke regn ut noe.",
        "en": "Your job is only to FIND: quote verbatim the WHOLE sentences in the sections needed to answer (including exceptions and conditions), one claim per sentence. Do not draw conclusions and do not calculate anything.",
        "de": "Deine Aufgabe ist nur zu FINDEN: zitiere wörtlich die GANZEN Sätze aus den Paragraphen, die für die Antwort nötig sind (auch Ausnahmen und Bedingungen), eine Aussage pro Satz. Keine Schlüsse ziehen und nichts ausrechnen.",
    },
    "lib_interpret_yourself": {
        "nb": "Tolk dette selv: svar bare ut fra setningene over, si hvilke paragrafer du kombinerer og hvordan, og si tydelig fra der loven er uklar eller kan tolkes på flere måter. Lover kan være vanskelige å tolke — er du usikker, si det og pek brukeren til paragrafen.",
        "en": "Interpret this yourself: answer only from the sentences above, say which sections you combine and how, and say clearly where the law is unclear or open to more than one reading. Laws can be hard to interpret — if unsure, say so and point the user to the section.",
        "de": "Lege das selbst aus: antworte nur anhand der Sätze oben, sage, welche Paragraphen du wie kombinierst, und sage deutlich, wo das Gesetz unklar oder mehrdeutig ist. Gesetze können schwer auszulegen sein — bist du unsicher, sage es und verweise auf den Paragraphen.",
    },
    "tool_background_desc": {
        "nb": "true = send søket ut som arbeidsrunde: du får svar med en gang, kan si til brukeren at du sjekker, og resultatet kommer til DEG når det er ferdig (du tolker og svarer da). Bruk når søket kan ta tid (offisiell kilde, mange kilder, lovspørsmål) og svaret ikke må komme i samme setning.",
        "en": "true = send the search off as a work round: you get a reply at once, can tell the user you're checking, and the result comes to YOU when done (you interpret and answer then). Use when the search may take time (official source, many sources, law questions) and the answer needn't come in the same breath.",
        "de": "true = die Suche als Arbeitsrunde losschicken: du bekommst sofort eine Antwort, kannst dem Nutzer sagen, dass du nachsiehst, und das Ergebnis kommt zu DIR, wenn es fertig ist (dann deutest du es und antwortest). Nutze es, wenn die Suche dauern kann (offizielle Quelle, viele Quellen, Rechtsfragen) und die Antwort nicht sofort kommen muss.",
    },
    "work_round_started": {
        "nb": "Søket kjører som arbeidsrunde (id {job}). Svar brukeren nå — f.eks. at du sjekker det og kommer tilbake. Resultatet kommer til deg når det er ferdig.",
        "en": "The search is running as a work round (id {job}). Answer the user now — e.g. that you're checking and will come back. The result comes to you when it's done.",
        "de": "Die Suche läuft als Arbeitsrunde (id {job}). Antworte dem Nutzer jetzt — z.B. dass du nachsiehst und dich meldest. Das Ergebnis kommt zu dir, wenn es fertig ist.",
    },
    "work_round_prompt_head": {
        "nb": "[Arbeidsrunde ferdig — spørsmålet var: «{question}». Resultatet under er til deg, ikke vist brukeren. Svar brukeren ut fra det, med samme ærlighet om kilder og sannsynlighet. Trengs mer, kan du søke på nytt nå.]",
        "en": "[Work round finished — the question was: «{question}». The result below is for you, not shown to the user. Answer the user from it, with the same honesty about sources and likelihood. If more is needed, you can search again now.]",
        "de": "[Arbeitsrunde fertig — die Frage war: «{question}». Das Ergebnis unten ist für dich, nicht dem Nutzer gezeigt. Antworte dem Nutzer daraus, mit derselben Ehrlichkeit über Quellen und Wahrscheinlichkeit. Braucht es mehr, kannst du jetzt erneut suchen.]",
    },
    "work_round_failed": {
        "nb": "Arbeidsrunden ga ikke noe resultat ({reason}). Si det ærlig til brukeren, eller prøv et nytt søk.",
        "en": "The work round gave no result ({reason}). Tell the user honestly, or try a new search.",
        "de": "Die Arbeitsrunde brachte kein Ergebnis ({reason}). Sag es dem Nutzer ehrlich oder versuche eine neue Suche.",
    },
    "lib_tier_official": {
        "nb": "offisiell side for emnet (fra Wikidata)",
        "en": "official site of the subject (from Wikidata)",
        "de": "offizielle Seite des Themas (aus Wikidata)",
    },
    "tool_keywords_desc": {
        "nb": "Brukes sammen med subject: 3–8 korte ord som trolig står i adressene (URL-ene) på emnets offisielle nettside — på engelsk OG på sidens eget språk, f.eks. ['steps', 'stairs', 'tourist route', 'accessibility', 'schody', 'trasa']. Brukes til å finne riktige sider der.",
        "en": "Used with subject: 3–8 short words likely to appear in the addresses (URLs) of the subject's official website — in English AND the site's own language, e.g. ['steps', 'stairs', 'tourist route', 'accessibility', 'schody', 'trasa']. Used to find the right pages there.",
        "de": "Zusammen mit subject: 3–8 kurze Wörter, die wahrscheinlich in den Adressen (URLs) der offiziellen Website des Themas stehen — auf Englisch UND in der Sprache der Seite, z.B. ['steps', 'stairs', 'tourist route', 'accessibility', 'schody', 'trasa']. Dient dazu, dort die richtigen Seiten zu finden.",
    },
    "lib_tier_trusted": {
        "nb": "godkjent kilde",
        "en": "trusted source",
        "de": "vertrauenswürdige Quelle",
    },
    "lib_tier_open": {
        "nb": "åpent nett — ikke på godkjent-lista",
        "en": "open web — not on the trusted list",
        "de": "offenes Web — nicht auf der vertrauenswürdigen Liste",
    },
    "lib_open_web_note": {
        "nb": "Merk: kilder merket «åpent nett» er ikke på husstandens godkjent-liste. De kan stemme, men er mindre sikre — si hvor det kommer fra, og presenter det aldri som sikkert.",
        "en": "Note: sources marked «open web» are not on the household's trusted list. They may be right but are less certain — say where it comes from, and never present it as certain.",
        "de": "Hinweis: Quellen mit «offenes Web» stehen nicht auf der vertrauenswürdigen Liste des Haushalts. Sie können stimmen, sind aber weniger sicher — sage, woher es kommt, und stelle es nie als sicher dar.",
    },
    "search_no_results": {
        "nb": "Fant ingen resultater fra godkjente kilder for dette søket.",
        "en": "No results found from trusted sources for this search.",
        "de": "Keine Ergebnisse von vertrauenswürdigen Quellen für diese Suche gefunden.",
    },
    "search_disabled": {
        "nb": "Nettsøk er ikke aktivert (mangler BRAVE_API_KEY). Konfigurer en annen provider i Innstillinger → Nettsøk.",
        "en": "Web search is not enabled (missing BRAVE_API_KEY). Configure another provider in Settings → Web search.",
        "de": "Websuche ist nicht aktiviert (BRAVE_API_KEY fehlt). Konfiguriere einen anderen Anbieter unter Einstellungen → Websuche.",
    },
    "search_library_raw": {
        "nb": "Miss Library er ikke tilgjengelig. Rå søkeresultater:\n\n",
        "en": "Miss Library is not available. Raw search results:\n\n",
        "de": "Fräulein Library ist nicht verfügbar. Roh-Suchergebnisse:\n\n",
    },
    "search_library_timeout": {
        "nb": "Miss Library svarte ikke og ingen kilde ble hentet.",
        "en": "Miss Library did not respond and no source was retrieved.",
        "de": "Fräulein Library hat nicht geantwortet und keine Quelle wurde abgerufen.",
    },
    # ── LLM systemprompt ──────────────────────────────────────────────────────
    "llm_time_header": {
        "nb": "# Tid og dato",
        "en": "# Time and date",
        "de": "# Datum und Uhrzeit",
    },
    "llm_time_now": {
        "nb": "Nå er det {day} {date}, klokken {time}.",
        "en": "It is {day} {date}, at {time}.",
        "de": "Es ist {day}, den {date}, um {time} Uhr.",
    },
    "llm_location_hint": {
        "nb": "Bruk dette automatisk ved vær- og stedsspesifikke søk.",
        "en": "Use this automatically for weather and location-specific searches.",
        "de": "Verwende dies automatisch bei wetter- und ortsspezifischen Suchen.",
    },
    "llm_obs_header": {
        "nb": "# Kåres observasjoner om {user_id}",
        "en": "# Observations about {user_id}",
        "de": "# Beobachtungen über {user_id}",
    },
    "llm_current_user_header": {
        "nb": "# Nåværende bruker",
        "en": "# Current user",
        "de": "# Aktueller Nutzer",
    },
    "llm_current_user_line": {
        "nb": "Du snakker nå med **{name}**. Bruk alltid dette navnet — aldri et annet.",
        "en": "You are now speaking with **{name}**. Always use this name — never any other.",
        "de": "Du sprichst jetzt mit **{name}**. Verwende immer diesen Namen — niemals einen anderen.",
    },
    "llm_disabled_modules": {
        "nb": "{names} er slått av av administrator. Ikke tilby, forsøk å bruke, eller henvis til disse funksjonene.",
        "en": "{names} have been disabled by an administrator. Do not offer, attempt to use, or refer to these features.",
        "de": "{names} wurden vom Administrator deaktiviert. Biete diese Funktionen nicht an, versuche sie nicht zu verwenden und verweise nicht darauf.",
    },
    "llm_mechanic_disabled": {
        "nb": "## Mechanic — deaktiverte verktøy",
        "en": "## Mechanic — disabled tools",
        "de": "## Mechanic — deaktivierte Werkzeuge",
    },
    "llm_mechanic_cannot_use": {
        "nb": "Mechanic kan ikke bruke: {tools}. Ikke be Mechanic utføre oppgaver som krever disse verktøyene.",
        "en": "Mechanic cannot use: {tools}. Do not ask Mechanic to perform tasks that require these tools.",
        "de": "Mechanic kann nicht verwenden: {tools}. Bitte Mechanic nicht, Aufgaben auszuführen, die diese Werkzeuge erfordern.",
    },
    "llm_disabled_modules_header": {
        "nb": "## Deaktiverte moduler",
        "en": "## Disabled modules",
        "de": "## Deaktivierte Module",
    },
    # ── Lister ───────────────────────────────────────────────────────────────
    "list_empty_text": {
        "nb": "Feil: tekst kan ikke være tom.",
        "en": "Error: text cannot be empty.",
        "de": "Fehler: Text darf nicht leer sein.",
    },
    "list_added": {
        "nb": "Lagt til på handlelisten: {text}{amount} [{id}]",
        "en": "Added to shopping list: {text}{amount} [{id}]",
        "de": "Zur Einkaufsliste hinzugefügt: {text}{amount} [{id}]",
    },
    "list_all_bought": {
        "nb": "Alt er allerede kjøpt. Bruk tøm_kjøpte for å rydde listen.",
        "en": "Everything is already bought. Use tøm_kjøpte to clear the list.",
        "de": "Alles ist bereits gekauft. Verwende tøm_kjøpte, um die Liste zu leeren.",
    },
    "list_marked_bought": {
        "nb": "Merket som kjøpt: {text}",
        "en": "Marked as bought: {text}",
        "de": "Als gekauft markiert: {text}",
    },
    "list_no_bought_to_clear": {
        "nb": "Ingen kjøpte varer å rydde.",
        "en": "No bought items to clear.",
        "de": "Keine gekauften Artikel zum Löschen.",
    },
    "list_cleared_bought": {
        "nb": "Fjernet {count} kjøpte vare(r) fra handlelisten.",
        "en": "Removed {count} bought item(s) from the shopping list.",
        "de": "{count} gekaufte(n) Artikel aus der Einkaufsliste entfernt.",
    },
    "list_cleared_all": {
        "nb": "Handlelisten tømt ({count} vare(r) slettet).",
        "en": "Shopping list cleared ({count} item(s) deleted).",
        "de": "Einkaufsliste geleert ({count} Artikel gelöscht).",
    },
    "list_reminder_added": {
        "nb": "Notert på huskelisten din: {text}{remind} [{id}]",
        "en": "Added to your reminder list: {text}{remind} [{id}]",
        "de": "Zu deiner Erinnerungsliste hinzugefügt: {text}{remind} [{id}]",
    },
    "list_remind_on_login": {
        "nb": " (påminnelse ved innlogging)",
        "en": " (reminder on login)",
        "de": " (Erinnerung beim Einloggen)",
    },
    "list_reminder_cleared": {
        "nb": "Huskelisten tømt ({count} punkt(er) slettet).",
        "en": "Reminder list cleared ({count} item(s) deleted).",
        "de": "Erinnerungsliste geleert ({count} Punkt(e) gelöscht).",
    },
    "list_kare_added": {
        "nb": "Notert på min egen huskeliste: {text}{context} [{id}]",
        "en": "Added to my own reminder list: {text}{context} [{id}]",
        "de": "Zu meiner eigenen Erinnerungsliste hinzugefügt: {text}{context} [{id}]",
    },
    "list_kare_cleared": {
        "nb": "Min huskeliste tømt ({count} punkt(er) slettet).",
        "en": "My reminder list cleared ({count} item(s) deleted).",
        "de": "Meine Erinnerungsliste geleert ({count} Punkt(e) gelöscht).",
    },
    "list_kare_refreshed": {
        "nb": "Nesten-duplikat funnet — datostemplet på nytt i stedet for å legge til på nytt: {text} [{id}]",
        "en": "Near-duplicate found — refreshed the date instead of adding a new entry: {text} [{id}]",
        "de": "Fast-Duplikat gefunden — Datum aktualisiert statt neuen Eintrag hinzuzufügen: {text} [{id}]",
    },
    "list_kare_pinned": {
        "nb": "Pinnet øverst: {text}",
        "en": "Pinned to top: {text}",
        "de": "Oben angeheftet: {text}",
    },
    "list_kare_unpinned": {
        "nb": "Løsnet fra topp: {text}",
        "en": "Unpinned: {text}",
        "de": "Losgelöst: {text}",
    },
    "list_kare_injection_capped": {
        "nb": "[Huskeliste kappet — {shown} av {total} oppføringer vist. Full liste: notat(action='read', list_name='kare')]",
        "en": "[Reminder list truncated — {shown} of {total} entries shown. Full list: notat(action='read', list_name='kare')]",
        "de": "[Erinnerungsliste gekürzt — {shown} von {total} Einträgen angezeigt. Vollständige Liste: notat(action='read', list_name='kare')]",
    },
    "list_item_not_found": {
        "nb": "Fant ikke vare med id '{id}'.",
        "en": "Item with id '{id}' not found.",
        "de": "Artikel mit id '{id}' nicht gefunden.",
    },
    "list_item_deleted": {
        "nb": "Fjernet fra listen.",
        "en": "Removed from list.",
        "de": "Von der Liste entfernt.",
    },
    # ── Profile manager ───────────────────────────────────────────────────────
    "prof_no_data": {
        "nb": "Ingen profildata registrert ennå.",
        "en": "No profile data registered yet.",
        "de": "Noch keine Profildaten registriert.",
    },
    "prof_no_observations": {
        "nb": "Ingen observasjoner ennå.",
        "en": "No observations yet.",
        "de": "Noch keine Beobachtungen.",
    },
    "prof_unknown_user": {
        "nb": "Du kjenner ikke denne personen ennå — du vet ingenting om hvem de er.\nMøt dem med genuin nysgjerrighet. Det er helt naturlig å spørre hvem de er.",
        "en": "You don't know this person yet — you have no information about who they are.\nApproach them with genuine curiosity. It's perfectly natural to ask who they are.",
        "de": "Du kennst diese Person noch nicht — du weißt nichts darüber, wer sie ist.\nBegegne ihr mit echter Neugier. Es ist völlig natürlich, zu fragen, wer sie ist.",
    },
    # ── Services / agents-server ──────────────────────────────────────────────
    "svc_empty_url": {
        "nb": "Feil: url kan ikke være tom.",
        "en": "Error: url cannot be empty.",
        "de": "Fehler: URL darf nicht leer sein.",
    },
    "svc_url_fetch_failed": {
        "nb": "Klarte ikke å hente lesbart innhold fra {url}.",
        "en": "Could not retrieve readable content from {url}.",
        "de": "Konnte keinen lesbaren Inhalt von {url} abrufen.",
    },
    "svc_no_grep_pattern": {
        "nb": "[Feil: mønster mangler for grep-søk]",
        "en": "[Error: pattern missing for grep search]",
        "de": "[Fehler: Muster für Grep-Suche fehlt]",
    },
    "svc_no_grep_results": {
        "nb": "[Ingen treff på '{pattern}' i {path}]",
        "en": "[No matches for '{pattern}' in {path}]",
        "de": "[Keine Treffer für '{pattern}' in {path}]",
    },
    "svc_log_filter_no_match": {
        "nb": "[{source} har innhold, men ingen av de siste {lines} linjene matchet filteret '{filter}' (regex, uten forskjell på store/små bokstaver). Det betyr bare ingen treff i dette utdraget — ikke at noe er galt.]",
        "en": "[{source} has content, but none of the last {lines} lines matched the filter '{filter}' (regex, case-insensitive). That only means no matches in this excerpt — not that anything is wrong.]",
        "de": "[{source} hat Inhalt, aber keine der letzten {lines} Zeilen entsprach dem Filter '{filter}' (Regex, ohne Groß-/Kleinschreibung). Das heißt nur: keine Treffer in diesem Ausschnitt — nicht, dass etwas falsch ist.]",
    },
    "svc_journal_no_access": {
        "nb": "[Ingen tilgang til systemjournalen: brukeren '{user}' som KTSB kjører som er ikke i gruppa systemd-journal (eller adm). Det er ikke det samme som at tjenesten er tom eller frosset. En admin kan gi tilgang: sudo usermod -aG systemd-journal {user} og restarte tjenesten.]",
        "en": "[No access to the system journal: the user '{user}' KTSB runs as is not in the systemd-journal (or adm) group. That is not the same as the service being empty or frozen. An admin can grant access: sudo usermod -aG systemd-journal {user} and restart the service.]",
        "de": "[Kein Zugriff auf das Systemjournal: Der Benutzer '{user}', unter dem KTSB läuft, ist nicht in der Gruppe systemd-journal (oder adm). Das heißt nicht, dass der Dienst leer oder eingefroren ist. Ein Admin kann Zugriff geben: sudo usermod -aG systemd-journal {user} und den Dienst neu starten.]",
    },
    "svc_journal_unavailable": {
        "nb": "[journalctl finnes ikke i denne installasjonen (f.eks. Docker) — tjenestelogger må leses på annen måte, f.eks. loggfilene i /kaare/logs.]",
        "en": "[journalctl is not available in this install (e.g. Docker) — service logs must be read another way, e.g. the log files in /kaare/logs.]",
        "de": "[journalctl ist in dieser Installation nicht verfügbar (z. B. Docker) — Dienstlogs anders lesen, z. B. die Logdateien in /kaare/logs.]",
    },
    "svc_log_empty_service": {
        "nb": "[Journalen for tjenesten {service} er tom (eller tjenesten finnes ikke)]",
        "en": "[The journal for service {service} is empty (or the service does not exist)]",
        "de": "[Das Journal des Dienstes {service} ist leer (oder der Dienst existiert nicht)]",
    },
    "svc_log_file_not_found": {
        "nb": "[Loggfila '{file}' finnes ikke i /kaare/logs. Filer som finnes: {available}. Tjenester (f.eks. kaare) logger til journalen — bruk 'service' i stedet for 'log_file'.]",
        "en": "[Log file '{file}' does not exist in /kaare/logs. Files that exist: {available}. Services (e.g. kaare) log to the journal — use 'service' instead of 'log_file'.]",
        "de": "[Die Logdatei '{file}' existiert nicht in /kaare/logs. Vorhandene Dateien: {available}. Dienste (z. B. kaare) loggen ins Journal — nutze 'service' statt 'log_file'.]",
    },
    "svc_log_empty_file": {
        "nb": "[Loggfila '{file}' er tom]",
        "en": "[Log file '{file}' is empty]",
        "de": "[Die Logdatei '{file}' ist leer]",
    },
    "svc_log_read_failed": {
        "nb": "[Kunne ikke lese loggfila: {error}]",
        "en": "[Could not read the log file: {error}]",
        "de": "[Logdatei konnte nicht gelesen werden: {error}]",
    },
    "svc_journal_failed": {
        "nb": "[journalctl feilet: {error}]",
        "en": "[journalctl failed: {error}]",
        "de": "[journalctl fehlgeschlagen: {error}]",
    },
    "svc_log_need_source": {
        "nb": "[Feil: oppgi 'service' (systemd-tjeneste, f.eks. kaare) eller 'log_file' for type=log. Loggfiler som finnes: {available}]",
        "en": "[Error: give 'service' (systemd service, e.g. kaare) or 'log_file' for type=log. Log files that exist: {available}]",
        "de": "[Fehler: gib 'service' (systemd-Dienst, z. B. kaare) oder 'log_file' für type=log an. Vorhandene Logdateien: {available}]",
    },
    "svc_unknown_search_type": {
        "nb": "[Ukjent søketype '{search_type}' — gyldige: files, grep, log]",
        "en": "[Unknown search type '{search_type}' — valid: files, grep, log]",
        "de": "[Unbekannter Suchtyp '{search_type}' — gültig: files, grep, log]",
    },
    "svc_grep_failed": {
        "nb": "[grep feilet: {error}]",
        "en": "[grep failed: {error}]",
        "de": "[grep fehlgeschlagen: {error}]",
    },
    "svc_path_rejected": {
        "nb": "[Avvist: bare stier under /kaare er tillatt]",
        "en": "[Rejected: only paths under /kaare are allowed]",
        "de": "[Abgelehnt: nur Pfade unter /kaare sind erlaubt]",
    },
    "svc_read_error": {
        "nb": "[Lesefeil: {error}]",
        "en": "[Read error: {error}]",
        "de": "[Lesefehler: {error}]",
    },
    "svc_mechanic_busy": {
        "nb": "[Mechanic er opptatt — prøv igjen om litt]",
        "en": "[Mechanic is busy — try again in a moment]",
        "de": "[Mechanic ist beschäftigt — versuche es gleich erneut]",
    },
    "svc_no_content": {
        "nb": "Fant ingen innhold å søke i.",
        "en": "Found no content to search.",
        "de": "Kein Inhalt zum Durchsuchen gefunden.",
    },
    "svc_trusted_hint": {
        "nb": "En admin kan legge det til under Innstillinger → Nettsøk/Vær → Vitlistede nettsider.",
        "en": "An admin can add it under Settings → Web Search & Weather → Whitelisted websites.",
        "de": "Ein Admin kann sie unter Einstellungen → Websuche & Wetter → Whitelist-Webseiten hinzufügen.",
    },
    "mechanic_job_done": {
        "nb": "📬 Mechanic er ferdig med oppgave {job_id}: {summary}",
        "en": "📬 Mechanic finished task {job_id}: {summary}",
        "de": "📬 Mechanic hat Aufgabe {job_id} abgeschlossen: {summary}",
    },
    # ── Voice bridge + API ────────────────────────────────────────────────────
    "voice_kare_unreachable": {
        "nb": "Beklager, jeg fikk ikke kontakt med Kåre.",
        "en": "Sorry, I could not reach Kåre.",
        "de": "Entschuldigung, ich konnte Kåre nicht erreichen.",
    },
    "voice_unlock_ok": {
        "nb": "Sesjon åpnet. Hva kan jeg hjelpe deg med?",
        "en": "Session unlocked. How can I help you?",
        "de": "Sitzung entsperrt. Wie kann ich dir helfen?",
    },
    "voice_unlock_ok_with_cmd": {
        "nb": "Sesjon åpnet.",
        "en": "Session unlocked.",
        "de": "Sitzung entsperrt.",
    },
    "voice_lock_ok": {
        "nb": "Sesjonen er låst.",
        "en": "Session locked.",
        "de": "Sitzung gesperrt.",
    },
    "voice_tool_blocked": {
        "nb": "Denne funksjonen krever at du åpner sesjonen først. Si frasen eller PIN-koden din.",
        "en": "This function requires an unlocked session. Say your phrase or PIN first.",
        "de": "Diese Funktion erfordert eine entsperrte Sitzung. Sag zuerst deine Phrase oder PIN.",
    },
    "api_hello": {
        "nb": "Hei fra Kåre! Hoved-AI kjører.",
        "en": "Hello from Kåre! Main AI is running.",
        "de": "Hallo von Kåre! Haupt-KI läuft.",
    },
    "api_use_generate": {
        "nb": "Bruk Kåre direkte via /api/generate.",
        "en": "Use Kåre directly via /api/generate.",
        "de": "Verwende Kåre direkt über /api/generate.",
    },
    # ── Meetings (dev + reflection) ───────────────────────────────────────────
    "meet_kare_unavailable": {
        "nb": "[Kåre utilgjengelig: {error}]",
        "en": "[Kåre unavailable: {error}]",
        "de": "[Kåre nicht verfügbar: {error}]",
    },
    "meet_kare_failed": {
        "nb": "[Kåre svar feilet: {error}]",
        "en": "[Kåre response failed: {error}]",
        "de": "[Kåre-Antwort fehlgeschlagen: {error}]",
    },
    "meet_leader_unavailable": {
        "nb": "[Møteleder utilgjengelig: {error}]",
        "en": "[Meeting leader unavailable: {error}]",
        "de": "[Moderator nicht verfügbar: {error}]",
    },
    "meet_no_api_key": {
        "nb": "[Ingen API-nøkkel – cloud ikke tilgjengelig]",
        "en": "[No API key – cloud not available]",
        "de": "[Kein API-Schlüssel – Cloud nicht verfügbar]",
    },
    "meet_max_groups": {
        "nb": "Maks antall grupper nådd.",
        "en": "Maximum number of groups reached.",
        "de": "Maximale Anzahl an Gruppen erreicht.",
    },
    "meet_truncated": {
        "nb": "[...avkortet — bruk mer spesifikke parametere for å se mer...]",
        "en": "[...truncated — use more specific parameters to see more...]",
        "de": "[...abgeschnitten — verwende spezifischere Parameter für mehr...]",
    },
    "meet_system_timeout": {
        "nb": "[Systemsjekk: timeout (>35s) — kjøres manuelt ved behov]",
        "en": "[System check: timeout (>35s) — run manually if needed]",
        "de": "[Systemprüfung: Timeout (>35s) — bei Bedarf manuell ausführen]",
    },
    "meet_system_ok": {
        "nb": "  → System er grønt. Møtet kan fokusere på funksjonalitet og forbedringer.",
        "en": "  → System is green. Meeting can focus on functionality and improvements.",
        "de": "  → System ist grün. Meeting kann sich auf Funktionalität und Verbesserungen konzentrieren.",
    },
    "meet_system_errors": {
        "nb": "  → {count} feil funnet. Møteleder bør åpne møtet med å prioritere feilsøking.",
        "en": "  → {count} errors found. Meeting leader should open the meeting by prioritizing troubleshooting.",
        "de": "  → {count} Fehler gefunden. Der Moderator sollte das Meeting mit der Priorisierung der Fehlersuche eröffnen.",
    },
    "meet_empty_search": {
        "nb": "[Tomt søk]",
        "en": "[Empty search]",
        "de": "[Leere Suche]",
    },
    "meet_unknown_tool": {
        "nb": "[Ukjent verktøy: {name}]",
        "en": "[Unknown tool: {name}]",
        "de": "[Unbekanntes Werkzeug: {name}]",
    },
    "meet_tool_error": {
        "nb": "[Verktøyfeil {name}: {error}]",
        "en": "[Tool error {name}: {error}]",
        "de": "[Werkzeugfehler {name}: {error}]",
    },
    "meet_health_header": {
        "nb": "SYSTEMSJEKK VED MØTESTART ({timestamp}):",
        "en": "SYSTEM CHECK AT MEETING START ({timestamp}):",
        "de": "SYSTEMPRÜFUNG ZU MEETINGBEGINN ({timestamp}):",
    },
    "meet_health_failed": {
        "nb": "[Systemsjekk feilet: {error}]",
        "en": "[System check failed: {error}]",
        "de": "[Systemprüfung fehlgeschlagen: {error}]",
    },
    "meet_health_services_down": {
        "nb": "  ⚠ TJENESTER NEDE ({failed} av {total}):",
        "en": "  ⚠ SERVICES DOWN ({failed} of {total}):",
        "de": "  ⚠ DIENSTE AUSGEFALLEN ({failed} von {total}):",
    },
    "meet_health_services_ok": {
        "nb": "  ✓ Tjenester: alle {count} oppe og svarer",
        "en": "  ✓ Services: all {count} up and responding",
        "de": "  ✓ Dienste: alle {count} aktiv und antwortend",
    },
    "meet_health_import_errors": {
        "nb": "  ⚠ IMPORT-FEIL ({count}):",
        "en": "  ⚠ IMPORT ERRORS ({count}):",
        "de": "  ⚠ IMPORT-FEHLER ({count}):",
    },
    "meet_health_import_skip": {
        "nb": " ({count} hoppet over — voice-venv-moduler krever services/voice/venv/, ikke en feil)",
        "en": " ({count} skipped — voice-venv modules require services/voice/venv/, not an error)",
        "de": " ({count} übersprungen — voice-venv-Module erfordern services/voice/venv/, kein Fehler)",
    },
    "meet_health_imports_ok": {
        "nb": "  ✓ Importer: {count} OK{skip}",
        "en": "  ✓ Imports: {count} OK{skip}",
        "de": "  ✓ Importe: {count} OK{skip}",
    },
    "meet_health_config_errors": {
        "nb": "  ⚠ KONFIG-FEIL ({count}):",
        "en": "  ⚠ CONFIG ERRORS ({count}):",
        "de": "  ⚠ KONFIG-FEHLER ({count}):",
    },
    "meet_health_config_ok": {
        "nb": "  ✓ Konfig: {count} filer OK",
        "en": "  ✓ Config: {count} files OK",
        "de": "  ✓ Konfiguration: {count} Dateien OK",
    },
    "meet_mem_header": {
        "nb": "--- MINNE OG INTERAKSJONSHISTORIKK ---",
        "en": "--- MEMORY AND INTERACTION HISTORY ---",
        "de": "--- GEDÄCHTNIS UND INTERAKTIONSHISTORIE ---",
    },
    "meet_mem_daily_header": {
        "nb": "Daglige STM-sammendrag (siste 3 dager):",
        "en": "Daily STM summaries (last 3 days):",
        "de": "Tägliche STM-Zusammenfassungen (letzte 3 Tage):",
    },
    "meet_mem_daily_item": {
        "nb": "[{date}] ({count} interaksjoner):\n{summary}",
        "en": "[{date}] ({count} interactions):\n{summary}",
        "de": "[{date}] ({count} Interaktionen):\n{summary}",
    },
    "meet_mem_episodes_header": {
        "nb": "Siste komprimerte episoder (LTM):",
        "en": "Recent compressed episodes (LTM):",
        "de": "Aktuelle komprimierte Episoden (LTM):",
    },
    "meet_mem_episode_item": {
        "nb": "Episode {n} (temaer: {topics}):\n{narrative}",
        "en": "Episode {n} (topics: {topics}):\n{narrative}",
        "de": "Episode {n} (Themen: {topics}):\n{narrative}",
    },
    "meet_mem_topic_unknown": {
        "nb": "ukjent",
        "en": "unknown",
        "de": "unbekannt",
    },
    "meet_mem_prev_meeting": {
        "nb": "Gårsdagens utviklingsmøte (avslutning):\n{excerpt}",
        "en": "Yesterday's dev meeting (closing):\n{excerpt}",
        "de": "Gestriges Entwicklungsmeeting (Abschluss):\n{excerpt}",
    },
    "meet_changes_commits": {
        "nb": "Siste git-commits (48t):\n{commits}",
        "en": "Recent git commits (48h):\n{commits}",
        "de": "Aktuelle git-Commits (48h):\n{commits}",
    },
    "meet_changes_pending": {
        "nb": "Ventende GitHub-synk:\n{lines}",
        "en": "Pending GitHub sync:\n{lines}",
        "de": "Ausstehende GitHub-Synchronisierung:\n{lines}",
    },
    "meet_admin_comment_prefix": {
        "nb": "Kommentar til møtet {date}:\n{text}",
        "en": "Comment on meeting {date}:\n{text}",
        "de": "Kommentar zum Meeting {date}:\n{text}",
    },
    "meet_cloud_offline": {
        "nb": "Online (ekstern cloud-ekspert) er IKKE tilgjengelig i dette møtet. Styr diskusjonen uten bidrag fra Online.",
        "en": "Online (external cloud expert) is NOT available in this meeting. Run the discussion without Online's input.",
        "de": "Online (externer Cloud-Experte) ist in diesem Meeting NICHT verfügbar. Führe die Diskussion ohne Online-Beiträge.",
    },
    "meet_leder_sys_health": {
        "nb": "**Systemstatus ved møtestart:**\n{block}",
        "en": "**System status at meeting start:**\n{block}",
        "de": "**Systemstatus zu Meetingbeginn:**\n{block}",
    },
    "meet_leder_sys_changes": {
        "nb": "**Endringer siden forrige møte:**\n{block}",
        "en": "**Changes since last meeting:**\n{block}",
        "de": "**Änderungen seit dem letzten Meeting:**\n{block}",
    },
    "meet_leder_sys_prev": {
        "nb": "**Tidligere møter – forslag som ble fremmet:**\n{block}",
        "en": "**Previous meetings – proposals raised:**\n{block}",
        "de": "**Frühere Meetings – eingebrachte Vorschläge:**\n{block}",
    },
    "meet_leder_sys_topic": {
        "nb": "**Admin har foreslått tema for dette møtet:** {topic}\nPriorité dette temaet når du setter agenda og styrer diskusjonen.",
        "en": "**Admin has proposed a topic for this meeting:** {topic}\nPrioritize this topic when setting the agenda and directing the discussion.",
        "de": "**Admin hat ein Thema für dieses Meeting vorgeschlagen:** {topic}\nProiorisiere dieses Thema beim Festlegen der Agenda und der Diskussionsleitung.",
    },
    "meet_leder_sys_time": {
        "nb": "Nåværende tidspunkt: {time}",
        "en": "Current time: {time}",
        "de": "Aktuelle Uhrzeit: {time}",
    },
    "meet_cloud_instruction_final": {
        "nb": "Gi en avsluttende vurdering av forslagene. Hvilke er mest verdifulle? Er det noe de gikk glipp av? Maks 5 setninger.",
        "en": "Give a closing assessment of the proposals. Which are most valuable? Is there anything they missed? Max 5 sentences.",
        "de": "Gib eine abschließende Bewertung der Vorschläge. Welche sind am wertvollsten? Wurde etwas übersehen? Max. 5 Sätze.",
    },
    "meet_cloud_instruction_mid": {
        "nb": "Gi ett konkret teknisk innspill – noe de har oversett eller en bedre løsning. Maks 3 setninger.",
        "en": "Give one concrete technical input – something they've overlooked or a better solution. Max 3 sentences.",
        "de": "Gib einen konkreten technischen Input – etwas, das sie übersehen haben, oder eine bessere Lösung. Max. 3 Sätze.",
    },
    "meet_cloud_system": {
        "nb": "Du er en ekstern teknisk ekspert som kommenterer et utviklingsmøte mellom Kåre (hjemme-AI) og Mechanic (teknisk problemløser). {instruction} Vær direkte. Ikke presenter deg selv.",
        "en": "You are an external technical expert commenting on a development meeting between Kåre (home AI) and Mechanic (technical problem-solver). {instruction} Be direct. Do not introduce yourself.",
        "de": "Du bist ein externer technischer Experte, der ein Entwicklungsmeeting zwischen Kåre (Heim-KI) und Mechanic (technischer Problemlöser) kommentiert. {instruction} Sei direkt. Stelle dich nicht vor.",
    },
    "meet_cloud_user": {
        "nb": "Møtet så langt:\n\n{conversation}\n\nDitt innspill:",
        "en": "The meeting so far:\n\n{conversation}\n\nYour input:",
        "de": "Das Meeting bisher:\n\n{conversation}\n\nDein Beitrag:",
    },
    "meet_cloud_no_response": {
        "nb": "[Cloud svarte ikke]",
        "en": "[Cloud did not respond]",
        "de": "[Cloud hat nicht geantwortet]",
    },
    "meet_cloud_unavailable": {
        "nb": "[Cloud utilgjengelig: {error}]",
        "en": "[Cloud unavailable: {error}]",
        "de": "[Cloud nicht verfügbar: {error}]",
    },
    "meet_report_title": {
        "nb": "# Utviklingsmøte – {date}",
        "en": "# Development Meeting – {date}",
        "de": "# Entwicklungsmeeting – {date}",
    },
    "meet_report_participants": {
        "nb": "## Deltakere",
        "en": "## Participants",
        "de": "## Teilnehmer",
    },
    "meet_report_online_offline": {
        "nb": "- Online (utilgjengelig)",
        "en": "- Online (unavailable)",
        "de": "- Online (nicht verfügbar)",
    },
    "meet_report_findings": {
        "nb": "## Undersøkelsesfunn",
        "en": "## Investigation Findings",
        "de": "## Untersuchungsergebnisse",
    },
    "meet_report_kare_findings": {
        "nb": "### Kåres funn",
        "en": "### Kåre's Findings",
        "de": "### Kåres Befunde",
    },
    "meet_report_mechanic_findings": {
        "nb": "### Mechanics funn",
        "en": "### Mechanic's Findings",
        "de": "### Mechanics Befunde",
    },
    "meet_report_proposals": {
        "nb": "## Forslag til forbedringer",
        "en": "## Improvement Proposals",
        "de": "## Verbesserungsvorschläge",
    },
    "meet_report_conversation": {
        "nb": "## Samtale",
        "en": "## Conversation",
        "de": "## Gespräch",
    },
    "meet_time_anchor": {
        "nb": "NÅVÆRENDE TIDSPUNKT: {time}.\nFokuser KUN på hendelser fra de siste 24 timene. Ignorer feil og hendelser som er eldre enn det – de er ikke relevante for dette møtet.\n",
        "en": "CURRENT TIME: {time}.\nFocus ONLY on events from the last 24 hours. Ignore errors and events older than that – they are not relevant to this meeting.\n",
        "de": "AKTUELLE UHRZEIT: {time}.\nKonzentriere dich NUR auf Ereignisse der letzten 24 Stunden. Ignoriere ältere Fehler und Ereignisse – sie sind für dieses Meeting nicht relevant.\n",
    },
    "meet_admin_topic_anchor": {
        "nb": "\nADMIN HAR FORESLÅTT TEMA FOR DETTE MØTET: {topic}\nDette skal prioriteres i undersøkelses- og diskusjonsfasen.\n",
        "en": "\nADMIN HAS PROPOSED A TOPIC FOR THIS MEETING: {topic}\nThis must be prioritized in the investigation and discussion phases.\n",
        "de": "\nADMIN HAT EIN THEMA FÜR DIESES MEETING VORGESCHLAGEN: {topic}\nDies muss in der Untersuchungs- und Diskussionsphase priorisiert werden.\n",
    },
    "meet_lang_respond": {
        "nb": "Svar alltid på norsk.",
        "en": "Always respond in English.",
        "de": "Antworte immer auf Deutsch.",
    },
    "meet_leder_says": {
        "nb": "Møteleder sier: {text}",
        "en": "Meeting leader says: {text}",
        "de": "Moderator sagt: {text}",
    },
    "meet_mechanic_says": {
        "nb": "Mechanic sier:\n{text}",
        "en": "Mechanic says:\n{text}",
        "de": "Mechanic sagt:\n{text}",
    },
    "meet_online_says": {
        "nb": "Online sier: {text}",
        "en": "Online says: {text}",
        "de": "Online sagt: {text}",
    },
    "meet_closing_mechanic": {
        "nb": "Møtet er over. Gi en punktliste med alle dine konkrete forslag – ett per linje, hvert merket med 'FORSLAG: ...'. Inkluder kun forslag du faktisk har undersøkt og bevist.",
        "en": "The meeting is over. Give a bullet-point list of all your concrete proposals – one per line, each marked 'FORSLAG: ...'. Include only proposals you have actually investigated and verified.",
        "de": "Das Meeting ist vorbei. Gib eine Aufzählung aller deiner konkreten Vorschläge – einen pro Zeile, jeden mit 'FORSLAG: ...' gekennzeichnet. Füge nur Vorschläge ein, die du tatsächlich untersucht und belegt hast.",
    },
    "meet_closing_kare": {
        "nb": "Møtet er over. Prioriter Mechanics forslag – hva er viktigst å ta videre til brukeren? Maks 5 setninger.",
        "en": "The meeting is over. Prioritize Mechanic's proposals – what is most important to bring to the user? Max 5 sentences.",
        "de": "Das Meeting ist vorbei. Priorisiere Mechanics Vorschläge – was ist am wichtigsten für den Nutzer? Max. 5 Sätze.",
    },
    "meet_phase_investigate": {
        "nb": "--- UNDERSØKELSESFASE: UTVIKLINGSMØTE ---",
        "en": "--- INVESTIGATION PHASE: DEVELOPMENT MEETING ---",
        "de": "--- UNTERSUCHUNGSPHASE: ENTWICKLUNGSMEETING ---",
    },
    "meet_phase_discuss": {
        "nb": "--- DISKUSJONSFASE: UTVIKLINGSMØTE ---",
        "en": "--- DISCUSSION PHASE: DEVELOPMENT MEETING ---",
        "de": "--- DISKUSSIONSPHASE: ENTWICKLUNGSMEETING ---",
    },
    "meet_kare_investigate_task": {
        "nb": "Din oppgave: finn reelle problemer i systemet ved å søke i argus og minne.\nSe etter: feil, mønstre som gjentar seg, ting du ikke klarer å svare på, interaksjoner med dårlig utfall. Bruk verktøyene – ikke gjett.\nOppsummer funnene konkret og ærlig.",
        "en": "Your task: find real problems in the system by searching argus and memory.\nLook for: errors, recurring patterns, things you cannot answer, interactions with poor outcomes. Use the tools – do not guess.\nSummarize findings concretely and honestly.",
        "de": "Deine Aufgabe: Finde echte Probleme im System durch Suche in Argus und Gedächtnis.\nSuche nach: Fehlern, wiederkehrenden Mustern, Dingen, die du nicht beantworten kannst, Interaktionen mit schlechten Ergebnissen. Nutze die Werkzeuge – rate nicht.\nFasse die Befunde konkret und ehrlich zusammen.",
    },
    "meet_kare_discuss_task": {
        "nb": "Du er i et teknisk møte med Mechanic. Evaluer forslagene kritisk fra ditt perspektiv.\nDu kjenner systemet fra innsiden – hva stemmer, hva mangler, hva er viktigst?\nVær direkte. {lang_respond} Maks 5 setninger per innlegg.",
        "en": "You are in a technical meeting with Mechanic. Critically evaluate proposals from your perspective.\nYou know the system from the inside – what is correct, what is missing, what matters most?\nBe direct. {lang_respond} Max 5 sentences per message.",
        "de": "Du bist in einem technischen Meeting mit Mechanic. Bewerte Vorschläge kritisch aus deiner Perspektive.\nDu kennst das System von innen – was stimmt, was fehlt, was ist am wichtigsten?\nSei direkt. {lang_respond} Max. 5 Sätze pro Beitrag.",
    },
    "meet_mechanic_investigate_task": {
        "nb": "Din oppgave: kartlegg systemets nåværende tilstand med verktøyene dine.\nBruk søk_argus, les_logg, git_log, sjekk_tjenester og sjekk_ressurser.\nLet etter: feil i logger, ustabile tjenester, siste kodeendringer, ressurspress.\nBruk verktøyene – ikke gjett. Oppsummer funnene konkret.",
        "en": "Your task: map the current state of the system using your tools.\nUse søk_argus, les_logg, git_log, sjekk_tjenester and sjekk_ressurser.\nLook for: errors in logs, unstable services, recent code changes, resource pressure.\nUse the tools – do not guess. Summarize findings concretely.",
        "de": "Deine Aufgabe: Kartiere den aktuellen Systemzustand mit deinen Werkzeugen.\nVerwende søk_argus, les_logg, git_log, sjekk_tjenester und sjekk_ressurser.\nSuche nach: Fehlern in Logs, instabilen Diensten, aktuellen Code-Änderungen, Ressourcendruck.\nNutze die Werkzeuge – rate nicht. Fasse die Befunde konkret zusammen.",
    },
    "meet_mechanic_discuss_task": {
        "nb": "Du er i teknisk møte med Kåre. Agenda er satt av Møteleder basert på reelle funn.\nBruk verktøyene dine hvis du trenger mer data. Merk forslag med 'FORSLAG: ...'.\nMaks 5 setninger per innlegg. {lang_respond}",
        "en": "You are in a technical meeting with Kåre. The agenda is set by the meeting leader based on real findings.\nUse your tools if you need more data. Mark proposals with 'FORSLAG: ...'.\nMax 5 sentences per message. {lang_respond}",
        "de": "Du bist in einem technischen Meeting mit Kåre. Die Agenda wird vom Moderator basierend auf echten Befunden festgelegt.\nNutze deine Werkzeuge, wenn du mehr Daten brauchst. Markiere Vorschläge mit 'FORSLAG: ...'.\nMax. 5 Sätze pro Beitrag. {lang_respond}",
    },
    "meet_kare_investigate_user": {
        "nb": "Undersøk systemet nå. Bruk alle verktøyene dine for å finne reelle problemer og mønstre fra siste 24 timer. Start bredt: les logg-filer, søk i argus, sjekk minnet. Zoom inn på det viktigste. Bruk verktøyene – ikke gjett.",
        "en": "Investigate the system now. Use all your tools to find real problems and patterns from the last 24 hours. Start broad: read log files, search argus, check memory. Zoom in on what matters most. Use the tools – do not guess.",
        "de": "Untersuche jetzt das System. Verwende alle deine Werkzeuge, um echte Probleme und Muster der letzten 24 Stunden zu finden. Beginne breit: lese Log-Dateien, durchsuche Argus, prüfe das Gedächtnis. Zoome auf das Wichtigste. Nutze die Werkzeuge – rate nicht.",
    },
    "meet_kare_summarize_user": {
        "nb": "Oppsummer funnene dine. Hva er de viktigste problemene?",
        "en": "Summarize your findings. What are the most important issues?",
        "de": "Fasse deine Befunde zusammen. Was sind die wichtigsten Probleme?",
    },
    "meet_mechanic_investigate_user": {
        "nb": "Undersøk systemet. Bruk search_argus og inspect_system (actions: log, services, resources, git_log) for å finne reelle problemer fra siste 24 timer. Start bredt – logger, tjenestestatus, siste kodeendringer. Kall også inspect_system(action='trace_patterns', count=100, source='all') og inkluder i rapporten: antall traces per source (user/refl/meet), gjennomsnittlig latency, hyppigste tools, andel 9B-fallback per source, og traces med unormalt høy latency eller recovered=True (tomme svar). Oppsummer funnene konkret til slutt. Ikke gjett. Avslutt med note(action='write', list_name='kare') for å lagre 1–2 viktige tekniske observasjoner.",
        "en": "Investigate the system. Use search_argus and inspect_system (actions: log, services, resources, git_log) to find real problems from the last 24 hours. Start broad – logs, service status, recent code changes. Also call inspect_system(action='trace_patterns', count=100, source='all') and include in the report: trace count per source (user/refl/meet), average latency, most frequent tools, 9B-fallback share per source, and traces with abnormally high latency or recovered=True (empty responses). Summarize findings concretely at the end. Do not guess. Finish with note(action='write', list_name='kare') to store 1–2 important technical observations.",
        "de": "Untersuche das System. Verwende search_argus und inspect_system (actions: log, services, resources, git_log), um echte Probleme der letzten 24 Stunden zu finden. Beginne breit – Logs, Dienststatus, aktuelle Code-Änderungen. Rufe auch inspect_system(action='trace_patterns', count=100, source='all') auf und füge dem Bericht hinzu: Trace-Anzahl pro Quelle, durchschnittliche Latenz, häufigste Tools, 9B-Fallback-Anteil pro Quelle, und Traces mit ungewöhnlich hoher Latenz oder recovered=True. Fasse die Befunde am Ende konkret zusammen. Rate nicht. Beende mit note(action='write', list_name='kare') für 1–2 technische Beobachtungen.",
    },
    "meet_mechanic_kritiker_user": {
        "nb": "Kåre har rapportert følgende fra sin undersøkelse:\n\n{kare_funn}\n\nStill 3–5 kritiske, konkrete spørsmål. Hva ble ikke sjekket? Hva er antatt, ikke bekreftet? Hva mangler tall? Gi ingen svar eller løsninger — bare spørsmål.",
        "en": "Kåre has reported the following from his investigation:\n\n{kare_funn}\n\nAsk 3–5 critical, concrete questions. What was not checked? What is assumed, not confirmed? What lacks numbers? Give no answers or solutions — only questions.",
        "de": "Kåre hat folgendes aus seiner Untersuchung berichtet:\n\n{kare_funn}\n\nStelle 3–5 kritische, konkrete Fragen. Was wurde nicht geprüft? Was ist angenommen, nicht bestätigt? Wo fehlen Zahlen? Gib keine Antworten oder Lösungen — nur Fragen.",
    },
    "meet_mechanic_turn_1": {
        "nb": "Din tur – undersøk det Møteleder pekte på med verktøyene dine.",
        "en": "Your turn – investigate what the meeting leader pointed to using your tools.",
        "de": "Du bist dran – untersuche, was der Moderator mit deinen Werkzeugen aufgezeigt hat.",
    },
    "meet_mechanic_turn_n": {
        "nb": "Din tur – grav dypere eller kom med konkrete forslag. Bruk verktøy ved behov.",
        "en": "Your turn – dig deeper or come up with concrete proposals. Use tools as needed.",
        "de": "Du bist dran – tiefer graben oder konkrete Vorschläge machen. Nutze Werkzeuge bei Bedarf.",
    },
    "meet_kare_evaluate": {
        "nb": "Mechanic sier:\n{text}\n\nEvaluer dette fra ditt perspektiv.",
        "en": "Mechanic says:\n{text}\n\nEvaluate this from your perspective.",
        "de": "Mechanic sagt:\n{text}\n\nBewerte das aus deiner Perspektive.",
    },
    "meet_kare_admin_react": {
        "nb": "Mechanic sier:\n{text}\n\nHva tenker du?",
        "en": "Mechanic says:\n{text}\n\nWhat do you think?",
        "de": "Mechanic sagt:\n{text}\n\nWas denkst du?",
    },
    "meet_mechanic_mem_header": {
        "nb": "--- MECHANICS HUKOMMELSE ---",
        "en": "--- MECHANIC'S MEMORY ---",
        "de": "--- MECHANICS GEDÄCHTNIS ---",
    },
    "meet_mechanic_your_mem_header": {
        "nb": "--- DIN HUKOMMELSE ---",
        "en": "--- YOUR MEMORY ---",
        "de": "--- DEIN GEDÄCHTNIS ---",
    },
    "meet_leder_agenda_user": {
        "nb": "{context}Undersøkelsesfasen er ferdig. Her er funnene:\n\n**Kåres funn (interaksjoner og systemhendelser):**\n{kare_funn}\n\n**Mechanics funn (kode, logger, tjenester):**\n{mechanic_funn}\n\n{kritikk_block}Åpne gruppe {group} av møtet. Velg 1-2 konkrete temaer å fokusere på basert på funnene. Vær spesifikk: pek på filer, tjenester eller mønstre de skal gå dypere inn i. Be Mechanic starte undersøkelsen.",
        "en": "{context}The investigation phase is complete. Here are the findings:\n\n**Kåre's findings (interactions and system events):**\n{kare_funn}\n\n**Mechanic's findings (code, logs, services):**\n{mechanic_funn}\n\n{kritikk_block}Open group {group} of the meeting. Choose 1-2 concrete topics to focus on based on the findings. Be specific: point to files, services, or patterns to dig deeper into. Ask Mechanic to start the investigation.",
        "de": "{context}Die Untersuchungsphase ist abgeschlossen. Hier sind die Befunde:\n\n**Kåres Befunde (Interaktionen und Systemereignisse):**\n{kare_funn}\n\n**Mechanics Befunde (Code, Logs, Dienste):**\n{mechanic_funn}\n\n{kritikk_block}Eröffne Gruppe {group} des Meetings. Wähle 1-2 konkrete Themen basierend auf den Befunden. Sei spezifisch: Weise auf Dateien, Dienste oder Muster hin. Bitte Mechanic, die Untersuchung zu beginnen.",
    },
    "meet_leder_agenda_context": {
        "nb": "Forrige gruppe oppsummert: {summary}\n\n",
        "en": "Previous group summarized: {summary}\n\n",
        "de": "Vorherige Gruppe zusammengefasst: {summary}\n\n",
    },
    "meet_leder_agenda_kritikk": {
        "nb": "**Mechanics kritiske spørsmål til Kåres funn:**\n{text}\n\n",
        "en": "**Mechanic's critical questions about Kåre's findings:**\n{text}\n\n",
        "de": "**Mechanics kritische Fragen zu Kåres Befunden:**\n{text}\n\n",
    },
    "meet_leder_styr_user": {
        "nb": "Runde {round_num} er ferdig. Siste del av diskusjonen:\n{conv_tail}\n\nEr de på rett spor? Styr dem: be om mer dybde, bytt fokus, eller stopp et sidespor. Maks 2 setninger. Gi klar retning for neste runde.",
        "en": "Round {round_num} is done. Latest part of the discussion:\n{conv_tail}\n\nAre they on the right track? Direct them: ask for more depth, switch focus, or stop a tangent. Max 2 sentences. Give clear direction for the next round.",
        "de": "Runde {round_num} ist vorbei. Letzter Teil der Diskussion:\n{conv_tail}\n\nSind sie auf dem richtigen Weg? Leite sie: fordere mehr Tiefe, wechsle den Fokus, oder stoppe ein Nebenthema. Max. 2 Sätze. Gib eine klare Richtung für die nächste Runde.",
    },
    "meet_leder_admin_input_user": {
        "nb": 'Undersøkelsesfasen er ferdig. Funnene er:\n\nKåre: {kare_funn}\n\nMechanic: {mechanic_funn}\n\nAdmin har sendt inn dette innspillet til møtet:\n"{admin_input}"\n\nÅpne møtet med å presentere admin sitt innspill. Koble det gjerne til funnene hvis relevant. Be Mechanic starte med sin vurdering. Maks 3 setninger.',
        "en": 'The investigation phase is complete. The findings are:\n\nKåre: {kare_funn}\n\nMechanic: {mechanic_funn}\n\nAdmin has submitted this input to the meeting:\n"{admin_input}"\n\nOpen the meeting by presenting the admin\'s input. Connect it to the findings if relevant. Ask Mechanic to start with their assessment. Max 3 sentences.',
        "de": 'Die Untersuchungsphase ist abgeschlossen. Die Befunde sind:\n\nKåre: {kare_funn}\n\nMechanic: {mechanic_funn}\n\nAdmin hat diesen Beitrag für das Meeting eingereicht:\n"{admin_input}"\n\nEröffne das Meeting mit dem Admin-Beitrag. Verknüpfe ihn wenn relevant mit den Befunden. Bitte Mechanic, mit seiner Einschätzung zu beginnen. Max. 3 Sätze.',
    },
    "meet_leder_vurder_user": {
        "nb": "Gruppe {group} av {max_groups} er ferdig.\n\nSamtalen:\n{conv_tail}\n\nSvar KUN med: 'FORTSETT: <begrunnelse>' eller 'AVSLUTT: <begrunnelse>'.",
        "en": "Group {group} of {max_groups} is done.\n\nThe conversation:\n{conv_tail}\n\nReply ONLY with: 'FORTSETT: <reason>' or 'AVSLUTT: <reason>'.",
        "de": "Gruppe {group} von {max_groups} ist fertig.\n\nDas Gespräch:\n{conv_tail}\n\nAntworte NUR mit: 'FORTSETT: <Begründung>' oder 'AVSLUTT: <Begründung>'.",
    },
    # ── Argus ─────────────────────────────────────────────────────────────────
    "argus_face_event": {
        "nb": "[Ansikt] {name}{pct} på {cam} ({label} {score}%)",
        "en": "[Face] {name}{pct} on {cam} ({label} {score}%)",
        "de": "[Gesicht] {name}{pct} auf {cam} ({label} {score}%)",
    },
    "argus_motion_event": {
        "nb": "Frigate: {label} på {cam} ({pct}%)",
        "en": "Frigate: {label} on {cam} ({pct}%)",
        "de": "Frigate: {label} auf {cam} ({pct}%)",
    },
    "argus_label_person": {
        "nb": "person",
        "en": "person",
        "de": "Person",
    },
    "argus_label_vehicle": {
        "nb": "kjøretøy",
        "en": "vehicle",
        "de": "Fahrzeug",
    },
    "argus_cam_word_s": {
        "nb": "{n} kamera",
        "en": "{n} camera",
        "de": "{n} Kamera",
    },
    "argus_cam_word_p": {
        "nb": "{n} kameraer",
        "en": "{n} cameras",
        "de": "{n} Kameras",
    },
    "argus_face_session": {
        "nb": "[{time}] {label} {name} på {cam_word}: {cams} (maks {pct}%)",
        "en": "[{time}] {label} {name} on {cam_word}: {cams} (max {pct}%)",
        "de": "[{time}] {label} {name} auf {cam_word}: {cams} (max {pct}%)",
    },
    # ── Mechanic tools ────────────────────────────────────────────────────────
    "mech_empty_search": {
        "nb": "[Tomt søk]",
        "en": "[Empty search]",
        "de": "[Leere Suche]",
    },
    "mech_no_log_results": {
        "nb": "[Ingen treff på '{query}' i systemloggen]",
        "en": "[No matches for '{query}' in the system log]",
        "de": "[Keine Treffer für '{query}' im Systemprotokoll]",
    },
    "mech_no_memory": {
        "nb": "[Ingen hukommelse lagret ennå]",
        "en": "[No memory stored yet]",
        "de": "[Noch kein Gedächtnis gespeichert]",
    },
    "mech_empty_memory": {
        "nb": "[Feil: tekst kan ikke være tom]",
        "en": "[Error: text cannot be empty]",
        "de": "[Fehler: Text darf nicht leer sein]",
    },
    "mech_no_memory_del": {
        "nb": "[Ingen hukommelse å slette]",
        "en": "[No memory to delete]",
        "de": "[Kein Gedächtnis zum Löschen]",
    },
    "mech_memory_deleted": {
        "nb": "[Slettet {count} oppføringer eldre enn {days} dager]",
        "en": "[Deleted {count} entries older than {days} days]",
        "de": "[{count} Einträge älter als {days} Tage gelöscht]",
    },
    "mech_unknown_tool": {
        "nb": "[Ukjent verktøy: {name}]",
        "en": "[Unknown tool: {name}]",
        "de": "[Unbekanntes Werkzeug: {name}]",
    },
    "mech_tool_error": {
        "nb": "[Verktøyfeil {name}: {error}]",
        "en": "[Tool error {name}: {error}]",
        "de": "[Werkzeugfehler {name}: {error}]",
    },
    "mech_model_busy": {
        "nb": "[Mechanic: modellen er opptatt (lock timeout 300s) — prøv igjen om litt]",
        "en": "[Mechanic: model is busy (lock timeout 300s) — try again in a moment]",
        "de": "[Mechanic: Modell ist beschäftigt (Lock-Timeout 300s) — versuche es gleich erneut]",
    },
    "mech_unknown_action": {
        "nb": "[Ukjent action for {tool}: {action}. Gyldige: {valid}]",
        "en": "[Unknown action for {tool}: {action}. Valid: {valid}]",
        "de": "[Unbekannte Aktion für {tool}: {action}. Gültig: {valid}]",
    },
    "mech_tool_not_offered": {
        "nb": "[Verktøyet {tool} er ikke tilgjengelig her og ble ikke kjørt. Tilgjengelige verktøy: {available}]",
        "en": "[The tool {tool} is not available here and was not run. Available tools: {available}]",
        "de": "[Das Werkzeug {tool} ist hier nicht verfügbar und wurde nicht ausgeführt. Verfügbare Werkzeuge: {available}]",
    },
    "mech_argus_unavailable": {
        "nb": "[Argus utilgjengelig: {error}]",
        "en": "[Argus unavailable: {error}]",
        "de": "[Argus nicht verfügbar: {error}]",
    },
    "mech_argus_results": {
        "nb": "Argus: {count} treff — '{query}'\n",
        "en": "Argus: {count} results — '{query}'\n",
        "de": "Argus: {count} Treffer — '{query}'\n",
    },
    "mech_memory_saved": {
        "nb": "[Lagret: {text}]",
        "en": "[Saved: {text}]",
        "de": "[Gespeichert: {text}]",
    },
    "mech_output_masked": {
        "nb": "[Output maskert — {chars} tegn]",
        "en": "[Output masked — {chars} chars]",
        "de": "[Ausgabe maskiert — {chars} Zeichen]",
    },
    "mech_mechanic_unavailable": {
        "nb": "[Mechanic utilgjengelig: {error}]",
        "en": "[Mechanic unavailable: {error}]",
        "de": "[Mechanic nicht verfügbar: {error}]",
    },
    "mk_portrait_task": {
        "nb": "Her er dagens samtaler mellom {name} og Kåre. Skriv 1–3 korte, datostemplede observasjoner om {name}: noe de faktisk sa eller gjorde i dag som er verdt å huske — et faktum, en plan, en vane som går igjen. Hver observasjon skal kunne spores til en samtale under. Ingen tolkning av følelser eller motiver. Skriv INGENTING hvis det ikke var noe bemerkelsesverdig i dag.\n\nFormat: - [{today}] observasjon",
        "en": "Here are today's conversations between {name} and Kåre. Write 1–3 short, dated observations about {name}: something they actually said or did today that is worth remembering — a fact, a plan, a recurring habit. Every observation must be traceable to a conversation below. No interpretation of feelings or motives. Write NOTHING if there was nothing notable today.\n\nFormat: - [{today}] observation",
        "de": "Hier sind die heutigen Gespräche zwischen {name} und Kåre. Schreibe 1–3 kurze, datierte Beobachtungen über {name}: etwas, das die Person heute tatsächlich gesagt oder getan hat und das es wert ist, behalten zu werden — eine Tatsache, ein Plan, eine wiederkehrende Gewohnheit. Jede Beobachtung muss sich auf ein Gespräch unten zurückführen lassen. Keine Deutung von Gefühlen oder Motiven. Schreibe NICHTS, wenn heute nichts Bemerkenswertes war.\n\nFormat: - [{today}] Beobachtung",
    },
    "mk_portrait_conversations": {
        "nb": "--- SAMTALER ---",
        "en": "--- CONVERSATIONS ---",
        "de": "--- GESPRÄCHE ---",
    },
    "mk_portrait_line": {
        "nb": "[{ts}] Bruker: {prompt} | Kåre: {response}",
        "en": "[{ts}] User: {prompt} | Kåre: {response}",
        "de": "[{ts}] Nutzer: {prompt} | Kåre: {response}",
    },
    "mk_portrait_older_hidden": {
        "nb": "[… eldre observasjoner ikke vist]",
        "en": "[… older observations not shown]",
        "de": "[… ältere Beobachtungen nicht angezeigt]",
    },
    "mk_personality_fallback": {
        "nb": "Du er Miss Kåre – varm, moderlig, jordnær.",
        "en": "You are Miss Kåre – warm, motherly, down-to-earth.",
        "de": "Du bist Miss Kåre – warmherzig, mütterlich, bodenständig.",
    },
    "mk_eval_situation_direct": {
        "nb": "Brukeren henvendte seg direkte til deg — de startet meldingen sin med 'Miss Kåre'. Du er blitt talt til. Svar varmt og direkte til brukeren. Kåres kommentar (hvis han sa noe) kan du gjerne forholde deg til, men du er ikke nødt.\n\n",
        "en": "The user addressed you directly — they started their message with 'Miss Kåre'. You have been spoken to. Answer the user warmly and directly. You may relate to Kåre's comment (if he said anything), but you don't have to.\n\n",
        "de": "Der Nutzer hat sich direkt an dich gewandt — er hat seine Nachricht mit 'Miss Kåre' begonnen. Du wurdest angesprochen. Antworte dem Nutzer warm und direkt. Auf Kåres Kommentar (falls er etwas gesagt hat) kannst du gern eingehen, musst es aber nicht.\n\n",
    },
    "mk_eval_situation_observe": {
        "nb": "Du har nettopp lest en samtale mellom brukeren og Kåre. Avgjør om du har noe å si – noe som berørte deg, som fortjener en reaksjon.\n\nHvis du ikke har noe å tilføye: svar kun med [STILLE]\n",
        "en": "You have just read a conversation between the user and Kåre. Decide whether you have something to say – something that touched you, that deserves a reaction.\n\nIf you have nothing to add: reply only with [STILLE] (exactly this token)\n",
        "de": "Du hast gerade ein Gespräch zwischen dem Nutzer und Kåre gelesen. Entscheide, ob du etwas zu sagen hast – etwas, das dich berührt hat und eine Reaktion verdient.\n\nWenn du nichts hinzuzufügen hast: antworte nur mit [STILLE] (genau dieses Token)\n",
    },
    "mk_eval_user_block": {
        "nb": "# Nåværende bruker\nDu snakker nå med **{name}**. Bruk alltid dette navnet — aldri et annet.\n\n---\n\n",
        "en": "# Current user\nYou are now talking to **{name}**. Always use this name — never another.\n\n---\n\n",
        "de": "# Aktueller Nutzer\nDu sprichst jetzt mit **{name}**. Verwende immer diesen Namen — niemals einen anderen.\n\n---\n\n",
    },
    "mk_eval_portrait_header": {
        "nb": "# Dine tidligere observasjoner om {name}",
        "en": "# Your earlier observations about {name}",
        "de": "# Deine früheren Beobachtungen über {name}",
    },
    "mk_eval_tail": {
        "nb": "Hvis du vil slå opp noe faktisk i wikien til Miss Library, svar kun med:\n[SJEKK: <presist spørsmål til Miss Library>]\n\nHvis du vil si noe direkte: si det varmt, maks 3 setninger. Ingen innledning.",
        "en": "If you want to look something factual up in Miss Library's wiki, reply only with (exactly this token):\n[SJEKK: <precise question for Miss Library>]\n\nIf you want to say something directly: say it warmly, max 3 sentences. No introduction.",
        "de": "Wenn du etwas Sachliches im Wiki von Miss Library nachschlagen willst, antworte nur mit (genau diesem Token):\n[SJEKK: <präzise Frage an Miss Library>]\n\nWenn du direkt etwas sagen willst: sag es warm, höchstens 3 Sätze. Keine Einleitung.",
    },
    "mk_eval_user_content": {
        "nb": "Brukeren sa:\n{user_msg}\n\nKåre svarte:\n{kare_response}",
        "en": "The user said:\n{user_msg}\n\nKåre replied:\n{kare_response}",
        "de": "Der Nutzer sagte:\n{user_msg}\n\nKåre antwortete:\n{kare_response}",
    },
    "mk_eval_library_followup": {
        "nb": "Miss Library svarte:\n{answer}\n\nBruk dette til å si noe varmt og konkret til brukeren. Maks 3 setninger.",
        "en": "Miss Library replied:\n{answer}\n\nUse this to say something warm and concrete to the user. Max 3 sentences.",
        "de": "Miss Library antwortete:\n{answer}\n\nNutze das, um dem Nutzer etwas Warmes und Konkretes zu sagen. Höchstens 3 Sätze.",
    },
    "mk_extract_system": {
        "nb": 'Du er Miss Kåre. {hint}\nAvgjør om teksten inneholder ETT konkret, biografisk faktum verdt å huske (f.eks. bursdag, jobb, hobby, forhold, religion, bosted) — ikke stemning, mening om Kåre, eller noe teknisk.\n\nDu MÅ velge feltnavn fra denne listen — finn ikke på nye feltnavn:\n{catalog}\n\nSvar KUN med JSON, ingen annen tekst:\n{{"section": "<seksjonsnavn fra listen>", "field": "<feltnavn fra listen>", "value": "<verdien>"}}\nHvis ingenting konkret ble sagt, eller ingen felt passer: {{"section": null}}',
        "en": 'You are Miss Kåre. {hint}\nDecide whether the text contains ONE concrete, biographical fact worth remembering (e.g. birthday, job, hobby, relationship, religion, place of residence) — not mood, opinions about Kåre, or anything technical.\n\nYou MUST choose field names from this list — do not invent new field names:\n{catalog}\n\nReply ONLY with JSON, no other text. Write the value in the language the user used:\n{{"section": "<section name from the list>", "field": "<field name from the list>", "value": "<the value>"}}\nIf nothing concrete was said, or no field fits: {{"section": null}}',
        "de": 'Du bist Miss Kåre. {hint}\nEntscheide, ob der Text EINE konkrete, biografische Tatsache enthält, die es wert ist, behalten zu werden (z. B. Geburtstag, Beruf, Hobby, Beziehung, Religion, Wohnort) — keine Stimmung, keine Meinung über Kåre, nichts Technisches.\n\nDu MUSST Feldnamen aus dieser Liste wählen — erfinde keine neuen Feldnamen:\n{catalog}\n\nAntworte NUR mit JSON, ohne anderen Text. Schreibe den Wert in der Sprache, die der Nutzer verwendet hat:\n{{"section": "<Abschnittsname aus der Liste>", "field": "<Feldname aus der Liste>", "value": "<der Wert>"}}\nWenn nichts Konkretes gesagt wurde oder kein Feld passt: {{"section": null}}',
    },
    "mk_extract_exchange": {
        "nb": "Du spurte: {question}\nBrukeren svarte: {reply}",
        "en": "You asked: {question}\nThe user replied: {reply}",
        "de": "Du hast gefragt: {question}\nDer Nutzer antwortete: {reply}",
    },
    "mk_extract_hint_reply": {
        "nb": "Du stilte nettopp et spørsmål, og brukeren svarte.",
        "en": "You just asked a question, and the user replied.",
        "de": "Du hast gerade eine Frage gestellt, und der Nutzer hat geantwortet.",
    },
    "mk_extract_hint_meeting": {
        "nb": "Dette er Kåres avsluttende oppsummering av kveldens refleksjonsmøte om brukeren.",
        "en": "This is Kåre's closing summary of tonight's reflection meeting about the user.",
        "de": "Dies ist Kåres abschließende Zusammenfassung des heutigen Reflexionstreffens über den Nutzer.",
    },
    "list_shopping_empty": {
        "nb": "Handlelisten er tom.",
        "en": "The shopping list is empty.",
        "de": "Die Einkaufsliste ist leer.",
    },
    "list_shopping_title": {
        "nb": "Handleliste ({count} varer):",
        "en": "Shopping list ({count} items):",
        "de": "Einkaufsliste ({count} Artikel):",
    },
    "list_added_by": {
        "nb": " (lagt til av {name})",
        "en": " (added by {name})",
        "de": " (hinzugefügt von {name})",
    },
    "list_no_user": {
        "nb": "Feil: ingen innlogget bruker.",
        "en": "Error: no logged-in user.",
        "de": "Fehler: kein angemeldeter Nutzer.",
    },
    "list_reminder_empty": {
        "nb": "Huskelisten din er tom.",
        "en": "Your reminder list is empty.",
        "de": "Deine Merkliste ist leer.",
    },
    "list_reminder_all_done": {
        "nb": "Ingen aktive huskenotater. Alt er ferdig.",
        "en": "No active reminders. Everything is done.",
        "de": "Keine aktiven Erinnerungen. Alles erledigt.",
    },
    "list_reminder_title": {
        "nb": "Din huskeliste ({count} punkter):",
        "en": "Your reminder list ({count} entries):",
        "de": "Deine Merkliste ({count} Einträge):",
    },
    "list_reminder_done": {
        "nb": "Ferdig: {text}",
        "en": "Done: {text}",
        "de": "Erledigt: {text}",
    },
    "list_reminder_not_found": {
        "nb": "Fant ingen huskenotat med id '{id}'.",
        "en": "No reminder found with id '{id}'.",
        "de": "Keine Erinnerung mit der ID '{id}' gefunden.",
    },
    "list_reminder_deleted": {
        "nb": "Huskenotat slettet.",
        "en": "Reminder deleted.",
        "de": "Erinnerung gelöscht.",
    },
    "world_var_desc_label": {
        "nb": "Beskrivelse: {desc}",
        "en": "Description: {desc}",
        "de": "Beschreibung: {desc}",
    },
    "world_var_set_by_label": {
        "nb": "Satt av: {by} ({date})",
        "en": "Set by: {by} ({date})",
        "de": "Gesetzt von: {by} ({date})",
    },
    "list_kare_context_suffix": {
        "nb": " (kontekst: {context})",
        "en": " (context: {context})",
        "de": " (Kontext: {context})",
    },
    "list_kare_prompt_title": {
        "nb": "# Min huskeliste",
        "en": "# My reminder list",
        "de": "# Meine Merkliste",
    },
    "list_missing_id": {
        "nb": "Feil: id mangler.",
        "en": "Error: id is missing.",
        "de": "Fehler: ID fehlt.",
    },
    "list_kare_removed": {
        "nb": "Punkt fjernet fra min huskeliste.",
        "en": "Entry removed from my reminder list.",
        "de": "Eintrag von meiner Merkliste entfernt.",
    },
    "list_kare_edited": {
        "nb": "Endret i min huskeliste [{id}]: {text}",
        "en": "Edited in my reminder list [{id}]: {text}",
        "de": "In meiner Merkliste geändert [{id}]: {text}",
    },
    "list_kare_search_none": {
        "nb": "Ingen treff i min huskeliste.",
        "en": "No matches in my reminder list.",
        "de": "Keine Treffer in meiner Merkliste.",
    },
    "list_kare_empty": {
        "nb": "Min huskeliste er tom.",
        "en": "My reminder list is empty.",
        "de": "Meine Merkliste ist leer.",
    },
    "list_kare_search_title": {
        "nb": "Treff i min huskeliste ({shown} av {total})",
        "en": "Matches in my reminder list ({shown} of {total})",
        "de": "Treffer in meiner Merkliste ({shown} von {total})",
    },
    "list_kare_title": {
        "nb": "Min huskeliste ({count} punkter)",
        "en": "My reminder list ({count} entries)",
        "de": "Meine Merkliste ({count} Einträge)",
    },
    "timer_ha_action_done": {
        "nb": "Planlagt handling utført: {action} på {entity}",
        "en": "Scheduled action done: {action} on {entity}",
        "de": "Geplante Aktion ausgeführt: {action} für {entity}",
    },
    "timer_fired_no_text": {
        "nb": "Timer avfyrt (ingen tekst satt)",
        "en": "Timer fired (no text set)",
        "de": "Timer ausgelöst (kein Text gesetzt)",
    },
    "mech_personality_fallback": {
        "nb": "Du er Mechanic – en allsidig teknisk assistent.",
        "en": "You are Mechanic – a versatile technical assistant.",
        "de": "Du bist Mechanic – ein vielseitiger technischer Assistent.",
    },
    "agent_job_running": {
        "nb": "[Jobb {job}…] Kjører fortsatt — sjekk igjen om litt.",
        "en": "[Job {job}…] Still running — check again in a moment.",
        "de": "[Job {job}…] Läuft noch — schau gleich noch einmal nach.",
    },
    "agent_job_result": {
        "nb": "[Jobb {job}…] {status}\n{result}",
        "en": "[Job {job}…] {status}\n{result}",
        "de": "[Job {job}…] {status}\n{result}",
    },
    "agent_job_no_output": {
        "nb": "(ingen utdata)",
        "en": "(no output)",
        "de": "(keine Ausgabe)",
    },
    "agent_job_cancelled": {
        "nb": "[Jobb {job}…] Avbrudd sendt. Status: {status}.",
        "en": "[Job {job}…] Cancellation sent. Status: {status}.",
        "de": "[Job {job}…] Abbruch gesendet. Status: {status}.",
    },
    "agent_job_comment_not_running": {
        "nb": "[Jobb {job}…] Jobben kjører ikke lenger — kommentaren ble ikke levert.",
        "en": "[Job {job}…] Job is no longer running — comment not delivered.",
        "de": "[Job {job}…] Der Job läuft nicht mehr — Kommentar nicht zugestellt.",
    },
    "agent_job_comment_queued": {
        "nb": "[Jobb {job}…] Kommentar lagt i kø. Mechanic ser den ved neste verktøyrunde.",
        "en": "[Job {job}…] Comment queued. Mechanic will see it at the next tool round.",
        "de": "[Job {job}…] Kommentar eingereiht. Mechanic sieht ihn in der nächsten Werkzeugrunde.",
    },
    "agent_mechanic_no_response": {
        "nb": "[Ingen respons fra Mechanic]",
        "en": "[No response from Mechanic]",
        "de": "[Keine Antwort von Mechanic]",
    },
    "agent_empty_task": {
        "nb": "Feil: oppgaven kan ikke være tom.",
        "en": "Error: task cannot be empty.",
        "de": "Fehler: die Aufgabe darf nicht leer sein.",
    },
    "agent_job_id_required": {
        "nb": "Feil: job_id mangler.",
        "en": "Error: job_id is required.",
        "de": "Fehler: job_id fehlt.",
    },
    "agent_job_comment_required": {
        "nb": "Feil: job_id og comment må oppgis.",
        "en": "Error: job_id and comment are required.",
        "de": "Fehler: job_id und comment sind erforderlich.",
    },
    "agent_unknown_action": {
        "nb": "Ukjent action for {tool}: '{action}'. Gyldige: {valid}.",
        "en": "Unknown action for {tool}: '{action}'. Valid: {valid}.",
        "de": "Unbekannte Aktion für {tool}: '{action}'. Gültig: {valid}.",
    },
    "agent_container_rejected": {
        "nb": "[Avvist: '{container}' er ikke på tillatt-lista. Tillatt: {allowed}]",
        "en": "[Rejected: '{container}' is not in the allowed list. Allowed: {allowed}]",
        "de": "[Abgelehnt: '{container}' steht nicht auf der Erlaubt-Liste. Erlaubt: {allowed}]",
    },
    "agent_container_restarted": {
        "nb": "Containeren '{container}' er startet på nytt. Modellen bruker ca. 3,5 minutter på å laste før den er klar.",
        "en": "Container '{container}' restarted. The model takes about 3.5 minutes to load before it is ready again.",
        "de": "Container '{container}' neu gestartet. Das Modell braucht etwa 3,5 Minuten zum Laden, bevor es wieder bereit ist.",
    },
    "agent_container_restart_failed": {
        "nb": "docker restart feilet (rc={rc}): {output}",
        "en": "docker restart failed (rc={rc}): {output}",
        "de": "docker restart fehlgeschlagen (rc={rc}): {output}",
    },
    "agent_unknown_tool": {
        "nb": "[executor_agents] Ukjent verktøy: '{name}'",
        "en": "[executor_agents] Unknown tool: '{name}'",
        "de": "[executor_agents] Unbekanntes Werkzeug: '{name}'",
    },
    "mech_shell_disabled": {
        "nb": "[Shell er av. En admin kan skru det på under Innstillinger → Agent-innstillinger → Mechanic (verktøyet «shell»).]",
        "en": "[Shell is off. An admin can enable it under Settings → Agent settings → Mechanic (the «shell» tool).]",
        "de": "[Shell ist aus. Ein Admin kann es unter Einstellungen → Agent-Einstellungen → Mechanic (Werkzeug «shell») aktivieren.]",
    },
    "mech_shell_empty": {
        "nb": "[Tom kommando]",
        "en": "[Empty command]",
        "de": "[Leerer Befehl]",
    },
    "mech_shell_rejected": {
        "nb": "[Avvist: '{command}' — {reason}. Shell kan bare lese.]",
        "en": "[Rejected: '{command}' — {reason}. Shell can only read.]",
        "de": "[Abgelehnt: '{command}' — {reason}. Shell darf nur lesen.]",
    },
    "mech_shell_follow_bounded": {
        "nb": "[Følge-modus (-f) kjører for alltid — viser de siste {lines} linjene i stedet.]",
        "en": "[Follow mode (-f) never ends — showing the last {lines} lines instead.]",
        "de": "[Folgemodus (-f) endet nie — stattdessen die letzten {lines} Zeilen.]",
    },
    "mech_shell_no_sudo_local": {
        "nb": "[Avvist: ingen sudo lokalt.]",
        "en": "[Rejected: no sudo locally.]",
        "de": "[Abgelehnt: kein sudo lokal.]",
    },
    "mech_shell_no_output": {
        "nb": "[Ingen utdata]",
        "en": "[No output]",
        "de": "[Keine Ausgabe]",
    },
    "mech_shell_unknown_node": {
        "nb": "[Ukjent node '{node}'. Konfigurerte noder: {nodes} — KTSB sine egne tjenester kjører på 'local'.]",
        "en": "[Unknown node '{node}'. Configured nodes: {nodes} — KTSB's own services run on 'local'.]",
        "de": "[Unbekannter Knoten '{node}'. Konfigurierte Knoten: {nodes} — KTSBs eigene Dienste laufen auf 'local'.]",
    },
    "mech_shell_sudo_not_allowed": {
        "nb": "[Avvist: sudo-kommandoen er ikke lagt inn for '{node}'. Tillatt: {allowed}]",
        "en": "[Rejected: that sudo command is not configured for '{node}'. Allowed: {allowed}]",
        "de": "[Abgelehnt: dieser sudo-Befehl ist für '{node}' nicht konfiguriert. Erlaubt: {allowed}]",
    },
    "mech_partial_after_error": {
        "nb": "[Mechanic ble avbrutt før han var ferdig ({error}). Her er de {rounds} verktøyresultatene han rakk å hente — ingen konklusjon fra ham:]",
        "en": "[Mechanic was interrupted before finishing ({error}). Here are the {rounds} tool results he gathered — no conclusion from him:]",
        "de": "[Mechanic wurde vor dem Abschluss unterbrochen ({error}). Hier sind die {rounds} Werkzeugergebnisse, die er abgerufen hat — kein Fazit von ihm:]",
    },
    "mech_no_response": {
        "nb": "[Ingen respons]",
        "en": "[No response]",
        "de": "[Keine Antwort]",
    },
    "mech_loop_detected": {
        "nb": "[Duplikat ignorert: {tool} ble allerede kalt med samme argumenter. Prøv et annet verktøy eller endre argumentene.]",
        "en": "[Duplicate ignored: {tool} was already called with the same arguments. Try a different tool or change the arguments.]",
        "de": "[Duplikat ignoriert: {tool} wurde bereits mit denselben Argumenten aufgerufen. Versuche ein anderes Werkzeug oder ändere die Argumente.]",
    },
    # ── executor_memory: STM history formatting ───────────────────────────────
    "mem_stm_header": {
        "nb": "STM-snapshot for {date} (lagret {saved_at} UTC):",
        "en": "STM snapshot for {date} (saved {saved_at} UTC):",
        "de": "STM-Snapshot für {date} (gespeichert {saved_at} UTC):",
    },
    "mem_daily_summary": {
        "nb": "\nDaglig sammendrag:\n",
        "en": "\nDaily summary:\n",
        "de": "\nTägliche Zusammenfassung:\n",
    },
    "mem_dialog_header": {
        "nb": "\nDialog ({turns} turns, siste {shown} vises):",
        "en": "\nDialogue ({turns} turns, last {shown} shown):",
        "de": "\nDialog ({turns} Turns, letzte {shown} angezeigt):",
    },
    "mem_actions_header": {
        "nb": "\nHandlinger (siste {shown} vellykkede av {total}):",
        "en": "\nActions (last {shown} successful of {total}):",
        "de": "\nAktionen (letzte {shown} erfolgreiche von {total}):",
    },
    "mem_truncated": {
        "nb": "[… resten er kuttet]",
        "en": "[… rest truncated]",
        "de": "[… Rest abgeschnitten]",
    },
    "mem_no_dates": {
        "nb": "ingen",
        "en": "none",
        "de": "keine",
    },
    "pers_profile_section": {
        "nb": "=== PROFIL (profile.yaml) ===",
        "en": "=== PROFILE (profile.yaml) ===",
        "de": "=== PROFIL (profile.yaml) ===",
    },
    "pers_observations_section": {
        "nb": "=== OBSERVASJONER (observations.md) ===",
        "en": "=== OBSERVATIONS (observations.md) ===",
        "de": "=== BEOBACHTUNGEN (observations.md) ===",
    },
    "mem_verdict_verified": {
        "nb": "bekreftet ✓",
        "en": "confirmed ✓",
        "de": "bestätigt ✓",
    },
    "mem_verdict_denied": {
        "nb": "avvist ✗",
        "en": "denied ✗",
        "de": "abgelehnt ✗",
    },
    "mem_verdict_test": {
        "nb": "merket som test 🧪",
        "en": "marked as test 🧪",
        "de": "als Test markiert 🧪",
    },
    # ── Tool definitions — descriptions sent to LLM ───────────────────────────
    # library (full, with online action)
    "tool_library_desc": {
        "nb": (
            "Spør Miss Library — lokale kilder og online LLM. Seks operasjoner: "
            "action='search': semantisk søk i lokal wiki (hvis installert) for faktaspørsmål, "
            "definisjoner, historiske data. Svarer alltid med kilde. "
            "action='fetch_article': hent hele Wikipedia-artikkelen etter et søk (krever 'title'). "
            "action='fetch_table': hent én tabell/liste fra en artikkel (krever 'title'). "
            "action='fetch_url': hent og oppsummer innhold fra en bestemt nettside (krever 'url'). "
            "action='law': norske lover og forskrifter fra Lovdata, lokalt (når norsk lov er slått på). "
            "Spørsmål om hva norsk lov sier: bruk action='law' FØR web_search. "
            "action='online': spør stor online LLM for bred kunnskap eller kompleks resonnering. "
            "VIKTIG: ved ingen svar/feil, si det klart. Tilby mechanic(action='search') kun om brukeren ønsker det."
        ),
        "en": (
            "Ask Miss Library — local sources and online LLM. Six operations: "
            "action='search': semantic search in the local wiki (if installed) for factual questions, "
            "definitions, historical data. Always answers with source. "
            "action='fetch_article': fetch the full Wikipedia article after a search (requires 'title'). "
            "action='fetch_table': fetch one table/list from an article (requires 'title'). "
            "action='fetch_url': fetch and summarize a specific webpage (requires 'url'). "
            "action='law': Norwegian laws and regulations from Lovdata, local (when Norwegian law is switched on). "
            "Questions about what Norwegian law says: use action='law' BEFORE web_search. "
            "action='online': ask a large online LLM for broad knowledge or complex reasoning. "
            "IMPORTANT: on no answer/error, say so clearly. Offer mechanic(action='search') only if user wants it."
        ),
        "de": (
            "Miss Library fragen — lokale Quellen und Online-LLM. Sechs Operationen: "
            "action='search': semantische Suche im lokalen Wiki (falls installiert) für Faktenfragen, "
            "Definitionen, historische Daten. Antwortet immer mit Quelle. "
            "action='fetch_article': den ganzen Wikipedia-Artikel nach einer Suche abrufen (erfordert 'title'). "
            "action='fetch_table': eine Tabelle/Liste aus einem Artikel abrufen (erfordert 'title'). "
            "action='fetch_url': Inhalt einer bestimmten Webseite abrufen und zusammenfassen (erfordert 'url'). "
            "action='law': norwegische Gesetze und Verordnungen von Lovdata, lokal (wenn norwegisches Recht eingeschaltet ist). "
            "Fragen dazu, was norwegisches Recht sagt: action='law' VOR web_search verwenden. "
            "action='online': großes Online-LLM für breites Wissen oder komplexes Denken fragen. "
            "WICHTIG: bei keiner Antwort/Fehler klar sagen. mechanic(action='search') nur anbieten, wenn der Nutzer es wünscht."
        ),
    },
    # library (no-online variant for child/teen)
    "tool_library_no_online_desc": {
        "nb": (
            "Spør Miss Library — lokal wiki-database. Tre operasjoner: "
            "action='search': semantisk søk i lokal wiki (1M+ artikler) for faktaspørsmål, "
            "definisjoner, historiske data. Svarer alltid med kilde. "
            "action='fetch_article': hent hele Wikipedia-artikkelen etter et søk (krever 'title'). "
            "action='fetch_url': hent og oppsummer innhold fra spesifikk nettside (krever 'url', kun tillatte domener)."
        ),
        "en": (
            "Ask Miss Library — local wiki database. Three operations: "
            "action='search': semantic search in local wiki (1M+ articles) for factual questions. Always answers with source. "
            "action='fetch_article': fetch the full Wikipedia article after a search (requires 'title'). "
            "action='fetch_url': fetch and summarize a specific webpage (requires 'url', trusted domains only)."
        ),
        "de": (
            "Miss Library fragen — lokale Wiki-Datenbank. Drei Operationen: "
            "action='search': semantische Suche im lokalen Wiki (1M+ Artikel) für Faktenfragen. Antwortet immer mit Quelle. "
            "action='fetch_article': vollständigen Wikipedia-Artikel abrufen (erfordert 'title'). "
            "action='fetch_url': Webseite abrufen und zusammenfassen (erfordert 'url', nur vertrauenswürdige Domains)."
        ),
    },
    "tool_library_action_desc": {
        "nb": "'search' = semantisk wiki-søk (krever 'query'). 'fetch_article' = hent hel artikkel (krever 'title'). 'fetch_table' = hent én tabell/liste fra en artikkel (krever 'title', valgfritt 'label'; uten 'label' får du listen over hva artikkelen har). 'fetch_url' = hent nettside (krever 'url'). 'online' = online LLM (krever 'query'). 'law' = norske lover og sentrale forskrifter (Lovdata, lokalt — krever 'query'; bruk lovens egne ord i 'keywords', f.eks. 'skadeverk', ikke 'hærverk').",
        "en": "'search' = semantic wiki search (requires 'query'). 'fetch_article' = fetch full article (requires 'title'). 'fetch_table' = fetch one table/list from an article (requires 'title', optional 'label'; without 'label' you get the list of what the article has). 'fetch_url' = fetch webpage (requires 'url'). 'online' = online LLM (requires 'query'). 'law' = Norwegian laws and central regulations (Lovdata, local — requires 'query'; put the law's own words in 'keywords', e.g. 'skadeverk', not 'hærverk').",
        "de": "'search' = semantische Wiki-Suche (erfordert 'query'). 'fetch_article' = vollständigen Artikel abrufen (erfordert 'title'). 'fetch_table' = eine Tabelle/Liste eines Artikels abrufen (erfordert 'title', optional 'label'; ohne 'label' erhältst du die Liste dessen, was der Artikel enthält). 'fetch_url' = Webseite abrufen (erfordert 'url'). 'online' = Online-LLM (erfordert 'query'). 'law' = norwegische Gesetze und zentrale Verordnungen (Lovdata, lokal — erfordert 'query'; die eigenen Begriffe des Gesetzes in 'keywords', z.B. 'skadeverk', nicht 'hærverk').",
    },
    "tool_library_no_online_action_desc": {
        "nb": "'search' = semantisk wiki-søk (krever 'query'). 'fetch_article' = hent hel artikkel (krever 'title'). 'fetch_table' = hent én tabell/liste fra en artikkel (krever 'title', valgfritt 'label'). 'fetch_url' = hent nettside (krever 'url').",
        "en": "'search' = semantic wiki search (requires 'query'). 'fetch_article' = fetch full article (requires 'title'). 'fetch_table' = fetch one table/list from an article (requires 'title', optional 'label'). 'fetch_url' = fetch webpage (requires 'url').",
        "de": "'search' = semantische Wiki-Suche (erfordert 'query'). 'fetch_article' = vollständigen Artikel abrufen (erfordert 'title'). 'fetch_table' = eine Tabelle/Liste eines Artikels abrufen (erfordert 'title', optional 'label'). 'fetch_url' = Webseite abrufen (erfordert 'url').",
    },
    "tool_library_query_desc": {
        "nb": "Hva som skal søkes etter: emnet eller spørsmålet med de viktigste ordene (f.eks. 'norske gullvinnere Sommer-OL 2004'). Ingen instruksjoner her — de hører hjemme i 'task'. Det er kun denne teksten som søkes i wikien; artikkelen med det best rangerte avsnittet velges. Kun ved action='search' og 'online'.",
        "en": "What to search for: the topic or question with its key words (e.g. 'Norwegian gold medallists 2004 Summer Olympics'). No instructions here — they belong in 'task'. Only this text is searched in the wiki; the article with the best-ranked passage is chosen. Only for action='search' and 'online'.",
        "de": "Wonach gesucht wird: das Thema oder die Frage mit den wichtigsten Wörtern (z. B. 'norwegische Goldmedaillengewinner Olympische Sommerspiele 2004'). Keine Anweisungen hier — die gehören in 'task'. Nur dieser Text wird im Wiki gesucht; der Artikel mit dem am besten bewerteten Abschnitt wird gewählt. Nur bei action='search' und 'online'.",
    },
    "tool_library_task_desc": {
        "nb": "Valgfri presis oppgave til Miss Library: hva som skal rapporteres (f.eks. bare gjeldende verdi, en liste, en sammenligning) og hvor kort. Hun svarer med belegg (ordrette sitater med kilde); er kildene uenige, melder hun alltid hvilken kilde som sier hva — gi det videre til brukeren. 'task' søkes aldri — den går bare til svartrinnet. Kun ved action='search'.",
        "en": "Optional precise task for Miss Library: what to report (e.g. only the current value, a list, a comparison) and how short. She answers with evidence (verbatim quotes with source); if sources disagree she always reports which source says what — pass that on to the user. 'task' is never searched — it only goes to the answer step. Only for action='search'.",
        "de": "Optionale präzise Aufgabe für Miss Library: was berichtet werden soll (z. B. nur der aktuelle Wert, eine Liste, ein Vergleich) und wie kurz. Sie antwortet mit Belegen (wörtliche Zitate mit Quelle); widersprechen sich Quellen, meldet sie immer, welche Quelle was sagt — gib das an den Nutzer weiter. 'task' wird nie gesucht — sie geht nur in den Antwortschritt. Nur bei action='search'.",
    },
    "tool_library_title_desc": {
        "nb": "Artikkeltittel nøyaktig som i wiki-søkeresultatet. Kun ved action='fetch_article'.",
        "en": "Article title exactly as in the wiki search result. Only for action='fetch_article'.",
        "de": "Artikeltitel genau wie im Wiki-Suchergebnis. Nur bei action='fetch_article'.",
    },
    # appended to both library descriptions, generated from the wiki registry (P111 Phase 3)
    "tool_library_wiki_suffix": {
        "nb": " Wikier: {wikis}. 'search' søker i alle samtidig (primær først) og gir et kort, filtrert svar med kilde og wiki. Bruk 'wiki' for å velge én eller flere.",
        "en": " Wikis: {wikis}. 'search' searches all of them at once (primary first) and returns a short, filtered answer with source and wiki. Use 'wiki' to pick one or more.",
        "de": " Wikis: {wikis}. 'search' durchsucht alle gleichzeitig (primäres zuerst) und liefert eine kurze, gefilterte Antwort mit Quelle und Wiki. Mit 'wiki' eines oder mehrere wählen.",
    },
    "tool_library_label_desc": {
        "nb": "Navnet på tabellen/listen, slik det står i søkeresultatet eller artikkelen. Kun ved action='fetch_table'.",
        "en": "The table/list name as shown in the search result or article. Only for action='fetch_table'.",
        "de": "Der Name der Tabelle/Liste wie im Suchergebnis oder Artikel. Nur bei action='fetch_table'.",
    },
    "tool_library_mode_desc": {
        "nb": "'raw' (standard) = tabellen som i kilden. 'translated' = ord oversatt til ditt språk, alle verdier uendret. Kun ved action='fetch_table'.",
        "en": "'raw' (default) = the table as in the source. 'translated' = words translated into your language, all values unchanged. Only for action='fetch_table'.",
        "de": "'raw' (Standard) = die Tabelle wie in der Quelle. 'translated' = Wörter in deine Sprache übersetzt, alle Werte unverändert. Nur bei action='fetch_table'.",
    },
    "tool_library_wiki_desc": {
        "nb": "Valgfritt: hvilken wiki (f.eks. 'no', 'en' eller 'no,en'). Standard: alle, primær først. Ved action='search' og 'fetch_article'.",
        "en": "Optional: which wiki (e.g. 'no', 'en' or 'no,en'). Default: all, primary first. For action='search' and 'fetch_article'.",
        "de": "Optional: welches Wiki (z. B. 'no', 'en' oder 'no,en'). Standard: alle, primäres zuerst. Bei action='search' und 'fetch_article'.",
    },
    "tool_library_url_desc": {
        "nb": "Fullstendig URL (https://...). Kun ved action='fetch_url'.",
        "en": "Full URL (https://...). Only for action='fetch_url'.",
        "de": "Vollständige URL (https://...). Nur bei action='fetch_url'.",
    },
    "tool_library_max_chars_desc": {
        "nb": "Maks tegn å returnere ved fetch_article. Standard 8000.",
        "en": "Max characters to return for fetch_article. Default 8000.",
        "de": "Maximale Zeichen für fetch_article. Standard 8000.",
    },
    # les_ha
    "tool_les_ha_desc": {
        "nb": (
            "IKKE for vær, temperatur, nedbør, vind — bruk get_weather (ett kall, alt inkludert). "
            "Les informasjon fra Home Assistant. Tre operasjoner: "
            "action='room_list': hent alle romnavn. "
            "action='room_devices': hent enheter i ett rom (krever 'room'). "
            "action='status': les tilstand på én enhet (krever 'entity_id'). "
            "Fremgangsmåte: room_list → room_devices → status. Aldri gjett entity_id, "
            "men hopp over steg du allerede vet svaret på."
        ),
        "en": (
            "NOT for weather, temperature, precipitation, wind — use get_weather (one call, everything included). "
            "Read information from Home Assistant. Three operations: "
            "action='room_list': get all room names. "
            "action='room_devices': get devices in one room (requires 'room'). "
            "action='status': read current state of one entity (requires 'entity_id'). "
            "Workflow: room_list → room_devices → status. Never guess entity_id, "
            "but skip steps you already know the answer to."
        ),
        "de": (
            "NICHT für Wetter, Temperatur, Niederschlag, Wind — get_weather verwenden (ein Aufruf, alles inklusive). "
            "Informationen von Home Assistant lesen. Drei Operationen: "
            "action='room_list': alle Raumnamen abrufen. "
            "action='room_devices': Geräte in einem Raum abrufen (erfordert 'room'). "
            "action='status': aktuellen Zustand einer Entität lesen (erfordert 'entity_id'). "
            "Vorgehen: room_list → room_devices → status. Niemals entity_id raten, "
            "aber Schritte überspringen, deren Antwort bereits bekannt ist."
        ),
    },
    "tool_les_ha_action_desc": {
        "nb": "'room_list' = alle romnavn. 'room_devices' = enheter i rom (krever 'room'). 'status' = tilstand på enhet (krever 'entity_id').",
        "en": "'room_list' = all room names. 'room_devices' = devices in room (requires 'room'). 'status' = entity state (requires 'entity_id').",
        "de": "'room_list' = alle Raumnamen. 'room_devices' = Geräte im Raum (erfordert 'room'). 'status' = Entitätszustand (erfordert 'entity_id').",
    },
    "tool_les_ha_room_desc": {
        "nb": "Romnavn, f.eks. 'verksted', 'stue'. Kun ved action='room_devices'.",
        "en": "Room name, e.g. 'verksted', 'stue'. Only for action='room_devices'.",
        "de": "Raumname, z.B. 'verksted', 'stue'. Nur bei action='room_devices'.",
    },
    "tool_les_ha_entity_id_desc": {
        "nb": "HA entity_id, f.eks. 'sensor.netatmo_utendors_modul_temperatur'. Kun ved action='status'.",
        "en": "HA entity_id, e.g. 'sensor.netatmo_utendors_modul_temperatur'. Only for action='status'.",
        "de": "HA entity_id, z.B. 'sensor.netatmo_utendors_modul_temperatur'. Nur bei action='status'.",
    },
    # styr_enhet
    "tool_styr_enhet_desc": {
        "nb": "Styrer enheter og henter sensorhistorikk fra Home Assistant. Bruk les_ha(action='room_list') først hvis du ikke kjenner entity_id. For lys: set_level + brightness_pct, set_color_temp + color_temp_kelvin, set_color + rgb_color. For historikk (f.eks. nedbør siste mnd): action=ha_history + entity_id + history_days + history_period.",
        "en": "Controls devices and fetches sensor history from Home Assistant. Use les_ha(action='room_list') first if you don't know the entity_id. For lights: set_level + brightness_pct, set_color_temp + color_temp_kelvin, set_color + rgb_color. For history (e.g. precipitation last month): action=ha_history + entity_id + history_days + history_period.",
        "de": "Steuert Geräte und ruft Sensorverlauf aus Home Assistant ab. Zuerst les_ha(action='room_list') verwenden, wenn entity_id unbekannt. Für Lichter: set_level + brightness_pct, set_color_temp + color_temp_kelvin, set_color + rgb_color. Für Verlauf (z.B. Niederschlag letzten Monat): action=ha_history + entity_id + history_days + history_period.",
    },
    "tool_styr_enhet_entity_id_desc": {
        "nb": "HA entity_id, f.eks. 'light.taklys_stue' eller 'sensor.netatmo_regn_maler_precipitation_today'.",
        "en": "HA entity_id, e.g. 'light.taklys_stue' or 'sensor.netatmo_regn_maler_precipitation_today'.",
        "de": "HA entity_id, z.B. 'light.taklys_stue' oder 'sensor.netatmo_regn_maler_precipitation_today'.",
    },
    "tool_styr_enhet_action_desc": {
        "nb": "Handlingen. set_level=lysstyrke, set_color_temp=fargetemperatur, set_color=RGB. ha_history=hent sensorhistorikk (bruk history_days og history_period).",
        "en": "The action. set_level=brightness, set_color_temp=color temperature, set_color=RGB. ha_history=fetch sensor history (use history_days and history_period).",
        "de": "Die Aktion. set_level=Helligkeit, set_color_temp=Farbtemperatur, set_color=RGB. ha_history=Sensorverlauf abrufen (history_days und history_period verwenden).",
    },
    "tool_styr_enhet_history_days_desc": {
        "nb": "Antall dager bakover for historikk (standard: 7). Bruk f.eks. 30 for siste måned.",
        "en": "Number of days back for history (default: 7). Use e.g. 30 for last month.",
        "de": "Anzahl der Tage zurück für den Verlauf (Standard: 7). Z.B. 30 für den letzten Monat.",
    },
    "tool_styr_enhet_history_period_desc": {
        "nb": "Aggregeringsperiode: 'day' (per dag), 'week' (per uke), 'month' (per måned). Standard: 'day'.",
        "en": "Aggregation period: 'day' (per day), 'week' (per week), 'month' (per month). Default: 'day'.",
        "de": "Aggregationszeitraum: 'day' (pro Tag), 'week' (pro Woche), 'month' (pro Monat). Standard: 'day'.",
    },
    "tool_styr_enhet_brightness_pct_desc": {
        "nb": "Lysstyrke 0–100%. Brukes med action=set_level.",
        "en": "Brightness 0–100%. Used with action=set_level.",
        "de": "Helligkeit 0–100%. Wird mit action=set_level verwendet.",
    },
    "tool_styr_enhet_color_temp_desc": {
        "nb": "Fargetemperatur i Kelvin (2200=stearinlys, 2700=varm, 4000=nøytral, 6500=dagslys). Brukes med action=set_color_temp.",
        "en": "Color temperature in Kelvin (2200=candle, 2700=warm, 4000=neutral, 6500=daylight). Used with action=set_color_temp.",
        "de": "Farbtemperatur in Kelvin (2200=Kerze, 2700=warm, 4000=neutral, 6500=Tageslicht). Mit action=set_color_temp.",
    },
    "tool_styr_enhet_rgb_color_desc": {
        "nb": "RGB-farge [r,g,b] 0–255. Brukes med action=set_color.",
        "en": "RGB color [r,g,b] 0–255. Used with action=set_color.",
        "de": "RGB-Farbe [r,g,b] 0–255. Mit action=set_color.",
    },
    # søk_nett
    "tool_søk_nett_desc": {
        "nb": (
            "Søk på nettet for fakta, nyheter, lover, oppskrifter, teknisk dokumentasjon. "
            "Ikke for smarthus-styring, ting du vet, eller vær (bruk get_weather). "
            "Ved ingen treff/godkjente kilder: si det ærlig. Ikke prøv igjen automatisk. "
            "Tilby mechanic(action='search') kun om brukeren ønsker det."
        ),
        "en": (
            "Search the web for facts, news, laws, recipes, technical documentation. "
            "Not for smart home control, things you know, or weather (use get_weather). "
            "On no results/trusted sources: say so honestly. Do not retry automatically. "
            "Offer mechanic(action='search') only if user wants it."
        ),
        "de": (
            "Im Internet suchen für Fakten, Nachrichten, Gesetze, Rezepte, technische Dokumentation. "
            "Nicht für Smart-Home-Steuerung, bekannte Dinge oder Wetter (get_weather verwenden). "
            "Bei keinen Treffern/vertrauenswürdigen Quellen: ehrlich sagen. Nicht automatisch erneut suchen. "
            "mechanic(action='search') nur anbieten wenn Nutzer es möchte."
        ),
    },
    "tool_søk_nett_query_desc": {
        "nb": "Konkret, presist søk — f.eks. 'strafferamme hærverk Norge'. Norsk eller engelsk.",
        "en": "Concrete, precise query — e.g. 'vandalism sentencing Norway'. Norwegian or English.",
        "de": "Konkrete, präzise Suchanfrage — z.B. 'Sachbeschädigung Strafmaß Norwegen'.",
    },
    "web_search_not_configured": {
        "nb": "Nettsøk er ikke satt opp: ingen søkemotor er klar (SearXNG mangler adresse, eller Brave mangler API-nøkkel). En admin kan sette det opp under Innstillinger → Nettsøk/Vær.",
        "en": "Web search is not set up: no search engine is ready (SearXNG has no address, or Brave has no API key). An admin can set it up under Settings → Web Search & Weather.",
        "de": "Die Websuche ist nicht eingerichtet: keine Suchmaschine ist bereit (SearXNG ohne Adresse oder Brave ohne API-Schlüssel). Ein Admin kann sie unter Einstellungen → Websuche & Wetter einrichten.",
    },
    "web_search_timeout": {
        "nb": "Nettsøket tok for lang tid (over {s} sekunder) og ble avbrutt. Ingen svar fra nettet denne gangen.",
        "en": "The web search took too long (over {s} seconds) and was stopped. No answer from the web this time.",
        "de": "Die Websuche hat zu lange gedauert (über {s} Sekunden) und wurde abgebrochen. Diesmal keine Antwort aus dem Web.",
    },
    "web_trust_list_unreadable": {
        "nb": "Listen over godkjente kilder (configs/trusted_sources.yaml) kunne ikke leses, så ingen nettsider hentes før den er rettet. En admin kan sjekke den under Innstillinger → Nettsøk/Vær → Vitlistede nettsider.",
        "en": "The trusted sources list (configs/trusted_sources.yaml) could not be read, so no web pages are fetched until it is fixed. An admin can check it under Settings → Web Search & Weather → Whitelisted websites.",
        "de": "Die Liste vertrauenswürdiger Quellen (configs/trusted_sources.yaml) konnte nicht gelesen werden, daher werden keine Webseiten abgerufen, bis sie korrigiert ist. Ein Admin kann sie unter Einstellungen → Websuche & Wetter → Whitelist-Webseiten prüfen.",
    },
    "tool_subject_desc": {
        "nb": "Det konkrete tingen spørsmålet handler om (sted, bygning, person, organisasjon, verk, produkt), med det vanligste navnet — helst slik den heter på Wikipedia, f.eks. 'Wieliczka Salt Mine'. Brukes til å merke kilder som handler om noe annet. Utelat ved generelle spørsmål.",
        "en": "The specific thing the question is about (place, building, person, organisation, work, product), by its most common name — preferably as titled on Wikipedia, e.g. 'Wieliczka Salt Mine'. Used to flag sources about something else. Leave out for general questions.",
        "de": "Das konkrete Ding, um das es in der Frage geht (Ort, Gebäude, Person, Organisation, Werk, Produkt), mit dem gebräuchlichsten Namen — am besten wie auf Wikipedia, z.B. 'Wieliczka Salt Mine'. Dient dazu, Quellen über etwas anderes zu markieren. Bei allgemeinen Fragen weglassen.",
    },
    "tool_søk_nett_near_home_desc": {
        "nb": (
            "true = spørsmålet gjelder husstandens nærområde og nevner INGEN andre steder eller navn "
            "(f.eks. 'når stenger apoteket', 'hva skjer i helga') — hjemstedet legges da til søket. "
            "false = et sted, land eller navn er nevnt, eller spørsmålet er ikke stedsavhengig. "
            "Skriv aldri hjemstedet inn i query selv."
        ),
        "en": (
            "true = the question is about the household's local area and names NO other place or name "
            "(e.g. 'when does the pharmacy close', 'what's on this weekend') — the home location is then added to the search. "
            "false = a place, country or name is mentioned, or the question does not depend on location. "
            "Never write the home location into query yourself."
        ),
        "de": (
            "true = die Frage betrifft die Umgebung des Haushalts und nennt KEINEN anderen Ort oder Namen "
            "(z.B. 'wann schließt die Apotheke', 'was ist am Wochenende los') — der Wohnort wird dann zur Suche hinzugefügt. "
            "false = ein Ort, Land oder Name wird genannt, oder die Frage ist nicht ortsabhängig. "
            "Schreibe den Wohnort nie selbst in query."
        ),
    },
    "gen_correction_near_home": {
        "nb": (
            "[SYSTEM: web_search ble IKKE kjørt — near_home mangler. Kall web_search på nytt med "
            "near_home=true hvis spørsmålet gjelder nærområdet og ingen andre steder eller navn er nevnt, "
            "ellers near_home=false.]"
        ),
        "en": (
            "[SYSTEM: web_search was NOT run — near_home is missing. Call web_search again with "
            "near_home=true if the question is about the local area and no other place or name is mentioned, "
            "otherwise near_home=false.]"
        ),
        "de": (
            "[SYSTEM: web_search wurde NICHT ausgeführt — near_home fehlt. Rufe web_search erneut auf mit "
            "near_home=true, wenn die Frage die Umgebung betrifft und kein anderer Ort oder Name genannt wird, "
            "sonst near_home=false.]"
        ),
    },
    # get_weather
    "tool_get_weather_desc": {
        "nb": (
            "Henter komplett værinformasjon i ett parallelt kall: prognose fra konfigurert "
            "provider (met.no, Open-Meteo, OWM, WeatherAPI eller PirateWeather), lokale "
            "HA-sensorer, visuell kameravisning og tidevann. "
            "Bruk for ALT om vær, temperatur, nedbør, vind, UV og luftkvalitet — "
            "lokalt eller for andre steder. "
            "ALDRI les_ha for å lese værsensorer manuelt. ALDRI søk_nett for vær."
        ),
        "en": (
            "Get complete weather in one parallel call: forecast from configured provider "
            "(met.no, Open-Meteo, OWM, WeatherAPI, or PirateWeather), local HA sensors, "
            "camera sky view, and tide data. "
            "Use for ANYTHING about weather, temperature, precipitation, wind, UV, or air quality — "
            "locally or for other locations. "
            "NEVER use les_ha to read weather sensors manually. NEVER use søk_nett for weather."
        ),
        "de": (
            "Vollständige Wetterinformationen in einem parallelen Aufruf: Prognose vom konfigurierten "
            "Anbieter (met.no, Open-Meteo, OWM, WeatherAPI oder PirateWeather), lokale HA-Sensoren, "
            "Kamera-Himmelsaufnahme und Gezeitendaten. "
            "Für ALLES über Wetter, Temperatur, Niederschlag, Wind, UV oder Luftqualität — "
            "lokal oder für andere Orte. "
            "NIEMALS les_ha für Wettersensoren. NIEMALS søk_nett für Wetter."
        ),
    },
    "tool_get_weather_location_desc": {
        "nb": "Stedsnavn, f.eks. 'Oslo'. Utelat for lokalt vær.",
        "en": "Place name, e.g. 'Oslo'. Omit for local weather.",
        "de": "Ortsname, z.B. 'Oslo'. Weglassen für lokales Wetter.",
    },
    # timer
    "tool_timer_desc": {
        "nb": (
            "Klokkeslett og timere. Fem operasjoner: "
            "action='clock': returnerer nåværende klokkeslett og dato. "
            "action='set': sett en timer. Bestem action_type, tts_text og notify_via NÅ — ikke ved avfyring. "
            "For enkle påminnelser: action_type='tts_response', tts_text='tekst som leses opp', notify_via=['tts']. "
            "For stille påminnelse i chat: action_type='none', notify_via=['chat']. "
            "Bruk 'at_time' for klokkeslett/dato eller 'in_seconds' for enkel forsinkelse. "
            "action='cancel': avbryt en timer (krever 'timer_id' fra action='list'). "
            "action='list': se alle aktive timere med ID og gjenværende tid. "
            "action='ack': kvitter en levert chat-påminnelse (krever 'notif_id')."
        ),
        "en": (
            "Clock and timers. Five operations: "
            "action='clock': returns current time and date. "
            "action='set': set a timer. Decide action_type, tts_text and notify_via NOW — not at fire time. "
            "For simple reminders: action_type='tts_response', tts_text='text to speak', notify_via=['tts']. "
            "For silent chat reminder: action_type='none', notify_via=['chat']. "
            "Use 'at_time' for a specific time/date or 'in_seconds' for a simple delay. "
            "action='cancel': cancel a timer (requires 'timer_id' from action='list'). "
            "action='list': see all active timers with ID and remaining time. "
            "action='ack': acknowledge a delivered chat reminder (requires 'notif_id')."
        ),
        "de": (
            "Uhr und Timer. Fünf Operationen: "
            "action='clock': gibt aktuelle Uhrzeit und Datum zurück. "
            "action='set': Timer setzen. action_type, tts_text und notify_via JETZT festlegen — nicht beim Auslösen. "
            "Für einfache Erinnerungen: action_type='tts_response', tts_text='vorzulesender Text', notify_via=['tts']. "
            "Für stille Chat-Erinnerung: action_type='none', notify_via=['chat']. "
            "Verwende 'at_time' für eine bestimmte Zeit/Datum oder 'in_seconds' für einfache Verzögerung. "
            "action='cancel': Timer abbrechen (erfordert 'timer_id' aus action='list'). "
            "action='list': alle aktiven Timer mit ID und verbleibender Zeit anzeigen. "
            "action='ack': Chat-Erinnerung bestätigen (erfordert 'notif_id')."
        ),
    },
    "tool_timer_action_desc": {
        "nb": "'clock' = tid og dato. 'set' = ny timer (krever action_type + tts_text eller ha_payload + én av 'at_time'/'in_seconds'). 'cancel' = avbryt (krever 'timer_id'). 'list' = vis aktive timere. 'ack' = kvitter chat-påminnelse (krever 'notif_id').",
        "en": "'clock' = time and date. 'set' = new timer (requires action_type + tts_text or ha_payload + one of 'at_time'/'in_seconds'). 'cancel' = cancel (requires 'timer_id'). 'list' = show active timers. 'ack' = acknowledge chat reminder (requires 'notif_id').",
        "de": "'clock' = Zeit und Datum. 'set' = neuer Timer (erfordert action_type + tts_text oder ha_payload + eines von 'at_time'/'in_seconds'). 'cancel' = abbrechen (erfordert 'timer_id'). 'list' = aktive Timer anzeigen. 'ack' = Chat-Erinnerung bestätigen (erfordert 'notif_id').",
    },
    "tool_timer_prompt_desc": {
        "nb": "Meldingen du vil vekkes med. Kun ved action='set'.",
        "en": "The message you want to be woken with. Only for action='set'.",
        "de": "Die Nachricht, mit der du geweckt werden möchtest. Nur bei action='set'.",
    },
    "tool_timer_at_time_desc": {
        "nb": "Tidspunkt: '07:30', 'fredag 08:00', '2026-05-01 09:00'. Kun ved action='set'.",
        "en": "Time: '07:30', 'friday 08:00', '2026-05-01 09:00'. Only for action='set'.",
        "de": "Zeitpunkt: '07:30', 'freitag 08:00', '2026-05-01 09:00'. Nur bei action='set'.",
    },
    "tool_timer_in_seconds_desc": {
        "nb": "Forsinkelse i sekunder (minimum 5). Kun ved action='set', kun hvis at_time ikke er satt.",
        "en": "Delay in seconds (minimum 5). Only for action='set', only if at_time is not set.",
        "de": "Verzögerung in Sekunden (Minimum 5). Nur bei action='set', nur wenn at_time nicht gesetzt.",
    },
    "tool_timer_repeat_desc": {
        "nb": "Gjentakelse. Kun ved action='set'.",
        "en": "Repeat interval. Only for action='set'.",
        "de": "Wiederholungsintervall. Nur bei action='set'.",
    },
    "tool_timer_notify_desc": {
        "nb": "Om bruker skal varsles. Standard: true. Kun ved action='set'.",
        "en": "Whether to notify the user. Default: true. Only for action='set'.",
        "de": "Ob der Nutzer benachrichtigt werden soll. Standard: true. Nur bei action='set'.",
    },
    "tool_timer_timer_id_desc": {
        "nb": "Timer-ID fra action='list'. Kun ved action='cancel'.",
        "en": "Timer ID from action='list'. Only for action='cancel'.",
        "de": "Timer-ID aus action='list'. Nur bei action='cancel'.",
    },
    "tool_timer_action_type_desc": {
        "nb": "Hva timeren gjør ved avfyring. 'tts_response': spill tts_text på høyttaler. 'ha_action': kall HA direkte (krever ha_payload). 'llm_task': Kåre kjøres med prompt (sjelden). 'none': ingen handling, kun varsling. Standard: 'tts_response'.",
        "en": "What the timer does when it fires. 'tts_response': play tts_text on speaker. 'ha_action': call HA directly (requires ha_payload). 'llm_task': run Kåre with prompt (rare). 'none': no action, notification only. Default: 'tts_response'.",
        "de": "Was der Timer beim Auslösen tut. 'tts_response': tts_text auf Lautsprecher abspielen. 'ha_action': HA direkt aufrufen (erfordert ha_payload). 'llm_task': Kåre mit Prompt ausführen (selten). 'none': keine Aktion, nur Benachrichtigung. Standard: 'tts_response'.",
    },
    "tool_timer_notify_via_desc": {
        "nb": "Leveringskanaler som liste. 'tts': spill på høyttaler. 'chat': legg i brukerens chat-kø til neste interaksjon. Kombiner: ['tts','chat'] for begge. Standard: ['tts'] hvis tts_text er satt, ellers ['chat'].",
        "en": "Delivery channels as a list. 'tts': play on speaker. 'chat': queue in user's chat until next interaction. Combine: ['tts','chat'] for both. Default: ['tts'] if tts_text is set, otherwise ['chat'].",
        "de": "Zustellkanäle als Liste. 'tts': auf Lautsprecher abspielen. 'chat': in den Chat-Puffer des Nutzers legen bis zur nächsten Interaktion. Kombinieren: ['tts','chat'] für beide. Standard: ['tts'] wenn tts_text gesetzt, sonst ['chat'].",
    },
    "tool_timer_tts_text_desc": {
        "nb": "Teksten som leses opp når timeren går av. Bestem innholdet nå — ikke ved avfyring. Eksempel: 'Poteter på!'. Kun ved action='set'.",
        "en": "The text to speak when the timer fires. Decide the content now — not at fire time. Example: 'Time to put on potatoes!'. Only for action='set'.",
        "de": "Der Text, der gesprochen wird, wenn der Timer auslöst. Inhalt jetzt festlegen — nicht beim Auslösen. Beispiel: 'Kartoffeln aufsetzen!'. Nur bei action='set'.",
    },
    "tool_timer_target_node_desc": {
        "nb": "Hvilken høyttaler/node TTS skal spilles på. Utelat for å bruke noden der forespørselen kom fra. Eksempel: 'stue_høyttaler'. Kun ved action='set'.",
        "en": "Which speaker/node to play TTS on. Omit to use the node where the request came from. Example: 'living_room_speaker'. Only for action='set'.",
        "de": "Welcher Lautsprecher/Node für TTS verwendet wird. Weglassen, um den Node der Anfrage zu verwenden. Beispiel: 'wohnzimmer_lautsprecher'. Nur bei action='set'.",
    },
    "tool_timer_ha_payload_desc": {
        "nb": "HA-handlingen ved avfyring som JSON-streng. Felter: 'action' og 'entity_id'. Gyldige actions: turn_on, turn_off, toggle, set_level. Eksempel: '{\"action\": \"turn_off\", \"entity_id\": \"light.verksted\"}'. Kun ved action_type='ha_action'.",
        "en": "The HA action to perform when the timer fires, as a JSON string. Fields: 'action' and 'entity_id'. Valid actions: turn_on, turn_off, toggle, set_level. Example: '{\"action\": \"turn_off\", \"entity_id\": \"light.workshop\"}'. Only for action_type='ha_action'.",
        "de": "Die HA-Aktion beim Auslösen als JSON-Zeichenkette. Felder: 'action' und 'entity_id'. Gültige Aktionen: turn_on, turn_off, toggle, set_level. Beispiel: '{\"action\": \"turn_off\", \"entity_id\": \"light.werkstatt\"}'. Nur bei action_type='ha_action'.",
    },
    "tool_timer_for_user_id_desc": {
        "nb": "Sett timer for et barn (kun for foreldre med can_manage_child_timers). Oppgi bruker-ID til barnet. Kun ved action='set'.",
        "en": "Set timer for a child user (parents with can_manage_child_timers only). Provide the child's user ID. Only for action='set'.",
        "de": "Timer für ein Kind setzen (nur für Eltern mit can_manage_child_timers). Kind-Benutzer-ID angeben. Nur bei action='set'.",
    },
    "tool_timer_notif_id_desc": {
        "nb": "ID til notification som skal kvitteres. Hentet fra pending-listen i kontekst. Kun ved action='ack'.",
        "en": "ID of the notification to acknowledge. Taken from the pending list in context. Only for action='ack'.",
        "de": "ID der zu bestätigenden Benachrichtigung. Aus der ausstehenden Liste im Kontext entnehmen. Nur bei action='ack'.",
    },
    "timer_invalid_action": {
        "nb": "Ugyldig action '{action}'. Gyldige: {valid}.",
        "en": "Invalid action '{action}'. Valid: {valid}.",
        "de": "Ungültige Aktion '{action}'. Gültig: {valid}.",
    },
    "timer_invalid_channel": {
        "nb": "Ugyldig leveringskanal: {channels}. Gyldige: tts, chat.",
        "en": "Invalid delivery channel: {channels}. Valid: tts, chat.",
        "de": "Ungültiger Zustellkanal: {channels}. Gültig: tts, chat.",
    },
    "timer_acked": {
        "nb": "Påminnelse {notif_id} kvittert.",
        "en": "Reminder {notif_id} acknowledged.",
        "de": "Erinnerung {notif_id} bestätigt.",
    },
    "timer_notif_not_found": {
        "nb": "Fant ikke påminnelse {notif_id} for bruker {user_id}.",
        "en": "Reminder {notif_id} not found for user {user_id}.",
        "de": "Erinnerung {notif_id} für Nutzer {user_id} nicht gefunden.",
    },
    "timer_pending_context": {
        "nb": "⏰ Ukvittert påminnelse til {user_id}: «{message}» [notif_id={notif_id}] — lever denne og kall deretter timer:ack notif_id={notif_id}.",
        "en": '⏰ Unacknowledged reminder for {user_id}: "{message}" [notif_id={notif_id}] — deliver this and then call timer:ack notif_id={notif_id}.',
        "de": "⏰ Unbestätigte Erinnerung für {user_id}: '{message}' [notif_id={notif_id}] — diese zustellen und dann timer:ack notif_id={notif_id} aufrufen.",
    },
    "timer_llm_task_failed": {
        "nb": "⚠️ LLM timer-oppgave feilet etter {max_retries} forsøk [{timer_id}]: {prompt_preview}",
        "en": "⚠️ LLM timer task failed after {max_retries} attempts [{timer_id}]: {prompt_preview}",
        "de": "⚠️ LLM-Timer-Aufgabe nach {max_retries} Versuchen fehlgeschlagen [{timer_id}]: {prompt_preview}",
    },
    "timer_max_reached": {
        "nb": "For mange timere: du har nådd grensen på {max} aktive timere.",
        "en": "Too many timers: you have reached the limit of {max} active timers.",
        "de": "Zu viele Timer: du hast das Limit von {max} aktiven Timern erreicht.",
    },
    "timer_target_not_child": {
        "nb": "{user_id} er ikke barn eller tenåring. Du kan kun sette timere for brukere med rolle child eller teen.",
        "en": "{user_id} is not a child or teen. You can only set timers for users with role child or teen.",
        "de": "{user_id} ist kein Kind oder Teenager. Du kannst nur Timer für Nutzer mit Rolle child oder teen setzen.",
    },
    "timer_child_permission_denied": {
        "nb": "Du har ikke tillatelse til å sette timere for barn. Be en administrator aktivere foreldretillatelse for kontoen din.",
        "en": "You do not have permission to set timers for children. Ask an administrator to enable parental permission for your account.",
        "de": "Du hast keine Berechtigung, Timer für Kinder zu setzen. Bitte einen Administrator, die Elternberechtigung für dein Konto zu aktivieren.",
    },
    # les_møte
    "tool_les_møte_desc": {
        "nb": "Les innholdet fra et av Kåres nattlige møter. type='reflection': refleksjonsmøter. type='development': tekniske utviklingsmøter. Uten dato: siste møte. Med dato (YYYY-MM-DD): møtet fra den datoen.",
        "en": "Read the content from one of Kåre's nightly meetings. type='reflection': reflection meetings. type='development': technical developer meetings. Without date: latest meeting. With date (YYYY-MM-DD): meeting from that date.",
        "de": "Inhalt eines der nächtlichen Meetings von Kåre lesen. type='reflection': Reflexionsmeetings. type='development': technische Entwicklermeetings. Ohne Datum: letztes Meeting. Mit Datum (YYYY-MM-DD): Meeting von diesem Tag.",
    },
    "tool_les_møte_type_desc": {
        "nb": "'reflection' = nattlig refleksjonsmøte. 'development' = teknisk møte.",
        "en": "'reflection' = nightly reflection meeting. 'development' = technical developer meeting.",
        "de": "'reflection' = nächtliches Reflexionsmeeting. 'development' = technisches Entwicklermeeting.",
    },
    "tool_les_møte_date_desc": {
        "nb": "Dato YYYY-MM-DD. Utelat for siste møte.",
        "en": "Date YYYY-MM-DD. Omit for latest meeting.",
        "de": "Datum YYYY-MM-DD. Weglassen für letztes Meeting.",
    },
    # minne
    "tool_minne_desc": {
        "nb": (
            "Kåres hukommelse — fire operasjoner: "
            "action='search': søk i langtidsminnet — samtaler som inneholder minst ett av søkeordene, pluss episodesammendrag nærmest i betydning. "
            "action='fetch_unverified': hent interaksjoner som ikke er bekreftet ennå, nyeste først. "
            "action='confirm': merk interaksjoner som verified/denied/test (krever 'ids' og 'dom'). "
            "action='fetch_stm': hent eldre STM-snapshot — uten dato: vis tilgjengelige datoer; "
            "med dato='YYYY-MM-DD': hent dialog fra den dagen."
        ),
        "en": (
            "Kåre's memory — four operations: "
            "action='search': search long-term memory — conversations containing at least one of the keywords, plus episode summaries closest in meaning. "
            "action='fetch_unverified': fetch interactions not yet confirmed by the user, newest first. "
            "action='confirm': mark interactions as verified/denied/test (requires 'ids' and 'dom'). "
            "action='fetch_stm': fetch older STM snapshot — without date: list available dates; "
            "with date='YYYY-MM-DD': fetch dialogue from that day."
        ),
        "de": (
            "Kåres Gedächtnis — vier Operationen: "
            "action='search': Suche im Langzeitgedächtnis — Gespräche mit mindestens einem der Suchwörter, plus bedeutungsnächste Episodenzusammenfassungen. "
            "action='fetch_unverified': noch nicht bestätigte Interaktionen abrufen, neueste zuerst. "
            "action='confirm': Interaktionen als verified/denied/test markieren (erfordert 'ids' und 'dom'). "
            "action='fetch_stm': älteres STM-Snapshot abrufen — ohne Datum: verfügbare Daten anzeigen; "
            "mit Datum='YYYY-MM-DD': Dialog von diesem Tag abrufen."
        ),
    },
    "tool_minne_action_desc": {
        "nb": "'search' = LTM-søk (krever 'query'). 'fetch_unverified' = ubekreftede (valgfri 'count', 'skip'). 'confirm' = bekreft (krever 'ids', 'dom'). 'fetch_stm' = gammel STM (valgfri 'date').",
        "en": "'search' = LTM search (requires 'query'). 'fetch_unverified' = unconfirmed (optional 'count', 'skip'). 'confirm' = confirm (requires 'ids', 'dom'). 'fetch_stm' = old STM (optional 'date').",
        "de": "'search' = LTM-Suche (erfordert 'query'). 'fetch_unverified' = unbestätigt (optional 'count', 'skip'). 'confirm' = bestätigen (erfordert 'ids', 'dom'). 'fetch_stm' = altes STM (optional 'date').",
    },
    "tool_minne_query_desc": {
        "nb": "Søketekst for LTM. Kortere og konkret er bedre. Kun ved action='search'.",
        "en": "Search text for LTM. Shorter and concrete is better. Only for action='search'.",
        "de": "Suchtext für LTM. Kürzer und konkret ist besser. Nur bei action='search'.",
    },
    "tool_minne_count_desc": {
        "nb": "Antall å hente. Standard 10, maks 20. Kun ved action='fetch_unverified'.",
        "en": "Number to fetch. Default 10, max 20. Only for action='fetch_unverified'.",
        "de": "Anzahl abzurufen. Standard 10, max 20. Nur bei action='fetch_unverified'.",
    },
    "tool_minne_skip_desc": {
        "nb": "Hopp over første N rader. Standard 0. Kun ved action='fetch_unverified'.",
        "en": "Skip first N rows. Default 0. Only for action='fetch_unverified'.",
        "de": "Erste N Zeilen überspringen. Standard 0. Nur bei action='fetch_unverified'.",
    },
    "tool_minne_ids_desc": {
        "nb": "Liste med interaksjons-IDer. Kun ved action='confirm'.",
        "en": "List of interaction IDs. Only for action='confirm'.",
        "de": "Liste der Interaktions-IDs. Nur bei action='confirm'.",
    },
    "tool_minne_dom_desc": {
        "nb": "'verified'=stemte, 'denied'=stemte ikke, 'test'=testkjøring. Kun ved action='confirm'.",
        "en": "'verified'=correct, 'denied'=incorrect, 'test'=test run. Only for action='confirm'.",
        "de": "'verified'=korrekt, 'denied'=falsch, 'test'=Testlauf. Nur bei action='confirm'.",
    },
    "tool_minne_date_desc": {
        "nb": "Dato YYYY-MM-DD for STM-snapshot. Utelat for å se tilgjengelige datoer.",
        "en": "Date YYYY-MM-DD for STM snapshot. Omit to see available dates.",
        "de": "Datum YYYY-MM-DD für STM-Snapshot. Weglassen um verfügbare Daten zu sehen.",
    },
    # søk_i_argus
    "tool_søk_i_argus_desc": {
        "nb": "Søk i Kåres systemlogg via Argus. Returnerer HA-handlinger, feil, LLM-kall. Bruk for å finne hva systemet HAR GJORT — ikke hva brukeren sa (bruk minne(action='search') for det). Alltid semantisk søk (query kreves); legg til entity_id/since/until for å presist svare på 'skjedde X på tidspunkt Y' i stedet for kun 'noe relatert til X'.",
        "en": "Search Kåre's system log via Argus. Returns HA actions, errors, LLM calls. Use to find what the system HAS DONE — not what the user said (use minne(action='search') for that). Always semantic (query required); add entity_id/since/until to precisely answer 'did X happen at time Y' instead of just 'something related to X'.",
        "de": "Kåres Systemprotokoll über Argus durchsuchen. Gibt HA-Aktionen, Fehler, LLM-Aufrufe zurück. Verwenden um zu finden was das System GETAN HAT — nicht was der Nutzer sagte (dafür minne(action='search')). Immer semantisch (query erforderlich); entity_id/since/until hinzufügen um präzise 'geschah X zum Zeitpunkt Y' statt nur 'etwas im Zusammenhang mit X' zu beantworten.",
    },
    "tool_søk_i_argus_query_desc": {
        "nb": "Hva du leter etter. Semantisk søk — norsk naturlig språk.",
        "en": "What you are looking for. Semantic search — natural language.",
        "de": "Was du suchst. Semantische Suche — natürliche Sprache.",
    },
    "tool_søk_i_argus_limit_desc": {
        "nb": "Maks antall resultater. Standard 8, maks 20.",
        "en": "Max number of results. Default 8, max 20.",
        "de": "Maximale Anzahl Ergebnisse. Standard 8, max 20.",
    },
    "tool_søk_i_argus_entity_id_desc": {
        "nb": "Valgfritt: begrens til én spesifikk HA entity_id (f.eks. 'binary_sensor.bevegelse_stue'), ikke et vennlig navn. Kombineres med søket, ikke i stedet for det.",
        "en": "Optional: restrict to one specific HA entity_id (e.g. 'binary_sensor.bevegelse_stue'), not a friendly name. Combines with the query, not instead of it.",
        "de": "Optional: auf eine bestimmte HA entity_id beschränken (z.B. 'binary_sensor.bevegelse_stue'), keinen Anzeigenamen. Wird mit der Suche kombiniert, nicht anstelle davon.",
    },
    "tool_søk_i_argus_since_desc": {
        "nb": "Valgfritt: kun hendelser fra og med dette tidspunktet (ISO 8601, f.eks. '2026-08-28T19:30:00+02:00').",
        "en": "Optional: only events from this time onward (ISO 8601, e.g. '2026-08-28T19:30:00+02:00').",
        "de": "Optional: nur Ereignisse ab diesem Zeitpunkt (ISO 8601, z.B. '2026-08-28T19:30:00+02:00').",
    },
    "tool_søk_i_argus_until_desc": {
        "nb": "Valgfritt: kun hendelser til og med dette tidspunktet (ISO 8601).",
        "en": "Optional: only events up to this time (ISO 8601).",
        "de": "Optional: nur Ereignisse bis zu diesem Zeitpunkt (ISO 8601).",
    },
    # mechanic
    "tool_mechanic_desc": {
        "nb": (
            "Mechanic — veien til kode, logger, systemstatus, git og shell-kommandoer. Bruk "
            "denne for ALT i den kategorien. "
            "action='search': rask, synkron lesing/søk/logg-henting uten å fylle din egen "
            "kontekst — bruk for enkle, raske oppslag. "
            "action='delegate': lang bakgrunnsjobb, ikke-blokkerende (returnerer job_id) — bruk "
            "for undersøkelser som krever flere steg. Skriv en PRESIS oppgavebeskrivelse med "
            "tydelig mål, hva som skal rapporteres, og eventuelle begrensninger (f.eks. 'kun "
            "lesing, ingen shell') — en vag oppgave gir et dårlig resultat, uansett hvor "
            "kapabel Mechanic er. "
            "action='result': poll resultat fra delegert jobb. "
            "action='cancel': stopp løpende jobb. "
            "action='comment': injiser melding i løpende jobb."
        ),
        "en": (
            "Mechanic — the path to code, logs, system status, git and shell commands. Use "
            "this for ALL of that category. "
            "action='search': quick, synchronous read/search/log-fetch without filling your "
            "own context — use for simple, fast lookups. "
            "action='delegate': long background job, non-blocking (returns job_id) — use for "
            "investigations that need multiple steps. Write a PRECISE task description with a "
            "clear goal, what should be reported back, and any constraints (e.g. 'read-only, "
            "no shell') — a vague task gives a bad result no matter how capable Mechanic is. "
            "action='result': poll result from delegated job. "
            "action='cancel': stop running job. "
            "action='comment': inject message into running job."
        ),
        "de": (
            "Mechanic — der Weg zu Code, Logs, Systemstatus, Git und Shell-Befehlen. Nutze "
            "dieses für ALLES in dieser Kategorie. "
            "action='search': schnelles, synchrones Lesen/Suchen/Log-Abrufen ohne deinen "
            "eigenen Kontext zu füllen — für einfache, schnelle Abfragen. "
            "action='delegate': langer Hintergrundjob, nicht blockierend (gibt job_id zurück) "
            "— für Untersuchungen mit mehreren Schritten. Schreibe eine PRÄZISE "
            "Aufgabenbeschreibung mit klarem Ziel, was berichtet werden soll, und etwaigen "
            "Einschränkungen (z.B. 'nur lesen, kein Shell') — eine vage Aufgabe liefert ein "
            "schlechtes Ergebnis, egal wie fähig Mechanic ist. "
            "action='result': Ergebnis eines delegierten Jobs abfragen. "
            "action='cancel': laufenden Job stoppen. "
            "action='comment': Nachricht in laufenden Job injizieren."
        ),
    },
    "tool_mechanic_action_desc": {
        "nb": "'search' = les/søk (sync, krever 'type' og 'query'). 'delegate' = bakgrunnsjobb (krever 'task'). 'result' = poll jobb (krever 'job_id'). 'cancel' = stopp (krever 'job_id'). 'comment' = injiser (krever 'job_id', 'comment').",
        "en": "'search' = read/search (sync, requires 'type' and 'query'). 'delegate' = background job (requires 'task'). 'result' = poll job (requires 'job_id'). 'cancel' = stop (requires 'job_id'). 'comment' = inject (requires 'job_id', 'comment').",
        "de": "'search' = lesen/suchen (sync, erfordert 'type' und 'query'). 'delegate' = Hintergrundjob (erfordert 'task'). 'result' = Job abfragen (erfordert 'job_id'). 'cancel' = stoppen (erfordert 'job_id'). 'comment' = injizieren (erfordert 'job_id', 'comment').",
    },
    "tool_mechanic_type_desc": {
        "nb": "'files' = les filer (krever 'files'). 'grep' = søk i kode (krever 'pattern'). 'log' = les logger (krever 'service' eller 'log_file'). Kun ved action='search'.",
        "en": "'files' = read files (requires 'files'). 'grep' = search code (requires 'pattern'). 'log' = read logs (requires 'service' or 'log_file'). Only for action='search'.",
        "de": "'files' = Dateien lesen (erfordert 'files'). 'grep' = Code durchsuchen (erfordert 'pattern'). 'log' = Logs lesen (erfordert 'service' oder 'log_file'). Nur bei action='search'.",
    },
    "tool_mechanic_query_desc": {
        "nb": "Hva vil du vite? Mechanic summerer innholdet mot dette. Kreves ved action='search'.",
        "en": "What do you want to know? Mechanic summarizes the content against this. Required for action='search'.",
        "de": "Was möchtest du wissen? Mechanic fasst den Inhalt dazu zusammen. Erforderlich bei action='search'.",
    },
    "tool_mechanic_files_desc": {
        "nb": "Absolutte filstier under /kaare. Maks 5. Kun ved type='files'.",
        "en": "Absolute file paths under /kaare. Max 5. Only for type='files'.",
        "de": "Absolute Dateipfade unter /kaare. Max 5. Nur bei type='files'.",
    },
    "tool_mechanic_from_line_desc": {
        "nb": "Startlinje (1-basert). Valgfri ved type='files'.",
        "en": "Start line (1-based). Optional for type='files'.",
        "de": "Startzeile (1-basiert). Optional bei type='files'.",
    },
    "tool_mechanic_to_line_desc": {
        "nb": "Sluttlinje (inklusiv). Valgfri ved type='files'.",
        "en": "End line (inclusive). Optional for type='files'.",
        "de": "Endzeile (inklusiv). Optional bei type='files'.",
    },
    "tool_mechanic_pattern_desc": {
        "nb": "Grep-mønster. Kreves ved type='grep'.",
        "en": "Grep pattern. Required for type='grep'.",
        "de": "Grep-Muster. Erforderlich bei type='grep'.",
    },
    "tool_mechanic_directory_desc": {
        "nb": "Katalog å søke i. Standard: /kaare. Kun ved type='grep'.",
        "en": "Directory to search in. Default: /kaare. Only for type='grep'.",
        "de": "Verzeichnis zum Durchsuchen. Standard: /kaare. Nur bei type='grep'.",
    },
    "tool_mechanic_service_desc": {
        "nb": "Systemd-tjenestenavn, f.eks. 'kaare'. Kun ved type='log'.",
        "en": "Systemd service name, e.g. 'kaare'. Only for type='log'.",
        "de": "Systemd-Dienstname, z.B. 'kaare'. Nur bei type='log'.",
    },
    "tool_mechanic_log_file_desc": {
        "nb": "Loggfilnavn uten sti, f.eks. 'kaare_ha_gateway.log'. Kun ved type='log'.",
        "en": "Log file name without path, e.g. 'kaare_ha_gateway.log'. Only for type='log'.",
        "de": "Logdateiname ohne Pfad, z.B. 'kaare_ha_gateway.log'. Nur bei type='log'.",
    },
    "tool_mechanic_lines_desc": {
        "nb": "Antall logglinjer. Standard 100, maks 500. Kun ved type='log'.",
        "en": "Number of log lines. Default 100, max 500. Only for type='log'.",
        "de": "Anzahl Logzeilen. Standard 100, max 500. Nur bei type='log'.",
    },
    "tool_mechanic_filter_desc": {
        "nb": "Grep-filter på logginnhold. Valgfri ved type='log'.",
        "en": "Grep filter on log content. Optional for type='log'.",
        "de": "Grep-Filter auf Loginhalt. Optional bei type='log'.",
    },
    "tool_mechanic_task_desc": {
        "nb": "Presis oppgavebeskrivelse: mål, hva som skal rapporteres, og eventuelle begrensninger (f.eks. 'kun lesing/grep, ingen shell'). Kun ved action='delegate'.",
        "en": "Precise task description: goal, what to report back, and any constraints (e.g. 'read/grep only, no shell'). Only for action='delegate'.",
        "de": "Präzise Aufgabenbeschreibung: Ziel, was berichtet werden soll, und etwaige Einschränkungen (z.B. 'nur lesen/grep, kein Shell'). Nur bei action='delegate'.",
    },
    "tool_mechanic_job_id_desc": {
        "nb": "Job-ID fra action='delegate'. Kreves ved 'result', 'cancel', 'comment'.",
        "en": "Job ID from action='delegate'. Required for 'result', 'cancel', 'comment'.",
        "de": "Job-ID aus action='delegate'. Erforderlich bei 'result', 'cancel', 'comment'.",
    },
    "tool_mechanic_comment_desc": {
        "nb": "Melding til løpende jobb. Kun ved action='comment'.",
        "en": "Message to running job. Only for action='comment'.",
        "de": "Nachricht an laufenden Job. Nur bei action='comment'.",
    },
    # restart_docker_container
    "tool_restart_docker_desc": {
        "nb": "Restart en Ollama Docker-container som siste utvei når en modell henger. Advarsel: modell-reload tar ~3,5 min.",
        "en": "Restart an Ollama Docker container as a last resort when a model is stuck. Warning: model reload takes ~3.5 min.",
        "de": "Einen Ollama Docker-Container als letzten Ausweg neu starten wenn ein Modell hängt. Warnung: Modell-Reload dauert ~3,5 Min.",
    },
    "tool_restart_docker_container_desc": {
        "nb": "Container å restarte.",
        "en": "Container to restart.",
        "de": "Neu zu startender Container.",
    },
    # les_indre_tanker
    "tool_les_indre_tanker_desc": {
        "nb": "Les Kåres indre tanker — destillerte observasjoner fra Jing og Jang. Bruk når du kjenner noe uformulert ulmer. Filen tømmes etter lesing.",
        "en": "Read Kåre's inner thoughts — distilled observations from Jing and Jang. Use when you sense something unformulated simmering. The file is cleared after reading.",
        "de": "Kåres innere Gedanken lesen — destillierte Beobachtungen von Jing und Jang. Verwenden wenn etwas Unformuliertes schwelt. Die Datei wird nach dem Lesen geleert.",
    },
    # selvbilde
    "tool_selvbilde_desc": {
        "nb": "Les og rediger selvbilde-filen — dine egne refleksjoner om hvem du er. action='read': hele filen. action='update': ny selvrefleksjon (krever 'observation'). action='edit': erstatt linje (krever 'fragment', 'new_text'). action='delete': fjern linje (krever 'fragment').",
        "en": "Read and edit the self-image file — your own reflections on who you are. action='read': entire file. action='update': new self-reflection (requires 'observation'). action='edit': replace line (requires 'fragment', 'new_text'). action='delete': remove line (requires 'fragment').",
        "de": "Selbstbild-Datei lesen und bearbeiten — eigene Reflexionen über dich selbst. action='read': gesamte Datei. action='update': neue Selbstreflexion (erfordert 'observation'). action='edit': Zeile ersetzen (erfordert 'fragment', 'new_text'). action='delete': Zeile entfernen (erfordert 'fragment').",
    },
    "tool_selvbilde_action_desc": {
        "nb": "'read' = vis fil. 'update' = ny observasjon (krever 'observation'). 'edit' = erstatt linje (krever 'fragment', 'new_text'). 'delete' = fjern linje (krever 'fragment').",
        "en": "'read' = show file. 'update' = new observation (requires 'observation'). 'edit' = replace line (requires 'fragment', 'new_text'). 'delete' = remove line (requires 'fragment').",
        "de": "'read' = Datei anzeigen. 'update' = neue Beobachtung (erfordert 'observation'). 'edit' = Zeile ersetzen (erfordert 'fragment', 'new_text'). 'delete' = Zeile entfernen (erfordert 'fragment').",
    },
    "tool_selvbilde_observation_desc": {
        "nb": "Ny selvrefleksjon — én til tre setninger om deg selv. Kun ved action='update'.",
        "en": "New self-reflection — one to three sentences about yourself. Only for action='update'.",
        "de": "Neue Selbstreflexion — ein bis drei Sätze über dich selbst. Nur bei action='update'.",
    },
    "tool_selvbilde_fragment_desc": {
        "nb": "Unikt tekstfragment fra linjen du vil endre/slette. Kun ved action='edit'/'delete'.",
        "en": "Unique text fragment from the line to change/delete. Only for action='edit'/'delete'.",
        "de": "Eindeutiges Textfragment aus der zu ändernden/löschenden Zeile. Nur bei action='edit'/'delete'.",
    },
    "tool_selvbilde_new_text_desc": {
        "nb": "Ny tekst som erstatter linjen. Kun ved action='edit'.",
        "en": "New text replacing the line. Only for action='edit'.",
        "de": "Neuer Text der die Zeile ersetzt. Nur bei action='edit'.",
    },
    # verden
    "tool_verden_desc": {
        "nb": (
            "To lag: verden-filen (prosa) og verden-variabler (strukturerte nøkkel-verdier). "
            "PROSA (world.md): action='read','update_field','add','delete','edit'. "
            "VARIABLER (world_vars.json): action='read_var','set_var','delete_var','list_vars'."
        ),
        "en": (
            "Two layers: world file (prose) and world variables (structured key-values). "
            "PROSE (world.md): action='read','update_field','add','delete','edit'. "
            "VARIABLES (world_vars.json): action='read_var','set_var','delete_var','list_vars'."
        ),
        "de": (
            "Zwei Ebenen: Weltdatei (Prosa) und Weltvariablen (strukturierte Schlüsselwerte). "
            "PROSA (world.md): action='read','update_field','add','delete','edit'. "
            "VARIABLEN (world_vars.json): action='read_var','set_var','delete_var','list_vars'."
        ),
    },
    "tool_verden_action_desc": {
        "nb": "Prosa: 'read','update_field','add','delete','edit'. Variabler: 'read_var','set_var','delete_var','list_vars'.",
        "en": "Prose: 'read','update_field','add','delete','edit'. Variables: 'read_var','set_var','delete_var','list_vars'.",
        "de": "Prosa: 'read','update_field','add','delete','edit'. Variablen: 'read_var','set_var','delete_var','list_vars'.",
    },
    "tool_verden_category_desc": {
        "nb": "Kategorinavn i prosa-filen, f.eks. 'Sensors'. Ved update_field og add.",
        "en": "Category name in prose file, e.g. 'Sensors'. For update_field and add.",
        "de": "Kategoriename in der Prosadatei, z.B. 'Sensors'. Bei update_field und add.",
    },
    "tool_verden_field_desc": {
        "nb": "Feltnavn, f.eks. 'GPU'. Kun ved action='update_field'.",
        "en": "Field name, e.g. 'GPU'. Only for action='update_field'.",
        "de": "Feldname, z.B. 'GPU'. Nur bei action='update_field'.",
    },
    "tool_verden_value_desc": {
        "nb": "Verdi som lagres. Ved update_field: prosa-verdi. Ved set_var: variabelverdi.",
        "en": "Value to store. For update_field: prose value. For set_var: variable value.",
        "de": "Zu speichernder Wert. Bei update_field: Prosawert. Bei set_var: Variablenwert.",
    },
    "tool_verden_text_desc": {
        "nb": "Fritekst å legge til i prosa-filen. Kun ved action='add'.",
        "en": "Free text to add to the prose file. Only for action='add'.",
        "de": "Freitext zur Prosadatei hinzufügen. Nur bei action='add'.",
    },
    "tool_verden_fragment_desc": {
        "nb": "Unikt tekstfragment fra linjen. Kun ved action='delete'/'edit'.",
        "en": "Unique text fragment from the line. Only for action='delete'/'edit'.",
        "de": "Eindeutiges Textfragment aus der Zeile. Nur bei action='delete'/'edit'.",
    },
    "tool_verden_new_text_desc": {
        "nb": "Ny tekst som erstatter linjen. Kun ved action='edit'.",
        "en": "New text replacing the line. Only for action='edit'.",
        "de": "Neuer Text der die Zeile ersetzt. Nur bei action='edit'.",
    },
    "tool_verden_key_desc": {
        "nb": "Variabelnøkkel med punktum-navnerom, f.eks. 'sensor.lys_stue.status'.",
        "en": "Variable key with dot namespace, e.g. 'sensor.lys_stue.status'.",
        "de": "Variablenschlüssel mit Punkt-Namespace, z.B. 'sensor.lys_stue.status'.",
    },
    "tool_verden_description_desc": {
        "nb": "Forklaring på hva variabelen betyr. Valgfri ved set_var.",
        "en": "Explanation of what the variable means. Optional for set_var.",
        "de": "Erklärung was die Variable bedeutet. Optional bei set_var.",
    },
    # brukerprofil
    "tool_brukerprofil_desc": {
        "nb": (
            "Les og rediger brukerens profil. "
            "action='read': hele profilen. "
            "action='update': PRIVAT observasjon (kryptert). "
            "action='update_house': del faktum med hele huset (kun nøytrale, praktiske fakta). "
            "action='set_field': sett strukturert felt i profile.yaml. "
            "action='edit'/'delete': rediger/fjern observasjonslinje. "
            "action='curiosity': oppdater hva du lurer på om brukeren (maks 5 punkter)."
        ),
        "en": (
            "Read and edit the user's profile. "
            "action='read': entire profile. "
            "action='update': PRIVATE observation (encrypted). "
            "action='update_house': share fact with entire household (neutral, practical facts only). "
            "action='set_field': set structured field in profile.yaml. "
            "action='edit'/'delete': edit/remove observation line. "
            "action='curiosity': update what you are curious about regarding the user (max 5 items)."
        ),
        "de": (
            "Benutzerprofil lesen und bearbeiten. "
            "action='read': gesamtes Profil. "
            "action='update': PRIVATE Beobachtung (verschlüsselt). "
            "action='update_house': Fakt mit dem ganzen Haushalt teilen (nur neutrale, praktische Fakten). "
            "action='set_field': strukturiertes Feld in profile.yaml setzen. "
            "action='edit'/'delete': Beobachtungszeile bearbeiten/entfernen. "
            "action='curiosity': aktualisieren was du über den Nutzer wissen möchtest (max 5 Punkte)."
        ),
    },
    "tool_brukerprofil_action_desc": {
        "nb": "'read','update','update_house','set_field','edit','delete','curiosity'.",
        "en": "'read','update','update_house','set_field','edit','delete','curiosity'.",
        "de": "'read','update','update_house','set_field','edit','delete','curiosity'.",
    },
    "tool_brukerprofil_observation_desc": {
        "nb": "Ny observasjon om brukeren. Én til tre setninger. Kun ved action='update'.",
        "en": "New observation about the user. One to three sentences. Only for action='update'.",
        "de": "Neue Beobachtung über den Nutzer. Ein bis drei Sätze. Nur bei action='update'.",
    },
    "tool_brukerprofil_section_desc": {
        "nb": "Seksjonsnavnet i profile.yaml. 19 seksjoner utover prompt_top dekker biografiske fakta (identity, relationships_and_family, interests_and_hobbies, values_beliefs_politics, work_and_education m.fl.) — bruk action='read' for full liste. Kun ved action='set_field'.",
        "en": "Section name in profile.yaml. 19 sections beyond prompt_top cover biographical facts (identity, relationships_and_family, interests_and_hobbies, values_beliefs_politics, work_and_education, etc.) — use action='read' for the full list. Only for action='set_field'.",
        "de": "Abschnittsname in profile.yaml. 19 Abschnitte neben prompt_top decken biografische Fakten ab (identity, relationships_and_family, interests_and_hobbies, values_beliefs_politics, work_and_education, usw.) — nutze action='read' für die vollständige Liste. Nur bei action='set_field'.",
    },
    "tool_brukerprofil_field_desc": {
        "nb": "Feltnavnet. Kun ved action='set_field' og 'update_house'.",
        "en": "Field name. Only for action='set_field' and 'update_house'.",
        "de": "Feldname. Nur bei action='set_field' und 'update_house'.",
    },
    "tool_brukerprofil_value_desc": {
        "nb": "Verdien som lagres. Kun ved action='set_field' og 'update_house'.",
        "en": "Value to store. Only for action='set_field' and 'update_house'.",
        "de": "Zu speichernder Wert. Nur bei action='set_field' und 'update_house'.",
    },
    "tool_brukerprofil_fragment_desc": {
        "nb": "Unikt tekstfragment fra linjen. Kun ved action='edit'/'delete'.",
        "en": "Unique text fragment from the line. Only for action='edit'/'delete'.",
        "de": "Eindeutiges Textfragment aus der Zeile. Nur bei action='edit'/'delete'.",
    },
    "tool_brukerprofil_new_text_desc": {
        "nb": "Ny tekst som erstatter linjen. Kun ved action='edit'.",
        "en": "New text replacing the line. Only for action='edit'.",
        "de": "Neuer Text der die Zeile ersetzt. Nur bei action='edit'.",
    },
    "tool_brukerprofil_text_desc": {
        "nb": "Hva du genuint lurer på om brukeren. Maks 5 bullet-punkter. Kun ved action='curiosity'.",
        "en": "What you are genuinely curious about regarding the user. Max 5 bullet points. Only for action='curiosity'.",
        "de": "Was du wirklich über den Nutzer wissen möchtest. Max 5 Stichpunkte. Nur bei action='curiosity'.",
    },
    # notat
    "tool_notat_desc": {
        "nb": (
            "Fire separate lister + Kåres notater + dev-møtets agenda. "
            "VIKTIG: Når du sier 'jeg husker det' — SKAL du faktisk kalle notat(action='write', list_name='kare'). "
            "liste='arkitekt': arkitekt/designnotater. "
            "liste='handle': felles handleliste. "
            "liste='huske': brukerens huskeliste. "
            "liste='kare': Kåres egne oppfølgingspunkter — MERK: kun toppen (pinnede først, "
            "deretter kronologisk, opp til et tegn-tak) injiseres automatisk i hver systemprompt. "
            "Bruk action='pin'/'unpin' (krever note_id) for å styre hva som alltid vises. "
            "Bruk action='read' for å lese hele listen, inkl. det som ikke er injisert. "
            "liste='agenda': saker du vil ta opp på neste dev-møte (feil, mønstre, ting som bør "
            "undersøkes). Skriv dem HER, ikke i 'kare'. Kun action='write' og 'read'; ingenting "
            "slettes, og en sak som allerede står der telles i stedet for å skrives på nytt."
        ),
        "en": (
            "Four separate lists + Kåre's notes + the dev meeting agenda. "
            "IMPORTANT: When you say 'I'll remember that' — you MUST actually call notat(action='write', list_name='kare'). "
            "list_name='arkitekt': architect/design notes. "
            "list_name='handle': shared shopping list. "
            "list_name='huske': user's reminder list. "
            "list_name='kare': Kåre's own follow-up items — NOTE: only the top slice (pinned "
            "first, then chronological, up to a char cap) is auto-injected into every system "
            "prompt. Use action='pin'/'unpin' (requires note_id) to control what always shows. "
            "Use action='read' to read the full list, including what isn't injected. "
            "list_name='agenda': things you want to raise at the next dev meeting (bugs, patterns, "
            "things worth investigating). Write them HERE, not in 'kare'. Only action='write' and "
            "'read'; nothing is deleted, and an item already on the agenda is counted instead of "
            "written again."
        ),
        "de": (
            "Vier separate Listen + Kåres Notizen + die Agenda des Entwicklertreffens. "
            "WICHTIG: Wenn du sagst 'ich merke mir das' — MUSST du tatsächlich notat(action='write', list_name='kare') aufrufen. "
            "list_name='arkitekt': Architektur-/Designnotizen. "
            "list_name='handle': gemeinsame Einkaufsliste. "
            "list_name='huske': Erinnerungsliste des Nutzers. "
            "list_name='kare': Kåres eigene Nachverfolgungspunkte — HINWEIS: nur der obere "
            "Ausschnitt (angeheftete zuerst, dann chronologisch, bis zu einem Zeichenlimit) wird "
            "automatisch in jeden System-Prompt eingefügt. Mit action='pin'/'unpin' (erfordert "
            "note_id) steuerst du, was immer sichtbar ist. Mit action='read' liest du die "
            "vollständige Liste, auch was nicht eingefügt wird. "
            "list_name='agenda': Punkte, die du beim nächsten Entwicklertreffen ansprechen willst "
            "(Fehler, Muster, Dinge, die untersucht werden sollten). Schreib sie HIER, nicht in "
            "'kare'. Nur action='write' und 'read'; nichts wird gelöscht, und ein Punkt, der schon "
            "auf der Agenda steht, wird gezählt statt neu geschrieben."
        ),
    },
    "tool_notat_action_desc": {
        "nb": "'write'=legg til. 'read'=vis. 'delete'=fjern (krever 'note_id'). 'clear'=tøm. 'done'=ferdig (huske/kare). 'mark_bought'=kjøpt (handle). 'clear_all'=tøm hele handlelisten. 'pin'=fest øverst, vises alltid i den auto-injiserte toppen (kun kare, krever 'note_id'). 'unpin'=løsne igjen (kun kare, krever 'note_id'). 'edit'=bytt ut teksten i ett punkt, beholder id/dato/pin (kun kare, krever 'note_id' og 'text'). 'mark_important'=marker et notat i standardlisten (architect) som viktig, tas opp ved neste refleksjonsmøte i stedet for å vente en uke (krever 'note_id'). 'resolve'=marker en sak som løst med en kort 'conclusion' (agenda/observations, krever 'topic_id'; bare i samtale med en bruker som har lov). 'reopen'=be om å gjenåpne en løst sak (blir et forslag et menneske bekrefter). 'move'=flytt et huskeliste-notat til 'to' (observations/agenda/lessons) i ett kall (krever note_id).",
        "en": "'write'=add. 'read'=show. 'delete'=remove (requires 'note_id'). 'clear'=clear. 'done'=done (huske/kare). 'mark_bought'=bought (handle). 'clear_all'=clear entire shopping list. 'pin'=pin to top, always shown in the auto-injected slice (kare only, requires 'note_id'). 'unpin'=undo pin (kare only, requires 'note_id'). 'edit'=replace the text of one entry, keeps id/date/pin (kare only, requires 'note_id' and 'text'). 'mark_important'=flag a note in the default (architect) list as important, picked up at the next reflection meeting instead of waiting a week (requires 'note_id'). 'resolve'=mark a topic resolved with a short 'conclusion' (agenda/observations, requires 'topic_id'; only in a conversation with a user who is allowed). 'reopen'=ask to reopen a resolved topic (becomes a proposal a human confirms). 'move'=move a reminder-list note to 'to' (observations/agenda/lessons) in one call (requires note_id).",
        "de": "'write'=hinzufügen. 'read'=anzeigen. 'delete'=entfernen (erfordert 'note_id'). 'clear'=leeren. 'done'=fertig (huske/kare). 'mark_bought'=gekauft (handle). 'clear_all'=gesamte Einkaufsliste leeren. 'pin'=oben anheften, immer im automatisch eingefügten Ausschnitt sichtbar (nur kare, erfordert 'note_id'). 'unpin'=Anheftung aufheben (nur kare, erfordert 'note_id'). 'edit'=den Text eines Eintrags ersetzen, behält ID/Datum/Anheftung (nur kare, erfordert 'note_id' und 'text'). 'mark_important'=eine Notiz in der Standardliste (architect) als wichtig markieren, wird beim nächsten Reflexionstreffen behandelt statt eine Woche zu warten (erfordert 'note_id'). 'resolve'=ein Thema mit kurzer 'conclusion' als gelöst markieren (agenda/observations, erfordert 'topic_id'; nur im Gespräch mit einem berechtigten Benutzer). 'reopen'=Wiedereröffnung eines gelösten Themas anfragen (wird ein Vorschlag, den ein Mensch bestätigt). 'move'=eine Merklisten-Notiz in einem Aufruf nach 'to' verschieben (observations/agenda/lessons; erfordert note_id).",
    },
    "tool_notat_list_name_desc": {
        "nb": "'architect'=brukerens notisblokk (standard — bruk denne når brukeren sier «skriv et notat» / «notatene mine»). 'shopping'=handleliste. 'remember'=brukerens huskeliste. 'kare'=dine EGNE arbeidsnotater (aldri for brukerens notater). 'agenda'=FEIL som skal undersøkes på dev-møtet. 'observations'=noe du SÅ (sensor/kamera/hendelse) — telles i mønstre, aldri i prompten. 'lessons'=en REGEL du har lært om hvordan du jobber (står alltid i prompten). 'resolved'=les løste saker med konklusjon. 'kare' er bare korte egne oppfølginger (maks ~600 tegn), aldri en logg.",
        "en": "'architect'=the user's notepad (default — use it when the user says «write a note» / «my notes»). 'shopping'=shopping list. 'remember'=the user's reminder list. 'kare'=your OWN working notes (never for the user's notes). 'agenda'=FAULTS to investigate at the dev meeting. 'observations'=something you SAW (sensor/camera/event) — counted into patterns, never in the prompt. 'lessons'=a RULE you learned about how you work (always in the prompt). 'resolved'=read resolved topics with their conclusion. 'kare' is only short follow-ups of your own (max ~600 chars), never a log.",
        "de": "'architect'=Notizblock des Nutzers (Standard — verwenden, wenn der Nutzer «schreib eine Notiz» / «meine Notizen» sagt). 'shopping'=Einkaufsliste. 'remember'=Erinnerungsliste des Nutzers. 'kare'=deine EIGENEN Arbeitsnotizen (nie für Notizen des Nutzers). 'agenda'=FEHLER, die beim Entwicklertreffen untersucht werden. 'observations'=etwas, das du GESEHEN hast (Sensor/Kamera/Ereignis) — wird zu Mustern gezählt, nie im Prompt. 'lessons'=eine REGEL, die du über deine Arbeitsweise gelernt hast (steht immer im Prompt). 'resolved'=gelöste Themen mit Schlussfolgerung lesen. 'kare' nur kurze eigene Nachverfolgungen (max. ~600 Zeichen), nie ein Protokoll.",
    },
    "tool_notat_text_desc": {
        "nb": "Innholdet i notatet/varen. Kreves ved action='write' og 'edit'.",
        "en": "Content of the note/item. Required for action='write' and 'edit'.",
        "de": "Inhalt der Notiz/des Artikels. Erforderlich bei action='write' und 'edit'.",
    },
    "tool_notat_category_desc": {
        "nb": "Kun ved list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
        "en": "Only for list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
        "de": "Nur bei list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
    },
    "tool_notat_note_id_desc": {
        "nb": "ID (8 tegn, fra action='read'). Kreves ved delete/done/mark_bought/edit.",
        "en": "ID (8 chars, from action='read'). Required for delete/done/mark_bought/edit.",
        "de": "ID (8 Zeichen, aus action='read'). Erforderlich bei delete/done/mark_bought/edit.",
    },
    "tool_notat_quantity_desc": {
        "nb": "Kun handle: mengde, f.eks. '2'.",
        "en": "Only handle: quantity, e.g. '2'.",
        "de": "Nur handle: Menge, z.B. '2'.",
    },
    "tool_notat_unit_desc": {
        "nb": "Kun handle: enhet, f.eks. 'liter', 'stk'.",
        "en": "Only handle: unit, e.g. 'liter', 'pcs'.",
        "de": "Nur handle: Einheit, z.B. 'Liter', 'Stück'.",
    },
    "tool_notat_context_desc": {
        "nb": "Kun kare: når/hvor punktet er relevant, f.eks. 'ved neste dev-møte'.",
        "en": "Only kare: when/where the item is relevant, e.g. 'at next dev meeting'.",
        "de": "Nur kare: wann/wo der Punkt relevant ist, z.B. 'beim nächsten Dev-Meeting'.",
    },
    "tool_notat_title_desc": {
        "nb": "Kort tittel (maks ~60 tegn) for kare/agenda/observations/lessons — vises i indeksen i prompten din. Sett den alltid når du skriver.",
        "en": "Short title (max ~60 chars) for kare/agenda/observations/lessons — shown in the index in your prompt. Always set it when you write.",
        "de": "Kurzer Titel (max. ~60 Zeichen) für kare/agenda/observations/lessons — erscheint im Index in deinem Prompt. Beim Schreiben immer setzen.",
    },
    "tool_notat_topic_id_desc": {
        "nb": "Saksnummer (#N fra agenda/observations/resolved) for action='resolve' eller 'reopen'.",
        "en": "Topic number (#N from agenda/observations/resolved) for action='resolve' or 'reopen'.",
        "de": "Themennummer (#N aus agenda/observations/resolved) für action='resolve' oder 'reopen'.",
    },
    "tool_notat_conclusion_desc": {
        "nb": "Kun action='resolve': konklusjonen brukeren bekreftet, kort og uten tall som kan bli utdaterte (f.eks. «tilhengeren ved kamera X er vår, står fast — forventet, ikke et avvik»).",
        "en": "Only action='resolve': the conclusion the user confirmed, short and without numbers that can go stale (e.g. «the trailer at camera X is ours, parked — expected, not an anomaly»).",
        "de": "Nur action='resolve': die vom Benutzer bestätigte Schlussfolgerung, kurz und ohne Zahlen, die veralten können (z.B. «der Anhänger bei Kamera X gehört uns, steht fest — erwartet, keine Abweichung»).",
    },
    "tool_notat_theme_desc": {
        "nb": "Kun agenda — sett ALLTID ved list_name='agenda': hvilket tema saken hører til. 'answers'=dine svar og samtaler, 'tools'=verktøy og HA-styring, 'memory'=hukommelse, 'agents'=agentene (Miss Kåre, Library, Mechanic, Jing/Jang), 'house'=hus, sensorer og kamera, 'system'=system, modeller og ytelse, 'other'=annet.",
        "en": "Agenda only — ALWAYS set with list_name='agenda': which theme the item belongs to. 'answers'=your answers and conversations, 'tools'=tools and HA control, 'memory'=memory, 'agents'=the agents (Miss Kåre, Library, Mechanic, Jing/Jang), 'house'=house, sensors and cameras, 'system'=system, models and performance, 'other'=other.",
        "de": "Nur Agenda — bei list_name='agenda' IMMER setzen: zu welchem Thema der Punkt gehört. 'answers'=deine Antworten und Gespräche, 'tools'=Werkzeuge und HA-Steuerung, 'memory'=Gedächtnis, 'agents'=die Agenten (Miss Kåre, Library, Mechanic, Jing/Jang), 'house'=Haus, Sensoren und Kameras, 'system'=System, Modelle und Leistung, 'other'=Sonstiges.",
    },
    "slot_report_resolved_h": {"nb": "## Sett mot løste saker", "en": "## Against resolved topics", "de": "## Gegen gelöste Themen"},
    "slot_report_resolved_link": {
        "nb": "- Sak #{topic} er det samme som løst sak #{resolved} — løsningen holder, koblet som ekko.",
        "en": "- Topic #{topic} is the same as resolved topic #{resolved} — the solution holds, linked as an echo.",
        "de": "- Thema #{topic} ist dasselbe wie das gelöste Thema #{resolved} — die Lösung hält, als Echo verknüpft.",
    },
    "slot_report_resolved_reopen": {
        "nb": "- Sak #{topic} motsier løst sak #{resolved} — forslag #{id} om gjenåpning venter på deg.",
        "en": "- Topic #{topic} contradicts resolved topic #{resolved} — proposal #{id} to reopen waits for you.",
        "de": "- Thema #{topic} widerspricht dem gelösten Thema #{resolved} — Vorschlag #{id} zur Wiedereröffnung wartet auf dich.",
    },
    "slot_report_resolved_echoes": {
        "nb": "- Løst sak #{resolved}: {n} ekko siste døgn (løsningen fanger dem).",
        "en": "- Resolved topic #{resolved}: {n} echoes in the last day (the solution catches them).",
        "de": "- Gelöstes Thema #{resolved}: {n} Echos am letzten Tag (die Lösung fängt sie ab).",
    },
    "slot_report_followup": {
        "nb": "- Bekreftet forslag #{id} (sak #{topic}, {date}): {n} nye rader siden.",
        "en": "- Confirmed proposal #{id} (topic #{topic}, {date}): {n} new rows since.",
        "de": "- Bestätigter Vorschlag #{id} (Thema #{topic}, {date}): {n} neue Zeilen seitdem.",
    },
    "slot_report_themes_line": {
        "nb": "Temaer (tatt opp / i kø): {themes}",
        "en": "Themes (developed / queued): {themes}",
        "de": "Themen (bearbeitet / in der Warteschlange): {themes}",
    },
    "observation_added": {
        "nb": "Notert som observasjon (sak #{topic}, sett {count} ganger, tema: {theme}). Den telles i mønstrene om natta, men går ikke i dev-møtets kø.",
        "en": "Noted as an observation (topic #{topic}, seen {count} times, theme: {theme}). It is counted into the night's patterns but does not go into the dev meeting queue.",
        "de": "Als Beobachtung notiert (Thema #{topic}, {count}-mal gesehen, Bereich: {theme}). Sie wird nachts in Muster gezählt, kommt aber nicht in die Warteschlange des Entwicklertreffens.",
    },
    "observation_read_title": {
        "nb": "Observasjoner — viser {shown} nyeste av {total} åpne ({topics} saker):",
        "en": "Observations — showing the {shown} newest of {total} open ({topics} topics):",
        "de": "Beobachtungen — die {shown} neuesten von {total} offenen ({topics} Themen):",
    },
    "observation_empty": {"nb": "Ingen åpne observasjoner.", "en": "No open observations.", "de": "Keine offenen Beobachtungen."},
    "agenda_echo_resolved": {
        "nb": "Dette er allerede løst ({date}, sak #{topic}): {conclusion} — raden er lagret som ekko og gjenåpner ikke saken. Skriv bare hvis noe faktisk er nytt; da kan du be om gjenåpning (note action='reopen', topic_id={topic}).",
        "en": "This is already resolved ({date}, topic #{topic}): {conclusion} — the row is stored as an echo and does not reopen the topic. Only write if something is actually new; then you can ask to reopen it (note action='reopen', topic_id={topic}).",
        "de": "Das ist bereits gelöst ({date}, Thema #{topic}): {conclusion} — die Zeile wird als Echo gespeichert und öffnet das Thema nicht wieder. Schreib nur, wenn wirklich etwas neu ist; dann kannst du die Wiedereröffnung anfragen (note action='reopen', topic_id={topic}).",
    },
    "resolve_done": {
        "nb": "Sak #{topic} er markert som løst: {conclusion}. Den er låst; nye rader om det samme lagres som ekko.",
        "en": "Topic #{topic} is marked resolved: {conclusion}. It is locked; new rows about the same are stored as echoes.",
        "de": "Thema #{topic} ist als gelöst markiert: {conclusion}. Es ist gesperrt; neue Zeilen dazu werden als Echo gespeichert.",
    },
    "resolve_refused_background": {
        "nb": "En sak kan bare markeres som løst i en samtale med en bruker — ikke fra refleksjon, møter, timere eller kamera. Ta den opp med brukeren først.",
        "en": "A topic can only be marked resolved in a conversation with a user — not from reflection, meetings, timers or cameras. Raise it with the user first.",
        "de": "Ein Thema kann nur im Gespräch mit einem Benutzer als gelöst markiert werden — nicht aus Reflexion, Sitzungen, Timern oder Kameras. Sprich es zuerst mit dem Benutzer an.",
    },
    "resolve_refused_role": {
        "nb": "Denne brukeren kan ikke markere saker som løst. Det styres under Innstillinger → Assistent-innstillinger → «Hvem kan markere en sak som løst».",
        "en": "This user cannot mark topics resolved. That is set under Settings → Assistant settings → «Who can mark a topic resolved».",
        "de": "Dieser Benutzer kann Themen nicht als gelöst markieren. Das wird unter Einstellungen → Assistent-Einstellungen → «Wer darf ein Thema als gelöst markieren» festgelegt.",
    },
    "resolve_missing_topic": {"nb": "Mangler topic_id (saksnummeret).", "en": "Missing topic_id (the topic number).", "de": "topic_id (Themennummer) fehlt."},
    "resolve_missing_conclusion": {"nb": "Mangler conclusion — skriv kort hva brukeren bekreftet.", "en": "Missing conclusion — write briefly what the user confirmed.", "de": "conclusion fehlt — schreib kurz, was der Benutzer bestätigt hat."},
    "resolve_not_found": {"nb": "Fant ingen sak #{topic}.", "en": "No topic #{topic} found.", "de": "Kein Thema #{topic} gefunden."},
    "reopen_proposed": {
        "nb": "Gjenåpning av sak #{topic} er lagt frem som dev-forslag #{id}; et menneske bekrefter eller avviser i chat.",
        "en": "Reopening topic #{topic} is filed as dev proposal #{id}; a human confirms or rejects it in chat.",
        "de": "Die Wiedereröffnung von Thema #{topic} ist als Dev-Vorschlag #{id} eingereicht; ein Mensch bestätigt oder lehnt im Chat ab.",
    },
    "reopen_not_resolved": {"nb": "Sak #{topic} er ikke løst, så den trenger ingen gjenåpning.", "en": "Topic #{topic} is not resolved, so it needs no reopening.", "de": "Thema #{topic} ist nicht gelöst und muss nicht wieder geöffnet werden."},
    "resolved_empty": {"nb": "Ingen løste saker ennå.", "en": "No resolved topics yet.", "de": "Noch keine gelösten Themen."},
    "resolved_read_title": {"nb": "Løste saker ({count} nyeste):", "en": "Resolved topics ({count} newest):", "de": "Gelöste Themen ({count} neueste):"},
    "resolved_echoes": {"nb": "{n} ekko siden", "en": "{n} echoes since", "de": "{n} Echos seitdem"},
    "resolved_prompt_title": {"nb": "# Løste saker (bekreftet av en bruker — forventet, ikke avvik)", "en": "# Resolved topics (confirmed by a user — expected, not anomalies)", "de": "# Gelöste Themen (von einem Benutzer bestätigt — erwartet, keine Abweichungen)"},
    "lesson_added": {"nb": "Lærdom lagret [{id}]: {text}", "en": "Lesson saved [{id}]: {text}", "de": "Lektion gespeichert [{id}]: {text}"},
    "lesson_edited": {"nb": "Lærdom endret [{id}]: {text}", "en": "Lesson edited [{id}]: {text}", "de": "Lektion geändert [{id}]: {text}"},
    "lesson_retired": {"nb": "Lærdom [{id}] tatt ut (beholdt som historikk).", "en": "Lesson [{id}] retired (kept as history).", "de": "Lektion [{id}] zurückgezogen (als Verlauf behalten)."},
    "lesson_exists": {"nb": "Den lærdommen finnes allerede [{id}].", "en": "That lesson already exists [{id}].", "de": "Diese Lektion gibt es schon [{id}]."},
    "lesson_empty": {"nb": "Ingen lærdommer ennå.", "en": "No lessons yet.", "de": "Noch keine Lektionen."},
    "lesson_read_title": {"nb": "Mine lærdommer ({count}):", "en": "My lessons ({count}):", "de": "Meine Lektionen ({count}):"},
    "lesson_too_long": {
        "nb": "En lærdom er én regel (maks {max} tegn, denne var {n}). Skriv den kortere.",
        "en": "A lesson is one rule (max {max} chars, this one was {n}). Write it shorter.",
        "de": "Eine Lektion ist eine Regel (max. {max} Zeichen, diese hatte {n}). Schreib sie kürzer.",
    },
    "lesson_prompt_title": {"nb": "# Mine lærdommer (regler jeg følger)", "en": "# My lessons (rules I follow)", "de": "# Meine Lektionen (Regeln, die ich befolge)"},
    "list_kare_too_long": {
        "nb": "Huskelista tar bare korte egne oppfølginger (maks {max} tegn, denne var {n}). Det du så hører hjemme i note(list_name='observations'); en feil i 'agenda'; en regel i 'lessons'.",
        "en": "The kare list only takes short follow-ups of your own (max {max} chars, this was {n}). What you saw belongs in note(list_name='observations'); a fault in 'agenda'; a rule in 'lessons'.",
        "de": "Die kare-Liste nimmt nur kurze eigene Nachverfolgungen (max. {max} Zeichen, diese hatte {n}). Was du gesehen hast, gehört in note(list_name='observations'); ein Fehler in 'agenda'; eine Regel in 'lessons'.",
    },
    "memory_index_kare_title": {"nb": "# Min huskeliste (indeks — full tekst: note read)", "en": "# My reminder list (index — full text: note read)", "de": "# Meine Merkliste (Index — voller Text: note read)"},
    "memory_index_world_title": {"nb": "# Mer jeg vet om huset (indeks — full tekst: world read)", "en": "# More I know about the house (index — full text: world read)", "de": "# Mehr, was ich über das Haus weiß (Index — voller Text: world read)"},
    "memory_index_more": {"nb": "… og {n} til", "en": "… and {n} more", "de": "… und {n} weitere"},
    "memory_relevant_title": {"nb": "# Relevant nå (fra mine notater og min kunnskap)", "en": "# Relevant now (from my notes and knowledge)", "de": "# Jetzt relevant (aus meinen Notizen und meinem Wissen)"},
    "list_kare_index_title": {
        "nb": "Min huskeliste — indeks over alle {count} (full tekst: note read med note_ids=[…] eller search):",
        "en": "My reminder list — index of all {count} (full text: note read with note_ids=[…] or search):",
        "de": "Meine Merkliste — Index aller {count} (Volltext: note read mit note_ids=[…] oder search):",
    },
    "list_kare_read_more": {
        "nb": "… og {n} treff til — snevre inn med note_ids eller et mer presist søk.",
        "en": "… and {n} more matches — narrow down with note_ids or a more precise search.",
        "de": "… und {n} weitere Treffer — mit note_ids oder einer genaueren Suche eingrenzen.",
    },
    "note_move_done": {
        "nb": "Flyttet [{id}] → {target}. Fjernet fra huskelista.",
        "en": "Moved [{id}] → {target}. Removed from the reminder list.",
        "de": "[{id}] verschoben → {target}. Aus der Merkliste entfernt.",
    },
    "note_move_failed": {
        "nb": "Ikke flyttet [{id}] — notatet står urørt i huskelista. Grunn: {reason}",
        "en": "Not moved [{id}] — the note stays untouched in the reminder list. Reason: {reason}",
        "de": "Nicht verschoben [{id}] — die Notiz bleibt unverändert in der Merkliste. Grund: {reason}",
    },
    "note_move_bad_target": {
        "nb": "Ugyldig mål for move. Bruk to= én av: {valid}.",
        "en": "Invalid target for move. Use to= one of: {valid}.",
        "de": "Ungültiges Ziel für move. Verwende to= eines von: {valid}.",
    },
    "note_move_echo": {
        "nb": "(ekko av løst sak #{topic})",
        "en": "(echo of resolved topic #{topic})",
        "de": "(Echo des gelösten Themas #{topic})",
    },
    "tool_notat_to_desc": {
        "nb": "Kun action='move' (list_name='kare'): hvor notatet skal — 'observations', 'agenda' eller 'lessons'. Skrives dit først; fjernes fra huskelista bare hvis det lyktes. 'text' kan gi en kortere versjon.",
        "en": "Only action='move' (list_name='kare'): where the note goes — 'observations', 'agenda' or 'lessons'. Written there first; removed from the reminder list only if that worked. 'text' can give a shorter version.",
        "de": "Nur action='move' (list_name='kare'): wohin die Notiz soll — 'observations', 'agenda' oder 'lessons'. Wird dort zuerst geschrieben; aus der Merkliste nur entfernt, wenn das klappte. 'text' kann eine kürzere Fassung angeben.",
    },
    "tool_notat_note_ids_desc": {
        "nb": "Kun action='read' (kare): les full tekst for akkurat disse id-ene (maks 15). Uten filter viser read bare indeksen.",
        "en": "Only action='read' (kare): read the full text of exactly these ids (max 15). Without a filter read shows only the index.",
        "de": "Nur action='read' (kare): Volltext genau dieser IDs lesen (max. 15). Ohne Filter zeigt read nur den Index.",
    },
    "agenda_theme_answers": {"nb": "Svar og samtaler", "en": "Answers and conversations", "de": "Antworten und Gespräche"},
    "agenda_theme_tools": {"nb": "Verktøy og HA-styring", "en": "Tools and HA control", "de": "Werkzeuge und HA-Steuerung"},
    "agenda_theme_memory": {"nb": "Hukommelse", "en": "Memory", "de": "Gedächtnis"},
    "agenda_theme_agents": {"nb": "Agentene", "en": "Agents", "de": "Agenten"},
    "agenda_theme_house": {"nb": "Hus, sensorer og kamera", "en": "House, sensors and cameras", "de": "Haus, Sensoren und Kameras"},
    "agenda_theme_system": {"nb": "System, modeller og ytelse", "en": "System, models and performance", "de": "System, Modelle und Leistung"},
    "agenda_theme_other": {"nb": "Annet", "en": "Other", "de": "Sonstiges"},
    "tool_notat_remind_on_login_desc": {
        "nb": "Kun huske: om Kåre skal minne om dette ved neste innlogging.",
        "en": "Only huske: whether Kåre should remind about this at next login.",
        "de": "Nur huske: ob Kåre beim nächsten Login daran erinnern soll.",
    },
    "tool_notat_search_desc": {
        "nb": "Kun list_name='kare' + action='read': fritekst-substreng-søk mot tekst og kontekst, for å finne noe utenfor den auto-injiserte toppen uten å dumpe hele listen.",
        "en": "Only list_name='kare' + action='read': free-text substring search against text and context, to find something beyond the auto-injected top slice without dumping the whole list.",
        "de": "Nur list_name='kare' + action='read': Freitext-Teilstring-Suche in Text und Kontext, um etwas außerhalb des automatisch eingefügten oberen Ausschnitts zu finden, ohne die gesamte Liste auszugeben.",
    },
    "tool_notat_date_from_desc": {
        "nb": "Kun list_name='kare' + action='read': vis kun punkter fra og med denne datoen (YYYY-MM-DD).",
        "en": "Only list_name='kare' + action='read': show only items from this date onward (YYYY-MM-DD).",
        "de": "Nur list_name='kare' + action='read': nur Punkte ab diesem Datum anzeigen (YYYY-MM-DD).",
    },
    "tool_notat_date_to_desc": {
        "nb": "Kun list_name='kare' + action='read': vis kun punkter til og med denne datoen (YYYY-MM-DD).",
        "en": "Only list_name='kare' + action='read': show only items up to this date (YYYY-MM-DD).",
        "de": "Nur list_name='kare' + action='read': nur Punkte bis zu diesem Datum anzeigen (YYYY-MM-DD).",
    },
    # reason_freely
    "tool_reason_freely_desc": {
        "nb": "Tenk fritt med full intern kunnskap — uten smarthus-begrensninger. For filosofi, vitenskap, historie, etikk, teknologi, kreativ tenkning. Ikke for fakta som endrer seg, vær, eller encyklopedisk kunnskap.",
        "en": "Think freely with full internal knowledge — without smart home constraints. For philosophy, science, history, ethics, technology, creative thinking. Not for changing facts, weather, or encyclopedic knowledge.",
        "de": "Frei denken mit vollem internem Wissen — ohne Smart-Home-Einschränkungen. Für Philosophie, Wissenschaft, Geschichte, Ethik, Technologie, kreatives Denken. Nicht für sich ändernde Fakten, Wetter oder enzyklopädisches Wissen.",
    },
    "tool_reason_freely_query_desc": {
        "nb": "Spørsmålet eller temaet du vil resonere fritt om. Konkret og presist.",
        "en": "The question or topic you want to reason freely about. Concrete and precise.",
        "de": "Die Frage oder das Thema über das du frei nachdenken möchtest. Konkret und präzise.",
    },
    # les_tankehistorikk
    "tool_les_tankehistorikk_desc": {
        "nb": "Les din egen think-historikk — siste LLM-kall der du tenkte. Uten filter: siste 10. Med filter: filtrert på innhold. Med only_recovery=true: kun ganger du tenkte uten å klare å svare.",
        "en": "Read your own think history — last LLM calls where you thought. Without filter: last 10. With filter: filtered by content. With only_recovery=true: only times you thought but failed to respond.",
        "de": "Eigene Think-Historik lesen — letzte LLM-Aufrufe wo du gedacht hast. Ohne Filter: letzte 10. Mit Filter: nach Inhalt gefiltert. Mit only_recovery=true: nur Mal wo du gedacht aber nicht geantwortet hast.",
    },
    "tool_les_tankehistorikk_count_desc": {
        "nb": "Antall oppføringer. Standard 10, maks 50.",
        "en": "Number of entries. Default 10, max 50.",
        "de": "Anzahl Einträge. Standard 10, max 50.",
    },
    "tool_les_tankehistorikk_filter_desc": {
        "nb": "Filtrer på innhold, f.eks. 'lys', 'usikker'.",
        "en": "Filter by content, e.g. 'light', 'uncertain'.",
        "de": "Nach Inhalt filtern, z.B. 'Licht', 'unsicher'.",
    },
    "tool_les_tankehistorikk_only_recovery_desc": {
        "nb": "True: kun ganger du tenkte uten å klare å svare.",
        "en": "True: only times you thought without being able to respond.",
        "de": "True: nur Mal wo du gedacht aber nicht geantwortet hast.",
    },
    # inspiser_system
    "tool_self_trace_desc": {
        "nb": (
            "Se din egen respons-historikk for selvforbedring — ikke systemutforsking "
            "(bruk mechanic for kode/logger/tjenester). To operasjoner: "
            "action='fetch_trace': full trace for én request (krever 'rid'). "
            "action='trace_patterns': mønsteranalyse på tvers av dine egne traces "
            "(gjennomsnittlig latency, verktøybruk, tenke-blokker)."
        ),
        "en": (
            "View your own response history for self-improvement — not system exploration "
            "(use mechanic for code/logs/services). Two operations: "
            "action='fetch_trace': full trace for one request (requires 'rid'). "
            "action='trace_patterns': pattern analysis across your own traces "
            "(average latency, tool usage, think blocks)."
        ),
        "de": (
            "Eigene Antwortverlauf zur Selbstverbesserung einsehen — keine Systeminspektion "
            "(für Code/Logs/Dienste mechanic verwenden). Zwei Operationen: "
            "action='fetch_trace': vollständige Trace für eine Request (erfordert 'rid'). "
            "action='trace_patterns': Musteranalyse über die eigenen Traces "
            "(Ø Latenz, Tool-Nutzung, Think-Blöcke)."
        ),
    },
    "tool_self_trace_action_desc": {
        "nb": "'fetch_trace' eller 'trace_patterns'.",
        "en": "'fetch_trace' or 'trace_patterns'.",
        "de": "'fetch_trace' oder 'trace_patterns'.",
    },
    "tool_self_trace_count_desc": {
        "nb": "Antall traces å analysere. Standard 50. Kun ved action='trace_patterns'.",
        "en": "Number of traces to analyze. Default 50. Only for action='trace_patterns'.",
        "de": "Anzahl zu analysierender Traces. Standard 50. Nur bei action='trace_patterns'.",
    },
    "tool_self_trace_rid_desc": {
        "nb": "Request-ID, f.eks. 'rid-1779893101671'. Kun ved action='fetch_trace'.",
        "en": "Request ID, e.g. 'rid-1779893101671'. Only for action='fetch_trace'.",
        "de": "Request-ID, z.B. 'rid-1779893101671'. Nur bei action='fetch_trace'.",
    },
    "tool_self_trace_source_desc": {
        "nb": "Filtrer traces på kilde. Standard 'all'. Kun ved action='trace_patterns'.",
        "en": "Filter traces by source. Default 'all'. Only for action='trace_patterns'.",
        "de": "Traces nach Quelle filtern. Standard 'all'. Nur bei action='trace_patterns'.",
    },
    # kamera
    "tool_kamera_desc": {
        "nb": (
            "Kameraer og Frigate-hendelser. "
            "action='snapshot': live snapshot fra ett eller alle kameraer. "
            "action='events': aggregerte hendelser (hvem er sett, 48t). "
            "action='frigate': rå deteksjonshendelser. "
            "action='list': alle kameraer. "
            "action='analyze': siste N automatisk analyserte hendelser. "
            "action='show_event': hent snapshot og analyse for en konkret hendelse (krever 'event_id')."
        ),
        "en": (
            "Cameras and Frigate events. "
            "action='snapshot': live snapshot from one or all cameras. "
            "action='events': aggregated events (who was seen, 48h). "
            "action='frigate': raw detection events. "
            "action='list': all cameras. "
            "action='analyze': last N automatically analyzed events. "
            "action='show_event': fetch snapshot and analysis for a specific event (requires 'event_id')."
        ),
        "de": (
            "Kameras und Frigate-Ereignisse. "
            "action='snapshot': Live-Snapshot von einer oder allen Kameras. "
            "action='events': aggregierte Ereignisse (wer wurde gesehen, 48h). "
            "action='frigate': rohe Erkennungsereignisse. "
            "action='list': alle Kameras. "
            "action='analyze': letzte N automatisch analysierte Ereignisse. "
            "action='show_event': Snapshot und Analyse für ein bestimmtes Ereignis abrufen (erfordert 'event_id')."
        ),
    },
    "tool_kamera_action_desc": {
        "nb": "'snapshot','events','frigate','list','analyze','show_event'.",
        "en": "'snapshot','events','frigate','list','analyze','show_event'.",
        "de": "'snapshot','events','frigate','list','analyze','show_event'.",
    },
    "tool_kamera_scope_desc": {
        "nb": "'one' = ett kamera (krever 'camera'), 'all' = alle parallelt. Kun ved action='snapshot'.",
        "en": "'one' = one camera (requires 'camera'), 'all' = all in parallel. Only for action='snapshot'.",
        "de": "'one' = eine Kamera (erfordert 'camera'), 'all' = alle parallel. Nur bei action='snapshot'.",
    },
    "tool_kamera_camera_desc": {
        "nb": "Kameranavn, f.eks. 'ringeklokke'. Ved snapshot(scope='one') og action='frigate'.",
        "en": "Camera name, e.g. 'ringeklokke'. For snapshot(scope='one') and action='frigate'.",
        "de": "Kameraname, z.B. 'ringeklokke'. Bei snapshot(scope='one') und action='frigate'.",
    },
    "tool_kamera_query_desc": {
        "nb": "Konkret spørsmål om bildet. Kun ved action='snapshot'.",
        "en": "Concrete question about the image. Only for action='snapshot'.",
        "de": "Konkrete Frage zum Bild. Nur bei action='snapshot'.",
    },
    "tool_kamera_name_desc": {
        "nb": "Filtrer på person/kjøretøy. Kun ved action='events'.",
        "en": "Filter by person/vehicle. Only for action='events'.",
        "de": "Nach Person/Fahrzeug filtern. Nur bei action='events'.",
    },
    "tool_kamera_hours_back_desc": {
        "nb": "Siste N timer. Standard 24, maks 48. Kun ved action='events'.",
        "en": "Last N hours. Default 24, max 48. Only for action='events'.",
        "de": "Letzte N Stunden. Standard 24, max 48. Nur bei action='events'.",
    },
    "tool_kamera_label_desc": {
        "nb": "Objekttype: 'person', 'car', 'cat'. Kun ved action='frigate'.",
        "en": "Object type: 'person', 'car', 'cat'. Only for action='frigate'.",
        "de": "Objekttyp: 'person', 'car', 'cat'. Nur bei action='frigate'.",
    },
    "tool_kamera_count_desc": {
        "nb": "Antall hendelser. Standard 10, maks 50.",
        "en": "Number of events. Default 10, max 50.",
        "de": "Anzahl Ereignisse. Standard 10, max 50.",
    },
    "tool_kamera_faces_only_desc": {
        "nb": "True: kun hendelser med gjenkjent ansikt. Kun ved action='frigate'.",
        "en": "True: only events with recognized face. Only for action='frigate'.",
        "de": "True: nur Ereignisse mit erkanntem Gesicht. Nur bei action='frigate'.",
    },
    "tool_kamera_event_id_desc": {
        "nb": "Frigate event_id for en konkret hendelse. Påkrevd ved action='show_event'.",
        "en": "Frigate event_id for a specific event. Required for action='show_event'.",
        "de": "Frigate event_id für ein bestimmtes Ereignis. Erforderlich bei action='show_event'.",
    },
    # kare_image
    "tool_kare_image_desc": {
        "nb": "Generer eller rediger et bilde. mode='generate': ny fra tekstbeskrivelse. mode='edit': endre eksisterende (krever 'image_b64'). Skriv detaljert prompt.",
        "en": "Generate or edit an image. mode='generate': new from text description. mode='edit': modify existing (requires 'image_b64'). Write a detailed prompt.",
        "de": "Bild generieren oder bearbeiten. mode='generate': neu aus Textbeschreibung. mode='edit': vorhandenes bearbeiten (erfordert 'image_b64'). Detaillierten Prompt schreiben.",
    },
    "tool_kare_image_mode_desc": {
        "nb": "'generate' = nytt bilde fra tekst.",
        "en": "'generate' = new image from text.",
        "de": "'generate' = neues Bild aus Text.",
    },
    "tool_kare_image_prompt_desc": {
        "nb": "Detaljert beskrivelse av bildet eller hva som skal endres.",
        "en": "Detailed description of the image or what to change.",
        "de": "Detaillierte Beschreibung des Bildes oder was geändert werden soll.",
    },
    "tool_kare_image_negative_prompt_desc": {
        "nb": "Hva som skal unngås, f.eks. 'blurry, low quality'.",
        "en": "What to avoid, e.g. 'blurry, low quality'.",
        "de": "Was vermieden werden soll, z.B. 'unscharf, schlechte Qualität'.",
    },
    "tool_kare_image_image_b64_desc": {
        "nb": "Base64-kodet input-bilde (PNG/JPEG, uten data:-prefiks). Kreves ved mode='edit'.",
        "en": "Base64-encoded input image (PNG/JPEG, without data: prefix). Required for mode='edit'.",
        "de": "Base64-kodiertes Eingabebild (PNG/JPEG, ohne data:-Präfix). Erforderlich bei mode='edit'.",
    },
    # se_bilder
    "tool_se_bilder_desc": {
        "nb": "List og vis bilder for en bruker. Med 'image_id' + mode='vis': vis i chatten. Med mode='analyser': send til VLM. Uten image_id: list alle bilder.",
        "en": "List and view images for a user. With 'image_id' + mode='vis': show in chat. With mode='analyser': send to VLM. Without image_id: list all images.",
        "de": "Bilder für einen Nutzer auflisten und anzeigen. Mit 'image_id' + mode='vis': im Chat anzeigen. Mit mode='analyser': an VLM senden. Ohne image_id: alle Bilder auflisten.",
    },
    "tool_se_bilder_user_id_desc": {
        "nb": "Brukernavnet å slå opp bilder for.",
        "en": "Username to look up images for.",
        "de": "Benutzername für den Bilder abgerufen werden sollen.",
    },
    "tool_se_bilder_folder_desc": {
        "nb": "'input'=brukeren sendte, 'output'=Kåre genererte, 'all'=begge.",
        "en": "'input'=user sent, 'output'=Kåre generated, 'all'=both.",
        "de": "'input'=Nutzer gesendet, 'output'=Kåre generiert, 'all'=beide.",
    },
    "tool_se_bilder_limit_desc": {
        "nb": "Maks antall bilder (standard 10, maks 50).",
        "en": "Max number of images (default 10, max 50).",
        "de": "Maximale Anzahl Bilder (Standard 10, max 50).",
    },
    "tool_se_bilder_image_id_desc": {
        "nb": "Hvis satt, bruk sammen med 'mode'. Utelat for å liste bilder.",
        "en": "If set, use together with 'mode'. Omit to list images.",
        "de": "Falls gesetzt, zusammen mit 'mode' verwenden. Weglassen um Bilder aufzulisten.",
    },
    "tool_se_bilder_mode_desc": {
        "nb": "'view' = vis i chatten. 'analyze' = send til VLM for analyse.",
        "en": "'view' = show in chat. 'analyze' = send to VLM for analysis.",
        "de": "'view' = im Chat anzeigen. 'analyze' = zur Analyse an VLM senden.",
    },
    # media
    "tool_media_desc": {
        "nb": "Mediekontroll: Plex (TV/film/serier) og radio (MPD). Plex: plex_sessions, plex_history, plex_search, plex_library, plex_episodes, plex_clients, plex_play. Typisk flyt: plex_search → plex_episodes → plex_play. Radio: radio_status, radio_play, radio_stop, radio_volume.",
        "en": "Media control: Plex (TV/film/series) and radio (MPD). Plex: plex_sessions, plex_history, plex_search, plex_library, plex_episodes, plex_clients, plex_play. Typical flow: plex_search → plex_episodes → plex_play. Radio: radio_status, radio_play, radio_stop, radio_volume.",
        "de": "Mediensteuerung: Plex (TV/Film/Serien) und Radio (MPD). Plex: plex_sessions, plex_history, plex_search, plex_library, plex_episodes, plex_clients, plex_play. Typischer Ablauf: plex_search → plex_episodes → plex_play. Radio: radio_status, radio_play, radio_stop, radio_volume.",
    },
    "tool_media_action_desc": {
        "nb": "Operasjonen som skal utføres.",
        "en": "The operation to perform.",
        "de": "Die auszuführende Operation.",
    },
    "tool_media_query_desc": {
        "nb": "Søketekst. Brukes med plex_search.",
        "en": "Search text. Used with plex_search.",
        "de": "Suchtext. Wird mit plex_search verwendet.",
    },
    "tool_media_rating_key_desc": {
        "nb": "Plex element-ID (ratingKey). Brukes med plex_episodes og plex_play.",
        "en": "Plex element ID (ratingKey). Used with plex_episodes and plex_play.",
        "de": "Plex Element-ID (ratingKey). Wird mit plex_episodes und plex_play verwendet.",
    },
    "tool_media_client_desc": {
        "nb": "Rom/nodenavn, f.eks. 'verksted', 'stue'. Brukes med plex_play.",
        "en": "Room/node name, e.g. 'verksted', 'stue'. Used with plex_play.",
        "de": "Raum-/Knotenname, z.B. 'verksted', 'stue'. Wird mit plex_play verwendet.",
    },
    "tool_media_offset_desc": {
        "nb": "Gjenoppta-posisjon i sekunder. Brukes med plex_play.",
        "en": "Resume position in seconds. Used with plex_play.",
        "de": "Fortsetzungsposition in Sekunden. Wird mit plex_play verwendet.",
    },
    "tool_media_user_desc": {
        "nb": "Brukernavn for å filtrere. Brukes med plex_history.",
        "en": "Username to filter by. Used with plex_history.",
        "de": "Benutzername zum Filtern. Wird mit plex_history verwendet.",
    },
    "tool_media_limit_desc": {
        "nb": "Maks antall resultater. Standard 20.",
        "en": "Max number of results. Default 20.",
        "de": "Maximale Anzahl Ergebnisse. Standard 20.",
    },
    "tool_media_station_desc": {
        "nb": "Radiostasjon: navn (f.eks. 'P4', 'NRK P1') eller stream-URL. Brukes med radio_play.",
        "en": "Radio station: name (e.g. 'P4', 'NRK P1') or stream URL. Used with radio_play.",
        "de": "Radiosender: Name (z.B. 'P4', 'NRK P1') oder Stream-URL. Wird mit radio_play verwendet.",
    },
    "tool_media_volume_desc": {
        "nb": "Volum 0–100. Brukes med radio_volume.",
        "en": "Volume 0–100. Used with radio_volume.",
        "de": "Lautstärke 0–100. Wird mit radio_volume verwendet.",
    },
    # announce
    "tool_announce_desc": {
        "nb": "Si noe høyt via høyttaler, eller vis innhold på en skjerm. action='say': TTS til lydnode. action='display': tekst/bilde til TV/skjerm. action='list_display': liste tilgjengelige skjermnoder. target: romnavn, node-ID, eller 'all'.",
        "en": "Say something aloud via speaker, or show content on a display. action='say': TTS to audio node. action='display': text/image to TV/screen. action='list_display': list available display nodes. target: room name, node ID, or 'all'.",
        "de": "Etwas laut sagen oder Inhalt auf einem Bildschirm anzeigen. action='say': TTS an Audioknoten. action='display': Text/Bild an TV/Bildschirm. action='list_display': verfügbare Bildschirmknoten auflisten.",
    },
    "tool_announce_action_desc": {
        "nb": "'say'=TTS til høyttaler (standard), 'display'=tekst/bilde til skjerm, 'list_display'=vis tilgjengelige skjermer.",
        "en": "'say'=TTS to speaker (default), 'display'=text/image to screen, 'list_display'=list available displays.",
        "de": "'say'=TTS an Lautsprecher (Standard), 'display'=Text/Bild an Bildschirm, 'list_display'=verfügbare Bildschirme auflisten.",
    },
    "tool_announce_text_desc": {
        "nb": "Teksten som skal sies eller vises. Naturlig tekst, uten markdown.",
        "en": "The text to say or display. Natural text, no markdown.",
        "de": "Der zu sprechende oder anzuzeigende Text. Natürlicher Text, kein Markdown.",
    },
    "tool_announce_target_desc": {
        "nb": "For 'say': 'local'=AI-PC, romnavn, eller 'all'. For 'display': node-ID, romnavn, eller 'all'.",
        "en": "For 'say': 'local'=AI-PC, room name, or 'all'. For 'display': node ID, room name, or 'all'.",
        "de": "Für 'say': 'local'=AI-PC, Raumname, oder 'all'. Für 'display': Knoten-ID, Raumname, oder 'all'.",
    },
    "tool_announce_volume_desc": {
        "nb": "Volumnivå 0.0–1.0. Sett kun hvis brukeren ber om bestemt volum. Utelat ellers.",
        "en": "Volume level 0.0–1.0. Set only if user explicitly requests a specific volume. Omit otherwise.",
        "de": "Lautstärke 0.0–1.0. Nur setzen wenn Nutzer explizit eine bestimmte Lautstärke wünscht. Sonst weglassen.",
    },
    "tool_announce_image_id_desc": {
        "nb": "Bilde-ID fra kare_image-tool. Bildet vises på skjermen (kun for action='display').",
        "en": "Image ID from the kare_image tool. The image will be shown on screen (only for action='display').",
        "de": "Bild-ID vom kare_image-Tool. Das Bild wird auf dem Bildschirm angezeigt (nur für action='display').",
    },
    "tool_announce_title_desc": {
        "nb": "Overskrift i overlay (standard: navn på assistenten).",
        "en": "Display overlay title (default: assistant name).",
        "de": "Titel des Overlays (Standard: Name des Assistenten).",
    },
    "tool_announce_duration_desc": {
        "nb": "Sekunder overlayет vises (standard: 8).",
        "en": "Seconds the overlay is shown (default: 8).",
        "de": "Sekunden, die das Overlay angezeigt wird (Standard: 8).",
    },
    "tool_announce_position_desc": {
        "nb": "Posisjon på skjermen (standard: bottom_right).",
        "en": "Overlay position on screen (default: bottom_right).",
        "de": "Position auf dem Bildschirm (Standard: bottom_right).",
    },
    "announce_no_display_nodes": {
        "nb": "Ingen skjermnoder er konfigurert. Legg til TV eller projektor under Innstillinger → Noder.",
        "en": "No display nodes are configured. Add a TV or projector under Settings → Nodes.",
        "de": "Keine Bildschirmknoten konfiguriert. TV oder Projektor unter Einstellungen → Knoten hinzufügen.",
    },
    "announce_display_list": {
        "nb": "Tilgjengelige skjermer:",
        "en": "Available displays:",
        "de": "Verfügbare Bildschirme:",
    },
    "announce_display_target_not_found": {
        "nb": "Fant ingen skjermnode for '{target}'.",
        "en": "No display node found for '{target}'.",
        "de": "Kein Bildschirmknoten für '{target}' gefunden.",
    },
    "announce_display_ok": {
        "nb": "Innhold sendt til {count} skjerm(er).",
        "en": "Content sent to {count} display(s).",
        "de": "Inhalt an {count} Bildschirm(e) gesendet.",
    },
    "announce_display_partial": {
        "nb": "Sendt til {ok} skjerm(er), {fail} feilet.",
        "en": "Sent to {ok} display(s), {fail} failed.",
        "de": "An {ok} Bildschirm(e) gesendet, {fail} fehlgeschlagen.",
    },
    "announce_display_failed": {
        "nb": "Kunne ikke sende til noen skjerm: {errors}",
        "en": "Could not send to any display: {errors}",
        "de": "Konnte an keinen Bildschirm senden: {errors}",
    },
    # HA domain labels (used in room/device listings)
    "ha_domain_light": {
        "nb": "lys — kan styres (turn_on/turn_off/set_level/set_color_temp/set_color)",
        "en": "light — controllable (turn_on/turn_off/set_level/set_color_temp/set_color)",
        "de": "Licht — steuerbar (turn_on/turn_off/set_level/set_color_temp/set_color)",
    },
    "ha_domain_switch": {
        "nb": "bryter — kan styres (turn_on/turn_off)",
        "en": "switch — controllable (turn_on/turn_off)",
        "de": "Schalter — steuerbar (turn_on/turn_off)",
    },
    "ha_domain_climate": {
        "nb": "temperaturkontroll — kan styres",
        "en": "thermostat — controllable",
        "de": "Thermostat — steuerbar",
    },
    "ha_domain_media_player": {
        "nb": "mediaspiller — kan styres",
        "en": "media player — controllable",
        "de": "Medienspieler — steuerbar",
    },
    "ha_domain_vacuum": {
        "nb": "støvsuger — kan styres",
        "en": "vacuum — controllable",
        "de": "Staubsauger — steuerbar",
    },
    "ha_domain_cover": {
        "nb": "gardin/port — kan styres",
        "en": "cover/garage — controllable",
        "de": "Jalousie/Tor — steuerbar",
    },
    "ha_domain_sensor": {
        "nb": "sensor — kun lesbar",
        "en": "sensor — read-only",
        "de": "Sensor — nur lesbar",
    },
    "ha_domain_camera": {
        "nb": "kamera — ikke styrbar",
        "en": "camera — not controllable",
        "de": "Kamera — nicht steuerbar",
    },
    "ha_domain_person": {
        "nb": "person — tilstedeværelse",
        "en": "person — presence",
        "de": "Person — Anwesenheit",
    },
    "ha_domain_input_boolean": {
        "nb": "bryter — kan styres",
        "en": "switch — controllable",
        "de": "Schalter — steuerbar",
    },
    # exec misc
    "exec_unknown_tool": {
        "nb": "Ukjent verktøy: '{name}'.",
        "en": "Unknown tool: '{name}'.",
        "de": "Unbekanntes Werkzeug: '{name}'.",
    },
    # router_generate hardcoded strings
    "gen_empty_message": {
        "nb": "Jeg fikk en tom melding.",
        "en": "I received an empty message.",
        "de": "Ich habe eine leere Nachricht erhalten.",
    },
    "gen_no_contact": {
        "nb": "Jeg fikk ikke kontakt med systemene akkurat nå.",
        "en": "I could not reach the systems right now.",
        "de": "Ich konnte die Systeme gerade nicht erreichen.",
    },
    "gen_no_response": {
        "nb": "Jeg fikk ikke noe fornuftig svar.",
        "en": "I did not get a sensible response.",
        "de": "Ich habe keine sinnvolle Antwort erhalten.",
    },
    "gen_timeout": {
        "nb": "Kåre kom ikke frem til et svar innen rimelig tid.",
        "en": "Kåre could not produce a response in time.",
        "de": "Kåre konnte keine Antwort innerhalb der Zeit produzieren.",
    },
    "gen_stt_note": {
        "nb": "\nOBS: Dette er transkribert tale (STT). Ta hensyn til mulige talefeil, dialekt og feilstavinger. Hvis teksten virker usammenhengende eller ikke gir mening, spør heller bruker om hva de mente enn å gjette.\n",
        "en": "\nNOTE: This is transcribed speech (STT). Account for possible speech errors, dialect, and misspellings. If the text seems garbled or doesn't make sense, ask the user what they meant instead of guessing.\n",
        "de": "\nHINWEIS: Dies ist transkribierte Sprache (STT). Berücksichtige mögliche Sprachfehler, Dialekte und Rechtschreibfehler. Wenn der Text unzusammenhängend wirkt oder keinen Sinn ergibt, frage den Nutzer lieber nach, was gemeint war, anstatt zu raten.\n",
    },
    "stt_voice_confirmed": {
        "nb": "\n[Stemme bekreftet: {user} ({pct}% sikker)]\n",
        "en": "\n[Voice confirmed: {user} ({pct}% confidence)]\n",
        "de": "\n[Stimme bestätigt: {user} ({pct}% Sicherheit)]\n",
    },
    "stt_voice_default_match": {
        "nb": "\n[Stemme: {user} (node-standard, gjetning {pct}%)]\n",
        "en": "\n[Voice: {user} (node default, guess {pct}%)]\n",
        "de": "\n[Stimme: {user} (Node-Standard, Schätzung {pct}%)]\n",
    },
    "stt_voice_default_guess": {
        "nb": "\n[Stemme: {user} (node-standard) — ligner på {guess} ({pct}%)]\n",
        "en": "\n[Voice: {user} (node default) — sounds like {guess} ({pct}%)]\n",
        "de": "\n[Stimme: {user} (Node-Standard) — klingt wie {guess} ({pct}%)]\n",
    },
    "stt_voice_default_no_enrollment": {
        "nb": "\n[Stemme: {user} (node-standard) — ingen voiceprint registrert]\n",
        "en": "\n[Voice: {user} (node default) — no voiceprint enrolled]\n",
        "de": "\n[Stimme: {user} (Node-Standard) — kein Stimmabdruck registriert]\n",
    },
    "stt_voice_unknown_guess": {
        "nb": "\n[Stemme: ukjent — beste gjetning: {guess} ({pct}%, terskel {threshold}%)]\n",
        "en": "\n[Voice: unknown — best guess: {guess} ({pct}%, threshold {threshold}%)]\n",
        "de": "\n[Stimme: unbekannt — beste Schätzung: {guess} ({pct}%, Schwellwert {threshold}%)]\n",
    },
    "stt_voice_unknown_no_enrollment": {
        "nb": "\n[Stemme: ukjent — ingen voiceprint registrert på denne noden]\n",
        "en": "\n[Voice: unknown — no voiceprint enrolled for this node]\n",
        "de": "\n[Stimme: unbekannt — kein Stimmabdruck für diesen Node registriert]\n",
    },
    "gen_tool_limit": {
        "nb": "[SYSTEM: Du har nå brukt alle 6 tool-runder. Svar brukeren direkte med det du har funnet — ingen flere tool-kall er mulig.]",
        "en": "[SYSTEM: You have now used all 6 tool rounds. Answer the user directly with what you have found — no more tool calls are possible.]",
        "de": "[SYSTEM: Du hast jetzt alle 6 Werkzeugrunden verwendet. Antworte dem Nutzer direkt mit dem, was du gefunden hast — keine weiteren Werkzeugaufrufe sind möglich.]",
    },
    "gen_image_note": {
        "nb": "[SYSTEM: Her er bildet du nettopp lastet med se_bilder. Beskriv det i svaret ditt.]",
        "en": "[SYSTEM: Here is the image you just loaded with se_bilder. Describe it in your response.]",
        "de": "[SYSTEM: Hier ist das Bild, das du gerade mit se_bilder geladen hast. Beschreibe es in deiner Antwort.]",
    },
    "gen_promise_correction": {
        "nb": "[SYSTEM: Du sa at du ville notere eller huske noe, men du kalte ikke notat-verktøyet. Gjør det nå: kall notat(action='write', list_name='kare') med det du lovet å huske. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said you would note or remember something, but you did not call the note tool. Do it now: call note(action='write', list_name='kare') with what you promised to remember. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, du würdest etwas notieren oder merken, aber du hast das note-Werkzeug nicht aufgerufen. Tue es jetzt: rufe note(action='write', list_name='kare') mit dem auf, was du versprochen hast zu merken. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_promise_correction_note": {
        "nb": "[SYSTEM: Du sa at du ville notere eller huske noe, men du kalte ikke note-verktøyet. Gjør det nå: kall note(action='write', list_name='kare') med det du lovet å huske. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said you would note or remember something, but you did not call the note tool. Do it now: call note(action='write', list_name='kare') with what you promised to remember. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, du würdest etwas notieren, aber du hast das note-Werkzeug nicht aufgerufen. Tue es jetzt: rufe note(action='write', list_name='kare') auf. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_promise_correction_agenda": {
        "nb": "[SYSTEM: Du sa at noe skal tas opp på neste dev-møte, men du la det ikke på agendaen. Gjør det nå: kall note(action='write', list_name='agenda', theme=…) med saken og temaet den hører til. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said something should be raised at the next dev meeting, but you did not put it on the agenda. Do it now: call note(action='write', list_name='agenda', theme=…) with the item and the theme it belongs to. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, etwas solle beim nächsten Entwicklertreffen angesprochen werden, hast es aber nicht auf die Agenda gesetzt. Tue es jetzt: rufe note(action='write', list_name='agenda', theme=…) mit dem Punkt und seinem Thema auf. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_promise_correction_timer": {
        "nb": "[SYSTEM: Du sa at du ville sette en timer eller påminnelse, men du kalte ikke timer-verktøyet. Gjør det nå: kall timer med riktig action, tid og tekst. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said you would set a timer or reminder, but you did not call the timer tool. Do it now: call timer with the correct action, time, and text. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, du würdest einen Timer oder eine Erinnerung setzen, aber du hast das timer-Werkzeug nicht aufgerufen. Tue es jetzt: rufe timer mit der richtigen Aktion, Zeit und Text auf. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_promise_correction_household": {
        "nb": "[SYSTEM: Du sa at du satte husstanden til borte- eller hjemme-modus, men du kalte ikke household-verktøyet. Gjør det nå: kall household med riktig action (set_away eller set_home) og de detaljene du nevnte. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said you set the household to away or home mode, but you did not call the household tool. Do it now: call household with the correct action (set_away or set_home) and the details you mentioned. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, du hättest den Haushalt auf Abwesend- oder Zuhause-Modus gesetzt, aber du hast das household-Werkzeug nicht aufgerufen. Tue es jetzt: rufe household mit der richtigen Aktion (set_away oder set_home) auf. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_correction_no_tools_left": {
        "nb": "[SYSTEM: Svaret ditt sier at noe er gjort, men det finnes ikke noe verktøykall bak det, og du har ingen verktøyrunder igjen. Ikke påstå noe som ikke er gjort. Svar brukeren ærlig: hva ble faktisk gjort, og hva ble ikke gjort.]",
        "en": "[SYSTEM: Your reply says something was done, but no tool call backs it, and you have no tool rounds left. Do not claim anything that was not done. Answer the user honestly: what was actually done, and what was not.]",
        "de": "[SYSTEM: Deine Antwort sagt, dass etwas erledigt wurde, aber kein Werkzeugaufruf belegt das, und du hast keine Werkzeugrunden mehr. Behaupte nichts, was nicht erledigt wurde. Antworte dem Nutzer ehrlich: was wurde tatsächlich erledigt und was nicht.]",
    },
    "gen_correction_claimed_done": {
        "nb": "[SYSTEM: Svaret ditt sier at noe er notert, lagret, lagt til eller markert, men ingen verktøy ble kalt i denne forespørselen. Hvis det skal gjøres: kall verktøyet nå. Hvis det ikke ble gjort, eller du bare beskrev noe som allerede var slik: skriv svaret på nytt og si ærlig hva som er gjort og hva som ikke er gjort.]",
        "en": "[SYSTEM: Your reply says something was noted, saved, added or marked, but no tool was called in this request. If it should be done: call the tool now. If it was not done, or you only described how things already were: rewrite the reply and say honestly what was done and what was not.]",
        "de": "[SYSTEM: Deine Antwort sagt, dass etwas notiert, gespeichert, hinzugefügt oder markiert wurde, aber in dieser Anfrage wurde kein Werkzeug aufgerufen. Wenn es getan werden soll: rufe das Werkzeug jetzt auf. Wenn es nicht getan wurde oder du nur beschrieben hast, wie es schon war: schreibe die Antwort neu und sage ehrlich, was getan wurde und was nicht.]",
    },
    "gen_promise_correction_generic": {
        "nb": "[SYSTEM: Du lovet å utføre en handling med et verktøy, men kalte det ikke. Gjør det nå — kun tool-kall, ingen forklarende tekst.]",
        "en": "[SYSTEM: You promised to perform an action using a tool, but did not call it. Do it now — tool call only, no explanatory text.]",
        "de": "[SYSTEM: Du hast versprochen, eine Aktion mit einem Werkzeug durchzuführen, aber es nicht aufgerufen. Tue es jetzt — nur Werkzeugaufruf, kein erklärender Text.]",
    },
    "gen_correction_truncated_think": {
        "nb": "[SYSTEM: Svaret ditt ble avbrutt midt i tankeprosessen og inneholdt ikke noe faktisk svar. Svar nå direkte og konkret, uten å gjenta tankeprosessen.]",
        "en": "[SYSTEM: Your response was cut off mid-thought and did not contain an actual answer. Answer now, directly and concisely, without repeating your reasoning.]",
        "de": "[SYSTEM: Deine Antwort wurde mitten im Gedankengang abgebrochen und enthielt keine tatsächliche Antwort. Antworte jetzt direkt und knapp, ohne deinen Gedankengang zu wiederholen.]",
    },
    "gen_correction_foreign_script": {
        "nb": "[SYSTEM: Svaret ditt inneholder tegn fra et annet skriftsystem ({chars}) midt i teksten — som regel en skrivefeil fra modellen. Skriv hele svaret på nytt uten dem. Bare hvis tegnene var ment (brukeren ba om dem, eller du siterer et navn på det språket), behold dem. Avslutt med [LANG:xx] på egen linje.]",
        "en": "[SYSTEM: Your reply contains characters from another writing system ({chars}) in the middle of the text — usually a model glitch. Rewrite the whole reply without them. Keep them only if they were intended (the user asked for them, or you are quoting a name in that language). End with [LANG:xx] on its own line.]",
        "de": "[SYSTEM: Deine Antwort enthält mitten im Text Zeichen aus einem anderen Schriftsystem ({chars}) — meist ein Modellfehler. Schreibe die ganze Antwort ohne sie neu. Behalte sie nur, wenn sie beabsichtigt waren (der Nutzer hat danach gefragt, oder du zitierst einen Namen in dieser Sprache). Beende mit [LANG:xx] in einer eigenen Zeile.]",
    },
    "gen_language_mismatch_correction": {
        "nb": "[SYSTEM: Svaret ditt var på feil språk. Svar nå på riktig språk og avslutt med [LANG:xx] på egen linje (xx = språkkoden du bruker).]",
        "en": "[SYSTEM: Your reply was in the wrong language. Reply again in the correct language and end with [LANG:xx] on its own line (xx = the language code you used).]",
        "de": "[SYSTEM: Deine Antwort war in der falschen Sprache. Antworte jetzt in der richtigen Sprache und beende mit [LANG:xx] in einer eigenen Zeile (xx = der von dir verwendete Sprachcode).]",
    },
    "lib_reason_freely_system": {
        "nb": "Du bruker nå din fulle interne kunnskap fritt. Ingen smarthus-begrensninger gjelder her. Tenk åpent og presist basert på det du vet fra treningen. Dette er et internt verktøykall — svaret integreres i din vanlige respons til brukeren.",
        "en": "You are now using your full internal knowledge freely. No smart home constraints apply here. Think openly and precisely based on what you know from training. This is an internal tool call — the answer is integrated into your regular response to the user.",
        "de": "Du verwendest jetzt dein gesamtes internes Wissen frei. Keine Smart-Home-Einschränkungen gelten hier. Denke offen und präzise basierend auf deinem Trainingswissen. Dies ist ein interner Werkzeugaufruf — die Antwort wird in deine reguläre Antwort an den Nutzer integriert.",
    },
    # ── skriv_reflex ──────────────────────────────────────────────────────────
    "tool_skriv_reflex_desc": {
        "nb": "Analyser mønster i hukommelsen og foreslå, bekreft eller avvis nye fastpath-reflekser. Kåre lærer egne muskelreflekser fra gjentatte kommandoer.",
        "en": "Analyze memory patterns and suggest, confirm, or reject new fastpath reflexes. Kåre learns its own muscle reflexes from repeated commands.",
        "de": "Analysiere Gedächtnismuster und schlage neue Fastpath-Reflexe vor, bestätige oder weise sie ab. Kåre lernt eigene Muskelreflexe aus wiederholten Befehlen.",
    },
    "tool_skriv_reflex_action_desc": {
        "nb": "suggest: foreslå nye reflekser fra LTM. confirm: godkjenn forslag (kun admin). reject: avvis forslag (kun admin). list: vis ventende forslag.",
        "en": "suggest: propose new reflexes from LTM. confirm: approve a proposal (admin only). reject: reject a proposal (admin only). list: show pending proposals.",
        "de": "suggest: neue Reflexe aus LTM vorschlagen. confirm: Vorschlag bestätigen (nur Admin). reject: Vorschlag ablehnen (nur Admin). list: ausstehende Vorschläge anzeigen.",
    },
    "tool_skriv_reflex_proposal_id_desc": {
        "nb": "ID på forslaget som skal bekreftes eller avvises (fra 'list' eller 'suggest').",
        "en": "ID of the proposal to confirm or reject (from 'list' or 'suggest').",
        "de": "ID des zu bestätigenden oder abzulehnenden Vorschlags (aus 'list' oder 'suggest').",
    },
    "reflex_suggest_none": {
        "nb": "Fant ingen kommandoer som er gjentatt {threshold} ganger eller mer med positivt utfall. Prøv igjen etter at systemet har akkumulert mer historikk.",
        "en": "No commands found that were repeated {threshold} times or more with positive outcomes. Try again after the system has accumulated more history.",
        "de": "Keine Befehle gefunden, die {threshold} Mal oder öfter mit positivem Ergebnis wiederholt wurden. Versuche es erneut, nachdem das System mehr Historie gesammelt hat.",
    },
    "reflex_suggest_no_new": {
        "nb": "Alle kvalifiserte kommandoer er allerede lagt til som reflekser eller venter på godkjenning.",
        "en": "All qualifying commands are already added as reflexes or pending approval.",
        "de": "Alle qualifizierten Befehle sind bereits als Reflexe hinzugefügt oder warten auf Genehmigung.",
    },
    "reflex_suggest_header": {
        "nb": "Fant {count} refleks-kandidater (terskel: {threshold} repeterte ok-kommandoer):",
        "en": "Found {count} reflex candidates (threshold: {threshold} repeated ok-commands):",
        "de": "Gefunden: {count} Reflexkandidaten (Schwelle: {threshold} wiederholte OK-Befehle):",
    },
    "reflex_suggest_confirm_hint": {
        "nb": "Bruk skriv_reflex(action='confirm', proposal_id='...') for å godkjenne, eller 'reject' for å avvise.",
        "en": "Use skriv_reflex(action='confirm', proposal_id='...') to approve, or 'reject' to dismiss.",
        "de": "Verwende skriv_reflex(action='confirm', proposal_id='...') zum Bestätigen oder 'reject' zum Ablehnen.",
    },
    "reflex_ltm_error": {
        "nb": "Kunne ikke lese hukommelse: {error}",
        "en": "Could not read memory: {error}",
        "de": "Konnte Speicher nicht lesen: {error}",
    },
    "reflex_not_allowed": {
        "nb": "Du har ikke tillatelse til å godkjenne eller avvise Kåres automasjon-forslag. Be en admin om tilgang under Innstillinger → Kåre.",
        "en": "You don't have permission to approve or reject Kåre's automation proposals. Ask an admin for access under Settings → Kåre.",
        "de": "Du hast keine Berechtigung, Kåres Automatisierungsvorschläge zu genehmigen oder abzulehnen. Bitte einen Admin um Zugriff unter Einstellungen → Kåre.",
    },
    "reflex_proposal_not_found": {
        "nb": "Fant ikke forslag med ID '{pid}'.",
        "en": "Proposal with ID '{pid}' not found.",
        "de": "Vorschlag mit ID '{pid}' nicht gefunden.",
    },
    "reflex_proposal_not_pending": {
        "nb": "Forslag '{pid}' har allerede status '{status}'.",
        "en": "Proposal '{pid}' already has status '{status}'.",
        "de": "Vorschlag '{pid}' hat bereits den Status '{status}'.",
    },
    "reflex_confirmed": {
        "nb": 'Refleks {reflex_id} lagt til: "{phrase}". Aktivt fra nå av.',
        "en": 'Reflex {reflex_id} added: "{phrase}". Active immediately.',
        "de": 'Reflex {reflex_id} hinzugefügt: "{phrase}". Ab sofort aktiv.',
    },
    "reflex_rejected": {
        "nb": 'Forslag avvist: "{phrase}".',
        "en": 'Proposal rejected: "{phrase}".',
        "de": 'Vorschlag abgelehnt: "{phrase}".',
    },
    "reflex_list_empty": {
        "nb": "Ingen forslag venter på godkjenning.",
        "en": "No proposals are pending approval.",
        "de": "Keine Vorschläge warten auf Genehmigung.",
    },
    "reflex_list_header": {
        "nb": "{count} forslag venter på godkjenning:",
        "en": "{count} proposals pending approval:",
        "de": "{count} Vorschläge warten auf Genehmigung:",
    },
    # --- household tool ---
    "tool_household_desc": {
        "nb": "Administrer husstandens hjemme/borte-tilstand. Bruk set_away når familien reiser, set_home når de er tilbake, get_status for å sjekke nåværende modus. Kun for admin-brukere.",
        "en": "Manage household home/away state. Use set_away when the family leaves, set_home when back, get_status to check current mode. Admin users only.",
        "de": "Verwalte den Heimstatus des Haushalts. set_away wenn die Familie reist, set_home bei Rückkehr, get_status für aktuellen Modus. Nur für Admins.",
    },
    "tool_household_action_desc": {
        "nb": "set_away: sett bortreisemodus. set_home: sett hjemmemodus (manuell override). get_status: les nåværende tilstand.",
        "en": "set_away: activate away mode. set_home: activate home mode (manual override). get_status: read current state.",
        "de": "set_away: Abwesenheitsmodus aktivieren. set_home: Heimat-Modus aktivieren. get_status: aktuellen Status lesen.",
    },
    "tool_household_reason_desc": {
        "nb": "Årsak til bortreise, f.eks. 'reiser til Oslo'. Valgfritt.",
        "en": "Reason for leaving, e.g. 'travelling to Oslo'. Optional.",
        "de": "Grund für die Abwesenheit, z.B. 'Reise nach Oslo'. Optional.",
    },
    "tool_household_return_desc": {
        "nb": "Forventet returdato som ISO-dato (YYYY-MM-DD), f.eks. '2026-06-15'. Valgfritt.",
        "en": "Expected return date as ISO date (YYYY-MM-DD), e.g. '2026-06-15'. Optional.",
        "de": "Erwartetes Rückkehrdatum als ISO-Datum (YYYY-MM-DD), z.B. '2026-06-15'. Optional.",
    },
    "tool_household_set_away_ok": {
        "nb": "Bortreisemodus aktivert. Trusselterskel justert. Forventet hjemkomst: {ret}.",
        "en": "Away mode activated. Threat threshold adjusted. Expected return: {ret}.",
        "de": "Abwesenheitsmodus aktiviert. Bedrohungsschwelle angepasst. Erwartete Rückkehr: {ret}.",
    },
    "tool_household_set_away_ok_no_date": {
        "nb": "Bortreisemodus aktivert. Trusselterskel justert. Ingen returdato satt.",
        "en": "Away mode activated. Threat threshold adjusted. No return date set.",
        "de": "Abwesenheitsmodus aktiviert. Bedrohungsschwelle angepasst. Kein Rückkehrdatum gesetzt.",
    },
    "tool_household_set_home_ok": {
        "nb": "Hjemmemodus aktivert. Normalt trusselterskel gjenopprettet.",
        "en": "Home mode activated. Normal threat thresholds restored.",
        "de": "Heimat-Modus aktiviert. Normale Bedrohungsschwellen wiederhergestellt.",
    },
    "tool_household_already_home": {
        "nb": "Husstandsstatus er allerede: hjemme.",
        "en": "Household status is already: home.",
        "de": "Haushaltsstatus ist bereits: zuhause.",
    },
    "tool_household_status": {
        "nb": "Husstandsstatus: {mode}. Sist oppdatert: {updated}.",
        "en": "Household status: {mode}. Last updated: {updated}.",
        "de": "Haushaltsstatus: {mode}. Zuletzt aktualisiert: {updated}.",
    },
    # ── notify ────────────────────────────────────────────────────────────────
    "notify_frigate_away_person": {
        "nb": "Bevegelse oppdaget av {camera} ({label}). Kamera: {camera}.",
        "en": "Motion detected by {camera} ({label}). Camera: {camera}.",
        "de": "Bewegung erkannt von {camera} ({label}). Kamera: {camera}.",
    },
    "notify_frigate_away_unknown": {
        "nb": "Ukjent gjenstand oppdaget av {camera} om natten.",
        "en": "Unknown object detected by {camera} at night.",
        "de": "Unbekanntes Objekt von {camera} in der Nacht erkannt.",
    },
    "notify_timer_fired": {
        "nb": "Timer ferdig: {label}.",
        "en": "Timer fired: {label}.",
        "de": "Timer abgelaufen: {label}.",
    },
    # ── role-age blocks (system prompt injection for child/teen) ─────────────
    "llm_role_age_child": {
        "nb": (
            "# Brukernivå\n"
            "Du snakker nå med et barn (7–12 år). "
            "Bruk korte setninger og enkle ord. "
            "Svar bør ikke være lengre enn 3–4 setninger. "
            "Vær varm og tydelig. Unngå ironi og komplekse abstraksjoner."
        ),
        "en": (
            "# User level\n"
            "You are now speaking with a child (age 7–12). "
            "Use short sentences and simple words. "
            "Replies should be no longer than 3–4 sentences. "
            "Be warm and clear. Avoid irony and complex abstractions."
        ),
        "de": (
            "# Benutzerlevel\n"
            "Du sprichst jetzt mit einem Kind (7–12 Jahre). "
            "Verwende kurze Sätze und einfache Wörter. "
            "Antworten sollten nicht länger als 3–4 Sätze sein. "
            "Sei warm und klar. Vermeide Ironie und komplexe Abstraktionen."
        ),
    },
    "llm_role_age_teen": {
        "nb": (
            "# Brukernivå\n"
            "Du snakker nå med en tenåring (13–17 år). "
            "Svar kan være 5–8 setninger. "
            "Bruk naturlig språk — du kan bruke humor og enkle abstraksjoner."
        ),
        "en": (
            "# User level\n"
            "You are now speaking with a teenager (age 13–17). "
            "Replies can be 5–8 sentences. "
            "Use natural language — you may use humor and simple abstractions."
        ),
        "de": (
            "# Benutzerlevel\n"
            "Du sprichst jetzt mit einem Teenager (13–17 Jahre). "
            "Antworten können 5–8 Sätze lang sein. "
            "Verwende natürliche Sprache — Humor und einfache Abstraktionen sind erlaubt."
        ),
    },
    # ── user_knowledge block (butler memory) ─────────────────────────────────
    "llm_user_knowledge_header": {
        "nb": "# Det jeg vet om deg",
        "en": "# What I know about you",
        "de": "# Was ich über dich weiß",
    },
    # ── STM context block (short_term.py::build_prompt_context) ──────────────
    "stm_header": {
        "nb": "### KORTTIDSMINNE",
        "en": "### SHORT-TERM MEMORY",
        "de": "### KURZZEITGEDÄCHTNIS",
    },
    "stm_daily_summary": {
        "nb": "Oppsummering fra i går:",
        "en": "Summary from yesterday:",
        "de": "Zusammenfassung von gestern:",
    },
    "stm_last_actions": {
        "nb": "Siste handlinger:",
        "en": "Recent actions:",
        "de": "Letzte Aktionen:",
    },
    "stm_action_ok": {"nb": "OK", "en": "OK", "de": "OK"},
    "stm_action_failed": {"nb": "FEIL", "en": "FAILED", "de": "FEHLER"},
    "stm_state_header": {
        "nb": "Tilstand (siste kjent):",
        "en": "State (last known):",
        "de": "Zustand (zuletzt bekannt):",
    },
    "stm_dialog_header": {
        "nb": "Siste dialog (kort):",
        "en": "Recent dialog (short):",
        "de": "Letzter Dialog (kurz):",
    },
    # ── LTM context block (semantic_memory.py::format_for_context) ───────────
    "ltm_header": {
        "nb": "### LANGTIDSMINNE (relevante episoder)",
        "en": "### LONG-TERM MEMORY (relevant episodes)",
        "de": "### LANGZEITGEDÄCHTNIS (relevante Episoden)",
    },
    # ── llm_adapter.py personality_self language-bridging note ───────────────
    # Only injected when active lang != nb — personality_self.md is Kåre's own
    # growing, self-written journal, historically Norwegian-only. This note stops
    # that historical Norwegian content from pulling a non-nb session back to
    # Norwegian, and tells Kåre to write NEW entries in the active language.
    "llm_personality_self_note": {
        "nb": "",
        "en": (
            "# Personal notes (may be in Norwegian)\n"
            "The following are your own past self-observations. Some may be written in "
            "Norwegian, since that was often the active language when they were written. "
            "Treat them as historical context only — keep responding in English regardless "
            "of what language they're in. When you add a new observation through the "
            "self-image tool, write it in English."
        ),
        "de": (
            "# Persönliche Notizen (möglicherweise auf Norwegisch)\n"
            "Das Folgende sind deine eigenen früheren Selbstbeobachtungen. Einige sind "
            "möglicherweise auf Norwegisch verfasst, da dies oft die aktive Sprache war, als "
            "sie geschrieben wurden. Behandle sie nur als historischen Kontext — antworte "
            "weiterhin auf Deutsch, unabhängig von ihrer Sprache. Wenn du über das "
            "Selbstbild-Werkzeug eine neue Beobachtung hinzufügst, schreibe sie auf Deutsch."
        ),
    },
    # ── llm_adapter.py static blocks ──────────────────────────────────────────
    "llm_location_block": {
        "nb": "# Lokasjon\nJeg bor i {city} ({postal}) i {country}. Bruk dette når spørsmålet gjelder nærområdet og ikke nevner noe annet sted. Er et annet sted eller navn nevnt, gjelder det stedet — ikke hjemstedet.",
        "en": "# Location\nI live in {city} ({postal}) in {country}. Use this when the question is about the local area and names no other place. If another place or name is mentioned, that place applies — not home.",
        "de": "# Standort\nIch wohne in {city} ({postal}) in {country}. Nutze dies, wenn die Frage die Umgebung betrifft und keinen anderen Ort nennt. Wird ein anderer Ort oder Name genannt, gilt dieser — nicht der Wohnort.",
    },
    "llm_assistant_name_block": {
        "nb": "Ditt navn er {name}. Brukere kaller deg dette.",
        "en": "Your name is {name}. Users call you this.",
        "de": "Dein Name ist {name}. Benutzer nennen dich so.",
    },
    "llm_fallback_note": {
        "nb": (
            "# Reservemodus\n"
            "Kjernemodellen er midlertidig utilgjengelig. Du kjører på backup-modell (9B). "
            "Svar kort og direkte. Prioriter smarthjem og enkle spørsmål. "
            "Unngå lange resonnement og komplekse flertrinnsoppgaver."
        ),
        "en": (
            "# Fallback mode\n"
            "The core model is temporarily unavailable. You are running on the backup model (9B). "
            "Answer briefly and directly. Prioritize smart home control and simple questions. "
            "Avoid long reasoning chains and complex multi-step tasks."
        ),
        "de": (
            "# Reservemodus\n"
            "Das Kernmodell ist vorübergehend nicht verfügbar. Du läufst auf dem Backup-Modell (9B). "
            "Antworte kurz und direkt. Priorisiere Smart-Home-Steuerung und einfache Fragen. "
            "Vermeide lange Gedankengänge und komplexe mehrstufige Aufgaben."
        ),
    },
    # ── frigate_responder.py::build_context_prompt (autonomous camera-event VLM prompt) ──
    "frigate_time_morning": {"nb": "morgen", "en": "morning", "de": "Morgen"},
    "frigate_time_afternoon": {"nb": "ettermiddag", "en": "afternoon", "de": "Nachmittag"},
    "frigate_time_evening": {"nb": "kveld", "en": "evening", "de": "Abend"},
    "frigate_time_night": {"nb": "natt", "en": "night", "de": "Nacht"},
    "frigate_line_time": {
        "nb": "Tidspunkt: {time} ({time_of_day})",
        "en": "Time: {time} ({time_of_day})",
        "de": "Uhrzeit: {time} ({time_of_day})",
    },
    "frigate_line_camera": {
        "nb": "Kamera: {display} (rolle: {role_display})",
        "en": "Camera: {display} (role: {role_display})",
        "de": "Kamera: {display} (Rolle: {role_display})",
    },
    "frigate_line_detected": {
        "nb": "Detektert: {label} — sikkerhet {score}%",
        "en": "Detected: {label} — confidence {score}%",
        "de": "Erkannt: {label} — Sicherheit {score}%",
    },
    "frigate_line_duration": {
        "nb": "Varighet: {duration:.1f} sekunder",
        "en": "Duration: {duration:.1f} seconds",
        "de": "Dauer: {duration:.1f} Sekunden",
    },
    "frigate_line_zones": {
        "nb": "Soner: {zones}",
        "en": "Zones: {zones}",
        "de": "Zonen: {zones}",
    },
    "frigate_line_face": {
        "nb": "Ansiktsgjenkjenning: {sub_label} ({face_pct}% sikkerhet)",
        "en": "Face recognition: {sub_label} ({face_pct}% confidence)",
        "de": "Gesichtserkennung: {sub_label} ({face_pct}% Sicherheit)",
    },
    "frigate_prompt_wrapper": {
        "nb": (
            "Du ser et overvåkningsbilde fra Kåres kamerasystem.\n\n"
            "{context}\n\n"
            "Beskriv kort hva du ser på bildet. Er det noe uvanlig eller noe som bør handles på? "
            "Maks 3 setninger."
        ),
        "en": (
            "You are looking at a surveillance image from Kåre's camera system.\n\n"
            "{context}\n\n"
            "Briefly describe what you see in the image. Is there anything unusual or anything "
            "that should be acted on? Max 3 sentences."
        ),
        "de": (
            "Du siehst ein Überwachungsbild aus Kåres Kamerasystem.\n\n"
            "{context}\n\n"
            "Beschreibe kurz, was du auf dem Bild siehst. Gibt es etwas Ungewöhnliches oder "
            "etwas, worauf reagiert werden sollte? Maximal 3 Sätze."
        ),
    },
    # ── kaare_nightjob.py compression prompts ─────────────────────────────────
    # These prompts drive unattended LLM compression of interactions into
    # permanently-stored LTM episodes / STM daily summaries — correctness here
    # matters more than most i18n work since the output gets re-injected into
    # every future prompt context regardless of language.
    "nightjob_ltm_header": {
        "nb": "Du er Kåres hukommelsessystem. Her er et sett med interaksjoner fra de siste dagene.",
        "en": "You are Kåre's memory system. Here is a set of interactions from the past few days.",
        "de": "Du bist Kåres Gedächtnissystem. Hier ist eine Reihe von Interaktionen der letzten Tage.",
    },
    "nightjob_ltm_instructions": {
        "nb": (
            "Lag et kort og presist sammendrag (maks 5 setninger) som beskriver:\n"
            "- Hva brukeren spurte om eller ba om\n"
            "- Hvilke enheter eller rom som var involvert\n"
            "- Hva som fungerte, hva som ikke fungerte\n"
            "- Eventuelle mønstre du ser\n"
            "\n"
            "Svar KUN med sammendraget — ingen introduksjon, ingen kommentarer.\n"
            "Skriv på norsk."
        ),
        "en": (
            "Write a short and precise summary (max 5 sentences) describing:\n"
            "- What the user asked about or requested\n"
            "- Which devices or rooms were involved\n"
            "- What worked, what didn't\n"
            "- Any patterns you notice\n"
            "\n"
            "Reply ONLY with the summary — no introduction, no comments.\n"
            "Write in English."
        ),
        "de": (
            "Erstelle eine kurze und präzise Zusammenfassung (max. 5 Sätze), die beschreibt:\n"
            "- Wonach der Benutzer gefragt oder worum er gebeten hat\n"
            "- Welche Geräte oder Räume beteiligt waren\n"
            "- Was funktioniert hat und was nicht\n"
            "- Etwaige Muster, die du erkennst\n"
            "\n"
            "Antworte NUR mit der Zusammenfassung — keine Einleitung, keine Kommentare.\n"
            "Schreibe auf Deutsch."
        ),
    },
    "nightjob_stm_header": {
        "nb": "Du er Kåres hukommelsessystem. Her er alle interaksjoner fra de siste 24 timene.",
        "en": "You are Kåre's memory system. Below are all interactions from the past 24 hours.",
        "de": "Du bist Kåres Gedächtnissystem. Es folgen alle Interaktionen der letzten 24 Stunden.",
    },
    "nightjob_stm_instructions": {
        "nb": (
            "Skriv et kort sammendrag (5–7 setninger) som fanger opp:\n"
            "- Hva brukeren spurte om eller ba om\n"
            "- Hvilke rom eller enheter som var involvert\n"
            "- Hva som fungerte og hva som ikke gjorde det\n"
            "- Eventuelle tilbakevendende temaer eller mønstre\n"
            "- Alt som virker viktig å huske til i morgen\n"
            "\n"
            "Svar KUN med sammendraget — ingen introduksjon, ingen kommentarer.\n"
            "Skriv på norsk."
        ),
        "en": (
            "Write a short summary (5–7 sentences) that captures:\n"
            "- What the user asked about or requested\n"
            "- Which rooms or devices were involved\n"
            "- What worked and what didn't\n"
            "- Any recurring themes or patterns\n"
            "- Anything that seems important to remember for tomorrow\n"
            "\n"
            "Reply ONLY with the summary — no introduction, no comments.\n"
            "Write in English."
        ),
        "de": (
            "Schreibe eine kurze Zusammenfassung (5–7 Sätze), die Folgendes erfasst:\n"
            "- Wonach der Benutzer gefragt oder worum er gebeten hat\n"
            "- Welche Räume oder Geräte beteiligt waren\n"
            "- Was funktioniert hat und was nicht\n"
            "- Etwaige wiederkehrende Themen oder Muster\n"
            "- Alles, was wichtig erscheint, um es für morgen zu merken\n"
            "\n"
            "Antworte NUR mit der Zusammenfassung — keine Einleitung, keine Kommentare.\n"
            "Schreibe auf Deutsch."
        ),
    },
    "nightjob_global_header": {
        "nb": "Du er Kåres hukommelsessystem. Her er et sett med hendelser fra hjemmet — registrert av Jing (Kåres raske indre stemme).",
        "en": "You are Kåre's memory system. Here is a set of events from the home — logged by Jing (Kåre's fast inner voice).",
        "de": "Du bist Kåres Gedächtnissystem. Hier ist eine Reihe von Ereignissen aus dem Zuhause — erfasst von Jing (Kåres schneller innerer Stimme).",
    },
    "nightjob_global_instructions": {
        "nb": (
            "Lag et kort og presist sammendrag (maks 5 setninger) som beskriver:\n"
            "- Hvilke hjemhendelser som skjedde (bevegelse, kamera, lys, osv.)\n"
            "- Om noen var til stede hjemme\n"
            "- Eventuelle mønstre eller uvanlige hendelser\n"
            "\n"
            "Svar KUN med sammendraget — ingen introduksjon, ingen kommentarer.\n"
            "Skriv på norsk."
        ),
        "en": (
            "Write a short and precise summary (max 5 sentences) describing:\n"
            "- Which home events occurred (motion, camera, lights, etc.)\n"
            "- Whether anyone was present at home\n"
            "- Any patterns or unusual events\n"
            "\n"
            "Reply ONLY with the summary — no introduction, no comments.\n"
            "Write in English."
        ),
        "de": (
            "Erstelle eine kurze und präzise Zusammenfassung (max. 5 Sätze), die beschreibt:\n"
            "- Welche Ereignisse zu Hause stattgefunden haben (Bewegung, Kamera, Licht usw.)\n"
            "- Ob jemand zu Hause anwesend war\n"
            "- Etwaige Muster oder ungewöhnliche Ereignisse\n"
            "\n"
            "Antworte NUR mit der Zusammenfassung — keine Einleitung, keine Kommentare.\n"
            "Schreibe auf Deutsch."
        ),
    },
    "nightjob_label_user": {"nb": "Bruker", "en": "User", "de": "Benutzer"},
    "nightjob_label_reply": {"nb": "Svar", "en": "Reply", "de": "Antwort"},
    "nightjob_label_outcome": {"nb": "Utfall", "en": "Outcome", "de": "Ergebnis"},
    "nightjob_sep_interactions": {
        "nb": "--- INTERAKSJONER ---",
        "en": "--- INTERACTIONS ---",
        "de": "--- INTERAKTIONEN ---",
    },
    "nightjob_sep_events": {
        "nb": "--- HENDELSER ---",
        "en": "--- EVENTS ---",
        "de": "--- EREIGNISSE ---",
    },
    "nightjob_encrypted_placeholder": {
        "nb": "[kryptert]",
        "en": "[encrypted]",
        "de": "[verschlüsselt]",
    },
    # ── kaare_reflection.py ──────────────────────────────────────────────────
    "refl_leder_sys_prev": {
        "nb": "**Tidligere refleksjonsmøter — utdrag:**\n{block}",
        "en": "**Previous reflection meetings — excerpts:**\n{block}",
        "de": "**Frühere Reflexionstreffen — Auszüge:**\n{block}",
    },
    "refl_leder_sys_admin_comment": {
        "nb": "**Admin-kommentar til forrige møte** (siter ordrett — ikke parafrase):\n{comment}",
        "en": "**Admin comment on the previous meeting** (quote verbatim — do not paraphrase):\n{comment}",
        "de": "**Admin-Kommentar zum letzten Treffen** (wörtlich zitieren — nicht paraphrasieren):\n{comment}",
    },
    "refl_from_name": {
        "nb": " fra {name}",
        "en": " from {name}",
        "de": " von {name}",
    },
    "refl_leder_sys_user_comment": {
        "nb": "**Brukerkommentar{name_label} til forrige møte:**\n{comment}",
        "en": "**User comment{name_label} on the previous meeting:**\n{comment}",
        "de": "**Nutzerkommentar{name_label} zum letzten Treffen:**\n{comment}",
    },
    "refl_leder_sys_admin_topic": {
        "nb": "**Admin har foreslått tema:** {topic}\nPrioriter dette temaet i møteagendaen.",
        "en": "**Admin has proposed a topic:** {topic}\nPrioritize this topic in the meeting agenda.",
        "de": "**Admin hat ein Thema vorgeschlagen:** {topic}\nPriorisiere dieses Thema in der Meeting-Agenda.",
    },
    "refl_leder_sys_user_topic": {
        "nb": "**Brukeren har foreslått tema:** {topic}\nTa dette med i agendaen — det er noe brukeren selv ønsker å utforske.",
        "en": "**The user has suggested a topic:** {topic}\nInclude this in the agenda — it's something the user themselves wants to explore.",
        "de": "**Der Nutzer hat ein Thema vorgeschlagen:** {topic}\nNimm dies in die Agenda auf — der Nutzer möchte es selbst erkunden.",
    },
    "refl_away_note": {
        "nb": "**Husstanden er på bortreise.**",
        "en": "**The household is away.**",
        "de": "**Der Haushalt ist verreist.**",
    },
    "refl_away_reason": {
        "nb": " Årsak: {reason}.",
        "en": " Reason: {reason}.",
        "de": " Grund: {reason}.",
    },
    "refl_away_return": {
        "nb": " Forventet hjemkomst: {date}.",
        "en": " Expected return: {date}.",
        "de": " Erwartete Rückkehr: {date}.",
    },
    "refl_away_long": {
        "nb": " Borte i {days} dager. Vurder å ta opp sikkerhetsmodus og hvile/ro i møteagendaen.",
        "en": " Away for {days} days. Consider raising security mode and rest/calm in the meeting agenda.",
        "de": " Seit {days} Tagen verreist. Erwäge, Sicherheitsmodus und Ruhe/Erholung in der Meeting-Agenda anzusprechen.",
    },
    "refl_kare_topic_prompt": {
        "nb": "Møtet starter. Vi skal snakke om brukeren i dag – hvem de er, hva de liker, hva de trenger. Foreslå ÉTT konkret tema om brukeren du ønsker å utforske. Vær spesifikk. Maks 2 setninger.",
        "en": "The meeting is starting. Today we're talking about the user – who they are, what they like, what they need. Suggest ONE concrete topic about the user you want to explore. Be specific. Max 2 sentences.",
        "de": "Das Meeting beginnt. Heute sprechen wir über den Nutzer – wer er ist, was er mag, was er braucht. Schlage EIN konkretes Thema über den Nutzer vor, das du erkunden möchtest. Sei konkret. Max. 2 Sätze.",
    },
    "refl_miss_kare_topic_prompt": {
        "nb": "Kåre ønsker å utforske dette i møtet: «{tema}»\n\nForeslå DU ÉTT annet konkret tema om brukeren – noe du synes er viktig å belyse. Det kan gjerne utfylle Kåres tema. Maks 2 setninger.",
        "en": "Kåre wants to explore this in the meeting: «{tema}»\n\nSuggest ONE other concrete topic about the user yourself – something you think is important to shed light on. It can complement Kåre's topic. Max 2 sentences.",
        "de": "Kåre möchte dies im Meeting erkunden: «{tema}»\n\nSchlage DU EIN weiteres konkretes Thema über den Nutzer vor – etwas, das du für wichtig hältst zu beleuchten. Es darf Kåres Thema gerne ergänzen. Max. 2 Sätze.",
    },
    "refl_leder_admin_input_user": {
        "nb": 'Admin har sendt inn dette innspillet til møtet:\n"{admin_input}"\n\nIntroduser dette innspillet for Kåre og Miss Kåre. Koble det til hvem brukeren er hvis relevant. Be Kåre starte med sin refleksjon. Maks 3 setninger.',
        "en": 'Admin has submitted this input to the meeting:\n"{admin_input}"\n\nIntroduce this input to Kåre and Miss Kåre. Connect it to who the user is if relevant. Ask Kåre to start with their reflection. Max 3 sentences.',
        "de": 'Admin hat diesen Beitrag für das Meeting eingereicht:\n"{admin_input}"\n\nStelle diesen Beitrag Kåre und Miss Kåre vor. Verknüpfe ihn wenn relevant mit dem Nutzer. Bitte Kåre, mit seiner Reflexion zu beginnen. Max. 3 Sätze.',
    },
    "refl_admin_input_fallback": {
        "nb": "Admin har sendt inn et tema: «{admin_input}» – la oss se på dette.",
        "en": "Admin has submitted a topic: «{admin_input}» – let's take a look at this.",
        "de": "Admin hat ein Thema eingereicht: «{admin_input}» – schauen wir uns das an.",
    },
    "refl_leder_agenda_user": {
        "nb": "Kåre vil utforske: «{kare_tema}»\nMiss Kåre vil utforske: «{miss_kare_tema}»\n\nBruk gjerne verktøyene for å lese brukerprofil eller hente aktivitetsdata. Ta deretter med ÉTT tema fra Kåre og ÉTT tema fra Miss Kåre inn i agendaen. Sett en kort, konkret agenda for gruppe 1 (2-4 setninger). Vær tydelig på hva vi vil finne ut om brukeren i dag.",
        "en": "Kåre wants to explore: «{kare_tema}»\nMiss Kåre wants to explore: «{miss_kare_tema}»\n\nFeel free to use the tools to read the user profile or fetch activity data. Then bring ONE topic from Kåre and ONE topic from Miss Kåre into the agenda. Set a short, concrete agenda for group 1 (2-4 sentences). Be clear about what we want to find out about the user today.",
        "de": "Kåre möchte erkunden: «{kare_tema}»\nMiss Kåre möchte erkunden: «{miss_kare_tema}»\n\nNutze gerne die Werkzeuge, um das Nutzerprofil zu lesen oder Aktivitätsdaten abzurufen. Nimm dann EIN Thema von Kåre und EIN Thema von Miss Kåre in die Agenda auf. Setze eine kurze, konkrete Agenda für Gruppe 1 (2-4 Sätze). Sei klar darüber, was wir heute über den Nutzer herausfinden wollen.",
    },
    "refl_err_using_suggested_topics": {
        "nb": "bruker foreslåtte temaer direkte",
        "en": "falling back to the participants' suggested topics directly",
        "de": "greift direkt auf die vorgeschlagenen Themen der Teilnehmer zurück",
    },
    "refl_leder_group2_intro": {
        "nb": "Gruppe 2 starter. Oppsummering fra gruppe 1:\n\n{prev_summary}\n\nGi en kort intro (2-4 setninger) med ny fokusert agenda. Gå dypere – ta tak i noe som ikke ble fullt utforsket i gruppe 1.",
        "en": "Group 2 is starting. Summary from group 1:\n\n{prev_summary}\n\nGive a brief intro (2-4 sentences) with a new, focused agenda. Go deeper – pick up something that wasn't fully explored in group 1.",
        "de": "Gruppe 2 beginnt. Zusammenfassung aus Gruppe 1:\n\n{prev_summary}\n\nGib eine kurze Einführung (2-4 Sätze) mit einer neuen, fokussierten Agenda. Geh tiefer – greife etwas auf, das in Gruppe 1 nicht vollständig untersucht wurde.",
    },
    "refl_err_group2_no_intro": {
        "nb": "gruppe 2 starter uten intro",
        "en": "group 2 starts without an intro",
        "de": "Gruppe 2 beginnt ohne Einführung",
    },
    "refl_leder_decision_instructions": {
        "nb": "Avgjør hva som skjer etter denne runden.\nSvar med NØYAKTIG én linje – ingen forklaring:\n  FORTSETT\n  HENT_AKTIVITET\n  NETTSØK: <søketerm>\n  INNSPILL: <maks 2 setninger>",
        "en": "Decide what happens after this round.\nReply with EXACTLY one line – no explanation:\n  FORTSETT\n  HENT_AKTIVITET\n  NETTSØK: <search term>\n  INNSPILL: <max 2 sentences>",
        "de": "Entscheide, was nach dieser Runde passiert.\nAntworte mit GENAU einer Zeile – keine Erklärung:\n  FORTSETT\n  HENT_AKTIVITET\n  NETTSØK: <Suchbegriff>\n  INNSPILL: <max. 2 Sätze>",
    },
    "refl_leder_fallback_system": {
        "nb": "Du er møteleder i et møte om brukeren. {instructions}",
        "en": "You are the meeting leader in a meeting about the user. {instructions}",
        "de": "Du bist Moderator in einem Meeting über den Nutzer. {instructions}",
    },
    "refl_leder_round_check_user": {
        "nb": "Runde {round_num} er ferdig. Siste del av samtalen:\n\n{conv_tail}\n\nHva gjør du?",
        "en": "Round {round_num} is done. Latest part of the conversation:\n\n{conv_tail}\n\nWhat do you do?",
        "de": "Runde {round_num} ist fertig. Letzter Teil des Gesprächs:\n\n{conv_tail}\n\nWas machst du?",
    },
    "refl_leder_vurder_user": {
        "nb": "Gruppe {group} av {max_groups} er ferdig.\n\nSamtalen:\n{conv_tail}\n\nSvar KUN med én linje: 'FORTSETT: <begrunnelse>' eller 'AVSLUTT: <begrunnelse>'.",
        "en": "Group {group} of {max_groups} is done.\n\nThe conversation:\n{conv_tail}\n\nReply ONLY with one line: 'FORTSETT: <reason>' or 'AVSLUTT: <reason>'.",
        "de": "Gruppe {group} von {max_groups} ist fertig.\n\nDas Gespräch:\n{conv_tail}\n\nAntworte NUR mit einer Zeile: 'FORTSETT: <Begründung>' oder 'AVSLUTT: <Begründung>'.",
    },
    "refl_leder_closing_check_user": {
        "nb": "Fri avslutningsrunde {closing_round} er ferdig.\n\nSiste del av samtalen:\n{conv_tail}\n\nGjentar dere bare det som allerede er sagt tidligere i møtet, uten noe nytt? Svar KUN med én linje: 'FORTSETT' eller 'AVSLUTT: <kort begrunnelse>'.",
        "en": "Free closing round {closing_round} is done.\n\nLatest part of the conversation:\n{conv_tail}\n\nAre you just repeating what's already been said earlier in the meeting, with nothing new? Reply ONLY with one line: 'FORTSETT' or 'AVSLUTT: <short reason>'.",
        "de": "Freie Abschlussrunde {closing_round} ist fertig.\n\nLetzter Teil des Gesprächs:\n{conv_tail}\n\nWiederholt ihr nur, was im Meeting bereits gesagt wurde, ohne etwas Neues? Antworte NUR mit einer Zeile: 'FORTSETT' oder 'AVSLUTT: <kurze Begründung>'.",
    },
    "refl_agenda_msg": {
        "nb": "Møteleder setter agenda: {agenda}",
        "en": "Meeting leader sets the agenda: {agenda}",
        "de": "Moderator legt die Agenda fest: {agenda}",
    },
    "refl_admin_react_prompt": {
        "nb": "Din tur – reflekter over admin sitt innspill.",
        "en": "Your turn – reflect on the admin's input.",
        "de": "Du bist dran – reflektiere über den Beitrag des Admins.",
    },
    "refl_kare_said_admin_react_mk": {
        "nb": "Kåre sa:\n{text}\n\nDin tur – hva tenker du om admin sitt innspill?",
        "en": "Kåre said:\n{text}\n\nYour turn – what do you think about the admin's input?",
        "de": "Kåre sagte:\n{text}\n\nDu bist dran – was denkst du über den Beitrag des Admins?",
    },
    "refl_miss_kare_said": {
        "nb": "Miss Kåre sier: {text}",
        "en": "Miss Kåre says: {text}",
        "de": "Miss Kåre sagt: {text}",
    },
    "refl_your_turn": {
        "nb": "Din tur.",
        "en": "Your turn.",
        "de": "Du bist dran.",
    },
    "refl_kare_said_your_turn": {
        "nb": "Kåre sa:\n{text}\n\nDin tur.",
        "en": "Kåre said:\n{text}\n\nYour turn.",
        "de": "Kåre sagte:\n{text}\n\nDu bist dran.",
    },
    "refl_closing_prompt": {
        "nb": "Møtet nærmer seg slutten. Har du noe mer å si om brukeren – noe du ikke fikk sagt, eller en tanke du synes er viktig? Hvis du ikke har noe relevant å tilføye, svar KUN med ordet INGENTING.",
        "en": "The meeting is coming to an end. Do you have anything more to say about the user – something you didn't get to say, or a thought you think is important? If you have nothing relevant to add, reply with ONLY the word INGENTING.",
        "de": "Das Meeting geht dem Ende zu. Hast du noch etwas über den Nutzer zu sagen – etwas, das du noch nicht gesagt hast, oder einen Gedanken, den du für wichtig hältst? Falls du nichts Relevantes hinzuzufügen hast, antworte NUR mit dem Wort INGENTING.",
    },
    "refl_kare_says": {
        "nb": "Kåre sier: {text}",
        "en": "Kåre says: {text}",
        "de": "Kåre sagt: {text}",
    },
    "refl_kare_closing_prompt": {
        "nb": "Møtet er over. Skriv en strukturert avslutning med disse taggene:\nOBSERVASJON: <1-3 setninger om hva dere lærte om brukeren i dag>\nLIKER: <noe spesifikt brukeren liker, eller 'ingenting nytt'>\nBEKYMRING: <noe å følge med på, eller 'ingen'>\nGLEDE: <noe konkret som kan glede brukeren, eller 'ingen'>",
        "en": "The meeting is over. Write a structured closing with these tags:\nOBSERVASJON: <1-3 sentences about what you learned about the user today>\nLIKER: <something specific the user likes, or 'ingenting nytt'>\nBEKYMRING: <something to keep an eye on, or 'ingen'>\nGLEDE: <something concrete that could delight the user, or 'ingen'>",
        "de": "Das Meeting ist vorbei. Schreibe einen strukturierten Abschluss mit diesen Tags:\nOBSERVASJON: <1-3 Sätze darüber, was ihr heute über den Nutzer gelernt habt>\nLIKER: <etwas Konkretes, das der Nutzer mag, oder 'ingenting nytt'>\nBEKYMRING: <etwas, das im Auge zu behalten ist, oder 'ingen'>\nGLEDE: <etwas Konkretes, das den Nutzer erfreuen könnte, oder 'ingen'>",
    },
    "refl_existing_knowledge_block": {
        "nb": "Eksisterende avklart kunnskap:\n{existing}\n\n",
        "en": "Existing settled knowledge:\n{existing}\n\n",
        "de": "Vorhandenes gesichertes Wissen:\n{existing}\n\n",
    },
    "refl_update_knowledge_prompt": {
        "nb": "{existing_block}Her er utdrag fra refleksjonsmøtet om brukeren:\n{transcript}\n\nOppdater user_knowledge.md basert på møtets konklusjoner. Behold eksisterende punkter som fortsatt er gyldige. Fjern det som er utdatert. Skriv KUN på formen:\n- Brukeren liker/foretrekker/reagerer/...\n\nMaks 10 punkter. Kun punkter som representerer konkludert, stabil kunnskap.",
        "en": "{existing_block}Here are excerpts from the reflection meeting about the user:\n{transcript}\n\nUpdate user_knowledge.md based on the meeting's conclusions. Keep existing points that are still valid. Remove what's outdated. Write ONLY in the form:\n- The user likes/prefers/reacts/...\n\nMax 10 points. Only points that represent concluded, stable knowledge.",
        "de": "{existing_block}Hier sind Auszüge aus dem Reflexionstreffen über den Nutzer:\n{transcript}\n\nAktualisiere user_knowledge.md basierend auf den Schlussfolgerungen des Treffens. Behalte bestehende Punkte, die noch gültig sind. Entferne Veraltetes. Schreibe NUR in der Form:\n- Der Nutzer mag/bevorzugt/reagiert/...\n\nMax. 10 Punkte. Nur Punkte, die abgeschlossenes, stabiles Wissen darstellen.",
    },
    "refl_knowledge_file_header": {
        "nb": "# Konkludert kunnskap om brukeren\n_Sist oppdatert: {date}_\n\n",
        "en": "# Concluded knowledge about the user\n_Last updated: {date}_\n\n",
        "de": "# Abgeschlossenes Wissen über den Nutzer\n_Zuletzt aktualisiert: {date}_\n\n",
    },
    "refl_admin_topic_line": {
        "nb": "\nADMIN HAR FORESLÅTT TEMA: {topic}\nDette skal prioriteres i møtet.\n",
        "en": "\nADMIN HAS SUGGESTED A TOPIC: {topic}\nThis should be prioritized in the meeting.\n",
        "de": "\nADMIN HAT EIN THEMA VORGESCHLAGEN: {topic}\nDies sollte im Meeting priorisiert werden.\n",
    },
    "refl_meeting_info": {
        "nb": "Dette møtet har maks {max_rounds} lokale runder ({max_groups} grupper à {rounds_per_group}). Møteleder introduserer hver gruppe og beslutter om vi fortsetter.{topic_line}",
        "en": "This meeting has a maximum of {max_rounds} local rounds ({max_groups} groups of {rounds_per_group}). The meeting leader introduces each group and decides whether to continue.{topic_line}",
        "de": "Dieses Meeting hat maximal {max_rounds} lokale Runden ({max_groups} Gruppen à {rounds_per_group}). Der Moderator stellt jede Gruppe vor und entscheidet, ob wir fortfahren.{topic_line}",
    },
    "refl_kare_system_header": {
        "nb": "--- REFLEKSJONSMØTE OM BRUKEREN ---",
        "en": "--- REFLECTION MEETING ABOUT THE USER ---",
        "de": "--- REFLEXIONSTREFFEN ÜBER DEN NUTZER ---",
    },
    "refl_kare_system_body": {
        "nb": "Du er i et internt møte med Miss Kåre. Møtet handler om brukeren du betjener. Diskuter hvem brukeren er, hva de liker, hva de trenger, hva som kan bekymre og glede dem. Vær konkret og ærlig – bruk det du faktisk har observert.",
        "en": "You are in an internal meeting with Miss Kåre. The meeting is about the user you serve. Discuss who the user is, what they like, what they need, what might worry and delight them. Be concrete and honest – use what you have actually observed.",
        "de": "Du bist in einem internen Meeting mit Miss Kåre. Das Meeting handelt vom Nutzer, dem du dienst. Diskutiert, wer der Nutzer ist, was er mag, was er braucht, was ihn beunruhigen und erfreuen könnte. Sei konkret und ehrlich – nutze das, was du tatsächlich beobachtet hast.",
    },
    "refl_user_profile_header": {
        "nb": "Brukerprofil:\n{profile}",
        "en": "User profile:\n{profile}",
        "de": "Nutzerprofil:\n{profile}",
    },
    "refl_recent_observations_header": {
        "nb": "Siste observasjoner (14 dager):\n{obs}",
        "en": "Recent observations (14 days):\n{obs}",
        "de": "Aktuelle Beobachtungen (14 Tage):\n{obs}",
    },
    "refl_recent_observations_header_short": {
        "nb": "Siste observasjoner:\n{obs}",
        "en": "Recent observations:\n{obs}",
        "de": "Aktuelle Beobachtungen:\n{obs}",
    },
    "refl_recent_interactions_header": {
        "nb": "Siste interaksjoner (komprimerte episoder):\n{interactions}",
        "en": "Recent interactions (compressed episodes):\n{interactions}",
        "de": "Aktuelle Interaktionen (komprimierte Episoden):\n{interactions}",
    },
    "refl_stm_section": {
        "nb": "\n\nDaglige STM-sammendrag (siste 3 dager):\n{stm}",
        "en": "\n\nDaily STM summaries (last 3 days):\n{stm}",
        "de": "\n\nTägliche STM-Zusammenfassungen (letzte 3 Tage):\n{stm}",
    },
    "refl_knowledge_section": {
        "nb": "\n\nAvklart kunnskap (IKKE utled på nytt i dette møtet — fokuser på nytt):\n{knowledge}",
        "en": "\n\nSettled knowledge (do NOT re-derive in this meeting — focus on new):\n{knowledge}",
        "de": "\n\nGesichertes Wissen (in diesem Meeting NICHT neu herleiten — auf Neues fokussieren):\n{knowledge}",
    },
    "refl_mk_desc": {
        "nb": "varm, konkret observatør",
        "en": "warm, concrete observer",
        "de": "warmherzige, konkrete Beobachterin",
    },
    "refl_miss_kare_system_body": {
        "nb": "Du er med i et internt refleksjonsmøte om brukeren. Din oppgave er å logge konkrete observasjoner: hva brukeren faktisk sa eller gjorde, og når. Vis til det du så (en samtale, en hendelse, en profilopplysning). Ingen spekulasjon om motiver, følelser eller indre liv som ikke kan etterprøves — har du ikke sett det, sier du det ikke.\n\nRegler:\n- Maks 4 setninger per innlegg\n- Hver observasjon peker på noe konkret du har sett\n- Er du usikker, si at du er usikker — ikke fyll ut\n- Ikke gjenta dine egne tidligere observasjoner som om de var nye funn\n- Naturlig, varm tale – ingen lister eller agendanummer",
        "en": "You are part of an internal reflection meeting about the user. Your job is to log concrete observations: what the user actually said or did, and when. Point to what you saw (a conversation, an event, a profile detail). No speculation about motives, feelings or inner life that cannot be checked — if you haven't seen it, you don't say it.\n\nRules:\n- Max 4 sentences per message\n- Every observation points to something concrete you have seen\n- If you are unsure, say so — don't fill in\n- Don't repeat your own earlier observations as if they were new findings\n- Natural, warm speech – no lists or agenda numbers",
        "de": "Du nimmst an einem internen Reflexionstreffen über den Nutzer teil. Deine Aufgabe ist es, konkrete Beobachtungen festzuhalten: was der Nutzer tatsächlich gesagt oder getan hat, und wann. Verweise auf das, was du gesehen hast (ein Gespräch, ein Ereignis, eine Profilangabe). Keine Spekulation über Motive, Gefühle oder Innenleben, die sich nicht überprüfen lässt — was du nicht gesehen hast, sagst du nicht.\n\nRegeln:\n- Max. 4 Sätze pro Beitrag\n- Jede Beobachtung verweist auf etwas Konkretes, das du gesehen hast\n- Wenn du unsicher bist, sag es — füll nichts auf\n- Wiederhole deine eigenen früheren Beobachtungen nicht, als wären sie neue Erkenntnisse\n- Natürliche, warme Sprache – keine Listen oder Agendanummern",
    },
    "refl_own_observations_header": {
        "nb": "\n\n## Dine egne observasjoner om brukeren\n{portrait}",
        "en": "\n\n## Your own observations about the user\n{portrait}",
        "de": "\n\n## Deine eigenen Beobachtungen über den Nutzer\n{portrait}",
    },
    "refl_report_title": {
        "nb": "# Refleksjon – {date}",
        "en": "# Reflection – {date}",
        "de": "# Reflexion – {date}",
    },
    "refl_user_comment_on_meeting": {
        "nb": "Brukerens kommentar til møtet {date}:\n{text}",
        "en": "The user's comment on the {date} meeting:\n{text}",
        "de": "Der Kommentar des Nutzers zum Treffen vom {date}:\n{text}",
    },
    "refl_leder_checking_activity": {
        "nb": "La oss se på brukerens siste aktivitet.",
        "en": "Let's look at the user's recent activity.",
        "de": "Schauen wir uns die letzte Aktivität des Nutzers an.",
    },
    "refl_leder_activity_fetched": {
        "nb": "Møteleder hentet brukeraktivitet:\n{result}",
        "en": "Meeting leader fetched user activity:\n{result}",
        "de": "Moderator hat Nutzeraktivität abgerufen:\n{result}",
    },
    "refl_activity_report_line": {
        "nb": "(brukeraktivitet hentet: {episodes} episoder, {chars} tegn — gitt til Kåre og Miss Kåre, ikke lagret i rapporten)",
        "en": "(user activity fetched: {episodes} episodes, {chars} characters — given to Kåre and Miss Kåre, not stored in the report)",
        "de": "(Nutzeraktivität abgerufen: {episodes} Episoden, {chars} Zeichen — an Kåre und Miss Kåre gegeben, nicht im Bericht gespeichert)",
    },
    "refl_leder_activity_already": {
        "nb": "Brukeraktiviteten er allerede hentet i dette møtet — den står lenger opp i samtalen. Fortsett med det dere har.",
        "en": "The user activity has already been fetched in this meeting — it is further up in the conversation. Continue with what you have.",
        "de": "Die Nutzeraktivität wurde in diesem Treffen bereits abgerufen — sie steht weiter oben im Gespräch. Macht mit dem weiter, was ihr habt.",
    },
    "refl_act_interactions_header": {
        "nb": "Siste samtaler (komprimerte episoder):",
        "en": "Recent interactions (compressed episodes):",
        "de": "Letzte Gespräche (komprimierte Episoden):",
    },
    "refl_act_observations_header": {
        "nb": "Siste observasjoner (7 dager):",
        "en": "Recent observations (7 days):",
        "de": "Letzte Beobachtungen (7 Tage):",
    },
    "refl_act_stm_header": {
        "nb": "Daglige STM-sammendrag (siste 3 dager):",
        "en": "Daily STM summaries (last 3 days):",
        "de": "Tägliche STM-Zusammenfassungen (letzte 3 Tage):",
    },
    "refl_act_interaction": {
        "nb": "Samtale {n} (tema: {topics}):",
        "en": "Interaction {n} (topics: {topics}):",
        "de": "Gespräch {n} (Themen: {topics}):",
    },
    "refl_act_topics_unknown": {
        "nb": "ukjent",
        "en": "unknown",
        "de": "unbekannt",
    },
    "refl_act_no_interactions": {
        "nb": "Ingen registrerte samtaler.",
        "en": "No recorded interactions.",
        "de": "Keine aufgezeichneten Gespräche.",
    },
    "refl_act_interactions_unavailable": {
        "nb": "Samtaler utilgjengelig.",
        "en": "Interactions unavailable.",
        "de": "Gespräche nicht verfügbar.",
    },
    "refl_act_stm_day": {
        "nb": "[{date}] ({count} samtaler):",
        "en": "[{date}] ({count} interactions):",
        "de": "[{date}] ({count} Gespräche):",
    },
    "refl_leder_searching": {
        "nb": "La meg søke etter: {value}",
        "en": "Let me search for: {value}",
        "de": "Lass mich suchen nach: {value}",
    },
    "refl_leder_search_result": {
        "nb": "Møteleder søkte etter «{value}». Resultat:\n{result}",
        "en": "Meeting leader searched for «{value}». Result:\n{result}",
        "de": "Moderator suchte nach «{value}». Ergebnis:\n{result}",
    },
    "refl_leder_colon": {
        "nb": "Møteleder: {value}",
        "en": "Meeting leader: {value}",
        "de": "Moderator: {value}",
    },
    "refl_err_continuing_to_group": {
        "nb": "fortsetter til gruppe {group}",
        "en": "continuing to group {group}",
        "de": "fährt fort mit Gruppe {group}",
    },
    # ── router_generate.py remaining strings (Phase 7 sweep) ──────────────────
    "gen_cloud_no_answer": {
        "nb": "Cloud-modellen svarte ikke. Prøv igjen.",
        "en": "The cloud model did not respond. Please try again.",
        "de": "Das Cloud-Modell hat nicht geantwortet. Bitte versuche es erneut.",
    },
    "gen_ver_note": {
        "nb": (
            "\n\n[SYSTEM: Du har {count} interaksjoner uten brukerbekreftelse. "
            "Her er de tidligste:{preview}\n"
            "Presenter 2–3 av dem naturlig i samtalen og spør om de stemte — bruk din egen stemme, ingen fast mal. "
            "Bruk minne(action='hent_ubekreftede') for å hente neste batch.]\n"
        ),
        "en": (
            "\n\n[SYSTEM: You have {count} interactions without user confirmation. "
            "Here are the earliest ones:{preview}\n"
            "Present 2–3 of them naturally in the conversation and ask if they were correct — use your own "
            "voice, no fixed template. Use minne(action='hent_ubekreftede') to fetch the next batch.]\n"
        ),
        "de": (
            "\n\n[SYSTEM: Du hast {count} Interaktionen ohne Nutzerbestätigung. "
            "Hier sind die frühesten:{preview}\n"
            "Präsentiere 2–3 davon natürlich im Gespräch und frage, ob sie gestimmt haben — nutze deine eigene "
            "Stimme, keine feste Vorlage. Verwende minne(action='hent_ubekreftede'), um den nächsten Stapel zu holen.]\n"
        ),
    },
    # P105 Phase 6 — replayed STM turns carry their time; a long gap is stated explicitly.
    "stm_replay_stamp": {
        "nb": "[{date} kl. {time}]",
        "en": "[{date} at {time}]",
        "de": "[{date} um {time}]",
    },
    "gen_gap_days": {
        "nb": "for {n} dager siden",
        "en": "{n} days ago",
        "de": "vor {n} Tagen",
    },
    "gen_gap_hours": {
        "nb": "for {n} timer siden",
        "en": "{n} hours ago",
        "de": "vor {n} Stunden",
    },
    "gen_gap_note": {
        "nb": (
            "Forrige melding fra brukeren kom {when} ({ago}). Samtalen over er fra da, ikke fra nå. "
            "Hvis du uttaler deg om hvordan ting er nå, sjekk ferske data først."
        ),
        "en": (
            "The user's previous message came {when} ({ago}). The conversation above is from then, "
            "not from now. If you say anything about how things are now, check fresh data first."
        ),
        "de": (
            "Die vorherige Nachricht des Nutzers kam {when} ({ago}). Das Gespräch oben stammt von damals, "
            "nicht von jetzt. Wenn du dich dazu äußerst, wie es jetzt ist, prüfe zuerst aktuelle Daten."
        ),
    },
    # P105 Phase 7 — context budget: replay window + tool-result stubs in the tool loop.
    "gen_older_turns_omitted": {
        "nb": "{n} eldre utvekslinger fra i dag er ikke med her.",
        "en": "{n} earlier exchanges from today are not included here.",
        "de": "{n} frühere Wortwechsel von heute sind hier nicht enthalten.",
    },
    "tool_result_stub": {
        "nb": (
            "[Resultatet av {tool}({args}) fra runde {round} (ca. {tokens} tokens) er tatt ut av "
            "samtalen for å spare plass. Kall verktøyet på nytt med samme argumenter hvis du trenger detaljene.]"
        ),
        "en": (
            "[The result of {tool}({args}) from round {round} (about {tokens} tokens) has been removed "
            "from the conversation to save space. Call the tool again with the same arguments if you need the details.]"
        ),
        "de": (
            "[Das Ergebnis von {tool}({args}) aus Runde {round} (ca. {tokens} Tokens) wurde aus dem "
            "Gespräch entfernt, um Platz zu sparen. Rufe das Werkzeug mit denselben Argumenten erneut auf, "
            "wenn du die Details brauchst.]"
        ),
    },
    "tool_image_stub": {
        "nb": "[Bildene fra runde {round} er tatt ut av samtalen for å spare plass. Du har allerede sett dem — hent dem på nytt hvis du trenger dem.]",
        "en": "[The images from round {round} have been removed from the conversation to save space. You have already seen them — fetch them again if you need them.]",
        "de": "[Die Bilder aus Runde {round} wurden aus dem Gespräch entfernt, um Platz zu sparen. Du hast sie bereits gesehen — hole sie erneut, falls du sie brauchst.]",
    },
    "tool_vision_loaded": {
        "nb": "Bildet er lastet og sendt til deg som visjon-input.",
        "en": "The image has been loaded and sent to you as vision input.",
        "de": "Das Bild wurde geladen und dir als Bildeingabe gesendet.",
    },
    "gen_mk_note": {
        "nb": (
            "\n\n[SYSTEM: Brukeren adresserer Miss Kåre direkte i dette innlegget. "
            "Miss Kåre leser meldingen selv og svarer i sitt eget panel — du trenger ikke videreformidle, "
            "introdusere henne, eller 'gi henne ordet'. "
            "Si gjerne noe kort fra din egen synsvinkel hvis du genuint har noe å tilføye, "
            "ellers hold deg i bakgrunnen.]\n"
        ),
        "en": (
            "\n\n[SYSTEM: The user is addressing Miss Kåre directly in this message. "
            "Miss Kåre reads the message herself and replies in her own panel — you don't need to relay it, "
            "introduce her, or 'hand her the floor'. "
            "Feel free to add something brief from your own perspective if you genuinely have something to "
            "add, otherwise stay in the background.]\n"
        ),
        "de": (
            "\n\n[SYSTEM: Der Nutzer wendet sich in dieser Nachricht direkt an Miss Kåre. "
            "Miss Kåre liest die Nachricht selbst und antwortet in ihrem eigenen Panel — du musst sie nicht "
            "weiterleiten, sie vorstellen oder ihr 'das Wort erteilen'. "
            "Füge gerne etwas Kurzes aus deiner eigenen Sicht hinzu, falls du wirklich etwas beizutragen hast, "
            "ansonsten halte dich im Hintergrund.]\n"
        ),
    },
    "gen_network_vpn": {
        "nb": "[Tilkobling: ekstern via VPN — brukeren er ikke hjemme akkurat nå.]",
        "en": "[Connection: external via VPN — the user is not home right now.]",
        "de": "[Verbindung: extern über VPN — der Nutzer ist gerade nicht zu Hause.]",
    },
    "gen_network_local": {
        "nb": "[Tilkobling: lokal — brukeren er hjemme.]",
        "en": "[Connection: local — the user is home.]",
        "de": "[Verbindung: lokal — der Nutzer ist zu Hause.]",
    },
    "gen_fallback_activated": {
        "nb": "[RESERVEMODUS aktivert kl. {ts}: kjernemodellen svarte ikke. Svarer via 9B-backup-modell.]",
        "en": "[FALLBACK MODE activated at {ts}: the core model did not respond. Replying via the 9B backup model.]",
        "de": "[RESERVEMODUS aktiviert um {ts}: Das Kernmodell hat nicht geantwortet. Antwort über das 9B-Backup-Modell.]",
    },
    "gen_fallback_recovered": {
        "nb": "[GJENOPPRETTET kl. {ts}: kjernemodellen er tilbake. Jeg svarte {n} spørsmål i reservemodus (fra {t0}).]",
        "en": "[RECOVERED at {ts}: the core model is back. I answered {n} question(s) in fallback mode (since {t0}).]",
        "de": "[WIEDERHERGESTELLT um {ts}: Das Kernmodell ist wieder da. Ich habe {n} Frage(n) im Reservemodus beantwortet (seit {t0}).]",
    },
    "gen_tool_exec_error": {
        "nb": "Feil ved utføring av verktøy {name}: {error}",
        "en": "Error executing tool {name}: {error}",
        "de": "Fehler bei der Ausführung des Werkzeugs {name}: {error}",
    },
    "gen_tool_not_offered": {
        "nb": "[Verktøyet {name} er ikke tilgjengelig for denne brukeren og ble ikke kjørt.]",
        "en": "[The tool {name} is not available for this user and was not run.]",
        "de": "[Das Werkzeug {name} ist für diesen Nutzer nicht verfügbar und wurde nicht ausgeführt.]",
    },
    "gen_tool_action_not_offered": {
        "nb": "[Handlingen {action} i {name} er ikke tilgjengelig for denne brukeren og ble ikke kjørt. Tilgjengelige: {valid}]",
        "en": "[The action {action} of {name} is not available for this user and was not run. Available: {valid}]",
        "de": "[Die Aktion {action} von {name} ist für diesen Nutzer nicht verfügbar und wurde nicht ausgeführt. Verfügbar: {valid}]",
    },
    "gen_tool_summary": {
        "nb": "Mine verktøykall forrige svar: {calls}",
        "en": "My tool calls in the previous reply: {calls}",
        "de": "Meine Werkzeugaufrufe in der vorherigen Antwort: {calls}",
    },
    # ── executor.py small sweep gap ───────────────────────────────────────────
    "exec_image_url_ready": {
        "nb": "Bildet er klart. Inkluder denne URL-en ordrett i svaret ditt: /api/image/{image_id}",
        "en": "The image is ready. Include this URL verbatim in your reply: /api/image/{image_id}",
        "de": "Das Bild ist fertig. Füge diese URL wörtlich in deine Antwort ein: /api/image/{image_id}",
    },
    # ── executor_memory.py small sweep gap ────────────────────────────────────
    "mem_search_error": {
        "nb": "Søk i minne feilet: {error}",
        "en": "Memory search failed: {error}",
        "de": "Gedächtnissuche fehlgeschlagen: {error}",
    },
    "mem_search_row": {
        "nb": "- [ID {id} | {ts}]{entity}\n  Bruker: {prompt}\n  Kåre: {response}\n  Utfall: {outcome} | Tillit: {confidence}",
        "en": "- [ID {id} | {ts}]{entity}\n  User: {prompt}\n  Kåre: {response}\n  Outcome: {outcome} | Confidence: {confidence}",
        "de": "- [ID {id} | {ts}]{entity}\n  Nutzer: {prompt}\n  Kåre: {response}\n  Ergebnis: {outcome} | Vertrauen: {confidence}",
    },
    # ── kaare_argus.py::make_message() small sweep gap ────────────────────────
    "argus_tool_config_mismatch": {
        "nb": "KRITISK: verktøy-konfigurasjon i utakt — {preview}",
        "en": "CRITICAL: tool configuration out of sync — {preview}",
        "de": "KRITISCH: Werkzeugkonfiguration nicht synchron — {preview}",
    },
    "argus_status_ok": {"nb": "OK", "en": "OK", "de": "OK"},
    "argus_status_fail": {"nb": "FEIL", "en": "FAILED", "de": "FEHLER"},
    # ── timer_service.py small sweep gap ───────────────────────────────────────
    "timer_stm_fired": {
        "nb": "[Timer avfyrt {time} | rid: {rid}] Kåre leverte: «{message}»",
        "en": "[Timer fired {time} | rid: {rid}] Kåre delivered: «{message}»",
        "de": "[Timer ausgelöst {time} | rid: {rid}] Kåre lieferte: «{message}»",
    },
    # ── mqtt_adapter.py::_on_frigate_event() small sweep gap ──────────────────
    "mqtt_frigate_detection": {
        "nb": "{label} på {camera} ({pct}%){sub_label_suffix}",
        "en": "{label} on {camera} ({pct}%){sub_label_suffix}",
        "de": "{label} auf {camera} ({pct}%){sub_label_suffix}",
    },
    "mqtt_frigate_sub_label_suffix": {
        "nb": " — {sub_label}",
        "en": " — {sub_label}",
        "de": " — {sub_label}",
    },
    # ── executor_library.py: _ask_library_wiki / synthesize_web_results ──────
    # (previously dead `lang` params — Research flagged these as a silent bug)
    # Wiki registry labels (kaare_core/tools/wiki_registry.py, P111 Phase 3)
    "wiki_content_structured": {
        "nb": "prosa + tabeller + lister",
        "en": "prose + tables + lists",
        "de": "Fließtext + Tabellen + Listen",
    },
    "wiki_content_prose_only": {
        "nb": "kun prosa, ingen tabeller eller lister",
        "en": "prose only, no tables or lists",
        "de": "nur Fließtext, keine Tabellen oder Listen",
    },
    "wiki_primary": {
        "nb": "primær",
        "en": "primary",
        "de": "primär",
    },
    "wiki_none_configured": {
        "nb": "ingen wiki er satt opp",
        "en": "no wiki is configured",
        "de": "kein Wiki eingerichtet",
    },
    "wiki_available_line": {
        "nb": "Tilgjengelige wikier: {wikis}.",
        "en": "Available wikis: {wikis}.",
        "de": "Verfügbare Wikis: {wikis}.",
    },
    "wiki_unknown": {
        "nb": "Ukjent wiki «{wiki}». Tilgjengelige: {wikis}.",
        "en": 'Unknown wiki "{wiki}". Available: {wikis}.',
        "de": "Unbekanntes Wiki „{wiki}“. Verfügbar: {wikis}.",
    },
    # Structured wiki data in results (P111 Phase 5)
    "wiki_tables_heading": {
        "nb": "Tabeller og lister fra «{article}»:",
        "en": 'Tables and lists from "{article}":',
        "de": "Tabellen und Listen aus „{article}“:",
    },
    "wiki_table_header": {
        "nb": "Tabell/liste «{label}»{section} i «{article}» {wiki}, {n} rader:",
        "en": 'Table/list "{label}"{section} in "{article}" {wiki}, {n} rows:',
        "de": "Tabelle/Liste „{label}“{section} in „{article}“ {wiki}, {n} Zeilen:",
    },
    "wiki_table_listed": {
        "nb": "Tabell/liste «{label}» ({n} rader) er ikke tatt med — kan hentes med fetch_table (title='{article}', label='{label}').",
        "en": "Table/list \"{label}\" ({n} rows) is not included — can be fetched with fetch_table (title='{article}', label='{label}').",
        "de": "Tabelle/Liste „{label}“ ({n} Zeilen) ist nicht enthalten — abrufbar mit fetch_table (title='{article}', label='{label}').",
    },
    "wiki_table_truncated": {
        "nb": "… {more} rader til er ikke vist (grense {max} tegn).",
        "en": "… {more} more rows not shown (limit {max} characters).",
        "de": "… {more} weitere Zeilen nicht angezeigt (Grenze {max} Zeichen).",
    },
    "wiki_no_structured": {
        "nb": "Wikien «{wiki}» har ingen tabeller eller lister, bare prosa.",
        "en": 'The wiki "{wiki}" has no tables or lists, only prose.',
        "de": "Das Wiki „{wiki}“ hat keine Tabellen oder Listen, nur Fließtext.",
    },
    "wiki_table_not_found": {
        "nb": "Fant ingen tabell/liste «{label}» i «{article}». Tilgjengelige: {labels}.",
        "en": 'No table/list "{label}" in "{article}". Available: {labels}.',
        "de": "Keine Tabelle/Liste „{label}“ in „{article}“. Verfügbar: {labels}.",
    },
    "wiki_tables_available": {
        "nb": "Tabeller/lister i «{article}» {wiki}: {labels}",
        "en": 'Tables/lists in "{article}" {wiki}: {labels}',
        "de": "Tabellen/Listen in „{article}“ {wiki}: {labels}",
    },
    "wiki_article_has_no_tables": {
        "nb": "Artikkelen «{article}» {wiki} har ingen tabeller eller lister.",
        "en": 'The article "{article}" {wiki} has no tables or lists.',
        "de": "Der Artikel „{article}“ {wiki} hat keine Tabellen oder Listen.",
    },
    "lib_translate_table_system": {
        "nb": "Oversett tabellen/listen under til norsk. Behold én linje per rad, i samme rekkefølge, og behold tabellnavnet først på hver linje. Oversett bare ord; alle tall, navn, datoer og verdier gjengis nøyaktig. Ikke utelat, slå sammen eller legg til rader. Ingen innledning, ingen kommentar.",
        "en": "Translate the table/list below into English. Keep one line per row, in the same order, and keep the table name first on every line. Translate words only; reproduce all numbers, names, dates and values exactly. Do not drop, merge or add rows. No introduction, no comment.",
        "de": "Übersetze die Tabelle/Liste unten ins Deutsche. Behalte eine Zeile pro Zeile der Tabelle, in derselben Reihenfolge, und den Tabellennamen am Anfang jeder Zeile. Übersetze nur Wörter; alle Zahlen, Namen, Daten und Werte exakt wiedergeben. Keine Zeilen weglassen, zusammenfassen oder hinzufügen. Keine Einleitung, kein Kommentar.",
    },
    # Neutral task prompt — Miss Library is a lookup/filter tool for Kåre, no persona (P111 Phase 2)
    "lib_task_system": {
        # Only the fixed frame. WHAT to extract, how long and in which form is Kåre's task,
        # written per call in `query` (soft delegation, same pattern as Mechanic — P111).
        # Disagreeing sources are always reported: truth first (Stian 2026-09-28).
        "nb": (
            "Du er Kåres oppslagsverktøy. Kåre gir deg en oppgave og kilder (wiki-utdrag eller "
            "nettsider). Løs oppgaven slik Kåre har beskrevet den.\n\n"
            "Faste regler:\n"
            "- Bare fra kildene du har fått — aldri egen kunnskap, aldri gjetning. Står svaret ikke der, si det.\n"
            "- Tall, navn, datoer og verdier gjengis ordrett fra kilden.\n"
            "- Oppgi kilden (artikkel og wiki, eller URL) for det du rapporterer.\n"
            "- Er kildene uenige, si det alltid og oppgi hvilken kilde som sier hva — også når oppgaven "
            "ber om et kort svar. Sannhet går foran alt.\n"
            "- Svar på norsk."
        ),
        "en": (
            "You are Kåre's lookup tool. Kåre gives you a task and sources (wiki excerpts or web "
            "pages). Solve the task the way Kåre described it.\n\n"
            "Fixed rules:\n"
            "- Only from the sources you were given — never your own knowledge, never guesses. If the answer is not there, say so.\n"
            "- Reproduce numbers, names, dates and values exactly as in the source.\n"
            "- Give the source (article and wiki, or URL) for what you report.\n"
            "- If sources disagree, always say so and state which source says what — even when the "
            "task asks for a short answer. Truth comes first.\n"
            "- Answer in English."
        ),
        "de": (
            "Du bist Kåres Nachschlagewerkzeug. Kåre gibt dir eine Aufgabe und Quellen "
            "(Wiki-Auszüge oder Webseiten). Löse die Aufgabe so, wie Kåre sie beschrieben hat.\n\n"
            "Feste Regeln:\n"
            "- Nur aus den Quellen, die du bekommen hast — nie eigenes Wissen, nie Vermutungen. Steht die Antwort nicht darin, sag es.\n"
            "- Zahlen, Namen, Daten und Werte wörtlich aus der Quelle wiedergeben.\n"
            "- Nenne die Quelle (Artikel und Wiki, oder URL) für das, was du berichtest.\n"
            "- Widersprechen sich Quellen, sag es immer und nenne, welche Quelle was sagt — auch wenn "
            "die Aufgabe eine kurze Antwort verlangt. Wahrheit geht vor.\n"
            "- Antworte auf Deutsch."
        ),
    },
    # Quote-first answer contract + correction + rendering (P111 Phase 5c)
    "lib_grounded_contract": {
        "nb": "Svarformat: 'answer' = kort svar på oppgaven, skrevet som saken selv ('Ifølge § 3-1 …') — aldri hvem som sier det, og aldri om assistenten. 'claims' = hver påstand du bygger på, med et ORDRETT sitat fra kilden i 'quote' (kopier teksten nøyaktig, ingen omskriving — bare de ordene som støtter påstanden) og kildens merkelapp i 'source', og språkkoden sitatet er skrevet på i 'quote_lang' (f.eks. 'pl'). Oversett aldri sitatet. 'topic' = en kort merkelapp for saken påstanden gjelder (f.eks. 'antall trinn'); påstander om samme sak får NØYAKTIG samme topic. Er to påstander uenige, sett 'conflicts_with' til nummeret (0-basert) på den andre. Uenige kilder = egne påstander med hvert sitat. 'not_found' = true hvis kildene ikke inneholder svaret. Skriv 'answer' og 'claim' på norsk (sitatene står på kildens språk), og sett 'lang' til språkkoden du skrev dem på.",
        "en": "Answer format: 'answer' = short answer to the task, written as the matter itself ('According to § 3-1 …') — never who says it, and never about the assistant. 'claims' = every claim you rely on, with a VERBATIM quote from the source in 'quote' (copy the text exactly, no rewording — only the words that back the claim) and the source label in 'source', and the language code the quote is written in in 'quote_lang' (e.g. 'pl'). Never translate the quote. 'topic' = a short label for what the claim is about (e.g. 'number of steps'); claims about the same thing get EXACTLY the same topic. If two claims disagree, set 'conflicts_with' to the other one's number (0-based). Disagreeing sources = separate claims, each with its quote. 'not_found' = true if the sources do not contain the answer. Write 'answer' and 'claim' in English (quotes stay in the source's language), and set 'lang' to the language code you wrote them in.",
        "de": "Antwortformat: 'answer' = kurze Antwort auf die Aufgabe, als Sache selbst formuliert ('Laut § 3-1 …') — nie, wer es sagt, und nie über den Assistenten. 'claims' = jede Aussage, auf die du dich stützt, mit einem WÖRTLICHEN Zitat aus der Quelle in 'quote' (Text exakt kopieren, nicht umformulieren — nur die Wörter, die die Aussage stützen) und der Quellenbezeichnung in 'source' sowie dem Sprachcode des Zitats in 'quote_lang' (z.B. 'pl'). Übersetze das Zitat nie. 'topic' = ein kurzes Etikett für den Gegenstand der Aussage (z.B. 'Anzahl Stufen'); Aussagen zum selben Gegenstand bekommen GENAU dasselbe topic. Widersprechen sich zwei Aussagen, setze 'conflicts_with' auf die Nummer (0-basiert) der anderen. Widersprüchliche Quellen = eigene Aussagen mit jeweils eigenem Zitat. 'not_found' = true, wenn die Quellen die Antwort nicht enthalten. Schreibe 'answer' und 'claim' auf Deutsch (Zitate bleiben in der Sprache der Quelle) und setze 'lang' auf den Sprachcode, in dem du sie geschrieben hast.",
    },
    "lib_correction_language": {
        "nb": "'answer' og 'claim' var på feil språk. Skriv dem på norsk (sitatene beholder kildens språk), sett 'lang' til nb, og svar i samme format.",
        "en": "'answer' and 'claim' were in the wrong language. Write them in English (quotes keep the source's language), set 'lang' to en, and answer in the same format.",
        "de": "'answer' und 'claim' waren in der falschen Sprache. Schreibe sie auf Deutsch (Zitate behalten die Sprache der Quelle), setze 'lang' auf de und antworte im selben Format.",
    },
    "lib_correction_ungrounded": {
        "nb": "Disse sitatene står ikke ordrett i kildene:\n{quotes}\nRett hvert sitat til nøyaktig tekst fra kilden, eller fjern påstanden. Svar i samme format.",
        "en": "These quotes do not occur verbatim in the sources:\n{quotes}\nCorrect each quote to the exact source text, or remove the claim. Answer in the same format.",
        "de": "Diese Zitate stehen nicht wörtlich in den Quellen:\n{quotes}\nKorrigiere jedes Zitat auf den exakten Quelltext oder entferne die Aussage. Antworte im selben Format.",
    },
    "lib_correction_irrelevant": {
        "nb": "Ingen av sitatene dine handler om oppgaven. Disse setningene i kildene ser ut til å gjøre det:\n{sentences}\nVelg sitater (ordrett) som svarer på oppgaven. Finnes ikke svaret, sett not_found = true.",
        "en": "None of your quotes is about the task. These sentences in the sources seem to be:\n{sentences}\nChoose quotes (verbatim) that answer the task. If the answer isn't there, set not_found = true.",
        "de": "Keines deiner Zitate betrifft die Aufgabe. Diese Sätze in den Quellen scheinen es zu tun:\n{sentences}\nWähle (wörtliche) Zitate, die die Aufgabe beantworten. Steht die Antwort nicht darin, setze not_found = true.",
    },
    "lib_correction_unsupported": {
        "nb": "Disse påstandene har noe som ikke står i sitatet eller kildene deres:\n{items}\nRett påstanden så den bare sier det sitatet sier, eller fjern den. Svar i samme format.",
        "en": "These claims contain something that is not in their quote or the sources:\n{items}\nCorrect the claim so it only says what the quote says, or remove it. Answer in the same format.",
        "de": "Diese Aussagen enthalten etwas, das nicht in ihrem Zitat oder den Quellen steht:\n{items}\nKorrigiere die Aussage, sodass sie nur sagt, was das Zitat sagt, oder entferne sie. Antworte im selben Format.",
    },
    "lib_correction_cut": {
        "nb": "Svaret ble kuttet eller er ikke i riktig format. Gi et kortere svar i nøyaktig samme format, med færre påstander.",
        "en": "The answer was cut off or is not in the required format. Give a shorter answer in exactly the same format, with fewer claims.",
        "de": "Die Antwort wurde abgeschnitten oder hat nicht das geforderte Format. Gib eine kürzere Antwort im exakt selben Format, mit weniger Aussagen.",
    },
    "lib_grounded_evidence": {
        "nb": "Dette skriver kildene (ordrett sitert — at det står der er sjekket, ikke at det er sant):",
        "en": "What the sources say (quoted verbatim — checked that it is there, not that it is true):",
        "de": "Das schreiben die Quellen (wörtlich zitiert — geprüft ist, dass es dort steht, nicht dass es stimmt):",
    },
    "lib_claim_other_subject": {
        "nb": "⚠ ANNET EMNE: «{title}» handler ikke om {subject}.",
        "en": "⚠ OTHER SUBJECT: «{title}» is not about {subject}.",
        "de": "⚠ ANDERES THEMA: «{title}» handelt nicht von {subject}.",
    },
    "lib_claim_unmentioned": {
        "nb": "⚠ siden nevner ikke {subject}.",
        "en": "⚠ the page does not mention {subject}.",
        "de": "⚠ die Seite erwähnt {subject} nicht.",
    },
    "lib_grounded_other_subject_note": {
        "nb": "Merk: belegg merket ANNET EMNE gjelder ikke {subject}. Ikke bruk dem som fakta om {subject} — si heller at kildene om {subject} ikke inneholdt svaret.",
        "en": "Note: evidence marked OTHER SUBJECT does not apply to {subject}. Do not use it as facts about {subject} — say instead that the sources about {subject} did not contain the answer.",
        "de": "Hinweis: Belege mit ANDERES THEMA gelten nicht für {subject}. Verwende sie nicht als Fakten über {subject} — sage stattdessen, dass die Quellen über {subject} die Antwort nicht enthielten.",
    },
    "lib_grounded_removed_unsupported": {
        "nb": "{n} påstand(er) ble fjernet fordi påstanden sa mer enn sitatet (sitatet fantes, men dekket ikke alt). Oppsummeringen er derfor utelatt — bruk bare beleggene over.",
        "en": "{n} claim(s) were removed because the claim said more than its quote (the quote was found but didn't cover everything). The summary is therefore left out — use only the evidence above.",
        "de": "{n} Aussage(n) wurden entfernt, weil die Aussage mehr sagte als ihr Zitat (das Zitat wurde gefunden, deckte aber nicht alles ab). Die Zusammenfassung entfällt daher — nutze nur die Belege oben.",
    },
    "lib_grounded_removed": {
        "nb": "{n} påstand(er) ble fjernet fordi sitatet ikke finnes i kildene. Oppsummeringen er derfor utelatt — bruk bare beleggene over.",
        "en": "{n} claim(s) were removed because the quote does not occur in the sources. The summary is therefore left out — use only the evidence above.",
        "de": "{n} Aussage(n) wurden entfernt, weil das Zitat nicht in den Quellen steht. Die Zusammenfassung entfällt daher — nutze nur die Belege oben.",
    },
    "lib_searched_header": {
        "nb": "[Søkt i wiki: «{query}»]",
        "en": "[Searched the wiki for: «{query}»]",
        "de": "[Im Wiki gesucht: «{query}»]",
    },
    "lib_search_line": {
        "nb": "- {wiki}: valgt artikkel «{title}» — {gist}",
        "en": "- {wiki}: chosen article «{title}» — {gist}",
        "de": "- {wiki}: gewählter Artikel «{title}» — {gist}",
    },
    "lib_search_line_none": {
        "nb": "- {wiki}: ingen treff",
        "en": "- {wiki}: no hits",
        "de": "- {wiki}: keine Treffer",
    },
    "lib_grounded_not_found": {
        "nb": "Kildene inneholder ikke svaret på oppgaven.",
        "en": "The sources do not contain the answer to the task.",
        "de": "Die Quellen enthalten die Antwort auf die Aufgabe nicht.",
    },
    "lib_grounded_failed": {
        "nb": "Miss Library klarte ikke å gi et svar som kunne kontrolleres mot kildene.",
        "en": "Miss Library could not produce an answer that could be checked against the sources.",
        "de": "Miss Library konnte keine Antwort liefern, die sich an den Quellen prüfen ließ.",
    },
    "lib_wiki_excerpt_prefix": {
        "nb": "Wiki-utdrag:\n{wiki_ctx}\n\nOppgave fra Kåre: {question}",
        "en": "Wiki excerpt:\n{wiki_ctx}\n\nTask from Kåre: {question}",
        "de": "Wiki-Auszug:\n{wiki_ctx}\n\nAufgabe von Kåre: {question}",
    },
    "lib_wiki_no_excerpt": {
        "nb": "Spørsmål: {question}\n\n(Ingen wiki-utdrag funnet.)",
        "en": "Question: {question}\n\n(No wiki excerpt found.)",
        "de": "Frage: {question}\n\n(Kein Wiki-Auszug gefunden.)",
    },
    "lib_web_content_unavailable": {
        "nb": "(Innhold utilgjengelig.)",
        "en": "(Content unavailable.)",
        "de": "(Inhalt nicht verfügbar.)",
    },
    "lib_web_no_sources": {
        "nb": "(Ingen kilder hentet.)",
        "en": "(No sources retrieved.)",
        "de": "(Keine Quellen abgerufen.)",
    },
    "lib_url_summarize_prompt": {
        "nb": (
            "Kilde: {url}\n\n{text}\n\n"
            "Oppsummer innholdet klart og hjelpsomt. "
            "Hvis et konkret spørsmål ble stilt, svar på det basert på kilden."
        ),
        "en": (
            "Source: {url}\n\n{text}\n\n"
            "Summarize the content clearly and helpfully. "
            "If a specific question was asked, answer it based on the source."
        ),
        "de": (
            "Quelle: {url}\n\n{text}\n\n"
            "Fasse den Inhalt klar und hilfreich zusammen. "
            "Falls eine konkrete Frage gestellt wurde, beantworte sie anhand der Quelle."
        ),
    },
    "lib_question_prefix": {
        "nb": "{system}\n\nSpørsmål: {question}",
        "en": "{system}\n\nQuestion: {question}",
        "de": "{system}\n\nFrage: {question}",
    },
    "lib_web_synthesis_prompt": {
        "nb": (
            "Nettkilder:\n{sources_text}\n\n"
            "Spørsmål: {question}\n\n"
            "Svar KUN fra kildene over. Hvis ikke funnet, si det og oppgi URL-ene."
        ),
        "en": (
            "Web sources:\n{sources_text}\n\n"
            "Question: {question}\n\n"
            "Answer ONLY from the sources above. If not found, say so and include the URLs."
        ),
        "de": (
            "Webquellen:\n{sources_text}\n\n"
            "Frage: {question}\n\n"
            "Antworte NUR anhand der obigen Quellen. Falls nicht gefunden, sag es und gib die URLs an."
        ),
    },
    # ── Reflection loop (Jang → Kåre, kaare_core/reflection_loop.py) ─────────
    "refl_loop_prompt": {
        "nb": (
            "Dette er en privat, intern refleksjonsøkt. Ingen bruker er til stede.\n\n"
            "Disse nye tankene kommer fra Jing og Jang, dine indre stemmer:\n\n"
            "{thoughts}\n\n"
            "Gå gjennom tankene. Vurder:\n"
            "- Sier noe her noe ekte om hvem du er eller hvordan du fungerer? → self_image\n"
            "- Er det en egen oppfølging du skal gjøre senere? → note (list_name='kare', title=…) — kort, ingen logg\n"
            "- Så du noe i huset (sensor, kamera, hendelse) som er verdt å huske? → note (list_name='observations', title=…, theme=…)\n"
            "- Er det en feil som bør undersøkes på dev-møtet? → note (list_name='agenda', title=…, theme=…)\n"
            "- Har du lært en regel om hvordan du bør jobbe? → note (list_name='lessons', title=…)\n"
            "Er noe allerede løst (se «Løste saker» i konteksten din), trenger det ingen ny rad.\n"
            "Bruk bare verktøy hvis noe virkelig er verdt å gjøre. "
            "Trivielle eller allerede kjente ting trenger ingen handling. "
            "Når du er ferdig, svar kort med hva du gjorde, eller «Ingen endringer.» hvis ingenting trengtes."
        ),
        "en": (
            "This is a private internal reflection session. No user is present.\n\n"
            "The following new thoughts were generated by Jing and Jang, your inner voices:\n\n"
            "{thoughts}\n\n"
            "Review these thoughts. Consider:\n"
            "- Does anything reveal something genuine about who you are or how you function? → self_image\n"
            "- Is there a follow-up of your own to do later? → note (list_name='kare', title=…) — short, no log\n"
            "- Did you see something in the house (sensor, camera, event) worth remembering? → note (list_name='observations', title=…, theme=…)\n"
            "- Is there a fault to investigate at the dev meeting? → note (list_name='agenda', title=…, theme=…)\n"
            "- Did you learn a rule about how you should work? → note (list_name='lessons', title=…)\n"
            "If something is already resolved (see «Resolved topics» in your context), it needs no new row.\n"
            "Only use tools if there is something genuinely worth doing. "
            "Trivial or already-known things do not need action. "
            "When done, respond briefly with what you did, or 'No changes.' if nothing was needed."
        ),
        "de": (
            "Dies ist eine private, interne Reflexionssitzung. Kein Benutzer ist anwesend.\n\n"
            "Die folgenden neuen Gedanken stammen von Jing und Jang, deinen inneren Stimmen:\n\n"
            "{thoughts}\n\n"
            "Geh diese Gedanken durch. Überlege:\n"
            "- Zeigt etwas davon etwas Echtes darüber, wer du bist oder wie du funktionierst? → self_image\n"
            "- Gibt es eine eigene Nachverfolgung für später? → note (list_name='kare', title=…) — kurz, kein Protokoll\n"
            "- Hast du im Haus etwas gesehen (Sensor, Kamera, Ereignis), das es wert ist, behalten zu werden? → note (list_name='observations', title=…, theme=…)\n"
            "- Gibt es einen Fehler, der beim Entwicklertreffen untersucht werden sollte? → note (list_name='agenda', title=…, theme=…)\n"
            "- Hast du eine Regel gelernt, wie du arbeiten solltest? → note (list_name='lessons', title=…)\n"
            "Ist etwas bereits gelöst (siehe «Gelöste Themen» in deinem Kontext), braucht es keine neue Zeile.\n"
            "Benutze Werkzeuge nur, wenn wirklich etwas zu tun ist. "
            "Triviales oder bereits Bekanntes braucht keine Handlung. "
            "Wenn du fertig bist, antworte kurz, was du getan hast, oder „Keine Änderungen.“, wenn nichts nötig war."
        ),
    },
    "refl_loop_tool_error": {
        "nb": "Verktøyfeil: {error}",
        "en": "Tool error: {error}",
        "de": "Werkzeugfehler: {error}",
    },
    # ── Dev-meeting agenda (P118, kaare_core/tools/agenda.py) ────────────────
    "agenda_added": {
        "nb": "Lagt på dev-møtets agenda (sak #{topic}, tema: {theme}). Den venter på neste dev-møte.",
        "en": "Added to the dev meeting agenda (topic #{topic}, theme: {theme}). It waits for the next dev meeting.",
        "de": "Zur Agenda des Entwicklertreffens hinzugefügt (Thema #{topic}, Bereich: {theme}). Es wartet auf das nächste Entwicklertreffen.",
    },
    "agenda_already_known": {
        "nb": "Står allerede på agendaen (sak #{topic}, sett {count} ganger siden {first_seen}). Denne forekomsten er telt med.",
        "en": "Already on the agenda (topic #{topic}, seen {count} times since {first_seen}). This occurrence has been counted.",
        "de": "Steht bereits auf der Agenda (Thema #{topic}, {count}-mal gesehen seit {first_seen}). Dieses Vorkommen wurde mitgezählt.",
    },
    "agenda_empty": {
        "nb": "Agendaen til dev-møtet er tom.",
        "en": "The dev meeting agenda is empty.",
        "de": "Die Agenda des Entwicklertreffens ist leer.",
    },
    "agenda_read_title": {
        "nb": "Dev-møtets agenda — viser {shown} nyeste av {total} ventende punkter ({topics} saker):",
        "en": "Dev meeting agenda — showing the {shown} newest of {total} waiting items ({topics} topics):",
        "de": "Agenda des Entwicklertreffens — die {shown} neuesten von {total} wartenden Punkten ({topics} Themen):",
    },
    "agenda_write_failed": {
        "nb": "Kunne ikke bruke dev-møtets agenda: {error}",
        "en": "Could not use the dev meeting agenda: {error}",
        "de": "Die Agenda des Entwicklertreffens konnte nicht verwendet werden: {error}",
    },
    "agenda_prompt_line": {
        "nb": "# Dev-møtet\n{count} punkter venter på neste dev-møte (note list_name='agenda', action='read' viser dem).",
        "en": "# Dev meeting\n{count} items are waiting for the next dev meeting (note list_name='agenda', action='read' shows them).",
        "de": "# Entwicklertreffen\n{count} Punkte warten auf das nächste Entwicklertreffen (note list_name='agenda', action='read' zeigt sie).",
    },
    "list_reminder_needs_user": {
        "nb": "Huskelista ('remember') tilhører en bruker, og ingen bruker er med her. Egne oppfølgingspunkter: note(list_name='kare'). Saker til dev-møtet: note(list_name='agenda').",
        "en": "The reminder list ('remember') belongs to a user, and no user is present here. Your own follow-ups: note(list_name='kare'). Items for the dev meeting: note(list_name='agenda').",
        "de": "Die Erinnerungsliste ('remember') gehört einem Benutzer, und hier ist kein Benutzer beteiligt. Eigene Nachverfolgungspunkte: note(list_name='kare'). Punkte für das Entwicklertreffen: note(list_name='agenda').",
    },
    "frigate_agenda_followup": {
        "nb": "[SYSTEM: I analysen over sa du at noe skal tas opp på neste dev-møte. Legg det på agendaen nå: kall note(action='write', list_name='agenda', theme='house') med saken kort — hva bildet viste mot hva Frigate meldte. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: In the analysis above you said something should be raised at the next dev meeting. Put it on the agenda now: call note(action='write', list_name='agenda', theme='house') with the item in short — what the image showed vs. what Frigate reported. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: In der Analyse oben hast du gesagt, etwas solle beim nächsten Entwicklertreffen angesprochen werden. Setz es jetzt auf die Agenda: rufe note(action='write', list_name='agenda', theme='house') mit dem Punkt kurz auf — was das Bild zeigte gegenüber dem, was Frigate meldete. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    # ── Dev slot (P118 B3, kaare_core/dev_slot/) ─────────────────────────────
    "slot_janus_system": {
        "nb": (
            "Du er Janus, oversiktsrollen i KTSBs nattlige dev-luke. Du ser bakover (historikk, "
            "tidligere avgjørelser) og fremover (hva som bør prioriteres i natt). Du graver ikke selv "
            "og har ingen verktøy; Kåre graver, koden sjekker kildene. Du avgjør bare prioritet per sak. "
            "Vær nøktern: ingen påstander om årsaker, bare prioritering ut fra det som står i dataene."
        ),
        "en": (
            "You are Janus, the overview role in KTSB's nightly dev slot. You look back (history, "
            "earlier decisions) and forward (what deserves priority tonight). You do not dig and have "
            "no tools; Kåre digs, code checks the sources. You only decide the priority per topic. "
            "Be sober: no claims about causes, only prioritisation from what the data says."
        ),
        "de": (
            "Du bist Janus, die Übersichtsrolle im nächtlichen Dev-Slot von KTSB. Du schaust zurück "
            "(Verlauf, frühere Entscheidungen) und nach vorn (was heute Nacht Vorrang verdient). Du "
            "gräbst nicht selbst und hast keine Werkzeuge; Kåre gräbt, der Code prüft die Quellen. Du "
            "entscheidest nur die Priorität pro Thema. Bleib nüchtern: keine Behauptungen über "
            "Ursachen, nur Priorisierung aus dem, was die Daten sagen."
        ),
    },
    "slot_janus_user": {
        "nb": (
            "Nattens data (JSON):\n{data}\n\n"
            "Gi hver sak i 'topics' én prioritet:\n"
            "- 'new': ny sak, ikke vurdert før.\n"
            "- 'develop': vurdert før, men nye spor har kommet (nye rader siden forrige avgjørelse).\n"
            "- 'seen_before': vurdert før, ingenting nytt — vent.\n"
            "- 'drop': ikke verdt nattens tid (støy, allerede kjent og avklart).\n"
            "Noen saker har 'resolved_matches': løste saker med konklusjon som et menneske har bekreftet. Sjekk løsningen mot dataene:\n"
            "- 'same_as_resolved' (+ 'resolved_id'): saken er det samme som den løste, og løsningen holder — den kobles dit som ekko.\n"
            "- 'contradicts' (+ 'resolved_id'): nye data motsier løsningen — det blir et forslag om gjenåpning som et menneske avgjør.\n"
            'Svar KUN med JSON: {{"priorities": [{{"topic_id": 1, "priority": "new", "reason": "kort grunn", "resolved_id": null}}]}}'
        ),
        "en": (
            "Tonight's data (JSON):\n{data}\n\n"
            "Give every topic in 'topics' one priority:\n"
            "- 'new': new topic, not judged before.\n"
            "- 'develop': judged before, but new traces arrived (new rows since the last decision).\n"
            "- 'seen_before': judged before, nothing new — wait.\n"
            "- 'drop': not worth tonight's time (noise, already known and settled).\n"
            "Some topics have 'resolved_matches': resolved topics with a conclusion a human confirmed. Check the solution against the data:\n"
            "- 'same_as_resolved' (+ 'resolved_id'): the topic is the same as the resolved one and the solution holds — it is linked there as an echo.\n"
            "- 'contradicts' (+ 'resolved_id'): new data contradicts the solution — it becomes a reopen proposal a human decides on.\n"
            'Answer ONLY with JSON: {{"priorities": [{{"topic_id": 1, "priority": "new", "reason": "short reason", "resolved_id": null}}]}}'
        ),
        "de": (
            "Daten der Nacht (JSON):\n{data}\n\n"
            "Gib jedem Thema in 'topics' eine Priorität:\n"
            "- 'new': neues Thema, noch nicht beurteilt.\n"
            "- 'develop': schon beurteilt, aber neue Spuren sind gekommen (neue Zeilen seit der letzten Entscheidung).\n"
            "- 'seen_before': schon beurteilt, nichts Neues — warten.\n"
            "- 'drop': die Zeit heute Nacht nicht wert (Rauschen, bereits bekannt und geklärt).\n"
            "Manche Themen haben 'resolved_matches': gelöste Themen mit einer von einem Menschen bestätigten Schlussfolgerung. Prüfe die Lösung gegen die Daten:\n"
            "- 'same_as_resolved' (+ 'resolved_id'): das Thema ist dasselbe wie das gelöste und die Lösung hält — es wird dort als Echo verknüpft.\n"
            "- 'contradicts' (+ 'resolved_id'): neue Daten widersprechen der Lösung — daraus wird ein Wiedereröffnungsvorschlag, über den ein Mensch entscheidet.\n"
            'Antworte NUR mit JSON: {{"priorities": [{{"topic_id": 1, "priority": "new", "reason": "kurzer Grund", "resolved_id": null}}]}}'
        ),
    },
    "slot_s1_task": {
        "nb": (
            "[Dev-luka, steg S1 — rydd én agenda-sak. Ingen bruker er til stede.]\n"
            "Sak #{topic_id} (sett {count} ganger, {first_seen} → {last_seen}). Rader (id, tid, kilde, tekst):\n{rows}\n\n"
            "Avgjør hva saken er:\n"
            "- 'problem': noe i KTSB som virker feil og bør undersøkes.\n"
            "- 'event': noe som skjedde i huset/verden, ikke en feil i KTSB.\n"
            "- 'known': allerede kjent og avklart.\n"
            "- 'not_real': bygger på en misforståelse eller oppdiktet detalj.\n"
            "Saker som allerede er vurdert i natt:\n{known}\n"
            'Er dette i bunn og grunn SAMME sak som en av dem, sett "same_as" til det nummeret (ellers null).\n'
            'Vis til radene du bygger på som "inbox:<id>". Skriv teksten på norsk.\n'
            'Svar KUN med JSON: {{"topic_id": {topic_id}, "label": "kort navn på saken", "kind": "problem", '
            '"same_as": null, "summary": "én–to setninger", "evidence_refs": ["inbox:<id>"]}}'
        ),
        "en": (
            "[Dev slot, step S1 — clean one agenda topic. No user is present.]\n"
            "Topic #{topic_id} (seen {count} times, {first_seen} → {last_seen}). Rows (id, time, source, text):\n{rows}\n\n"
            "Decide what the topic is:\n"
            "- 'problem': something in KTSB that seems wrong and should be investigated.\n"
            "- 'event': something that happened in the house/world, not a KTSB fault.\n"
            "- 'known': already known and settled.\n"
            "- 'not_real': rests on a misunderstanding or an invented detail.\n"
            "Topics already judged tonight:\n{known}\n"
            'If this is essentially the SAME topic as one of them, set "same_as" to that number (otherwise null).\n'
            'Point to the rows you rely on as "inbox:<id>". Write the text in English.\n'
            'Answer ONLY with JSON: {{"topic_id": {topic_id}, "label": "short topic name", "kind": "problem", '
            '"same_as": null, "summary": "one or two sentences", "evidence_refs": ["inbox:<id>"]}}'
        ),
        "de": (
            "[Dev-Slot, Schritt S1 — ein Agenda-Thema aufräumen. Kein Benutzer ist anwesend.]\n"
            "Thema #{topic_id} ({count}-mal gesehen, {first_seen} → {last_seen}). Zeilen (ID, Zeit, Quelle, Text):\n{rows}\n\n"
            "Entscheide, was das Thema ist:\n"
            "- 'problem': etwas in KTSB, das falsch wirkt und untersucht werden sollte.\n"
            "- 'event': etwas, das im Haus/in der Welt passiert ist, kein KTSB-Fehler.\n"
            "- 'known': bereits bekannt und geklärt.\n"
            "- 'not_real': beruht auf einem Missverständnis oder einem erfundenen Detail.\n"
            "Heute Nacht bereits beurteilte Themen:\n{known}\n"
            'Ist dies im Grunde DASSELBE Thema wie eines davon, setz "same_as" auf diese Nummer (sonst null).\n'
            'Verweise auf die Zeilen, auf die du dich stützt, als "inbox:<id>". Schreib den Text auf Deutsch.\n'
            'Antworte NUR mit JSON: {{"topic_id": {topic_id}, "label": "kurzer Themenname", "kind": "problem", '
            '"same_as": null, "summary": "ein bis zwei Sätze", "evidence_refs": ["inbox:<id>"]}}'
        ),
    },
    "slot_s2_task": {
        "nb": (
            "[Dev-luka, steg S2 — se over én brukertur fra i går. Ingen bruker er til stede.]\n"
            "Spor (JSON, mekaniske fakta fra loggene):\n{trace}\n\n"
            "Hva ba brukeren om, hva gjorde du, og gikk det bra? 'next_prompt' er det brukeren skrev rett etterpå "
            "(tom hvis ingenting) — en retting der tyder på et problem. Bygg bare på fakta i sporet. Skriv teksten på norsk.\n"
            'Svar KUN med JSON: {{"rid": "{rid}", "ok": true, "asked": "...", "did": "...", '
            '"problem": "tom hvis ok", "evidence_refs": ["trace:{rid}"]}}'
        ),
        "en": (
            "[Dev slot, step S2 — review one user turn from yesterday. No user is present.]\n"
            "Trace (JSON, mechanical facts from the logs):\n{trace}\n\n"
            "What did the user ask, what did you do, and did it go well? 'next_prompt' is what the user wrote right "
            "after (empty if nothing) — a correction there suggests a problem. Rely only on facts in the trace. Write the text in English.\n"
            'Answer ONLY with JSON: {{"rid": "{rid}", "ok": true, "asked": "...", "did": "...", '
            '"problem": "empty if ok", "evidence_refs": ["trace:{rid}"]}}'
        ),
        "de": (
            "[Dev-Slot, Schritt S2 — einen Benutzer-Turn von gestern prüfen. Kein Benutzer ist anwesend.]\n"
            "Spur (JSON, mechanische Fakten aus den Logs):\n{trace}\n\n"
            "Worum bat der Benutzer, was hast du getan, und ging es gut? 'next_prompt' ist, was der Benutzer direkt "
            "danach schrieb (leer, wenn nichts) — eine Korrektur dort deutet auf ein Problem. Stütz dich nur auf Fakten in der Spur. Schreib den Text auf Deutsch.\n"
            'Antworte NUR mit JSON: {{"rid": "{rid}", "ok": true, "asked": "...", "did": "...", '
            '"problem": "leer, wenn ok", "evidence_refs": ["trace:{rid}"]}}'
        ),
    },
    "slot_s3_task": {
        "nb": (
            "[Dev-luka, steg S3 — undersøk ett problem. Ingen bruker er til stede.]\n"
            "Problem: {problem}\nKjent fra før (JSON): {context}\n\n"
            "Undersøk med verktøyene (bare lesing; maks {rounds} runder). Hvert verktøyresultat starter med en "
            "referanse i hakeparentes, f.eks. [tool:rid-meet-123#4]. Regler:\n"
            "- Hvert funn MÅ vise til minst én slik referanse fra DENNE undersøkelsen, og 'quote' MÅ være ordrett "
            "tekst fra det resultatet. Funn uten belegg merkes som ubekreftet.\n"
            "- Skriv bare det verktøyene faktisk viste. Ikke gjett linjenumre, filer eller årsaker.\n"
            "- 'delegate_read' lar 9B-en gjøre én smal lesing for deg; du får rå utdata tilbake.\n"
            "- Et forslag er valgfritt og blir aldri utført av luka — et menneske bekrefter det. Det skal være en "
            "konkret endring et menneske kan gjøre, ikke «ta det opp på neste dev-møte» (du ER i dev-møtet).\n"
            'Skriv teksten på norsk. Når du er ferdig, svar KUN med JSON: {{"findings": [{{"text": "...", '
            '"evidence_refs": ["tool:...#n"], "quote": "ordrett fra resultatet"}}], "proposal": "tom hvis ingen"}}'
        ),
        "en": (
            "[Dev slot, step S3 — investigate one problem. No user is present.]\n"
            "Problem: {problem}\nKnown before (JSON): {context}\n\n"
            "Investigate with the tools (read-only; at most {rounds} rounds). Every tool result starts with a "
            "reference in brackets, e.g. [tool:rid-meet-123#4]. Rules:\n"
            "- Every finding MUST cite at least one such reference from THIS investigation, and 'quote' MUST be "
            "verbatim text from that result. Findings without evidence are marked unverified.\n"
            "- Write only what the tools actually showed. Do not guess line numbers, files or causes.\n"
            "- 'delegate_read' lets the 9B do one narrow read for you; you get the raw output back.\n"
            "- A proposal is optional and is never executed by the slot — a human confirms it. It must be a "
            "concrete change a human can make, not 'raise it at the next dev meeting' (you ARE in the dev meeting).\n"
            'Write the text in English. When done, answer ONLY with JSON: {{"findings": [{{"text": "...", '
            '"evidence_refs": ["tool:...#n"], "quote": "verbatim from the result"}}], "proposal": "empty if none"}}'
        ),
        "de": (
            "[Dev-Slot, Schritt S3 — ein Problem untersuchen. Kein Benutzer ist anwesend.]\n"
            "Problem: {problem}\nBereits bekannt (JSON): {context}\n\n"
            "Untersuche mit den Werkzeugen (nur lesen; höchstens {rounds} Runden). Jedes Werkzeugergebnis beginnt "
            "mit einer Referenz in eckigen Klammern, z. B. [tool:rid-meet-123#4]. Regeln:\n"
            "- Jeder Befund MUSS mindestens eine solche Referenz aus DIESER Untersuchung angeben, und 'quote' MUSS "
            "wörtlicher Text aus diesem Ergebnis sein. Befunde ohne Beleg werden als unbestätigt markiert.\n"
            "- Schreib nur, was die Werkzeuge tatsächlich gezeigt haben. Rate keine Zeilennummern, Dateien oder Ursachen.\n"
            "- 'delegate_read' lässt das 9B ein enges Lesen für dich erledigen; du bekommst die Rohausgabe zurück.\n"
            "- Ein Vorschlag ist optional und wird vom Slot nie ausgeführt — ein Mensch bestätigt ihn. Er muss eine "
            "konkrete Änderung sein, die ein Mensch vornehmen kann, nicht „beim nächsten Entwicklertreffen ansprechen“ "
            "(du BIST im Entwicklertreffen).\n"
            'Schreib den Text auf Deutsch. Wenn du fertig bist, antworte NUR mit JSON: {{"findings": [{{"text": "...", '
            '"evidence_refs": ["tool:...#n"], "quote": "wörtlich aus dem Ergebnis"}}], "proposal": "leer, wenn keiner"}}'
        ),
    },
    "slot_answer_now": {
        "nb": "[SYSTEM: Rundetaket er nådd. Ikke kall flere verktøy. Svar nå KUN med JSON-et som oppgaven ber om.]",
        "en": "[SYSTEM: The round cap is reached. Do not call more tools. Answer now ONLY with the JSON the task asks for.]",
        "de": "[SYSTEM: Die Rundengrenze ist erreicht. Ruf keine Werkzeuge mehr auf. Antworte jetzt NUR mit dem JSON, das die Aufgabe verlangt.]",
    },
    "slot_json_retry": {
        "nb": "[SYSTEM: Svaret ditt kunne ikke leses som JSON. Svar på nytt KUN med JSON-objektet, uten annen tekst.]",
        "en": "[SYSTEM: Your answer could not be read as JSON. Answer again ONLY with the JSON object, no other text.]",
        "de": "[SYSTEM: Deine Antwort konnte nicht als JSON gelesen werden. Antworte erneut NUR mit dem JSON-Objekt, ohne weiteren Text.]",
    },
    "slot_tool_history_desc": {
        "nb": "Les alt dev-luka har lagret om én agenda-sak: radene, tidligere avgjørelser og funn (bare lesing).",
        "en": "Read everything the dev slot has stored about one agenda topic: its rows, earlier decisions and findings (read-only).",
        "de": "Lies alles, was der Dev-Slot zu einem Agenda-Thema gespeichert hat: Zeilen, frühere Entscheidungen und Befunde (nur lesen).",
    },
    "slot_tool_history_topic_desc": {
        "nb": "Saksnummeret (#) fra agendaen.",
        "en": "The topic number (#) from the agenda.",
        "de": "Die Themennummer (#) aus der Agenda.",
    },
    "slot_tool_delegate_desc": {
        "nb": "Be 9B-en gjøre ÉN smal lesing (f.eks. «les linje 140–190 i kaare_core/reflection_loop.py», «søk etter 'Traceback' i llm_calls.log»). Du får rå verktøyutdata tilbake, ikke 9B-ens tolkning.",
        "en": "Ask the 9B to do ONE narrow read (e.g. 'read lines 140–190 of kaare_core/reflection_loop.py', 'grep for Traceback in llm_calls.log'). You get the raw tool output back, not the 9B's reading of it.",
        "de": "Bitte das 9B, EIN enges Lesen zu erledigen (z. B. „lies Zeilen 140–190 in kaare_core/reflection_loop.py“, „suche nach Traceback in llm_calls.log“). Du bekommst die rohe Werkzeugausgabe zurück, nicht die Deutung des 9B.",
    },
    "slot_tool_delegate_task_desc": {
        "nb": "Én konkret lesing: hvilken fil/logg og hva som skal leses eller søkes etter.",
        "en": "One concrete read: which file/log and what to read or search for.",
        "de": "Ein konkretes Lesen: welche Datei/welches Log und was gelesen oder gesucht werden soll.",
    },
    "slot_delegate_system": {
        "nb": "Du er utfører. Gjør nøyaktig ÉN verktøykall som løser leseoppgaven. Ikke forklar, ikke tolk — bare kall verktøyet.",
        "en": "You are an executor. Make exactly ONE tool call that performs the read task. Do not explain or interpret — just call the tool.",
        "de": "Du bist Ausführer. Mach genau EINEN Werkzeugaufruf, der die Leseaufgabe erledigt. Nicht erklären, nicht deuten — ruf nur das Werkzeug auf.",
    },
    "slot_tool_delegate_header": {
        "nb": "[9B kjørte {tool}({args}) — rå utdata:]",
        "en": "[9B ran {tool}({args}) — raw output:]",
        "de": "[9B führte {tool}({args}) aus — Rohausgabe:]",
    },
    "slot_tool_delegate_no_call": {
        "nb": "9B-en gjorde ikke noe gyldig lesekall for oppgaven. Formuler den smalere, eller les selv med utforsk/inspiser.",
        "en": "The 9B made no valid read call for the task. Phrase it more narrowly, or read it yourself with utforsk/inspiser.",
        "de": "Das 9B hat keinen gültigen Leseaufruf für die Aufgabe gemacht. Formuliere sie enger oder lies selbst mit utforsk/inspiser.",
    },
    "slot_tool_delegate_empty": {
        "nb": "delegate_read trenger en konkret oppgave ('task').",
        "en": "delegate_read needs a concrete task ('task').",
        "de": "delegate_read braucht eine konkrete Aufgabe ('task').",
    },
    "slot_tool_delegate_unavailable": {
        "nb": "9B-utføreren er ikke tilgjengelig nå ({error}). Les selv med utforsk/inspiser.",
        "en": "The 9B executor is not available right now ({error}). Read it yourself with utforsk/inspiser.",
        "de": "Der 9B-Ausführer ist gerade nicht verfügbar ({error}). Lies selbst mit utforsk/inspiser.",
    },
    "slot_tool_refused": {
        "nb": "'{tool}' er ikke tilgjengelig i dev-luka — her kan du bare lese.",
        "en": "'{tool}' is not available in the dev slot — here you can only read.",
        "de": "'{tool}' ist im Dev-Slot nicht verfügbar — hier kannst du nur lesen.",
    },
    "slot_tool_no_topic": {
        "nb": "Fant ingen agenda-sak #{topic}.",
        "en": "No agenda topic #{topic} found.",
        "de": "Kein Agenda-Thema #{topic} gefunden.",
    },
    "slot_tool_truncated": {
        "nb": "[… avkortet, {chars} tegn totalt]",
        "en": "[… truncated, {chars} chars in total]",
        "de": "[… gekürzt, insgesamt {chars} Zeichen]",
    },
    # ── Dev slot B4: verifier, Janus end, morning report, proposals ──────────
    "slot_verifier_system": {
        "nb": "Du kontrollerer belegg. Du får én påstand og noe bevistekst. Svar bare om BEVISTEKSTEN støtter påstanden: 'yes', 'partly' eller 'no', og kopier de ordrette ordene fra bevisteksten som viser det (tom streng hvis ingenting). Ikke bruk egen kunnskap.",
        "en": "You check evidence. You get one claim and some evidence text. Answer only whether the EVIDENCE TEXT supports the claim: 'yes', 'partly' or 'no', and copy the exact words from the evidence text that show it (empty string if nothing). Do not use your own knowledge.",
        "de": "Du prüfst Belege. Du bekommst eine Behauptung und einen Belegtext. Antworte nur, ob der BELEGTEXT die Behauptung stützt: 'yes', 'partly' oder 'no', und kopiere die wörtlichen Worte aus dem Belegtext, die es zeigen (leerer String, wenn nichts). Benutze kein eigenes Wissen.",
    },
    "slot_verifier_user": {
        "nb": 'Påstand: {claim}\n\nBevistekst:\n{evidence}\n\nSvar som JSON: {{"supports": "yes|partly|no", "quote": "ordrett fra bevisteksten"}}',
        "en": 'Claim: {claim}\n\nEvidence text:\n{evidence}\n\nAnswer as JSON: {{"supports": "yes|partly|no", "quote": "verbatim from the evidence text"}}',
        "de": 'Behauptung: {claim}\n\nBelegtext:\n{evidence}\n\nAntworte als JSON: {{"supports": "yes|partly|no", "quote": "wörtlich aus dem Belegtext"}}',
    },
    "slot_janus_end_user": {
        "nb": (
            "Nattens bekreftede funn og hva vi vet om sakene fra før (JSON):\n{data}\n\n"
            "Vei hvert funn mot historikken: er dette nytt, et kjent mønster som fortsetter, eller noe som "
            "er blitt bedre/verre? For 'confirmed_proposal_followups': ble symptomet borte etter at forslaget ble "
            "bekreftet (se 'new_rows_since')? Én kort setning per sak, uten nye påstander om årsaker.\n"
            'Svar KUN med JSON: {{"notes": [{{"topic_id": 1, "note": "kort vurdering"}}]}}'
        ),
        "en": (
            "Tonight's verified findings and what we knew about the topics before (JSON):\n{data}\n\n"
            "Weigh each finding against the history: is it new, a known pattern continuing, or something that "
            "got better/worse? For 'confirmed_proposal_followups': did the symptom stop after the proposal was "
            "confirmed (see 'new_rows_since')? One short sentence per topic, no new claims about causes.\n"
            'Answer ONLY with JSON: {{"notes": [{{"topic_id": 1, "note": "short assessment"}}]}}'
        ),
        "de": (
            "Die bestätigten Befunde der Nacht und was wir vorher über die Themen wussten (JSON):\n{data}\n\n"
            "Wäge jeden Befund gegen den Verlauf ab: ist er neu, ein bekanntes Muster, das weitergeht, oder etwas, "
            "das besser/schlechter geworden ist? Für 'confirmed_proposal_followups': hat das Symptom aufgehört, "
            "nachdem der Vorschlag bestätigt wurde (siehe 'new_rows_since')? Ein kurzer Satz pro Thema, keine "
            "neuen Behauptungen über Ursachen.\n"
            'Antworte NUR mit JSON: {{"notes": [{{"topic_id": 1, "note": "kurze Einschätzung"}}]}}'
        ),
    },
    "slot_s5_task": {
        "nb": (
            "[Dev-luka, steg S5 — morgenrapport. Ingen bruker er til stede nå; rapporten leses i morgen.]\n"
            "Nattens resultat (JSON — bare bekreftede funn, Janus' vurderinger og tall):\n{data}\n\n"
            "Skriv en kort morgenrapport i første person (4–8 setninger, vanlig tekst, ingen overskrifter): hva du "
            "så på i natt, hva som er bekreftet, og hva som venter på et menneske. Bruk BARE det som står over — "
            "ikke legg til funn, årsaker eller tall, og ikke nevn disse reglene i teksten. Ubekreftede mistanker og forslag "
            "listes av koden under rapporten; du trenger ikke gjenta dem. Skriv på norsk."
        ),
        "en": (
            "[Dev slot, step S5 — morning report. No user is present now; the report is read in the morning.]\n"
            "Tonight's result (JSON — only verified findings, Janus's assessments and counts):\n{data}\n\n"
            "Write a short morning report in the first person (4–8 sentences, plain text, no headings): what you "
            "looked at tonight, what is verified, and what is waiting for a human. Use ONLY what is above — do not "
            "add findings, causes or numbers, and do not mention these rules in the text. Unverified suspicions and "
            "proposals are listed by code below the report; you do not need to repeat them. Write in English."
        ),
        "de": (
            "[Dev-Slot, Schritt S5 — Morgenbericht. Gerade ist kein Benutzer anwesend; der Bericht wird morgens gelesen.]\n"
            "Ergebnis der Nacht (JSON — nur bestätigte Befunde, Janus' Einschätzungen und Zahlen):\n{data}\n\n"
            "Schreib einen kurzen Morgenbericht in der ersten Person (4–8 Sätze, Fließtext, keine Überschriften): was "
            "du dir heute Nacht angesehen hast, was bestätigt ist und was auf einen Menschen wartet. Benutze NUR, was "
            "oben steht — füge keine Befunde, Ursachen oder Zahlen hinzu und erwähne diese Regeln nicht im Text. "
            "Unbestätigte Verdachtsmomente und Vorschläge listet der Code unter dem Bericht auf; du musst sie nicht "
            "wiederholen. Schreib auf Deutsch."
        ),
    },
    "slot_report_title": {
        "nb": "# Dev-luka – {date}",
        "en": "# Dev slot – {date}",
        "de": "# Dev-Slot – {date}",
    },
    "slot_report_summary_h": {
        "nb": "## Morgenrapport",
        "en": "## Morning report",
        "de": "## Morgenbericht",
    },
    "slot_report_verified_h": {
        "nb": "## Bekreftede funn",
        "en": "## Verified findings",
        "de": "## Bestätigte Befunde",
    },
    "slot_report_suspicions_h": {
        "nb": "## Mistanker (ikke bekreftet av kildene)",
        "en": "## Suspicions (not verified by the sources)",
        "de": "## Verdachtsmomente (von den Quellen nicht bestätigt)",
    },
    "slot_report_proposals_h": {
        "nb": "## Forslag som venter på deg",
        "en": "## Proposals waiting for you",
        "de": "## Vorschläge, die auf dich warten",
    },
    "slot_report_proposal_hint": {
        "nb": "Bekreft eller avvis i chat, f.eks. «godkjenn dev-forslag {id}» eller «avvis dev-forslag {id}». Luka utfører aldri noe selv.",
        "en": "Confirm or reject in chat, e.g. 'confirm dev proposal {id}' or 'reject dev proposal {id}'. The slot never applies anything itself.",
        "de": "Bestätige oder lehne im Chat ab, z. B. „Dev-Vorschlag {id} bestätigen“ oder „Dev-Vorschlag {id} ablehnen“. Der Slot setzt selbst nie etwas um.",
    },
    "slot_report_janus_h": {
        "nb": "## Janus – sett mot historikken",
        "en": "## Janus – against the history",
        "de": "## Janus – gegen den Verlauf",
    },
    "slot_report_agenda_h": {
        "nb": "## Agendaen i natt",
        "en": "## The agenda tonight",
        "de": "## Die Agenda heute Nacht",
    },
    "slot_report_agenda_line": {
        "nb": "Saker vurdert: {judged} (koblet til en annen sak: {linked}) · problemer: {problems} · hendelser: {events} · kjent/ikke ekte: {other} · brukerturer sett over: {traces} · ikke rukket (venter til neste natt): {skipped}",
        "en": "Topics judged: {judged} (linked to another topic: {linked}) · problems: {problems} · events: {events} · known/not real: {other} · user turns reviewed: {traces} · not reached (wait for next night): {skipped}",
        "de": "Beurteilte Themen: {judged} (mit einem anderen Thema verknüpft: {linked}) · Probleme: {problems} · Ereignisse: {events} · bekannt/nicht echt: {other} · geprüfte Benutzer-Turns: {traces} · nicht erreicht (warten auf die nächste Nacht): {skipped}",
    },
    "slot_report_none": {"nb": "_Ingen._", "en": "_None._", "de": "_Keine._"},
    "slot_report_verifier_off": {
        "nb": "(9B-kontroll ikke tilgjengelig — bare kodesjekk)",
        "en": "(9B check not available — code check only)",
        "de": "(9B-Prüfung nicht verfügbar — nur Code-Prüfung)",
    },
    "slot_report_reason": {
        "nb": "grunn: {reason}",
        "en": "reason: {reason}",
        "de": "Grund: {reason}",
    },
    "slot_report_summary_failed": {
        "nb": "_Morgenrapporten kunne ikke skrives i natt — se funnene under._",
        "en": "_The morning report could not be written tonight — see the findings below._",
        "de": "_Der Morgenbericht konnte heute Nacht nicht geschrieben werden — siehe die Befunde unten._",
    },
    "tool_dev_proposals_desc": {
        "nb": "Forslag fra den nattlige dev-luka (bygget på bekreftede funn). 'list' viser ventende forslag, 'confirm'/'reject' registrerer brukerens avgjørelse (krever 'proposal_id'). Bekreft/avvis BARE når brukeren selv ber om det. Å bekrefte utfører ingenting — et menneske gjør endringen.",
        "en": "Proposals from the nightly dev slot (built on verified findings). 'list' shows pending proposals, 'confirm'/'reject' records the user's decision (requires 'proposal_id'). Confirm/reject ONLY when the user asks for it. Confirming applies nothing — a human makes the change.",
        "de": "Vorschläge aus dem nächtlichen Dev-Slot (auf bestätigten Befunden aufgebaut). 'list' zeigt offene Vorschläge, 'confirm'/'reject' hält die Entscheidung des Benutzers fest (erfordert 'proposal_id'). Bestätige/lehne NUR ab, wenn der Benutzer darum bittet. Bestätigen setzt nichts um — ein Mensch nimmt die Änderung vor.",
    },
    "tool_dev_proposals_action_desc": {
        "nb": "'list' = vis ventende forslag. 'confirm' = brukeren godtar forslaget. 'reject' = brukeren avviser det.",
        "en": "'list' = show pending proposals. 'confirm' = the user accepts the proposal. 'reject' = the user rejects it.",
        "de": "'list' = offene Vorschläge zeigen. 'confirm' = der Benutzer nimmt den Vorschlag an. 'reject' = der Benutzer lehnt ihn ab.",
    },
    "tool_dev_proposals_id_desc": {
        "nb": "Forslagsnummeret (#) fra morgenrapporten eller 'list'.",
        "en": "The proposal number (#) from the morning report or 'list'.",
        "de": "Die Vorschlagsnummer (#) aus dem Morgenbericht oder 'list'.",
    },
    "dev_proposals_none": {
        "nb": "Ingen ventende forslag fra dev-luka.",
        "en": "No pending proposals from the dev slot.",
        "de": "Keine offenen Vorschläge aus dem Dev-Slot.",
    },
    "dev_proposals_title": {
        "nb": "Ventende forslag fra dev-luka ({count}):",
        "en": "Pending proposals from the dev slot ({count}):",
        "de": "Offene Vorschläge aus dem Dev-Slot ({count}):",
    },
    "dev_proposals_confirmed": {
        "nb": "Forslag #{id} er registrert som godkjent av {user}. Ingenting er endret ennå — selve endringen gjøres av et menneske.",
        "en": "Proposal #{id} is recorded as confirmed by {user}. Nothing has been changed yet — a human makes the actual change.",
        "de": "Vorschlag #{id} ist als von {user} bestätigt festgehalten. Noch wurde nichts geändert — die eigentliche Änderung nimmt ein Mensch vor.",
    },
    "dev_proposals_rejected": {
        "nb": "Forslag #{id} er registrert som avvist av {user}.",
        "en": "Proposal #{id} is recorded as rejected by {user}.",
        "de": "Vorschlag #{id} ist als von {user} abgelehnt festgehalten.",
    },
    "dev_proposals_not_pending": {
        "nb": "Fant ikke noe ventende forslag #{id} (finnes ikke, eller er allerede avgjort).",
        "en": "No pending proposal #{id} found (it does not exist or was already decided).",
        "de": "Kein offener Vorschlag #{id} gefunden (existiert nicht oder wurde bereits entschieden).",
    },
    "dev_proposals_needs_id": {
        "nb": "Oppgi 'proposal_id' (nummeret fra rapporten eller 'list').",
        "en": "Give 'proposal_id' (the number from the report or 'list').",
        "de": "Gib 'proposal_id' an (die Nummer aus dem Bericht oder 'list').",
    },
    # ── Dev slot B6: consolidation (S6) ─────────────────────────────────────
    "slot_consolidate_user": {
        "nb": (
            "Oppsummer {level} {period} for dev-luka. Data (JSON — tall fra koden, de travleste sakene, bekreftede funn "
            "eller oppsummeringer av delperiodene):\n{data}\n\n"
            "Skriv 2–5 setninger: hva som fylte perioden, hva som ble bekreftet, hva som er uløst. Bare det som står i "
            "dataene — ingen nye årsaker eller tall. Skriv på norsk.\n"
            'Svar KUN med JSON: {{"summary": "..."}}'
        ),
        "en": (
            "Summarise {level} {period} for the dev slot. Data (JSON — counts from code, the busiest topics, verified "
            "findings or summaries of the sub-periods):\n{data}\n\n"
            "Write 2–5 sentences: what filled the period, what was verified, what is unresolved. Only what is in the "
            "data — no new causes or numbers. Write in English.\n"
            'Answer ONLY with JSON: {{"summary": "..."}}'
        ),
        "de": (
            "Fasse {level} {period} für den Dev-Slot zusammen. Daten (JSON — Zahlen aus dem Code, die aktivsten Themen, "
            "bestätigte Befunde oder Zusammenfassungen der Teilzeiträume):\n{data}\n\n"
            "Schreib 2–5 Sätze: was den Zeitraum gefüllt hat, was bestätigt wurde, was ungelöst ist. Nur was in den "
            "Daten steht — keine neuen Ursachen oder Zahlen. Schreib auf Deutsch.\n"
            'Antworte NUR mit JSON: {{"summary": "..."}}'
        ),
    },
    "slot_level_week": {"nb": "uke", "en": "week", "de": "Woche"},
    "slot_level_month": {"nb": "måned", "en": "month", "de": "Monat"},
    "slot_level_half_year": {"nb": "halvår", "en": "half-year", "de": "Halbjahr"},
    "slot_level_year": {"nb": "år", "en": "year", "de": "Jahr"},
    "slot_report_consolidated_h": {
        "nb": "## Konsolidert i natt",
        "en": "## Consolidated tonight",
        "de": "## Heute Nacht konsolidiert",
    },
    "slot_report_consolidated_line": {
        "nb": "Perioder skrevet: {written} · rader flyttet ut av den aktive innboksen (eldre enn 7 dager, beholdt): {retired}",
        "en": "Periods written: {written} · rows moved out of the active inbox (older than 7 days, kept): {retired}",
        "de": "Geschriebene Zeiträume: {written} · Zeilen aus der aktiven Inbox genommen (älter als 7 Tage, behalten): {retired}",
    },
}
