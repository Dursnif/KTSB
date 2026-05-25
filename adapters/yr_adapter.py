# /kaare/adapters/yr_adapter.py
"""
Fetches weather forecasts from multiple providers.

Supported providers (set in configs/settings.yaml → weather.provider):
  met.no        — Meteorologisk institutt Locationforecast 2.0 (free, Norway-optimised)
  open-meteo    — Open-Meteo (free, global, no API key)
  openweathermap — OpenWeatherMap (free tier, global, API key required)
  weatherapi    — WeatherAPI.com (free tier, global, API key required)

Default location: from configs/settings.yaml → lokasjon.
Named location: geocoded via Nominatim → lat/lon → selected provider.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

log = logging.getLogger(__name__)

_SETTINGS_PATH   = Path("/kaare/configs/settings.yaml")
_WEATHER_ENV_PATH = Path("/kaare/configs/weather.env")
_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
_USER_AGENT      = "Kaare-AI/1.0 github.com/kaare-ai"

_MET_URL         = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
_OPEN_METEO_URL  = "https://api.open-meteo.com/v1/forecast"
_OWM_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
_OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_WAPI_FORECAST_URL = "https://api.weatherapi.com/v1/forecast.json"


# ── Config helpers ─────────────────────────────────────────────────────────────

def _read_weather_config() -> tuple[str, int]:
    """Returns (provider, forecast_days) from settings.yaml."""
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        wcfg = s.get("weather", {})
        return wcfg.get("provider", "met.no"), int(wcfg.get("forecast_days", 2))
    except Exception:
        return "met.no", 2


def _read_api_key(provider: str) -> str:
    """Read provider API key: env var first, then weather.env file."""
    key_map = {
        "openweathermap": "OPENWEATHERMAP_API_KEY",
        "weatherapi":     "WEATHERAPI_KEY",
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


def _les_standardsted() -> tuple[float, float, str]:
    """Returns (lat, lon, city_name) from settings.yaml, or (0, 0, '') if unconfigured."""
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        lok = s.get("location") or s.get("lokasjon", {})
        lat = float(lok["lat"])
        lon = float(lok["lon"])
        city = lok.get("city") or lok.get("sted", "ukjent")
        return lat, lon, city
    except Exception as exc:
        log.warning("[yr] Could not read settings.yaml: %s", exc)
        return 0.0, 0.0, ""


def _local_tz() -> ZoneInfo:
    try:
        from kaare_core.config import get_local_tz
        return get_local_tz()
    except Exception:
        return ZoneInfo("UTC")


# ── Geocoding ──────────────────────────────────────────────────────────────────

async def _geokod(sted: str) -> tuple[float, float, str] | None:
    """Geocode a place name via Nominatim. Returns (lat, lon, display_name) or None."""
    try:
        async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(
                _NOMINATIM_URL,
                params={"q": sted, "format": "json", "limit": 1},
            )
            r.raise_for_status()
            hits = r.json()
            if not hits:
                return None
            h = hits[0]
            return float(h["lat"]), float(h["lon"]), h.get("display_name", sted).split(",")[0]
    except Exception as exc:
        log.warning("[yr] Nominatim error for '%s': %s", sted, exc)
        return None


# ── met.no ────────────────────────────────────────────────────────────────────

_VINDRETNING = ["N","NNØ","NØ","ØNØ","Ø","ØSØ","SØ","SSØ","S","SSV","SV","VSV","V","VNV","NV","NNV"]

def _vindretning(grader: float) -> str:
    return _VINDRETNING[int((grader + 11.25) / 22.5) % 16]

def _met_symbol_norsk(kode: str) -> str:
    kode = kode.replace("_day","").replace("_night","").replace("_polartwilight","")
    oversett = {
        "clearsky": "klarvær", "fair": "lettskyet", "partlycloudy": "delvis skyet",
        "cloudy": "overskyet", "fog": "tåke",
        "lightrain": "lett regn", "rain": "regn", "heavyrain": "kraftig regn",
        "lightrainshowers": "lette regnbyger", "rainshowers": "regnbyger",
        "heavyrainshowers": "kraftige regnbyger",
        "lightsleet": "lett sludd", "sleet": "sludd", "sleetshowers": "sluddbyger",
        "lightsnow": "lett snø", "snow": "snø", "heavysnow": "kraftig snø",
        "snowshowers": "snøbyger", "lightsnowshowers": "lette snøbyger",
        "thunder": "torden", "rainandthunder": "regn og torden",
    }
    return oversett.get(kode, kode)


def _met_lag_sammendrag(timeseries: list, fra_utc: datetime, til_utc: datetime) -> str | None:
    punkter = [
        t for t in timeseries
        if fra_utc <= datetime.fromisoformat(t["time"].replace("Z", "+00:00")) < til_utc
    ]
    if not punkter:
        return None
    temps, vindhast, nedbor, symboler = [], [], [], []
    for p in punkter:
        d = p["data"]
        inst = d.get("instant", {}).get("details", {})
        if "air_temperature" in inst:
            temps.append(inst["air_temperature"])
        if "wind_speed" in inst:
            vindhast.append(inst["wind_speed"])
        for blokk in ("next_6_hours", "next_1_hours"):
            if blokk in d:
                det = d[blokk].get("details", {})
                if "precipitation_amount" in det:
                    nedbor.append(det["precipitation_amount"])
                sym = d[blokk].get("summary", {}).get("symbol_code", "")
                if sym:
                    symboler.append(sym)
                break
    deler = []
    if temps:
        deler.append(f"{min(temps):.0f}–{max(temps):.0f} °C")
    if nedbor:
        tot = sum(nedbor)
        deler.append(f"{tot:.1f} mm nedbør" if tot > 0.1 else "lite nedbør")
    if vindhast:
        deler.append(f"vind {sum(vindhast)/len(vindhast):.0f} m/s")
    if symboler:
        vanligst = Counter(symboler).most_common(1)[0][0]
        deler.insert(0, _met_symbol_norsk(vanligst))
    return ", ".join(deler) if deler else None


async def _fetch_met_no(lat: float, lon: float, visningsnavn: str, forecast_days: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_MET_URL, params={"lat": round(lat, 4), "lon": round(lon, 4)})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[yr] met.no error: %s", exc)
        return "Klarte ikke å hente værvarselet fra met.no."

    ts = data.get("properties", {}).get("timeseries", [])
    if not ts:
        return "Fikk tomt svar fra met.no."

    local_tz = _local_tz()
    nå_lokal = datetime.now(tz=local_tz)
    dag_start_lokal = nå_lokal.replace(hour=0, minute=0, second=0, microsecond=0)
    dag_start_utc = dag_start_lokal.astimezone(timezone.utc)

    første = ts[0]["data"]["instant"]["details"]
    nå_temp = første.get("air_temperature")
    nå_vind = første.get("wind_speed")
    nå_retning = første.get("wind_from_direction")
    nå_sym = ""
    for blokk in ("next_1_hours", "next_6_hours"):
        if blokk in ts[0]["data"]:
            nå_sym = _met_symbol_norsk(ts[0]["data"][blokk].get("summary", {}).get("symbol_code", ""))
            break

    nå_deler = []
    if nå_sym:
        nå_deler.append(nå_sym)
    if nå_temp is not None:
        nå_deler.append(f"{nå_temp:.0f} °C")
    if nå_vind is not None:
        ret = f" fra {_vindretning(nå_retning)}" if nå_retning is not None else ""
        nå_deler.append(f"vind {nå_vind:.0f} m/s{ret}")

    dag_names = ["I dag", "I morgen", "Overmorgen", "Om 3 dager", "Om 4 dager"]
    linjer = [f"Vær for {visningsnavn} (met.no):"]
    linjer.append(f"Nå: {', '.join(nå_deler) or 'ukjent'}")

    for i in range(min(forecast_days, len(dag_names))):
        fra = dag_start_utc + timedelta(days=i)
        til = fra + timedelta(days=1)
        sammendrag = _met_lag_sammendrag(ts, fra, til)
        if sammendrag:
            linjer.append(f"{dag_names[i]}: {sammendrag}")

    linjer.append("Kilde: met.no")
    return "\n".join(linjer)


# ── Open-Meteo ────────────────────────────────────────────────────────────────

_WMO_NORSK: dict[int, str] = {
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
}

def _wmo_norsk(code: int | None) -> str:
    if code is None:
        return "ukjent"
    return _WMO_NORSK.get(int(code), f"kode {code}")


async def _fetch_open_meteo(lat: float, lon: float, visningsnavn: str, forecast_days: int) -> str:
    try:
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,weathercode,windspeed_10m,winddirection_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode,windspeed_10m_max",
            "timezone": "auto",
            "forecast_days": forecast_days + 1,
        }
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_OPEN_METEO_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[yr] Open-Meteo error: %s", exc)
        return "Klarte ikke å hente værvarselet fra Open-Meteo."

    current = data.get("current", {})
    daily   = data.get("daily", {})

    nå_temp = current.get("temperature_2m")
    nå_vind = current.get("windspeed_10m")
    nå_code = current.get("weathercode")

    nå_deler = []
    if nå_code is not None:
        nå_deler.append(_wmo_norsk(nå_code))
    if nå_temp is not None:
        nå_deler.append(f"{nå_temp:.0f} °C")
    if nå_vind is not None:
        nå_deler.append(f"vind {nå_vind:.0f} km/t")

    dag_names = ["I dag", "I morgen", "Overmorgen", "Om 3 dager", "Om 4 dager"]
    times     = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precips   = daily.get("precipitation_sum", [])
    codes     = daily.get("weathercode", [])

    linjer = [f"Vær for {visningsnavn} (Open-Meteo):"]
    linjer.append(f"Nå: {', '.join(nå_deler) or 'ukjent'}")

    for i, dag in enumerate(times[:forecast_days]):
        _ = dag
        deler = [_wmo_norsk(codes[i] if i < len(codes) else None)]
        if i < len(min_temps) and i < len(max_temps):
            deler.append(f"{min_temps[i]:.0f}–{max_temps[i]:.0f} °C")
        if i < len(precips) and precips[i] is not None:
            p = precips[i]
            deler.append(f"{p:.1f} mm nedbør" if p > 0.1 else "lite nedbør")
        label = dag_names[i] if i < len(dag_names) else dag
        linjer.append(f"{label}: {', '.join(deler)}")

    linjer.append("Kilde: Open-Meteo")
    return "\n".join(linjer)


# ── OpenWeatherMap ────────────────────────────────────────────────────────────

def _owm_beskrivelse(weather_list: list) -> str:
    if not weather_list:
        return "ukjent"
    return weather_list[0].get("description", "ukjent")


async def _fetch_openweathermap(lat: float, lon: float, visningsnavn: str, forecast_days: int) -> str:
    api_key = _read_api_key("openweathermap")
    if not api_key:
        return "OpenWeatherMap krever en API-nøkkel. Gå til Innstillinger → Nettsøk og vær og legg inn nøkkelen."

    try:
        base_params = {"lat": round(lat, 4), "lon": round(lon, 4), "appid": api_key, "units": "metric", "lang": "no"}
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            curr_r = await client.get(_OWM_WEATHER_URL, params=base_params)
            curr_r.raise_for_status()
            curr = curr_r.json()

            fc_r = await client.get(_OWM_FORECAST_URL, params={**base_params, "cnt": forecast_days * 8 + 1})
            fc_r.raise_for_status()
            fc = fc_r.json()
    except Exception as exc:
        log.warning("[yr] OpenWeatherMap error: %s", exc)
        return "Klarte ikke å hente værvarselet fra OpenWeatherMap."

    nå_temp = curr.get("main", {}).get("temp")
    nå_vind = curr.get("wind", {}).get("speed")
    nå_desc = _owm_beskrivelse(curr.get("weather", []))

    nå_deler = [nå_desc]
    if nå_temp is not None:
        nå_deler.append(f"{nå_temp:.0f} °C")
    if nå_vind is not None:
        nå_deler.append(f"vind {nå_vind:.0f} m/s")

    # Group 3-hour intervals by date
    from collections import defaultdict
    by_date: dict[str, list] = defaultdict(list)
    for item in fc.get("list", []):
        date = item["dt_txt"][:10]
        by_date[date].append(item)

    dag_names = ["I dag", "I morgen", "Overmorgen", "Om 3 dager", "Om 4 dager"]
    sorted_dates = sorted(by_date.keys())

    linjer = [f"Vær for {visningsnavn} (OpenWeatherMap):"]
    linjer.append(f"Nå: {', '.join(nå_deler)}")

    for i, date in enumerate(sorted_dates[:forecast_days]):
        items = by_date[date]
        temps = [it["main"]["temp"] for it in items if "main" in it]
        winds = [it["wind"]["speed"] for it in items if "wind" in it]
        rains = [it.get("rain", {}).get("3h", 0) for it in items]
        descs = [_owm_beskrivelse(it.get("weather", [])) for it in items]
        deler = []
        if descs:
            deler.append(Counter(descs).most_common(1)[0][0])
        if temps:
            deler.append(f"{min(temps):.0f}–{max(temps):.0f} °C")
        tot_rain = sum(rains)
        if tot_rain > 0.1:
            deler.append(f"{tot_rain:.1f} mm nedbør")
        if winds:
            deler.append(f"vind {sum(winds)/len(winds):.0f} m/s")
        label = dag_names[i] if i < len(dag_names) else date
        linjer.append(f"{label}: {', '.join(deler) or 'ukjent'}")

    linjer.append("Kilde: OpenWeatherMap")
    return "\n".join(linjer)


# ── WeatherAPI ────────────────────────────────────────────────────────────────

async def _fetch_weatherapi(lat: float, lon: float, visningsnavn: str, forecast_days: int) -> str:
    api_key = _read_api_key("weatherapi")
    if not api_key:
        return "WeatherAPI krever en API-nøkkel. Gå til Innstillinger → Nettsøk og vær og legg inn nøkkelen."

    try:
        params = {
            "key": api_key,
            "q": f"{round(lat, 4)},{round(lon, 4)}",
            "days": forecast_days + 1,
            "lang": "no",
            "aqi": "no",
            "alerts": "no",
        }
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await client.get(_WAPI_FORECAST_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("[yr] WeatherAPI error: %s", exc)
        return "Klarte ikke å hente værvarselet fra WeatherAPI."

    current = data.get("current", {})
    nå_temp = current.get("temp_c")
    nå_vind = current.get("wind_kph")
    nå_desc = current.get("condition", {}).get("text", "ukjent")

    nå_deler = [nå_desc]
    if nå_temp is not None:
        nå_deler.append(f"{nå_temp:.0f} °C")
    if nå_vind is not None:
        nå_deler.append(f"vind {nå_vind:.0f} km/t")

    dag_names = ["I dag", "I morgen", "Overmorgen", "Om 3 dager", "Om 4 dager"]
    forecast_days_list = data.get("forecast", {}).get("forecastday", [])

    linjer = [f"Vær for {visningsnavn} (WeatherAPI):"]
    linjer.append(f"Nå: {', '.join(nå_deler)}")

    for i, day in enumerate(forecast_days_list[:forecast_days]):
        d = day.get("day", {})
        deler = [d.get("condition", {}).get("text", "ukjent")]
        mn = d.get("mintemp_c")
        mx = d.get("maxtemp_c")
        if mn is not None and mx is not None:
            deler.append(f"{mn:.0f}–{mx:.0f} °C")
        rain = d.get("totalprecip_mm", 0)
        if rain > 0.1:
            deler.append(f"{rain:.1f} mm nedbør")
        wind = d.get("maxwind_kph")
        if wind:
            deler.append(f"vind {wind:.0f} km/t")
        label = dag_names[i] if i < len(dag_names) else day.get("date", "")
        linjer.append(f"{label}: {', '.join(deler)}")

    linjer.append("Kilde: WeatherAPI")
    return "\n".join(linjer)


# ── Main entry point ───────────────────────────────────────────────────────────

async def hent_yr_varsel(sted: str | None = None) -> str:
    """
    Fetch weather forecast using the configured provider.

    - No location: uses default from configs/settings.yaml → lokasjon
    - With location: geocodes via Nominatim, then fetches from provider
    """
    provider, forecast_days = _read_weather_config()

    if sted and sted.strip():
        geo = await _geokod(sted.strip())
        if geo is None:
            return f"Fant ikke stedet '{sted}' — prøv et mer nøyaktig stedsnavn."
        lat, lon, visningsnavn = geo
    else:
        lat, lon, visningsnavn = _les_standardsted()
        if lat == 0.0 and lon == 0.0:
            return (
                "Lokasjon er ikke konfigurert. "
                "Gå til Innstillinger → Generelt og sett bredde- og lengdegrad."
            )

    if provider == "open-meteo":
        return await _fetch_open_meteo(lat, lon, visningsnavn, forecast_days)
    elif provider == "openweathermap":
        return await _fetch_openweathermap(lat, lon, visningsnavn, forecast_days)
    elif provider == "weatherapi":
        return await _fetch_weatherapi(lat, lon, visningsnavn, forecast_days)
    else:
        return await _fetch_met_no(lat, lon, visningsnavn, forecast_days)
