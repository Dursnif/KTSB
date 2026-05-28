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
        "nb": "vis_hendelse krever 'event_id'. Bruk action='analyser' for å se tilgjengelige event_id-er.",
        "en": "vis_hendelse requires 'event_id'. Use action='analyser' to see available event_ids.",
        "de": "vis_hendelse erfordert 'event_id'. Verwende action='analyser', um verfügbare event_ids zu sehen.",
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
        "nb": "oppdater_hus krever 'felt' og 'verdi'.",
        "en": "oppdater_hus requires 'felt' and 'verdi'.",
        "de": "oppdater_hus erfordert 'felt' und 'verdi'.",
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
        "nb": "Frøken Library er deaktivert. Aktiver den under Innstillinger → LLM/Modeller.",
        "en": "Miss Library is disabled. Enable it under Settings → LLM/Models.",
        "de": "Fräulein Library ist deaktiviert. Aktiviere sie unter Einstellungen → LLM/Modelle.",
    },
    "lib_online_no_answer": {
        "nb": "Frøken Library Online fant ingen svar.",
        "en": "Miss Library Online found no answer.",
        "de": "Fräulein Library Online hat keine Antwort gefunden.",
    },
    "lib_online_unavailable": {
        "nb": "Frøken Library Online ikke tilgjengelig: {error}",
        "en": "Miss Library Online not available: {error}",
        "de": "Fräulein Library Online nicht verfügbar: {error}",
    },
    "lib_no_answer": {
        "nb": "Frøken Library fant ingenting.",
        "en": "Miss Library found nothing.",
        "de": "Fräulein Library hat nichts gefunden.",
    },
    "lib_unavailable": {
        "nb": "Frøken Library ikke tilgjengelig: {error}",
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
        "nb": "Frøken Library fant ingenting på den adressen.",
        "en": "Miss Library found nothing at that address.",
        "de": "Fräulein Library hat an dieser Adresse nichts gefunden.",
    },
    "lib_url_error": {
        "nb": "Kunne ikke hente URL: {error}",
        "en": "Could not fetch URL: {error}",
        "de": "URL konnte nicht abgerufen werden: {error}",
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
}
