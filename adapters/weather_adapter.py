"""
Fetches weather forecasts from multiple providers.

Supported providers (set in configs/settings.yaml → weather.provider):
  met.no        — Meteorologisk institutt Locationforecast 2.0 (free, Norway-optimised)
  open-meteo    — Open-Meteo (free, global, no API key)
  openweathermap — OpenWeatherMap (free tier, global, API key required)
  weatherapi    — WeatherAPI.com (free tier, global, API key required)
  pirateweather — PirateWeather (free tier with key, global, Dark Sky-compatible)

Default location: from configs/settings.yaml → lokasjon.
Named location: geocoded via Nominatim → lat/lon → selected provider.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from kaare_core.tools.executor_camera import describe_snapshot

log = logging.getLogger(__name__)

_SETTINGS_PATH    = Path("/kaare/configs/settings.yaml")
_WEATHER_ENV_PATH = Path("/kaare/configs/weather.env")
_HA_TOKEN_PATH    = Path("/kaare/configs/ha_token.env")
_NOMINATIM_URL    = "https://nominatim.openstreetmap.org/search"
_USER_AGENT       = "Kaare-AI/1.0 github.com/kaare-ai"

_MET_URL           = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
_MET_ALERTS_URL    = "https://api.met.no/weatherapi/metalerts/2.0/current.json"
_MET_AQ_URL        = "https://api.met.no/weatherapi/airqualityforecast/0.1/"
_OPEN_METEO_URL    = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_OWM_WEATHER_URL   = "https://api.openweathermap.org/data/2.5/weather"
_OWM_FORECAST_URL  = "https://api.openweathermap.org/data/2.5/forecast"
_OWM_AQ_URL        = "https://api.openweathermap.org/data/2.5/air_pollution"
_WAPI_FORECAST_URL = "https://api.weatherapi.com/v1/forecast.json"
_PIRATE_URL             = "https://api.pirateweather.net/forecast"
_MET_SUNRISE_URL        = "https://api.met.no/weatherapi/sunrise/3.0/sun"
_KARTVERKET_TIDE_URL    = "https://vannstand.kartverket.no/tideapi.php"
_STORMGLASS_TIDE_URL    = "https://api.stormglass.io/v2/tide/extremes/point"


# ── i18n tables ────────────────────────────────────────────────────────────────

_T: dict[str, dict[str, str]] = {
    "day_0": {"nb": "I dag",      "en": "Today",              "de": "Heute"},
    "day_1": {"nb": "I morgen",   "en": "Tomorrow",           "de": "Morgen"},
    "day_2": {"nb": "Overmorgen", "en": "Day after tomorrow", "de": "Übermorgen"},
    "day_3": {"nb": "Om 3 dager", "en": "In 3 days",          "de": "In 3 Tagen"},
    "day_4": {"nb": "Om 4 dager", "en": "In 4 days",          "de": "In 4 Tagen"},

    "header":        {"nb": "Vær for {name} ({provider}):", "en": "Weather for {name} ({provider}):", "de": "Wetter für {name} ({provider}):"},
    "now":           {"nb": "Nå",      "en": "Now",    "de": "Jetzt"},
    "source":        {"nb": "Kilde",   "en": "Source", "de": "Quelle"},
    "unknown":       {"nb": "ukjent",  "en": "unknown","de": "unbekannt"},

    "wind_ms":       {"nb": "vind {speed:.0f} m/s",    "en": "wind {speed:.0f} m/s",   "de": "Wind {speed:.0f} m/s"},
    "wind_kmh":      {"nb": "vind {speed:.0f} km/t",   "en": "wind {speed:.0f} km/h",  "de": "Wind {speed:.0f} km/h"},
    "wind_from_ms":  {"nb": "vind {speed:.0f} m/s fra {dir}", "en": "wind {speed:.0f} m/s from {dir}", "de": "Wind {speed:.0f} m/s aus {dir}"},
    "wind_from_kmh": {"nb": "vind {speed:.0f} km/t fra {dir}", "en": "wind {speed:.0f} km/h from {dir}", "de": "Wind {speed:.0f} km/h aus {dir}"},
    "wind_gust_ms":  {"nb": "kast {gust:.0f} m/s", "en": "gusts {gust:.0f} m/s", "de": "Böen {gust:.0f} m/s"},
    "wind_gust_kmh": {"nb": "kast {gust:.0f} km/t", "en": "gusts {gust:.0f} km/h", "de": "Böen {gust:.0f} km/h"},
    "precip_last_hour": {"nb": "siste time {mm:.1f} mm", "en": "last hour {mm:.1f} mm", "de": "letzte Stunde {mm:.1f} mm"},
    "precip_today":     {"nb": "i dag {mm:.1f} mm",    "en": "today {mm:.1f} mm",     "de": "heute {mm:.1f} mm"},
    "precip":        {"nb": "{mm:.1f} mm nedbør",      "en": "{mm:.1f} mm precipitation", "de": "{mm:.1f} mm Niederschlag"},
    "precip_little": {"nb": "lite nedbør",              "en": "little precipitation",       "de": "wenig Niederschlag"},
    "feels_like":    {"nb": "føles som {temp:.0f} °C", "en": "feels like {temp:.0f} °C",  "de": "gefühlt {temp:.0f} °C"},
    "uv_low":        {"nb": "UV: {val} (lav)",         "en": "UV: {val} (low)",            "de": "UV: {val} (niedrig)"},
    "uv_moderate":   {"nb": "UV: {val} (moderat)",     "en": "UV: {val} (moderate)",       "de": "UV: {val} (mäßig)"},
    "uv_high":       {"nb": "UV: {val} (høy)",         "en": "UV: {val} (high)",           "de": "UV: {val} (hoch)"},
    "uv_very_high":  {"nb": "UV: {val} (svært høy)",   "en": "UV: {val} (very high)",      "de": "UV: {val} (sehr hoch)"},
    "uv_extreme":    {"nb": "UV: {val} (ekstrem)",     "en": "UV: {val} (extreme)",        "de": "UV: {val} (extrem)"},
    "sunrise":       {"nb": "sol opp {time}",          "en": "sunrise {time}",             "de": "Sonnenaufgang {time}"},
    "sunset":        {"nb": "sol ned {time}",           "en": "sunset {time}",              "de": "Sonnenuntergang {time}"},
    "tide_header":      {"nb": "Tidevann ({name}):",   "en": "Tides ({name}):",            "de": "Gezeiten ({name}):"},
    "tide_high":        {"nb": "høyvann {time} ({m:.1f} m)",  "en": "high {time} ({m:.1f} m)",    "de": "Hochwasser {time} ({m:.1f} m)"},
    "tide_low":         {"nb": "lavvann {time} ({m:.1f} m)",  "en": "low {time} ({m:.1f} m)",     "de": "Niedrigwasser {time} ({m:.1f} m)"},
    "tide_no_station":  {"nb": "Ingen tidevannsstasjon funnet i nærheten.", "en": "No tide station found nearby.", "de": "Keine Gezeitenstation in der Nähe gefunden."},
    "tide_no_key":      {"nb": "Stormglass API-nøkkel ikke satt.", "en": "Stormglass API key not set.", "de": "Stormglass API-Schlüssel nicht gesetzt."},

    "alert_prefix":  {"nb": "⚠️ Farevarsel",       "en": "⚠️ Weather alert",    "de": "⚠️ Wetterwarnung"},
    "alert_minor":   {"nb": "gult varsel",          "en": "yellow warning",     "de": "gelbe Warnung"},
    "alert_moderate":{"nb": "oransje varsel",       "en": "orange warning",     "de": "orange Warnung"},
    "alert_severe":  {"nb": "rødt varsel",          "en": "red warning",        "de": "rote Warnung"},
    "alert_extreme": {"nb": "EKSTREMT VARSEL",      "en": "EXTREME WARNING",    "de": "EXTREME WARNUNG"},

    "err_met_fetch":    {"nb": "Klarte ikke å hente værvarselet fra met.no.",
                         "en": "Could not fetch weather forecast from met.no.",
                         "de": "Wetterbericht von met.no konnte nicht abgerufen werden."},
    "err_met_empty":    {"nb": "Fikk tomt svar fra met.no.",
                         "en": "Received empty response from met.no.",
                         "de": "Leere Antwort von met.no erhalten."},
    "err_openmeteo":    {"nb": "Klarte ikke å hente værvarselet fra Open-Meteo.",
                         "en": "Could not fetch weather forecast from Open-Meteo.",
                         "de": "Wetterbericht von Open-Meteo konnte nicht abgerufen werden."},
    "err_owm_fetch":    {"nb": "Klarte ikke å hente værvarselet fra OpenWeatherMap.",
                         "en": "Could not fetch weather forecast from OpenWeatherMap.",
                         "de": "Wetterbericht von OpenWeatherMap konnte nicht abgerufen werden."},
    "err_wapi_fetch":   {"nb": "Klarte ikke å hente værvarselet fra WeatherAPI.",
                         "en": "Could not fetch weather forecast from WeatherAPI.",
                         "de": "Wetterbericht von WeatherAPI konnte nicht abgerufen werden."},
    "err_pirate_fetch": {"nb": "Klarte ikke å hente værvarselet fra PirateWeather.",
                         "en": "Could not fetch weather forecast from PirateWeather.",
                         "de": "Wetterbericht von PirateWeather konnte nicht abgerufen werden."},
    "err_owm_key":      {"nb": "OpenWeatherMap krever en API-nøkkel. Gå til Innstillinger → Nettsøk og vær og legg inn nøkkelen.",
                         "en": "OpenWeatherMap requires an API key. Go to Settings → Web search & weather to add the key.",
                         "de": "OpenWeatherMap benötigt einen API-Schlüssel. Gehe zu Einstellungen → Websuche & Wetter."},
    "err_wapi_key":     {"nb": "WeatherAPI krever en API-nøkkel. Gå til Innstillinger → Nettsøk og vær og legg inn nøkkelen.",
                         "en": "WeatherAPI requires an API key. Go to Settings → Web search & weather to add the key.",
                         "de": "WeatherAPI benötigt einen API-Schlüssel. Gehe zu Einstellungen → Websuche & Wetter."},
    "err_pirate_key":   {"nb": "PirateWeather krever en API-nøkkel. Registrer gratis på pirate-weather.apiable.io.",
                         "en": "PirateWeather requires an API key. Register for free at pirate-weather.apiable.io.",
                         "de": "PirateWeather benötigt einen API-Schlüssel. Kostenlos registrieren unter pirate-weather.apiable.io."},
    "err_geocode":      {"nb": "Fant ikke stedet '{name}' — prøv et mer nøyaktig stedsnavn.",
                         "en": "Could not find location '{name}' — try a more specific place name.",
                         "de": "Ort '{name}' nicht gefunden — versuche einen genaueren Ortsnamen."},
    "err_no_location":  {"nb": "Lokasjon er ikke konfigurert. Gå til Innstillinger → Generelt og sett bredde- og lengdegrad.",
                         "en": "Location is not configured. Go to Settings → General to set latitude and longitude.",
                         "de": "Standort ist nicht konfiguriert. Gehe zu Einstellungen → Allgemein."},
    "err_weather":      {"nb": "Klarte ikke å hente værvarselet.", "en": "Could not fetch the weather forecast.", "de": "Wettervorhersage konnte nicht abgerufen werden."},

    "local_sensors_header":      {"nb": "Ute akkurat nå (lokale sensorer)",
                                   "en": "Outdoors now (local sensors)",
                                   "de": "Draußen jetzt (lokale Sensoren)"},
    "local_sensors_unavailable": {"nb": "(lokale sensorer utilgjengelige)",
                                   "en": "(local sensors unavailable)",
                                   "de": "(lokale Sensoren nicht verfügbar)"},

    "cam_weather_header":        {"nb": "Visuelt værbilde (kamera)",
                                   "en": "Visual weather view (camera)",
                                   "de": "Visuelles Wetterbild (Kamera)"},
    "cam_weather_unavailable":   {"nb": "(kameravisning utilgjengelig)",
                                   "en": "(camera view unavailable)",
                                   "de": "(Kameraansicht nicht verfügbar)"},
    "cam_weather_prompt":        {
        "nb": "Se på dette bildet og beskriv kort hva slags vær det ser ut til å være. "
              "Fokuser på: himmel, skyer, lys, nedbør, vind (trær/planter), sikt. "
              "Svar på norsk i 1–2 setninger. Ikke nevn kameraet eller bildet.",
        "en": "Look at this image and briefly describe what the weather appears to be. "
              "Focus on: sky, clouds, light, precipitation, wind (trees/plants), visibility. "
              "Reply in English in 1–2 sentences. Do not mention the camera or the image.",
        "de": "Sieh dir dieses Bild an und beschreibe kurz das Wetter. "
              "Achte auf: Himmel, Wolken, Licht, Niederschlag, Wind (Bäume/Pflanzen), Sicht. "
              "Antworte auf Deutsch in 1–2 Sätzen. Erwähne weder die Kamera noch das Bild.",
    },

    "air_quality":     {"nb": "Luftkvalitet",  "en": "Air quality",  "de": "Luftqualität"},
    "aqi_good":        {"nb": "god",           "en": "good",         "de": "gut"},
    "aqi_fair":        {"nb": "akseptabel",    "en": "fair",         "de": "mäßig"},
    "aqi_moderate":    {"nb": "moderat",       "en": "moderate",     "de": "moderat"},
    "aqi_poor":        {"nb": "dårlig",        "en": "poor",         "de": "schlecht"},
    "aqi_very_poor":   {"nb": "svært dårlig",  "en": "very poor",    "de": "sehr schlecht"},
    "aqi_hazardous":   {"nb": "farlig",        "en": "hazardous",    "de": "gefährlich"},
    "aqi_norway_only": {"nb": " (kun Norge)",  "en": " (Norway only)", "de": " (nur Norwegen)"},
}

_WIND_DIRECTIONS: dict[str, list[str]] = {
    "nb": ["N","NNØ","NØ","ØNØ","Ø","ØSØ","SØ","SSØ","S","SSV","SV","VSV","V","VNV","NV","NNV"],
    "en": ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"],
    "de": ["N","NNO","NO","ONO","O","OSO","SO","SSO","S","SSW","SW","WSW","W","WNW","NW","NNW"],
}

_MET_SYMBOLS: dict[str, dict[str, str]] = {
    "nb": {
        "clearsky": "klarvær", "fair": "lettskyet", "partlycloudy": "delvis skyet",
        "cloudy": "overskyet", "fog": "tåke",
        "lightrain": "lett regn", "rain": "regn", "heavyrain": "kraftig regn",
        "lightrainshowers": "lette regnbyger", "rainshowers": "regnbyger",
        "heavyrainshowers": "kraftige regnbyger",
        "lightsleet": "lett sludd", "sleet": "sludd", "sleetshowers": "sluddbyger",
        "lightsnow": "lett snø", "snow": "snø", "heavysnow": "kraftig snø",
        "snowshowers": "snøbyger", "lightsnowshowers": "lette snøbyger",
        "thunder": "torden", "rainandthunder": "regn og torden",
        "snowandthunder": "snø og torden",
    },
    "en": {
        "clearsky": "clear sky", "fair": "mostly clear", "partlycloudy": "partly cloudy",
        "cloudy": "cloudy", "fog": "fog",
        "lightrain": "light rain", "rain": "rain", "heavyrain": "heavy rain",
        "lightrainshowers": "light rain showers", "rainshowers": "rain showers",
        "heavyrainshowers": "heavy rain showers",
        "lightsleet": "light sleet", "sleet": "sleet", "sleetshowers": "sleet showers",
        "lightsnow": "light snow", "snow": "snow", "heavysnow": "heavy snow",
        "snowshowers": "snow showers", "lightsnowshowers": "light snow showers",
        "thunder": "thunder", "rainandthunder": "rain and thunder",
        "snowandthunder": "snow and thunder",
    },
    "de": {
        "clearsky": "Klarer Himmel", "fair": "Überwiegend klar", "partlycloudy": "Teilweise bewölkt",
        "cloudy": "Bewölkt", "fog": "Nebel",
        "lightrain": "Leichter Regen", "rain": "Regen", "heavyrain": "Starker Regen",
        "lightrainshowers": "Leichte Regenschauer", "rainshowers": "Regenschauer",
        "heavyrainshowers": "Starke Regenschauer",
        "lightsleet": "Leichter Schneeregen", "sleet": "Schneeregen",
        "sleetshowers": "Schneeregenschauer",
        "lightsnow": "Leichter Schnee", "snow": "Schnee", "heavysnow": "Starker Schnee",
        "snowshowers": "Schneeschauer", "lightsnowshowers": "Leichte Schneeschauer",
        "thunder": "Donner", "rainandthunder": "Regen und Donner",
        "snowandthunder": "Schnee und Donner",
    },
}

_WMO_LABELS: dict[str, dict[int, str]] = {
    "nb": {
        0: "klarvær", 1: "nesten klart", 2: "delvis skyet", 3: "overskyet",
        45: "tåke", 48: "rimtåke",
        51: "lett yr", 53: "yr", 55: "tett yr",
        56: "frysende yr", 57: "tett frysende yr",
        61: "lett regn", 63: "regn", 65: "kraftig regn",
        66: "frysende regn", 67: "kraftig frysende regn",
        71: "lett snø", 73: "snø", 75: "kraftig snø", 77: "snøkorn",
        80: "regnbyger", 81: "kraftige regnbyger", 82: "svært kraftige regnbyger",
        85: "snøbyger", 86: "kraftige snøbyger",
        95: "torden", 96: "torden med hagl", 99: "torden med kraftig hagl",
    },
    "en": {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "rime fog",
        51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
        56: "freezing drizzle", 57: "dense freezing drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain",
        66: "freezing rain", 67: "heavy freezing rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
        80: "rain showers", 81: "heavy rain showers", 82: "very heavy rain showers",
        85: "snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
    },
    "de": {
        0: "Klarer Himmel", 1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bedeckt",
        45: "Nebel", 48: "Reifnebel",
        51: "Leichter Nieselregen", 53: "Nieselregen", 55: "Dichter Nieselregen",
        56: "Gefrierender Nieselregen", 57: "Dichter gefrierender Nieselregen",
        61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
        66: "Gefrierender Regen", 67: "Starker gefrierender Regen",
        71: "Leichter Schnee", 73: "Schnee", 75: "Starker Schnee", 77: "Schneekörner",
        80: "Regenschauer", 81: "Starke Regenschauer", 82: "Sehr starke Regenschauer",
        85: "Schneeschauer", 86: "Starke Schneeschauer",
        95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit starkem Hagel",
    },
}

_DAY_KEYS = ["day_0", "day_1", "day_2", "day_3", "day_4"]

_API_LANG_MAP  = {"nb": "no", "en": "en", "de": "de"}
_PIRATE_LANG_MAP = {"nb": "no", "en": "en", "de": "de"}


def _t(key: str, lang: str, **kwargs) -> str:
    s = _T.get(key, {}).get(lang) or _T.get(key, {}).get("nb") or key
    return s.format(**kwargs) if kwargs else s


def _wind_direction(degrees: float, lang: str) -> str:
    dirs = _WIND_DIRECTIONS.get(lang, _WIND_DIRECTIONS["nb"])
    return dirs[int((degrees + 11.25) / 22.5) % 16]


def _met_symbol(code: str, lang: str) -> str:
    code = code.replace("_day", "").replace("_night", "").replace("_polartwilight", "")
    table = _MET_SYMBOLS.get(lang, _MET_SYMBOLS["nb"])
    return table.get(code, code)


def _wmo_label(code: int | None, lang: str) -> str:
    if code is None:
        return _t("unknown", lang)
    table = _WMO_LABELS.get(lang, _WMO_LABELS["nb"])
    return table.get(int(code), f"code {int(code)}")


def _day_name(i: int, lang: str) -> str:
    if i < len(_DAY_KEYS):
        return _t(_DAY_KEYS[i], lang)
    return f"+{i}d"


def _uv_label(uv: float | None, lang: str) -> str | None:
    if uv is None:
        return None
    v = round(uv, 1)
    if v <= 2:
        return _t("uv_low", lang, val=v)
    if v <= 5:
        return _t("uv_moderate", lang, val=v)
    if v <= 7:
        return _t("uv_high", lang, val=v)
    if v <= 10:
        return _t("uv_very_high", lang, val=v)
    return _t("uv_extreme", lang, val=v)


def _aqi_label(value: float, scale: str, lang: str) -> str:
    """
    Convert a numeric AQI value to a localised quality label.

    scale:
      'eu'  — European AQI 0–500+ (Open-Meteo)
      'owm' — OWM scale 1–5
      'epa' — US EPA index 1–6 (WeatherAPI)
      'no'  — Norwegian met.no index 1–4
    """
    if scale == "owm":
        keys = ["aqi_good", "aqi_fair", "aqi_moderate", "aqi_poor", "aqi_very_poor"]
        return _t(keys[min(int(value) - 1, 4)], lang)
    if scale == "epa":
        keys = ["aqi_good", "aqi_fair", "aqi_moderate", "aqi_poor", "aqi_very_poor", "aqi_hazardous"]
        return _t(keys[min(int(value) - 1, 5)], lang)
    if scale == "no":
        keys = ["aqi_good", "aqi_moderate", "aqi_poor", "aqi_very_poor"]
        return _t(keys[min(int(value) - 1, 3)], lang)
    # eu: 0–500+
    v = float(value)
    if v <= 20:  return _t("aqi_good", lang)
    if v <= 40:  return _t("aqi_fair", lang)
    if v <= 60:  return _t("aqi_moderate", lang)
    if v <= 80:  return _t("aqi_poor", lang)
    if v <= 100: return _t("aqi_very_poor", lang)
    return _t("aqi_hazardous", lang)


# ── Config helpers ─────────────────────────────────────────────────────────────

def _read_weather_config() -> dict:
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        wcfg = s.get("weather", {})
        return {
            "provider":        wcfg.get("provider", "met.no"),
            "forecast_days":   int(wcfg.get("forecast_days", 2)),
            "show_feels_like": bool(wcfg.get("show_feels_like", False)),
            "show_uv_index":   bool(wcfg.get("show_uv_index", False)),
            "show_sun_times":  bool(wcfg.get("show_sun_times", False)),
            "show_alerts":     bool(wcfg.get("show_alerts", True)),
            "show_air_quality": bool(wcfg.get("show_air_quality", False)),
            "use_ha_sensors":           bool(wcfg.get("use_ha_sensors", False)),
            "ha_temp_entity":           wcfg.get("ha_temp_entity", "").strip(),
            "ha_wind_entity":           wcfg.get("ha_wind_entity", "").strip(),
            "ha_wind_gust_entity":      wcfg.get("ha_wind_gust_entity", "").strip(),
            "ha_wind_direction_entity": wcfg.get("ha_wind_direction_entity", "").strip(),
            "ha_precip_entity":         wcfg.get("ha_precip_entity", "").strip(),
            "ha_precip_last_hour_entity": wcfg.get("ha_precip_last_hour_entity", "").strip(),
            "ha_precip_today_entity":   wcfg.get("ha_precip_today_entity", "").strip(),
            "ha_humidity_entity":       wcfg.get("ha_humidity_entity", "").strip(),
            "ha_pressure_entity":       wcfg.get("ha_pressure_entity", "").strip(),
            "show_tides":               bool(wcfg.get("show_tides", False)),
            "tide_provider":            wcfg.get("tide_provider", "auto"),
            "stormglass_key":           _read_api_key("stormglass"),
            "use_camera_for_weather":   bool(wcfg.get("use_camera_for_weather", False)),
            "weather_camera":           wcfg.get("weather_camera", "").strip(),
        }
    except Exception:
        return {
            "provider": "met.no", "forecast_days": 2,
            "show_feels_like": False, "show_uv_index": False,
            "show_sun_times": False, "show_alerts": True,
            "show_air_quality": False, "use_ha_sensors": False,
            "ha_temp_entity": "", "ha_wind_entity": "",
            "ha_wind_gust_entity": "", "ha_wind_direction_entity": "",
            "ha_precip_entity": "", "ha_precip_last_hour_entity": "",
            "ha_precip_today_entity": "", "ha_humidity_entity": "",
            "ha_pressure_entity": "",
            "show_tides": False, "tide_provider": "auto", "stormglass_key": "",
            "use_camera_for_weather": False, "weather_camera": "",
        }


def _read_api_key(provider: str) -> str:
    key_map = {
        "openweathermap": "OPENWEATHERMAP_API_KEY",
        "weatherapi":     "WEATHERAPI_KEY",
        "pirateweather":  "PIRATEWEATHER_API_KEY",
        "stormglass":     "STORMGLASS_API_KEY",
    }
    env_var = key_map.get(provider, "")
    if not env_var:
        return ""
    val = os.getenv(env_var, "")
    if val:
        return val
    if _WEATHER_ENV_PATH.exists():
        for line in _WEATHER_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == env_var:
                    return v.strip()
    return ""


def _read_ha_config() -> tuple[str, str]:
    """Return (ha_base_url, ha_token). Either may be empty if not configured."""
    ha_url = ""
    token  = ""
    try:
        from kaare_core.config import get_service as _svc
        ha_url = (_svc("home_assistant", "url") or "").rstrip("/")
    except Exception:
        pass
    try:
        for line in _HA_TOKEN_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == "HA_TOKEN":
                    token = v.strip()
                    break
    except Exception:
        pass
    return ha_url, token


async def _fetch_ha_sensors(cfg: dict, lang: str) -> str | None:
    """
    Fetch current readings from the configured HA sensor entities.

    Wind: speed + optional gust + optional direction → one composed token.
    Precipitation: now + optional last-hour + optional today → one composed token.
    Falls back gracefully — a missing or unavailable entity is simply skipped.
    """
    entities = {k: v for k, v in {
        "temp":              cfg.get("ha_temp_entity", ""),
        "wind_speed":        cfg.get("ha_wind_entity", ""),
        "wind_gust":         cfg.get("ha_wind_gust_entity", ""),
        "wind_dir":          cfg.get("ha_wind_direction_entity", ""),
        "precip_now":        cfg.get("ha_precip_entity", ""),
        "precip_last_hour":  cfg.get("ha_precip_last_hour_entity", ""),
        "precip_today":      cfg.get("ha_precip_today_entity", ""),
        "humidity":          cfg.get("ha_humidity_entity", ""),
        "pressure":          cfg.get("ha_pressure_entity", ""),
    }.items() if v}

    if not entities:
        return None

    ha_url, token = _read_ha_config()
    if not ha_url or not token:
        log.warning("[weather] HA sensor fetch skipped: ha_url or token not configured")
        return _t("local_sensors_unavailable", lang)

    headers = {"Authorization": f"Bearer {token}"}
    results: dict[str, tuple[str, str]] = {}  # key → (state_value, unit)

    try:
        async with httpx.AsyncClient(timeout=4.0, headers=headers) as client:
            tasks = {
                key: client.get(f"{ha_url}/api/states/{entity_id}")
                for key, entity_id in entities.items()
            }
            responses = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for key, resp in zip(tasks.keys(), responses):
            if isinstance(resp, Exception):
                log.warning("[weather] HA entity '%s' error: %s", entities[key], resp)
                continue
            if resp.status_code != 200:
                log.warning("[weather] HA entity '%s' returned HTTP %s", entities[key], resp.status_code)
                continue
            data  = resp.json()
            state = data.get("state", "")
            if state in ("unavailable", "unknown", ""):
                continue
            unit = data.get("attributes", {}).get("unit_of_measurement", "")
            results[key] = (state, unit)
    except Exception as exc:
        log.warning("[weather] HA sensor fetch failed: %s", exc)
        return _t("local_sensors_unavailable", lang)

    if not results:
        return _t("local_sensors_unavailable", lang)

    parts: list[str] = []

    # Temperature
    if "temp" in results:
        val, unit = results["temp"]
        try:
            parts.append(f"{float(val):.1f} {unit or '°C'}")
        except ValueError:
            parts.append(f"{val} {unit}".strip())

    # Wind — compose speed + direction + gust into one token
    wind_parts: list[str] = []
    if "wind_speed" in results:
        val, unit = results["wind_speed"]
        try:
            speed = float(val)
            kmh = unit in ("km/h", "kph")
            if "wind_dir" in results:
                direction = results["wind_dir"][0]
                wind_parts.append(
                    _t("wind_from_kmh", lang, speed=speed, dir=direction) if kmh
                    else _t("wind_from_ms", lang, speed=speed, dir=direction)
                )
            else:
                wind_parts.append(
                    _t("wind_kmh", lang, speed=speed) if kmh
                    else _t("wind_ms", lang, speed=speed)
                )
        except ValueError:
            wind_parts.append(f"{val} {unit}".strip())

    if "wind_gust" in results:
        val, unit = results["wind_gust"]
        try:
            gust = float(val)
            kmh = unit in ("km/h", "kph")
            gust_str = _t("wind_gust_kmh", lang, gust=gust) if kmh else _t("wind_gust_ms", lang, gust=gust)
            if wind_parts:
                wind_parts[0] = f"{wind_parts[0]} ({gust_str})"
            else:
                wind_parts.append(gust_str)
        except ValueError:
            pass

    if wind_parts:
        parts.append(wind_parts[0])

    # Precipitation — now + last hour + today, composed into one token
    precip_parts: list[str] = []
    if "precip_now" in results:
        val, _ = results["precip_now"]
        try:
            mm = float(val)
            precip_parts.append(_t("precip", lang, mm=mm) if mm > 0.05 else _t("precip_little", lang))
        except ValueError:
            precip_parts.append(val)

    if "precip_last_hour" in results:
        val, _ = results["precip_last_hour"]
        try:
            mm = float(val)
            precip_parts.append(_t("precip_last_hour", lang, mm=mm))
        except ValueError:
            precip_parts.append(val)

    if "precip_today" in results:
        val, _ = results["precip_today"]
        try:
            mm = float(val)
            precip_parts.append(_t("precip_today", lang, mm=mm))
        except ValueError:
            precip_parts.append(val)

    if precip_parts:
        parts.append(", ".join(precip_parts))

    # Humidity
    if "humidity" in results:
        val, unit = results["humidity"]
        try:
            parts.append(f"{float(val):.0f} {unit or '%'}")
        except ValueError:
            parts.append(f"{val} {unit}".strip())

    # Pressure
    if "pressure" in results:
        val, unit = results["pressure"]
        try:
            parts.append(f"{float(val):.0f} {unit or 'hPa'}")
        except ValueError:
            parts.append(f"{val} {unit}".strip())

    if not parts:
        return None

    header = _t("local_sensors_header", lang)
    return f"{header}: {', '.join(parts)}"


async def _fetch_camera_view(cfg: dict, lang: str) -> str | None:
    """
    Fetch a snapshot from the configured weather camera and return a VLM description.
    Delegates to executor_camera.describe_snapshot — single implementation, ok-checked.
    Returns None / fallback string if camera or VLM is unavailable. Never raises.
    """
    camera = cfg.get("weather_camera", "").strip()
    if not camera:
        return None
    try:
        prompt = _t("cam_weather_prompt", lang)
        description = await describe_snapshot(camera, prompt)
        if not description:
            return _t("cam_weather_unavailable", lang)
        return f"{_t('cam_weather_header', lang)}: {description}"
    except Exception as exc:
        log.warning("[weather] Camera view failed: %s", exc)
        return _t("cam_weather_unavailable", lang)


def _read_default_location() -> tuple[float, float, str]:
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        lok = s.get("location") or s.get("lokasjon", {})
        lat = float(lok["lat"])
        lon = float(lok["lon"])
        city = lok.get("city") or lok.get("sted", "ukjent")
        return lat, lon, city
    except Exception as exc:
        log.warning("[weather] Could not read settings.yaml: %s", exc)
        return 0.0, 0.0, ""


def _local_tz() -> ZoneInfo:
    try:
        from kaare_core.config import get_local_tz
        return get_local_tz()
    except Exception:
        return ZoneInfo("UTC")


_TIDE_STATION_CACHE: list[dict] | None = None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _ampm_to_24h(s: str) -> str:
    """Convert '06:43 AM' or '06:43 am' to '06:43'. Returns original string on parse error."""
    try:
        return datetime.strptime(s.strip(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        try:
            return datetime.strptime(s.strip(), "%I:%M%p").strftime("%H:%M")
        except ValueError:
            return s


# ── Geocoding ──────────────────────────────────────────────────────────────────

async def _geocode(location: str) -> tuple[float, float, str] | None:
    try:
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(
                _NOMINATIM_URL,
                params={"q": location, "format": "json", "limit": 1},
            )
            r.raise_for_status()
            hits = r.json()
            if not hits:
                return None
            h = hits[0]
            return float(h["lat"]), float(h["lon"]), h.get("display_name", location).split(",")[0]
    except Exception as exc:
        log.warning("[weather] Nominatim error for '%s': %s", location, exc)
        return None


# ── MetAlerts ──────────────────────────────────────────────────────────────────

async def _fetch_metalerts(lat: float, lon: float, lang: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(
                _MET_ALERTS_URL,
                params={"lat": round(lat, 4), "lon": round(lon, 4)},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] MetAlerts error: %s", exc)
        return []

    alerts = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        severity = props.get("severity", "")
        event    = props.get("event", "")
        sev_key  = {
            "Minor":    "alert_minor",
            "Moderate": "alert_moderate",
            "Severe":   "alert_severe",
            "Extreme":  "alert_extreme",
        }.get(severity)
        if sev_key:
            prefix = _t("alert_prefix", lang)
            sev    = _t(sev_key, lang)
            alerts.append(f"{prefix} ({sev}): {event}")
    return alerts


# ── Air quality helpers ────────────────────────────────────────────────────────

async def _fetch_aq_open_meteo(lat: float, lon: float, lang: str) -> str | None:
    """
    Fetch current air quality from Open-Meteo Air Quality API (free, no key).
    Returns European AQI label + PM2.5/PM10/NO₂/O₃ as a single formatted line,
    or None on error.
    """
    try:
        params = {
            "latitude":  round(lat, 4),
            "longitude": round(lon, 4),
            "current":   "european_aqi,pm2_5,pm10,nitrogen_dioxide,ozone",
        }
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_OPEN_METEO_AQ_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] Open-Meteo AQ error: %s", exc)
        return None

    current = data.get("current", {})
    aqi  = current.get("european_aqi")
    pm25 = current.get("pm2_5")
    pm10 = current.get("pm10")
    no2  = current.get("nitrogen_dioxide")
    o3   = current.get("ozone")

    if aqi is None:
        return None

    label      = _aqi_label(aqi, "eu", lang)
    label_part = f"{_t('air_quality', lang)}: {label} (EU AQI {round(aqi)})"
    pollutants = []
    if pm25 is not None: pollutants.append(f"PM2.5 {pm25:.1f}")
    if pm10 is not None: pollutants.append(f"PM10 {pm10:.1f}")
    if no2  is not None: pollutants.append(f"NO₂ {no2:.1f}")
    if o3   is not None: pollutants.append(f"O₃ {o3:.1f}")

    if pollutants:
        return f"{label_part} — {', '.join(pollutants)} μg/m³"
    return label_part


def _aq_line_from_owm_response(data: dict, lang: str) -> str | None:
    """Extract and format an AQ line from an already-parsed OWM air_pollution response."""
    items = data.get("list", [])
    if not items:
        return None
    comp = items[0].get("components", {})
    aqi  = items[0].get("main", {}).get("aqi")
    if aqi is None:
        return None
    pm25 = comp.get("pm2_5")
    pm10 = comp.get("pm10")
    no2  = comp.get("no2")
    o3   = comp.get("o3")
    label      = _aqi_label(aqi, "owm", lang)
    label_part = f"{_t('air_quality', lang)}: {label} (AQI {aqi}/5)"
    pollutants = []
    if pm25 is not None: pollutants.append(f"PM2.5 {pm25:.1f}")
    if pm10 is not None: pollutants.append(f"PM10 {pm10:.1f}")
    if no2  is not None: pollutants.append(f"NO₂ {no2:.1f}")
    if o3   is not None: pollutants.append(f"O₃ {o3:.1f}")
    if pollutants:
        return f"{label_part} — {', '.join(pollutants)} μg/m³"
    return label_part


async def _fetch_aq_owm(lat: float, lon: float, api_key: str, lang: str) -> str | None:
    """
    Fetch current air pollution from OpenWeatherMap Air Pollution API (free tier).
    Returns OWM AQI label + key pollutants as a single formatted line, or None on error.
    """
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(
                _OWM_AQ_URL,
                params={"lat": round(lat, 4), "lon": round(lon, 4), "appid": api_key},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] OWM AQ error: %s", exc)
        return None

    items = data.get("list", [])
    if not items:
        return None
    comp = items[0].get("components", {})
    aqi  = items[0].get("main", {}).get("aqi")
    pm25 = comp.get("pm2_5")
    pm10 = comp.get("pm10")
    no2  = comp.get("no2")
    o3   = comp.get("o3")

    if aqi is None:
        return None

    label      = _aqi_label(aqi, "owm", lang)
    label_part = f"{_t('air_quality', lang)}: {label} (AQI {aqi}/5)"
    pollutants = []
    if pm25 is not None: pollutants.append(f"PM2.5 {pm25:.1f}")
    if pm10 is not None: pollutants.append(f"PM10 {pm10:.1f}")
    if no2  is not None: pollutants.append(f"NO₂ {no2:.1f}")
    if o3   is not None: pollutants.append(f"O₃ {o3:.1f}")

    if pollutants:
        return f"{label_part} — {', '.join(pollutants)} μg/m³"
    return label_part


async def _fetch_aq_met_no(lat: float, lon: float, lang: str) -> str | None:
    """
    Fetch current air quality from met.no Airqualityforecast API.
    Norway-only coverage. Returns AQI label + key pollutants, or None on error.
    """
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(
                _MET_AQ_URL,
                params={"lat": round(lat, 4), "lon": round(lon, 4)},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] met.no AQ error: %s", exc)
        return None

    # The API returns {"time": "...", "variables": {...}} for the current hour.
    # Handle both a direct object and an array (defensive).
    entry: dict = {}
    if isinstance(data, list) and data:
        entry = data[0]
    elif isinstance(data, dict):
        entry = data

    variables = entry.get("variables", {})
    aqi_raw = variables.get("AQI", {})
    aqi  = aqi_raw.get("value") if isinstance(aqi_raw, dict) else aqi_raw
    pm25 = (variables.get("pm25", {}) or {}).get("value")
    pm10 = (variables.get("pm10", {}) or {}).get("value")
    no2  = (variables.get("no2",  {}) or {}).get("value")
    o3   = (variables.get("o3",   {}) or {}).get("value")

    if aqi is None:
        return None

    label       = _aqi_label(float(aqi), "no", lang)
    norway_note = _t("aqi_norway_only", lang)
    label_part  = f"{_t('air_quality', lang)}: {label}{norway_note}"
    pollutants  = []
    if pm25 is not None: pollutants.append(f"PM2.5 {float(pm25):.1f}")
    if pm10 is not None: pollutants.append(f"PM10 {float(pm10):.1f}")
    if no2  is not None: pollutants.append(f"NO₂ {float(no2):.1f}")
    if o3   is not None: pollutants.append(f"O₃ {float(o3):.1f}")

    if pollutants:
        return f"{label_part} — {', '.join(pollutants)} μg/m³"
    return label_part


# ── met.no Sunrise API ────────────────────────────────────────────────────────

async def _fetch_met_sunrise(lat: float, lon: float, date_str: str) -> tuple[str, str]:
    """Fetch sunrise and sunset for a single date. Returns (sunrise_hhmm, sunset_hhmm)."""
    try:
        params = {"lat": round(lat, 4), "lon": round(lon, 4), "date": date_str}
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_MET_SUNRISE_URL, params=params)
            r.raise_for_status()
            d = r.json()
        sr = d.get("properties", {}).get("sunrise", {}).get("time", "")
        ss = d.get("properties", {}).get("sunset", {}).get("time", "")
        return (sr[11:16] if len(sr) >= 16 else "", ss[11:16] if len(ss) >= 16 else "")
    except Exception as exc:
        log.debug("[weather] met.no sunrise error for %s: %s", date_str, exc)
        return ("", "")


# ── Tide helpers ───────────────────────────────────────────────────────────────

async def _load_kartverket_stations() -> list[dict]:
    """Fetch and cache Kartverket tide station list. Returns list of {code, name, lat, lon}."""
    global _TIDE_STATION_CACHE
    if _TIDE_STATION_CACHE is not None:
        return _TIDE_STATION_CACHE
    try:
        params = {"tide_request": "stationlist", "lang": "en", "filetype": "xml"}
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_KARTVERKET_TIDE_URL, params=params)
            r.raise_for_status()
        root = ET.fromstring(r.text)
        stations = []
        for loc in root.iter("location"):
            try:
                stations.append({
                    "code": loc.attrib["code"],
                    "name": loc.attrib.get("name", loc.attrib["code"]),
                    "lat":  float(loc.attrib["latitude"]),
                    "lon":  float(loc.attrib["longitude"]),
                })
            except (KeyError, ValueError):
                continue
        _TIDE_STATION_CACHE = stations
        log.debug("[tides] Kartverket station list loaded: %d stations", len(stations))
        return stations
    except Exception as exc:
        log.warning("[tides] Failed to load Kartverket station list: %s", exc)
        return []


def _nearest_station(lat: float, lon: float, stations: list[dict], max_km: float = 300.0) -> dict | None:
    """Return nearest station within max_km, or None."""
    best: dict | None = None
    best_dist = float("inf")
    for s in stations:
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        if d < best_dist:
            best_dist = d
            best = s
    return best if best and best_dist <= max_km else None


async def _fetch_kartverket_tides(station: dict, cfg: dict, lang: str) -> str | None:
    """Fetch high/low tide table from Kartverket using lat/lon. Values in cm → converted to m."""
    local_tz = _local_tz()
    now_local = datetime.now(tz=local_tz)
    from_time = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    to_time = from_time + timedelta(days=cfg["forecast_days"])
    try:
        params = {
            "tide_request": "locationdata",
            "lat":          round(station["lat"], 4),
            "lon":          round(station["lon"], 4),
            "fromtime":     from_time.strftime("%Y-%m-%dT%H:%M"),
            "totime":       to_time.strftime("%Y-%m-%dT%H:%M"),
            "datatype":     "tab",
            "refcode":      "cd",
            "lang":         "en",
            "dst":          1,
            "filetype":     "xml",
        }
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_KARTVERKET_TIDE_URL, params=params)
            r.raise_for_status()
        root = ET.fromstring(r.text)
        entries = []
        for wl in root.iter("waterlevel"):
            flag = wl.attrib.get("flag", "").lower()
            if flag not in ("high", "low"):
                continue
            time_str = wl.attrib.get("time", "")  # e.g. 2026-05-30T05:13:00+02:00
            try:
                val_cm = float(wl.attrib.get("value", "0"))
            except ValueError:
                continue
            # Extract HH:MM from ISO8601 with offset
            hhmm = time_str[11:16] if len(time_str) >= 16 else time_str
            entries.append((hhmm, flag, val_cm / 100.0))  # cm → m
        if not entries:
            return None
        parts = []
        for hhmm, flag, val_m in entries[:8]:  # cap at 8 events (~2 days)
            key = "tide_high" if flag == "high" else "tide_low"
            parts.append(_t(key, lang, time=hhmm, m=val_m))
        name = station.get("name", "")
        return f"{_t('tide_header', lang, name=name)} {', '.join(parts)}"
    except Exception as exc:
        log.warning("[tides] Kartverket fetch error: %s", exc)
        return None


async def _fetch_stormglass_tides(lat: float, lon: float, cfg: dict, lang: str, api_key: str) -> str | None:
    """Fetch tide extremes from Stormglass API."""
    local_tz = _local_tz()
    now_local = datetime.now(tz=local_tz)
    start_ts = int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end_ts = start_ts + cfg["forecast_days"] * 86400
    try:
        params = {"lat": round(lat, 4), "lng": round(lon, 4), "start": start_ts, "end": end_ts, "datum": "MSL"}
        async with httpx.AsyncClient(timeout=10.0, headers={"Authorization": api_key, "User-Agent": _USER_AGENT}) as client:
            r = await client.get(_STORMGLASS_TIDE_URL, params=params)
            r.raise_for_status()
            data = r.json()
        extremes = data.get("data", [])
        if not extremes:
            return None
        parts = []
        for ev in extremes[:8]:
            t_str = ev.get("time", "")[:16]  # ISO8601 → YYYY-MM-DDTHH:MM UTC
            try:
                t_utc = datetime.strptime(t_str, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
                hhmm = t_utc.astimezone(local_tz).strftime("%H:%M")
            except ValueError:
                hhmm = t_str[11:16]
            val = ev.get("height", 0.0)
            flag = ev.get("type", "")
            key = "tide_high" if flag == "High" else "tide_low"
            parts.append(_t(key, lang, time=hhmm, m=float(val)))
        if not parts:
            return None
        return f"{_t('tide_header', lang, name='Stormglass')} {', '.join(parts)}"
    except Exception as exc:
        log.warning("[tides] Stormglass fetch error: %s", exc)
        return None


async def _fetch_tides(lat: float, lon: float, lang: str, cfg: dict) -> str | None:
    """Fetch tide data using configured provider (auto / kartverket / stormglass)."""
    provider = cfg.get("tide_provider", "auto")
    sg_key   = cfg.get("stormglass_key", "")

    if provider == "kartverket":
        stations = await _load_kartverket_stations()
        station = _nearest_station(lat, lon, stations)
        if not station:
            return _t("tide_no_station", lang)
        return await _fetch_kartverket_tides(station, cfg, lang)

    if provider == "stormglass":
        if not sg_key:
            return _t("tide_no_key", lang)
        return await _fetch_stormglass_tides(lat, lon, cfg, lang, sg_key)

    # auto: try Kartverket first, fall back to Stormglass
    stations = await _load_kartverket_stations()
    station = _nearest_station(lat, lon, stations)
    if station:
        return await _fetch_kartverket_tides(station, cfg, lang)
    if sg_key:
        return await _fetch_stormglass_tides(lat, lon, cfg, lang, sg_key)
    return None


# ── met.no ────────────────────────────────────────────────────────────────────

def _met_summary(timeseries: list, from_utc: datetime, to_utc: datetime, lang: str) -> str | None:
    points = [
        t for t in timeseries
        if from_utc <= datetime.fromisoformat(t["time"].replace("Z", "+00:00")) < to_utc
    ]
    if not points:
        return None
    temps, wind_speeds, precip, symbols = [], [], [], []
    for p in points:
        d = p["data"]
        inst = d.get("instant", {}).get("details", {})
        if "air_temperature" in inst:
            temps.append(inst["air_temperature"])
        if "wind_speed" in inst:
            wind_speeds.append(inst["wind_speed"])
        for block in ("next_6_hours", "next_1_hours"):
            if block in d:
                det = d[block].get("details", {})
                if "precipitation_amount" in det:
                    precip.append(det["precipitation_amount"])
                sym = d[block].get("summary", {}).get("symbol_code", "")
                if sym:
                    symbols.append(sym)
                break
    parts = []
    if temps:
        parts.append(f"{min(temps):.0f}–{max(temps):.0f} °C")
    if precip:
        tot = sum(precip)
        parts.append(_t("precip", lang, mm=tot) if tot > 0.1 else _t("precip_little", lang))
    if wind_speeds:
        parts.append(_t("wind_ms", lang, speed=sum(wind_speeds) / len(wind_speeds)))
    if symbols:
        vanligst = Counter(symbols).most_common(1)[0][0]
        parts.insert(0, _met_symbol(vanligst, lang))
    return ", ".join(parts) if parts else None


async def _fetch_met_no(lat: float, lon: float, display_name: str,
                        forecast_days: int, lang: str, cfg: dict) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_MET_URL, params={"lat": round(lat, 4), "lon": round(lon, 4)})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] met.no error: %s", exc)
        return _t("err_met_fetch", lang)

    ts = data.get("properties", {}).get("timeseries", [])
    if not ts:
        return _t("err_met_empty", lang)

    # Fetch alerts, air quality and sunrise in parallel when enabled
    alerts: list[str]       = []
    aq_line: str | None      = None
    sunrise_data: list[tuple[str, str]] = []  # list of (sunrise_hhmm, sunset_hhmm) per day
    fetch_alerts  = cfg["show_alerts"]
    fetch_aq      = cfg["show_air_quality"]
    fetch_sun     = cfg["show_sun_times"]

    local_tz = _local_tz()
    now_local = datetime.now(tz=local_tz)

    extra_coros: list = []
    extra_keys: list[str] = []
    if fetch_alerts:
        extra_coros.append(_fetch_metalerts(lat, lon, lang))
        extra_keys.append("alerts")
    if fetch_aq:
        extra_coros.append(_fetch_aq_met_no(lat, lon, lang))
        extra_keys.append("aq")
    if fetch_sun:
        for d in range(cfg["forecast_days"]):
            date_str = (now_local + timedelta(days=d)).strftime("%Y-%m-%d")
            extra_coros.append(_fetch_met_sunrise(lat, lon, date_str))
            extra_keys.append(f"sun_{d}")

    if extra_coros:
        results = await asyncio.gather(*extra_coros, return_exceptions=True)
        result_map = dict(zip(extra_keys, results))
        if fetch_alerts:
            v = result_map.get("alerts")
            alerts = v if isinstance(v, list) else []
        if fetch_aq:
            v = result_map.get("aq")
            aq_line = v if isinstance(v, str) else None
        if fetch_sun:
            for d in range(cfg["forecast_days"]):
                v = result_map.get(f"sun_{d}")
                sunrise_data.append(v if isinstance(v, tuple) else ("", ""))

    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_local.astimezone(timezone.utc)

    first    = ts[0]["data"]["instant"]["details"]
    now_temp = first.get("air_temperature")
    now_wind = first.get("wind_speed")
    now_dir  = first.get("wind_from_direction")
    now_uv   = first.get("ultraviolet_index_clear_sky")
    now_sym  = ""
    for block in ("next_1_hours", "next_6_hours"):
        if block in ts[0]["data"]:
            now_sym = _met_symbol(ts[0]["data"][block].get("summary", {}).get("symbol_code", ""), lang)
            break

    now_parts = []
    if now_sym:
        now_parts.append(now_sym)
    if now_temp is not None:
        now_parts.append(f"{now_temp:.0f} °C")
    if now_wind is not None:
        if now_dir is not None:
            now_parts.append(_t("wind_from_ms", lang, speed=now_wind, dir=_wind_direction(now_dir, lang)))
        else:
            now_parts.append(_t("wind_ms", lang, speed=now_wind))
    if cfg["show_uv_index"]:
        uv = _uv_label(now_uv, lang)
        if uv:
            now_parts.append(uv)

    lines = []
    for alert in alerts:
        lines.append(alert)
    lines.append(_t("header", lang, name=display_name, provider="met.no"))
    lines.append(f"{_t('now', lang)}: {', '.join(now_parts) or _t('unknown', lang)}")
    if aq_line:
        lines.append(aq_line)

    for i in range(min(forecast_days, len(_DAY_KEYS))):
        fra = day_start_utc + timedelta(days=i)
        til = fra + timedelta(days=1)
        summary = _met_summary(ts, fra, til, lang)
        sun_parts = []
        if fetch_sun and i < len(sunrise_data):
            sr, ss = sunrise_data[i]
            if sr:
                sun_parts.append(_t("sunrise", lang, time=sr))
            if ss:
                sun_parts.append(_t("sunset", lang, time=ss))
        day_line = f"{_day_name(i, lang)}: {summary}" if summary else _day_name(i, lang)
        if sun_parts:
            day_line += f", {', '.join(sun_parts)}"
        if summary or sun_parts:
            lines.append(day_line)

    lines.append(f"{_t('source', lang)}: met.no")
    return "\n".join(lines)


# ── Open-Meteo ────────────────────────────────────────────────────────────────

async def _fetch_open_meteo(lat: float, lon: float, display_name: str,
                             forecast_days: int, lang: str, cfg: dict) -> str:
    current_fields = ["temperature_2m", "weathercode", "windspeed_10m", "winddirection_10m"]
    daily_fields   = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum",
                      "weathercode", "windspeed_10m_max"]

    if cfg["show_feels_like"]:
        current_fields.append("apparent_temperature")
    if cfg["show_uv_index"]:
        daily_fields.append("uv_index_max")
    if cfg["show_sun_times"]:
        daily_fields += ["sunrise", "sunset"]

    forecast_params = {
        "latitude":      round(lat, 4),
        "longitude":     round(lon, 4),
        "current":       ",".join(current_fields),
        "daily":         ",".join(daily_fields),
        "timezone":      "auto",
        "forecast_days": forecast_days + 1,
    }

    # Run forecast and air quality in parallel when AQ is enabled
    aq_line: str | None = None
    try:
        if cfg["show_air_quality"]:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
                forecast_resp, aq_resp = await asyncio.gather(
                    client.get(_OPEN_METEO_URL, params=forecast_params),
                    client.get(
                        _OPEN_METEO_AQ_URL,
                        params={
                            "latitude":  round(lat, 4),
                            "longitude": round(lon, 4),
                            "current":   "european_aqi,pm2_5,pm10,nitrogen_dioxide,ozone",
                        },
                    ),
                    return_exceptions=True,
                )
            if isinstance(forecast_resp, Exception):
                raise forecast_resp
            forecast_resp.raise_for_status()
            data = forecast_resp.json()
            if not isinstance(aq_resp, Exception) and aq_resp.status_code == 200:
                aq_data    = aq_resp.json().get("current", {})
                aqi        = aq_data.get("european_aqi")
                pm25       = aq_data.get("pm2_5")
                pm10       = aq_data.get("pm10")
                no2        = aq_data.get("nitrogen_dioxide")
                o3         = aq_data.get("ozone")
                if aqi is not None:
                    label      = _aqi_label(aqi, "eu", lang)
                    label_part = f"{_t('air_quality', lang)}: {label} (EU AQI {round(aqi)})"
                    pollutants = []
                    if pm25 is not None: pollutants.append(f"PM2.5 {pm25:.1f}")
                    if pm10 is not None: pollutants.append(f"PM10 {pm10:.1f}")
                    if no2  is not None: pollutants.append(f"NO₂ {no2:.1f}")
                    if o3   is not None: pollutants.append(f"O₃ {o3:.1f}")
                    aq_line = (f"{label_part} — {', '.join(pollutants)} μg/m³"
                               if pollutants else label_part)
        else:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
                r = await client.get(_OPEN_METEO_URL, params=forecast_params)
                r.raise_for_status()
                data = r.json()
    except Exception as exc:
        log.warning("[weather] Open-Meteo error: %s", exc)
        return _t("err_openmeteo", lang)

    current = data.get("current", {})
    daily   = data.get("daily", {})

    now_temp     = current.get("temperature_2m")
    now_wind     = current.get("windspeed_10m")
    now_code     = current.get("weathercode")
    now_feels    = current.get("apparent_temperature")

    now_parts = []
    if now_code is not None:
        now_parts.append(_wmo_label(now_code, lang))
    if now_temp is not None:
        now_parts.append(f"{now_temp:.0f} °C")
    if cfg["show_feels_like"] and now_feels is not None:
        now_parts.append(_t("feels_like", lang, temp=now_feels))
    if now_wind is not None:
        now_parts.append(_t("wind_kmh", lang, speed=now_wind))

    times     = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precips   = daily.get("precipitation_sum", [])
    codes     = daily.get("weathercode", [])
    uv_vals   = daily.get("uv_index_max", [])
    sunrises  = daily.get("sunrise", [])
    sunsets   = daily.get("sunset", [])

    lines = [_t("header", lang, name=display_name, provider="Open-Meteo")]
    lines.append(f"{_t('now', lang)}: {', '.join(now_parts) or _t('unknown', lang)}")
    if aq_line:
        lines.append(aq_line)

    for i, _day in enumerate(times[:forecast_days]):
        parts = [_wmo_label(codes[i] if i < len(codes) else None, lang)]
        if i < len(min_temps) and i < len(max_temps):
            parts.append(f"{min_temps[i]:.0f}–{max_temps[i]:.0f} °C")
        if i < len(precips) and precips[i] is not None:
            p = precips[i]
            parts.append(_t("precip", lang, mm=p) if p > 0.1 else _t("precip_little", lang))
        if cfg["show_uv_index"] and i < len(uv_vals):
            uv = _uv_label(uv_vals[i], lang)
            if uv:
                parts.append(uv)
        if cfg["show_sun_times"]:
            if i < len(sunrises) and sunrises[i]:
                parts.append(_t("sunrise", lang, time=sunrises[i][11:16]))
            if i < len(sunsets) and sunsets[i]:
                parts.append(_t("sunset", lang, time=sunsets[i][11:16]))
        lines.append(f"{_day_name(i, lang)}: {', '.join(parts)}")

    lines.append(f"{_t('source', lang)}: Open-Meteo")
    return "\n".join(lines)


# ── OpenWeatherMap ────────────────────────────────────────────────────────────

async def _fetch_openweathermap(lat: float, lon: float, display_name: str,
                                 forecast_days: int, lang: str, cfg: dict) -> str:
    api_key = _read_api_key("openweathermap")
    if not api_key:
        return _t("err_owm_key", lang)

    try:
        base_params = {
            "lat": round(lat, 4), "lon": round(lon, 4),
            "appid": api_key, "units": "metric",
            "lang": _API_LANG_MAP.get(lang, "en"),
        }
        aq_params = {"lat": round(lat, 4), "lon": round(lon, 4), "appid": api_key}

        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            coros = [
                client.get(_OWM_WEATHER_URL, params=base_params),
                client.get(_OWM_FORECAST_URL, params={**base_params, "cnt": forecast_days * 8 + 1}),
            ]
            if cfg["show_air_quality"]:
                coros.append(client.get(_OWM_AQ_URL, params=aq_params))

            results = await asyncio.gather(*coros, return_exceptions=True)

        curr_r, fc_r = results[0], results[1]
        if isinstance(curr_r, Exception): raise curr_r
        if isinstance(fc_r,  Exception): raise fc_r
        curr_r.raise_for_status()
        fc_r.raise_for_status()
        curr = curr_r.json()
        fc   = fc_r.json()

        aq_line: str | None = None
        if cfg["show_air_quality"] and len(results) > 2:
            aq_r = results[2]
            if not isinstance(aq_r, Exception) and aq_r.status_code == 200:
                aq_line = await _aq_line_from_owm_response(aq_r.json(), lang)
    except Exception as exc:
        log.warning("[weather] OpenWeatherMap error: %s", exc)
        return _t("err_owm_fetch", lang)

    now_temp    = curr.get("main", {}).get("temp")
    now_wind    = curr.get("wind", {}).get("speed")
    now_feels   = curr.get("main", {}).get("feels_like")
    now_desc    = (curr.get("weather") or [{}])[0].get("description", _t("unknown", lang))
    owm_sys     = curr.get("sys", {})
    today_sr_ts = owm_sys.get("sunrise")
    today_ss_ts = owm_sys.get("sunset")

    now_parts = [now_desc]
    if now_temp is not None:
        now_parts.append(f"{now_temp:.0f} °C")
    if cfg["show_feels_like"] and now_feels is not None:
        now_parts.append(_t("feels_like", lang, temp=now_feels))
    if now_wind is not None:
        now_parts.append(_t("wind_ms", lang, speed=now_wind))

    from collections import defaultdict
    by_date: dict[str, list] = defaultdict(list)
    for item in fc.get("list", []):
        date = item["dt_txt"][:10]
        by_date[date].append(item)

    sorted_dates = sorted(by_date.keys())

    lines = [_t("header", lang, name=display_name, provider="OpenWeatherMap")]
    lines.append(f"{_t('now', lang)}: {', '.join(now_parts)}")
    if aq_line:
        lines.append(aq_line)

    local_tz = _local_tz()
    for i, date in enumerate(sorted_dates[:forecast_days]):
        items = by_date[date]
        temps = [it["main"]["temp"] for it in items if "main" in it]
        winds = [it["wind"]["speed"] for it in items if "wind" in it]
        rains = [it.get("rain", {}).get("3h", 0) for it in items]
        descs = [(it.get("weather") or [{}])[0].get("description", "") for it in items]
        parts = []
        if descs:
            parts.append(Counter(descs).most_common(1)[0][0])
        if temps:
            parts.append(f"{min(temps):.0f}–{max(temps):.0f} °C")
        tot_rain = sum(rains)
        if tot_rain > 0.1:
            parts.append(_t("precip", lang, mm=tot_rain))
        if winds:
            parts.append(_t("wind_ms", lang, speed=sum(winds) / len(winds)))
        # Sunrise/sunset only available today from current weather response (free tier)
        if cfg["show_sun_times"] and i == 0:
            if today_sr_ts:
                sr_local = datetime.fromtimestamp(today_sr_ts, tz=local_tz)
                parts.append(_t("sunrise", lang, time=sr_local.strftime("%H:%M")))
            if today_ss_ts:
                ss_local = datetime.fromtimestamp(today_ss_ts, tz=local_tz)
                parts.append(_t("sunset", lang, time=ss_local.strftime("%H:%M")))
        lines.append(f"{_day_name(i, lang)}: {', '.join(parts) or _t('unknown', lang)}")

    lines.append(f"{_t('source', lang)}: OpenWeatherMap")
    return "\n".join(lines)


# ── WeatherAPI ────────────────────────────────────────────────────────────────

async def _fetch_weatherapi(lat: float, lon: float, display_name: str,
                             forecast_days: int, lang: str, cfg: dict) -> str:
    api_key = _read_api_key("weatherapi")
    if not api_key:
        return _t("err_wapi_key", lang)

    try:
        params = {
            "key":     api_key,
            "q":       f"{round(lat, 4)},{round(lon, 4)}",
            "days":    forecast_days + 1,
            "lang":    _API_LANG_MAP.get(lang, "en"),
            "aqi":     "yes" if cfg["show_air_quality"] else "no",
            "alerts":  "no",
        }
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_WAPI_FORECAST_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] WeatherAPI error: %s", exc)
        return _t("err_wapi_fetch", lang)

    current   = data.get("current", {})
    now_temp  = current.get("temp_c")
    now_wind  = current.get("wind_kph")
    now_feels = current.get("feelslike_c")
    now_desc  = current.get("condition", {}).get("text", _t("unknown", lang))

    now_parts = [now_desc]
    if now_temp is not None:
        now_parts.append(f"{now_temp:.0f} °C")
    if cfg["show_feels_like"] and now_feels is not None:
        now_parts.append(_t("feels_like", lang, temp=now_feels))
    if now_wind is not None:
        now_parts.append(_t("wind_kmh", lang, speed=now_wind))

    # Air quality from current data (requires aqi=yes in request)
    aq_line: str | None = None
    if cfg["show_air_quality"]:
        aq = current.get("air_quality", {})
        epa = aq.get("us-epa-index")
        pm25 = aq.get("pm2_5")
        pm10 = aq.get("pm10")
        no2  = aq.get("no2")
        o3   = aq.get("o3")
        if epa is not None:
            label      = _aqi_label(epa, "epa", lang)
            label_part = f"{_t('air_quality', lang)}: {label} (US EPA {epa}/6)"
            pollutants = []
            if pm25 is not None: pollutants.append(f"PM2.5 {pm25:.1f}")
            if pm10 is not None: pollutants.append(f"PM10 {pm10:.1f}")
            if no2  is not None: pollutants.append(f"NO₂ {no2:.1f}")
            if o3   is not None: pollutants.append(f"O₃ {o3:.1f}")
            aq_line = (f"{label_part} — {', '.join(pollutants)} μg/m³"
                       if pollutants else label_part)

    forecast_days_list = data.get("forecast", {}).get("forecastday", [])

    lines = [_t("header", lang, name=display_name, provider="WeatherAPI")]
    lines.append(f"{_t('now', lang)}: {', '.join(now_parts)}")
    if aq_line:
        lines.append(aq_line)

    for i, day in enumerate(forecast_days_list[:forecast_days]):
        d = day.get("day", {})
        astro = day.get("astro", {})
        parts = [d.get("condition", {}).get("text", _t("unknown", lang))]
        mn = d.get("mintemp_c")
        mx = d.get("maxtemp_c")
        if mn is not None and mx is not None:
            parts.append(f"{mn:.0f}–{mx:.0f} °C")
        rain = d.get("totalprecip_mm", 0)
        if rain > 0.1:
            parts.append(_t("precip", lang, mm=rain))
        wind = d.get("maxwind_kph")
        if wind:
            parts.append(_t("wind_kmh", lang, speed=wind))
        if cfg["show_uv_index"]:
            uv = _uv_label(d.get("uv"), lang)
            if uv:
                parts.append(uv)
        if cfg["show_sun_times"]:
            sr = astro.get("sunrise", "")
            ss = astro.get("sunset", "")
            if sr:
                parts.append(_t("sunrise", lang, time=_ampm_to_24h(sr)))
            if ss:
                parts.append(_t("sunset", lang, time=_ampm_to_24h(ss)))
        lines.append(f"{_day_name(i, lang)}: {', '.join(parts)}")

    lines.append(f"{_t('source', lang)}: WeatherAPI")
    return "\n".join(lines)


# ── PirateWeather ─────────────────────────────────────────────────────────────

async def _fetch_pirateweather(lat: float, lon: float, display_name: str,
                                forecast_days: int, lang: str, cfg: dict) -> str:
    api_key = _read_api_key("pirateweather")
    if not api_key:
        return _t("err_pirate_key", lang)

    try:
        url = f"{_PIRATE_URL}/{api_key}/{round(lat, 4)},{round(lon, 4)}"
        params = {
            "units":   "si",
            "lang":    _PIRATE_LANG_MAP.get(lang, "en"),
            "exclude": "minutely",
        }
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[weather] PirateWeather error: %s", exc)
        return _t("err_pirate_fetch", lang)

    curr     = data.get("currently", {})
    now_temp = curr.get("temperature")
    now_wind = curr.get("windSpeed")
    now_desc = curr.get("summary", _t("unknown", lang))

    now_parts = [now_desc]
    if now_temp is not None:
        now_parts.append(f"{now_temp:.0f} °C")
    if now_wind is not None:
        now_parts.append(_t("wind_ms", lang, speed=now_wind))

    lines = [_t("header", lang, name=display_name, provider="PirateWeather")]
    lines.append(f"{_t('now', lang)}: {', '.join(now_parts)}")
    # PirateWeather has no air quality API — skip silently

    local_tz = _local_tz()
    daily_data = data.get("daily", {}).get("data", [])
    for i, day in enumerate(daily_data[1:forecast_days + 1]):
        parts = [day.get("summary", _t("unknown", lang))]
        mn = day.get("temperatureLow")
        mx = day.get("temperatureHigh")
        if mn is not None and mx is not None:
            parts.append(f"{mn:.0f}–{mx:.0f} °C")
        precip = day.get("precipAccumulation") or 0
        if precip > 0.1:
            parts.append(_t("precip", lang, mm=precip))
        wind = day.get("windSpeed")
        if wind:
            parts.append(_t("wind_ms", lang, speed=wind))
        if cfg["show_uv_index"]:
            uv = _uv_label(day.get("uvIndex"), lang)
            if uv:
                parts.append(uv)
        if cfg["show_sun_times"]:
            sr_ts = day.get("sunriseTime")
            ss_ts = day.get("sunsetTime")
            if sr_ts:
                sr_local = datetime.fromtimestamp(sr_ts, tz=local_tz)
                parts.append(_t("sunrise", lang, time=sr_local.strftime("%H:%M")))
            if ss_ts:
                ss_local = datetime.fromtimestamp(ss_ts, tz=local_tz)
                parts.append(_t("sunset", lang, time=ss_local.strftime("%H:%M")))
        lines.append(f"{_day_name(i + 1, lang)}: {', '.join(parts)}")

    lines.append(f"{_t('source', lang)}: PirateWeather (pirateweather.net)")
    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

async def fetch_weather(sted: str | None = None, lang: str = "nb") -> str:
    """
    Fetch weather forecast using the configured provider.

    - No location: uses default from configs/settings.yaml → lokasjon
    - With location: geocodes via Nominatim, then fetches from provider
    - lang: nb / en / de
    - If use_ha_sensors is enabled, local HA sensor readings are prepended
      to the forecast output. HA sensors and the forecast are fetched in parallel.
    """
    cfg = _read_weather_config()
    provider      = cfg["provider"]
    forecast_days = cfg["forecast_days"]

    if sted and sted.strip():
        geo = await _geocode(sted.strip())
        if geo is None:
            return _t("err_geocode", lang, name=sted)
        lat, lon, display_name = geo
    else:
        lat, lon, display_name = _read_default_location()
        if lat == 0.0 and lon == 0.0:
            return _t("err_no_location", lang)

    # Build forecast coroutine based on selected provider
    if provider == "open-meteo":
        forecast_coro = _fetch_open_meteo(lat, lon, display_name, forecast_days, lang, cfg)
    elif provider == "openweathermap":
        forecast_coro = _fetch_openweathermap(lat, lon, display_name, forecast_days, lang, cfg)
    elif provider == "weatherapi":
        forecast_coro = _fetch_weatherapi(lat, lon, display_name, forecast_days, lang, cfg)
    elif provider == "pirateweather":
        forecast_coro = _fetch_pirateweather(lat, lon, display_name, forecast_days, lang, cfg)
    else:
        forecast_coro = _fetch_met_no(lat, lon, display_name, forecast_days, lang, cfg)

    # Build extra parallel tasks (camera view + HA sensors + tides)
    extra_coros: dict[str, object] = {}
    if cfg["use_camera_for_weather"] and cfg.get("weather_camera"):
        extra_coros["camera"] = _fetch_camera_view(cfg, lang)
    if cfg["use_ha_sensors"]:
        extra_coros["sensors"] = _fetch_ha_sensors(cfg, lang)
    if cfg["show_tides"]:
        extra_coros["tides"] = _fetch_tides(lat, lon, lang, cfg)

    if not extra_coros:
        return await forecast_coro

    all_results = await asyncio.gather(forecast_coro, *extra_coros.values(), return_exceptions=True)
    forecast_text = all_results[0] if not isinstance(all_results[0], Exception) else _t("err_weather", lang)
    extra_results = dict(zip(extra_coros.keys(), all_results[1:]))

    parts: list[str] = []
    # Camera view shown first — visual context before the forecast
    camera_val = extra_results.get("camera")
    if camera_val and not isinstance(camera_val, Exception):
        parts.append(camera_val)
    sensor_val = extra_results.get("sensors")
    if sensor_val and not isinstance(sensor_val, Exception):
        parts.append(sensor_val)
    parts.append(forecast_text)
    tides_val = extra_results.get("tides")
    if tides_val and not isinstance(tides_val, Exception):
        parts.append(tides_val)
    return "\n\n".join(parts)


# primary alias (new name)
get_weather = fetch_weather
# backward-compatible alias (deprecated)
hent_yr_varsel = fetch_weather
