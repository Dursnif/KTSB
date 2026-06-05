import yaml
from pathlib import Path

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def get_lang(user_id: str = "global") -> str:
    if user_id and user_id != "global":
        try:
            profile_path = Path(f"/kaare/state/users/{user_id}/profile.yaml")
            if profile_path.exists():
                profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
                lang = (profile.get("prompt_top") or {}).get("language")
                if lang in ("nb", "en", "de"):
                    return lang
        except Exception:
            pass
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        lang = cfg.get("kare_language", "nb")
        if lang in ("nb", "en", "de"):
            return lang
    except Exception:
        pass
    return "nb"


def t(key: str, lang: str = "nb", **kwargs) -> str:
    s = _T.get(key, {}).get(lang) or _T.get(key, {}).get("nb") or key
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
    "ha_history_no_data": {
        "nb": "Ingen statistikkdata funnet for {entity_id}. Kontroller at sensoren har 'state_class' i HA og at long-term statistics er aktivert.",
        "en": "No statistics data found for {entity_id}. Check that the sensor has 'state_class' in HA and long-term statistics are enabled.",
        "de": "Keine Statistikdaten für {entity_id} gefunden. Prüfe, ob der Sensor 'state_class' in HA hat und Langzeitstatistiken aktiviert sind.",
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
    "ha_history_header": {
        "nb": "Sensorhistorikk — {entity_id} (siste {days} dager, per {period}):",
        "en": "Sensor history — {entity_id} (last {days} days, per {period}):",
        "de": "Sensorverlauf — {entity_id} (letzte {days} Tage, pro {period}):",
    },
    "ha_history_period_day":   {"nb": "dag",   "en": "day",   "de": "Tag"},
    "ha_history_period_week":  {"nb": "uke",   "en": "week",  "de": "Woche"},
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
        "nb": "Ingen lagret snapshot funnet for event_id '{event_id}'. Bildet er kanskje for gammelt eller lagring er deaktivert.",
        "en": "No stored snapshot found for event_id '{event_id}'. The image may be too old or storage is disabled.",
        "de": "Kein gespeicherter Snapshot für event_id '{event_id}' gefunden. Das Bild ist möglicherweise zu alt oder die Speicherung ist deaktiviert.",
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
        "nb": "Fant {count} relevante episoder fra minnet:",
        "en": "Found {count} relevant episodes from memory:",
        "de": "{count} relevante Episoden aus dem Gedächtnis gefunden:",
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
        "nb": "Ubekreftede interaksjoner{note}:",
        "en": "Unverified interactions{note}:",
        "de": "Unbestätigte Interaktionen{note}:",
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
        "nb": "Domenet i '{url}' er ikke på listen over betrodde kilder. Legg til domenet i Innstillinger → Nettsøk.",
        "en": "The domain in '{url}' is not on the trusted sources list. Add it in Settings → Web search.",
        "de": "Die Domain in '{url}' steht nicht auf der Liste vertrauenswürdiger Quellen. Füge sie unter Einstellungen → Websuche hinzu.",
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
        "nb": "[System: Din nåværende request-ID er **{rid}**. Bruk inspiser_system(action='fetch_trace', rid='{rid}') for å inspisere din egen trace.]",
        "en": "[System: Your current request ID is **{rid}**. Use inspiser_system(action='fetch_trace', rid='{rid}') to inspect your own trace.]",
        "de": "[System: Deine aktuelle Request-ID ist **{rid}**. Verwende inspiser_system(action='fetch_trace', rid='{rid}') um deine eigene Trace zu inspizieren.]",
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
        "en": "Plex search: \"{query}\"",
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
        "en": "▶ Starting playback{offset} on \"{client}\" ✅",
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
        "nb": "Legg det til under Innstillinger → Nettsøk → Godkjente kilder.",
        "en": "Add it under Settings → Web search → Trusted sources.",
        "de": "Füge es unter Einstellungen → Websuche → Vertrauenswürdige Quellen hinzu.",
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
            "Spør Miss Library — lokal wiki-database og online LLM. Fire operasjoner: "
            "action='search': semantisk søk i lokal wiki (1M+ artikler) for faktaspørsmål, "
            "definisjoner, historiske data. Svarer alltid med kilde. "
            "action='fetch_article': hent hele Wikipedia-artikkelen etter et søk (krever 'title'). "
            "action='fetch_url': hent og oppsummer innhold fra spesifikk nettside (krever 'url', kun tillatte domener). "
            "action='online': spør stor online LLM for bred kunnskap eller kompleks resonnering. "
            "VIKTIG: ved ingen svar/feil, si det klart. Tilby mechanic(action='search') kun om brukeren ønsker det."
        ),
        "en": (
            "Ask Miss Library — local wiki database and online LLM. Four operations: "
            "action='search': semantic search in local wiki (1M+ articles) for factual questions, "
            "definitions, historical data. Always answers with source. "
            "action='fetch_article': fetch the full Wikipedia article after a search (requires 'title'). "
            "action='fetch_url': fetch and summarize a specific webpage (requires 'url', trusted domains only). "
            "action='online': ask a large online LLM for broad knowledge or complex reasoning. "
            "IMPORTANT: on no answer/error, say so clearly. Offer mechanic(action='search') only if user wants it."
        ),
        "de": (
            "Miss Library fragen — lokale Wiki-Datenbank und Online-LLM. Vier Operationen: "
            "action='search': semantische Suche im lokalen Wiki (1M+ Artikel) für Faktenfragen, "
            "Definitionen, historische Daten. Antwortet immer mit Quelle. "
            "action='fetch_article': vollständigen Wikipedia-Artikel abrufen (erfordert 'title'). "
            "action='fetch_url': Webseite abrufen und zusammenfassen (erfordert 'url', nur vertrauenswürdige Domains). "
            "action='online': großes Online-LLM für breites Wissen oder komplexes Denken befragen. "
            "WICHTIG: bei keiner Antwort/Fehler klar mitteilen. mechanic(action='search') nur anbieten wenn Nutzer es möchte."
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
        "nb": "'search' = semantisk wiki-søk (krever 'query'). 'fetch_article' = hent hel artikkel (krever 'title'). 'fetch_url' = hent nettside (krever 'url'). 'online' = online LLM (krever 'query').",
        "en": "'search' = semantic wiki search (requires 'query'). 'fetch_article' = fetch full article (requires 'title'). 'fetch_url' = fetch webpage (requires 'url'). 'online' = online LLM (requires 'query').",
        "de": "'search' = semantische Wiki-Suche (erfordert 'query'). 'fetch_article' = vollständigen Artikel abrufen (erfordert 'title'). 'fetch_url' = Webseite abrufen (erfordert 'url'). 'online' = Online-LLM (erfordert 'query').",
    },
    "tool_library_no_online_action_desc": {
        "nb": "'search' = semantisk wiki-søk (krever 'query'). 'fetch_article' = hent hel artikkel (krever 'title'). 'fetch_url' = hent nettside (krever 'url').",
        "en": "'search' = semantic wiki search (requires 'query'). 'fetch_article' = fetch full article (requires 'title'). 'fetch_url' = fetch webpage (requires 'url').",
        "de": "'search' = semantische Wiki-Suche (erfordert 'query'). 'fetch_article' = vollständigen Artikel abrufen (erfordert 'title'). 'fetch_url' = Webseite abrufen (erfordert 'url').",
    },
    "tool_library_query_desc": {
        "nb": "Spørsmålet. Kun ved action='search' og 'online'.",
        "en": "The query. Only for action='search' and 'online'.",
        "de": "Die Anfrage. Nur bei action='search' und 'online'.",
    },
    "tool_library_title_desc": {
        "nb": "Artikkeltittel nøyaktig som i wiki-søkeresultatet. Kun ved action='fetch_article'.",
        "en": "Article title exactly as in the wiki search result. Only for action='fetch_article'.",
        "de": "Artikeltitel genau wie im Wiki-Suchergebnis. Nur bei action='fetch_article'.",
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
        "nb": "HA-handlingen ved avfyring. Objekt med 'action' og 'entity_id'. Gyldige actions: turn_on, turn_off, toggle, set_level. Eksempel: {\"action\": \"turn_off\", \"entity_id\": \"light.verksted\"}. Kun ved action_type='ha_action'.",
        "en": "The HA action to perform when the timer fires. Object with 'action' and 'entity_id'. Valid actions: turn_on, turn_off, toggle, set_level. Example: {\"action\": \"turn_off\", \"entity_id\": \"light.workshop\"}. Only for action_type='ha_action'.",
        "de": "Die HA-Aktion beim Auslösen. Objekt mit 'action' und 'entity_id'. Gültige Aktionen: turn_on, turn_off, toggle, set_level. Beispiel: {\"action\": \"turn_off\", \"entity_id\": \"light.werkstatt\"}. Nur bei action_type='ha_action'.",
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
        "en": "⏰ Unacknowledged reminder for {user_id}: \"{message}\" [notif_id={notif_id}] — deliver this and then call timer:ack notif_id={notif_id}.",
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
            "action='search': semantisk søk i langtidsminnet. "
            "action='fetch_unverified': hent interaksjoner som ikke er bekreftet ennå. "
            "action='confirm': merk interaksjoner som verified/denied/test (krever 'ids' og 'dom'). "
            "action='fetch_stm': hent eldre STM-snapshot — uten dato: vis tilgjengelige datoer; "
            "med dato='YYYY-MM-DD': hent dialog fra den dagen."
        ),
        "en": (
            "Kåre's memory — four operations: "
            "action='search': semantic search in long-term memory. "
            "action='fetch_unverified': fetch interactions not yet confirmed by the user. "
            "action='confirm': mark interactions as verified/denied/test (requires 'ids' and 'dom'). "
            "action='fetch_stm': fetch older STM snapshot — without date: list available dates; "
            "with date='YYYY-MM-DD': fetch dialogue from that day."
        ),
        "de": (
            "Kåres Gedächtnis — vier Operationen: "
            "action='search': semantische Suche im Langzeitgedächtnis. "
            "action='fetch_unverified': noch nicht bestätigte Interaktionen abrufen. "
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
        "nb": "Søk i Kåres systemlogg via Argus. Returnerer HA-handlinger, feil, LLM-kall. Bruk for å finne hva systemet HAR GJORT — ikke hva brukeren sa (bruk minne(action='search') for det).",
        "en": "Search Kåre's system log via Argus. Returns HA actions, errors, LLM calls. Use to find what the system HAS DONE — not what the user said (use minne(action='search') for that).",
        "de": "Kåres Systemprotokoll über Argus durchsuchen. Gibt HA-Aktionen, Fehler, LLM-Aufrufe zurück. Verwenden um zu finden was das System GETAN HAT — nicht was der Nutzer sagte (dafür minne(action='search')).",
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

    # mechanic
    "tool_mechanic_desc": {
        "nb": (
            "Mechanic — søk og oppsummer i kodebasen og logger. "
            "action='search': les filer/søk i kode/hent logg uten å fylle din kontekst. "
            "action='delegate': lang bakgrunnsjobb (returnerer job_id). "
            "action='result': poll resultat fra delegert jobb. "
            "action='cancel': stopp løpende jobb. "
            "action='comment': injiser melding i løpende jobb."
        ),
        "en": (
            "Mechanic — search and summarize in codebase and logs. "
            "action='search': read files/search code/fetch logs without filling your context. "
            "action='delegate': long background job (returns job_id). "
            "action='result': poll result from delegated job. "
            "action='cancel': stop running job. "
            "action='comment': inject message into running job."
        ),
        "de": (
            "Mechanic — Codebase und Logs durchsuchen und zusammenfassen. "
            "action='search': Dateien lesen/Code durchsuchen/Logs abrufen ohne den Kontext zu füllen. "
            "action='delegate': langer Hintergrundjob (gibt job_id zurück). "
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
        "nb": "Bakgrunnsoppgave (apt, reboot, SSH). Kun ved action='delegate'.",
        "en": "Background task (apt, reboot, SSH). Only for action='delegate'.",
        "de": "Hintergrundaufgabe (apt, reboot, SSH). Nur bei action='delegate'.",
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
        "nb": "Seksjonsnavnet i profile.yaml, f.eks. 'prompt_top'. Kun ved action='set_field'.",
        "en": "Section name in profile.yaml, e.g. 'prompt_top'. Only for action='set_field'.",
        "de": "Abschnittsname in profile.yaml, z.B. 'prompt_top'. Nur bei action='set_field'.",
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
            "Fire separate lister + Kåres notater. "
            "VIKTIG: Når du sier 'jeg husker det' — SKAL du faktisk kalle notat(action='write', list_name='kare'). "
            "liste='arkitekt': arkitekt/designnotater. "
            "liste='handle': felles handleliste. "
            "liste='huske': brukerens huskeliste. "
            "liste='kare': Kåres egne oppfølgingspunkter."
        ),
        "en": (
            "Four separate lists + Kåre's notes. "
            "IMPORTANT: When you say 'I'll remember that' — you MUST actually call notat(action='write', list_name='kare'). "
            "list_name='arkitekt': architect/design notes. "
            "list_name='handle': shared shopping list. "
            "list_name='huske': user's reminder list. "
            "list_name='kare': Kåre's own follow-up items."
        ),
        "de": (
            "Vier separate Listen + Kåres Notizen. "
            "WICHTIG: Wenn du sagst 'ich merke mir das' — MUSST du tatsächlich notat(action='write', list_name='kare') aufrufen. "
            "list_name='arkitekt': Architektur-/Designnotizen. "
            "list_name='handle': gemeinsame Einkaufsliste. "
            "list_name='huske': Erinnerungsliste des Nutzers. "
            "list_name='kare': Kåres eigene Nachverfolgungspunkte."
        ),
    },
    "tool_notat_action_desc": {
        "nb": "'write'=legg til. 'read'=vis. 'delete'=fjern (krever 'note_id'). 'clear'=tøm. 'done'=ferdig (huske/kare). 'mark_bought'=kjøpt (handle). 'clear_all'=tøm hele handlelisten.",
        "en": "'write'=add. 'read'=show. 'delete'=remove (requires 'note_id'). 'clear'=clear. 'done'=done (huske/kare). 'mark_bought'=bought (handle). 'clear_all'=clear entire shopping list.",
        "de": "'write'=hinzufügen. 'read'=anzeigen. 'delete'=entfernen (erfordert 'note_id'). 'clear'=leeren. 'done'=fertig (huske/kare). 'mark_bought'=gekauft (handle). 'clear_all'=gesamte Einkaufsliste leeren.",
    },
    "tool_notat_list_name_desc": {
        "nb": "'arkitekt'=designnotater (standard). 'handle'=handleliste. 'huske'=huskeliste. 'kare'=Kåres notater.",
        "en": "'arkitekt'=design notes (default). 'handle'=shopping list. 'huske'=reminder list. 'kare'=Kåre's notes.",
        "de": "'arkitekt'=Designnotizen (Standard). 'handle'=Einkaufsliste. 'huske'=Erinnerungsliste. 'kare'=Kåres Notizen.",
    },
    "tool_notat_text_desc": {
        "nb": "Innholdet i notatet/varen. Kreves ved action='write'.",
        "en": "Content of the note/item. Required for action='write'.",
        "de": "Inhalt der Notiz/des Artikels. Erforderlich bei action='write'.",
    },
    "tool_notat_category_desc": {
        "nb": "Kun ved list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
        "en": "Only for list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
        "de": "Nur bei list_name='arkitekt': 'huskeliste', 'gjøremål', 'påminnelse', 'diverse'.",
    },
    "tool_notat_note_id_desc": {
        "nb": "ID (8 tegn, fra action='read'). Kreves ved delete/done/mark_bought.",
        "en": "ID (8 chars, from action='read'). Required for delete/done/mark_bought.",
        "de": "ID (8 Zeichen, aus action='read'). Erforderlich bei delete/done/mark_bought.",
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
    "tool_notat_remind_on_login_desc": {
        "nb": "Kun huske: om Kåre skal minne om dette ved neste innlogging.",
        "en": "Only huske: whether Kåre should remind about this at next login.",
        "de": "Nur huske: ob Kåre beim nächsten Login daran erinnern soll.",
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

    # utforsk_kode
    "tool_utforsk_kode_desc": {
        "nb": "Utforsk /kaare-kodebasen. action='read': les fil (krever 'path'). action='list': list filer (valgfri 'directory', 'recursive'). action='search': grep-søk (krever 'pattern', valgfri 'directory').",
        "en": "Explore /kaare codebase. action='read': read file (requires 'path'). action='list': list files (optional 'directory', 'recursive'). action='search': grep search (requires 'pattern', optional 'directory').",
        "de": "/kaare-Codebase erkunden. action='read': Datei lesen (erfordert 'path'). action='list': Dateien auflisten (optional 'directory', 'recursive'). action='search': Grep-Suche (erfordert 'pattern', optional 'directory').",
    },
    "tool_utforsk_kode_action_desc": {
        "nb": "'read' = les fil (krever 'path'). 'list' = list filer (valgfri 'directory', 'recursive'). 'search' = grep (krever 'pattern').",
        "en": "'read' = read file (requires 'path'). 'list' = list files (optional 'directory', 'recursive'). 'search' = grep (requires 'pattern').",
        "de": "'read' = Datei lesen (erfordert 'path'). 'list' = Dateien auflisten (optional 'directory', 'recursive'). 'search' = grep (erfordert 'pattern').",
    },
    "tool_utforsk_kode_path_desc": {
        "nb": "Absolutt filsti, må starte med /kaare/. Kun ved action='read'.",
        "en": "Absolute file path, must start with /kaare/. Only for action='read'.",
        "de": "Absoluter Dateipfad, muss mit /kaare/ beginnen. Nur bei action='read'.",
    },
    "tool_utforsk_kode_from_line_desc": {
        "nb": "Første linje (1-basert). Kun ved action='read'.",
        "en": "First line (1-based). Only for action='read'.",
        "de": "Erste Zeile (1-basiert). Nur bei action='read'.",
    },
    "tool_utforsk_kode_to_line_desc": {
        "nb": "Siste linje (inklusiv). Kun ved action='read'.",
        "en": "Last line (inclusive). Only for action='read'.",
        "de": "Letzte Zeile (inklusiv). Nur bei action='read'.",
    },
    "tool_utforsk_kode_directory_desc": {
        "nb": "Absolutt mappe-sti under /kaare. Brukes ved 'list' og 'search'.",
        "en": "Absolute directory path under /kaare. Used for 'list' and 'search'.",
        "de": "Absoluter Verzeichnispfad unter /kaare. Für 'list' und 'search'.",
    },
    "tool_utforsk_kode_recursive_desc": {
        "nb": "List rekursivt (maks 200 filer). Kun ved action='list'. Standard: false.",
        "en": "List recursively (max 200 files). Only for action='list'. Default: false.",
        "de": "Rekursiv auflisten (max 200 Dateien). Nur bei action='list'. Standard: false.",
    },
    "tool_utforsk_kode_pattern_desc": {
        "nb": "Søketekst eller regex. Kun ved action='search'.",
        "en": "Search text or regex. Only for action='search'.",
        "de": "Suchtext oder Regex. Nur bei action='search'.",
    },

    # inspiser_system
    "tool_inspiser_system_desc": {
        "nb": (
            "Inspiser systemstatus, logger og request-traces. Sju operasjoner: "
            "action='log': les/søk i logger. "
            "action='services': systemd-status. "
            "action='resources': CPU/RAM/disk/GPU. "
            "action='git_diff': ukommitterte endringer. "
            "action='git_log': commit-historikk. "
            "action='fetch_trace': full trace for én request (krever 'rid'). "
            "action='trace_patterns': mønsteranalyse på tvers av traces."
        ),
        "en": (
            "Inspect system status, logs and request traces. Seven operations: "
            "action='log': read/search logs. "
            "action='services': systemd status. "
            "action='resources': CPU/RAM/disk/GPU. "
            "action='git_diff': uncommitted changes. "
            "action='git_log': commit history. "
            "action='fetch_trace': full trace for one request (requires 'rid'). "
            "action='trace_patterns': pattern analysis across traces."
        ),
        "de": (
            "Systemstatus, Logs und Request-Traces inspizieren. Sieben Operationen: "
            "action='log': Logs lesen/durchsuchen. "
            "action='services': systemd-Status. "
            "action='resources': CPU/RAM/Disk/GPU. "
            "action='git_diff': uncommittete Änderungen. "
            "action='git_log': Commit-Historik. "
            "action='fetch_trace': vollständige Trace für eine Request (erfordert 'rid'). "
            "action='trace_patterns': Musteranalyse über Traces."
        ),
    },
    "tool_inspiser_system_action_desc": {
        "nb": "'log','services','resources','git_diff','git_log','fetch_trace','trace_patterns'.",
        "en": "'log','services','resources','git_diff','git_log','fetch_trace','trace_patterns'.",
        "de": "'log','services','resources','git_diff','git_log','fetch_trace','trace_patterns'.",
    },
    "tool_inspiser_system_file_desc": {
        "nb": "Loggfilnavn uten sti, f.eks. 'kaare_ha_gateway.log'. Kun ved action='log'.",
        "en": "Log file name without path, e.g. 'kaare_ha_gateway.log'. Only for action='log'.",
        "de": "Logdateiname ohne Pfad, z.B. 'kaare_ha_gateway.log'. Nur bei action='log'.",
    },
    "tool_inspiser_system_lines_desc": {
        "nb": "Antall linjer (tail). Standard 20, maks 200. Kun ved action='log'.",
        "en": "Number of lines (tail). Default 20, max 200. Only for action='log'.",
        "de": "Anzahl Zeilen (tail). Standard 20, max 200. Nur bei action='log'.",
    },
    "tool_inspiser_system_pattern_desc": {
        "nb": "Søketekst/regex for grep. Kun ved action='log'.",
        "en": "Search text/regex for grep. Only for action='log'.",
        "de": "Suchtext/Regex für grep. Nur bei action='log'.",
    },
    "tool_inspiser_system_max_hits_desc": {
        "nb": "Maks grep-treff. Standard 50, maks 200. Kun ved action='log'.",
        "en": "Max grep hits. Default 50, max 200. Only for action='log'.",
        "de": "Max grep-Treffer. Standard 50, max 200. Nur bei action='log'.",
    },
    "tool_inspiser_system_from_line_desc": {
        "nb": "Første linje (1-basert). Kun ved action='log'.",
        "en": "First line (1-based). Only for action='log'.",
        "de": "Erste Zeile (1-basiert). Nur bei action='log'.",
    },
    "tool_inspiser_system_to_line_desc": {
        "nb": "Siste linje (inklusiv). Kun ved action='log'.",
        "en": "Last line (inclusive). Only for action='log'.",
        "de": "Letzte Zeile (inklusiv). Nur bei action='log'.",
    },
    "tool_inspiser_system_service_desc": {
        "nb": "Tjenestenavn for detaljert visning, f.eks. 'kaare'. Kun ved action='services'.",
        "en": "Service name for detailed view, e.g. 'kaare'. Only for action='services'.",
        "de": "Dienstname für Detailansicht, z.B. 'kaare'. Nur bei action='services'.",
    },
    "tool_inspiser_system_log_lines_desc": {
        "nb": "Antall journalctl-linjer. Standard 20, maks 50. Kun ved action='services'.",
        "en": "Number of journalctl lines. Default 20, max 50. Only for action='services'.",
        "de": "Anzahl journalctl-Zeilen. Standard 20, max 50. Nur bei action='services'.",
    },
    "tool_inspiser_system_path_desc": {
        "nb": "Absolutt filsti/mappe. Brukes ved 'git_diff'/'git_log'.",
        "en": "Absolute file path/directory. Used for 'git_diff'/'git_log'.",
        "de": "Absoluter Dateipfad/Verzeichnis. Für 'git_diff'/'git_log'.",
    },
    "tool_inspiser_system_count_desc": {
        "nb": "Antall commits (git_log) eller traces (trace_patterns). Standard 10/50.",
        "en": "Number of commits (git_log) or traces (trace_patterns). Default 10/50.",
        "de": "Anzahl Commits (git_log) oder Traces (trace_patterns). Standard 10/50.",
    },
    "tool_inspiser_system_rid_desc": {
        "nb": "Request-ID, f.eks. 'rid-1779893101671'. Kun ved action='fetch_trace'.",
        "en": "Request ID, e.g. 'rid-1779893101671'. Only for action='fetch_trace'.",
        "de": "Request-ID, z.B. 'rid-1779893101671'. Nur bei action='fetch_trace'.",
    },
    "tool_inspiser_system_source_desc": {
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
        "nb": "'ett' = ett kamera (krever 'camera'), 'alle' = alle parallelt. Kun ved action='snapshot'.",
        "en": "'ett' = one camera (requires 'camera'), 'alle' = all in parallel. Only for action='snapshot'.",
        "de": "'ett' = eine Kamera (erfordert 'camera'), 'alle' = alle parallel. Nur bei action='snapshot'.",
    },
    "tool_kamera_camera_desc": {
        "nb": "Kameranavn, f.eks. 'ringeklokke'. Ved snapshot(scope='ett') og action='frigate'.",
        "en": "Camera name, e.g. 'ringeklokke'. For snapshot(scope='ett') and action='frigate'.",
        "de": "Kameraname, z.B. 'ringeklokke'. Bei snapshot(scope='ett') und action='frigate'.",
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

    # ssh_kommando
    "tool_ssh_kommando_desc": {
        "nb": "Kjør read-only shell-kommando på en konfigurert nettverksnode via SSH. Tilgjengelige noder defineres i ssh_nodes.yaml (Settings → Tools). HA OS-noder: ha core/addon info/restart. sudo: per node_cfg sudo_commands-liste.",
        "en": "Run a read-only shell command on a configured network node via SSH. Available nodes are defined in ssh_nodes.yaml (Settings → Tools). HA OS nodes: ha core/addon info/restart. sudo: per node_cfg sudo_commands list.",
        "de": "Read-only Shell-Befehl auf einem konfigurierten Netzwerkknoten über SSH ausführen. Verfügbare Knoten werden in ssh_nodes.yaml definiert (Settings → Tools). HA OS-Knoten: ha core/addon info/restart. sudo: laut sudo_commands-Liste.",
    },
    "tool_ssh_kommando_node_desc": {
        "nb": "Node å kjøre kommandoen på.",
        "en": "Node to run the command on.",
        "de": "Knoten auf dem der Befehl ausgeführt werden soll.",
    },
    "tool_ssh_kommando_command_desc": {
        "nb": "Shell-kommando å kjøre.",
        "en": "Shell command to run.",
        "de": "Auszuführender Shell-Befehl.",
    },

    # local_kommando
    "tool_local_kommando_desc": {
        "nb": "Kjør read-only shell-kommando lokalt på AI-pc. For å inspisere systemtilstand, prosesser, nettverk, filer utenfor /kaare. Ingen sudo — kun lesing. For /kaare-kode/logger: bruk utforsk_kode/inspiser_system.",
        "en": "Run a read-only shell command locally on AI-pc. To inspect system state, processes, network, files outside /kaare. No sudo — read-only only. For /kaare code/logs use utforsk_kode/inspiser_system instead.",
        "de": "Read-only Shell-Befehl lokal auf AI-pc ausführen. Zum Inspizieren von Systemzustand, Prozessen, Netzwerk, Dateien außerhalb /kaare. Kein sudo — nur Lesen. Für /kaare-Code/Logs stattdessen utforsk_kode/inspiser_system verwenden.",
    },
    "tool_local_kommando_command_desc": {
        "nb": "Read-only shell-kommando. Ingen sudo.",
        "en": "Read-only shell command. No sudo.",
        "de": "Read-only Shell-Befehl. Kein sudo.",
    },

    # kare_image
    "tool_kare_image_desc": {
        "nb": "Generer eller rediger et bilde. mode='generate': ny fra tekstbeskrivelse. mode='edit': endre eksisterende (krever 'image_b64'). Skriv detaljert prompt.",
        "en": "Generate or edit an image. mode='generate': new from text description. mode='edit': modify existing (requires 'image_b64'). Write a detailed prompt.",
        "de": "Bild generieren oder bearbeiten. mode='generate': neu aus Textbeschreibung. mode='edit': vorhandenes bearbeiten (erfordert 'image_b64'). Detaillierten Prompt schreiben.",
    },
    "tool_kare_image_mode_desc": {
        "nb": "'generate' = nytt bilde fra tekst. 'edit' = rediger eksisterende (krever 'image_b64').",
        "en": "'generate' = new image from text. 'edit' = edit existing (requires 'image_b64').",
        "de": "'generate' = neues Bild aus Text. 'edit' = vorhandenes bearbeiten (erfordert 'image_b64').",
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
        "nb": "'vis' = vis i chatten. 'analyser' = send til VLM for analyse.",
        "en": "'vis' = show in chat. 'analyser' = send to VLM for analysis.",
        "de": "'vis' = im Chat anzeigen. 'analyser' = zur Analyse an VLM senden.",
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
        "nb": "\nOBS: Dette er transkribert tale (STT). Ta hensyn til mulige talefeil, dialekt og feilstavinger.\n",
        "en": "\nNOTE: This is transcribed speech (STT). Account for possible speech errors, dialect, and misspellings.\n",
        "de": "\nHINWEIS: Dies ist transkribierte Sprache (STT). Berücksichtige mögliche Sprachfehler, Dialekte und Rechtschreibfehler.\n",
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
    "gen_promise_correction_timer": {
        "nb": "[SYSTEM: Du sa at du ville sette en timer eller påminnelse, men du kalte ikke timer-verktøyet. Gjør det nå: kall timer med riktig action, tid og tekst. Kun tool-kall — ingen forklarende tekst.]",
        "en": "[SYSTEM: You said you would set a timer or reminder, but you did not call the timer tool. Do it now: call timer with the correct action, time, and text. Tool call only — no explanatory text.]",
        "de": "[SYSTEM: Du hast gesagt, du würdest einen Timer oder eine Erinnerung setzen, aber du hast das timer-Werkzeug nicht aufgerufen. Tue es jetzt: rufe timer mit der richtigen Aktion, Zeit und Text auf. Nur Werkzeugaufruf — kein erklärender Text.]",
    },
    "gen_promise_correction_generic": {
        "nb": "[SYSTEM: Du lovet å utføre en handling med et verktøy, men kalte det ikke. Gjør det nå — kun tool-kall, ingen forklarende tekst.]",
        "en": "[SYSTEM: You promised to perform an action using a tool, but did not call it. Do it now — tool call only, no explanatory text.]",
        "de": "[SYSTEM: Du hast versprochen, eine Aktion mit einem Werkzeug durchzuführen, aber es nicht aufgerufen. Tue es jetzt — nur Werkzeugaufruf, kein erklärender Text.]",
    },
    "lib_context_city_country": {
        "nb": "[Kontekst: {city}, {country}] ",
        "en": "[Context: {city}, {country}] ",
        "de": "[Kontext: {city}, {country}] ",
    },
    "lib_context_country": {
        "nb": "[Kontekst: {country}] ",
        "en": "[Context: {country}] ",
        "de": "[Kontext: {country}] ",
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
        "nb": "Refleks {reflex_id} lagt til: \"{phrase}\". Aktivt fra nå av.",
        "en": "Reflex {reflex_id} added: \"{phrase}\". Active immediately.",
        "de": "Reflex {reflex_id} hinzugefügt: \"{phrase}\". Ab sofort aktiv.",
    },
    "reflex_rejected": {
        "nb": "Forslag avvist: \"{phrase}\".",
        "en": "Proposal rejected: \"{phrase}\".",
        "de": "Vorschlag abgelehnt: \"{phrase}\".",
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
}
