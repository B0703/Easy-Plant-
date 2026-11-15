"""Descarga y procesa datos abiertos (sin cuenta):
- NASA POWER (clima diario: temperatura, lluvia, radiación)   https://power.larc.nasa.gov
- MODIS MOD13Q1 NDVI vía ORNL DAAC (250 m, cada 16 días)        https://modis.ornl.gov/data/modis_webservice.html
- NASA SRTM 90 m (altitud y pendiente) vía OpenTopoData           https://www.opentopodata.org
- SoilGrids (ISRIC, CC BY 4.0, beta) — estimación del suelo       https://rest.isric.org
- OpenStreetMap Nominatim — buscar lugares por nombre
Todo se guarda en caché local (data_cache/) para que la demo funcione aunque falle el internet.
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

CACHE = Path(__file__).parent / "data_cache"
CACHE.mkdir(exist_ok=True)
UA = {"User-Agent": "RotaSat-SpaceApps2026/0.2 (prototipo educativo)", "Accept": "application/json"}

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,TOA_SW_DWN,ALLSKY_SFC_SW_DWN"
MODIS_URL = "https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset"
SRTM_URL = "https://api.opentopodata.org/v1/srtm90m"
SOIL_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
GEO_URL = "https://nominatim.openstreetmap.org/search"

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
GRADIENTE = 6.5  # °C por cada 1000 m (gradiente térmico estándar de la atmósfera)


def _cache_path(name: str) -> Path:
    return CACHE / (name.replace("/", "_").replace("|", "_").replace(" ", "_") + ".json")


def _get_json(url: str, params: dict, cache_name: str, retries: int = 3, timeout: int = 120) -> dict:
    p = _cache_path(cache_name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=UA)
            r.raise_for_status()
            data = r.json()
            p.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as e:  # red inestable en el evento: reintentar
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


# ----------------------------------------------------------------- NASA POWER
def power_daily(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    """Clima diario NASA POWER (sin corregir). start/end = 'YYYYMMDD'."""
    lat, lon = round(lat, 3), round(lon, 3)
    params = dict(parameters=POWER_PARAMS, community="AG", latitude=lat, longitude=lon,
                  start=start, end=end, format="JSON")
    js = _get_json(POWER_URL, params, f"power_{lat}_{lon}_{start}_{end}")
    df = pd.DataFrame(js["properties"]["parameter"])
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.replace(-999.0, float("nan"))
    df.attrs["elevacion"] = js["geometry"]["coordinates"][2]
    return df


# Costa peruana: inversión térmica (capa de nubes del mar), muy fuerte en invierno. El gradiente de 6.5 °C/km
# solo vale en verano; en invierno el aire de altura NO es más frío. Peso del ajuste por mes:
PESO_COSTA = {12: 1, 1: 1, 2: 1, 3: 1, 4: 0.66, 5: 0.33, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0.33, 11: 0.66}


def es_costa(df: pd.DataFrame, elev_real: float | None) -> bool:
    lluvia_anual = df["PRECTOTCORR"].sum() / max(1, df.index.year.nunique())
    return elev_real is not None and elev_real < 1500 and lluvia_anual < 300


def _derivar(df: pd.DataFrame, elev_real: float | None) -> pd.DataFrame:
    """Corrige temperatura por altitud (celda POWER ~50 km vs. altitud real SRTM) y calcula ET0."""
    df = df.copy()
    elev_celda = df.attrs.get("elevacion", float("nan"))
    delta = 0.0
    costa = False
    if elev_real is not None and not math.isnan(elev_celda):
        delta = GRADIENTE * (elev_celda - elev_real) / 1000.0
        costa = es_costa(df, elev_real)
        ajuste = df.index.month.map(PESO_COSTA).astype(float) * delta if costa else delta
        for col in ("T2M", "T2M_MAX", "T2M_MIN"):
            df[col] = df[col] + ajuste
    df.attrs["ajuste_c"] = delta
    df.attrs["inversion_costera"] = costa
    df.attrs["elevacion"] = elev_celda
    ra_mm = 0.408 * df["TOA_SW_DWN"]                       # MJ/m2/día -> mm/día equivalentes
    rango = (df["T2M_MAX"] - df["T2M_MIN"]).clip(lower=0)
    df["ET0"] = 0.0023 * ra_mm * (df["T2M"] + 17.8) * rango.pow(0.5)  # Hargreaves-Samani (FAO-56 ec. 52)
    return df


def climatologia(lat: float, lon: float, elev_real: float | None = None, lluvia: pd.Series | None = None,
                 fuente_lluvia: str | None = None,
                 y0: int = 2005, y1: int = 2024) -> tuple[pd.DataFrame, dict]:
    """Promedios mensuales de 20 años: lluvia, ET0, temperaturas, días de helada y de calor.
    lluvia: serie mensual (1..12) de GPM IMERG; si no se da, se usa la lluvia de NASA POWER."""
    df = _derivar(power_daily(lat, lon, f"{y0}0101", f"{y1}1231"), elev_real)
    n_years = y1 - y0 + 1
    p90 = df["T2M_MAX"].quantile(0.90)  # calor extremo relativo al lugar
    g = df.groupby(df.index.month)
    clim = pd.DataFrame({
        "lluvia_mm": g["PRECTOTCORR"].sum() / n_years,
        "et0_mm": g["ET0"].sum() / n_years,
        "t_media": g["T2M"].mean(),
        "tmin_c": g["T2M_MIN"].mean(),
        "tmax_c": g["T2M_MAX"].mean(),
        "dias_helada": g["T2M_MIN"].apply(lambda s: (s <= 2.0).sum()) / n_years,   # riesgo de helada (aire a 2 m <= 2 °C)
        "dias_helada_fuerte": g["T2M_MIN"].apply(lambda s: (s <= 0.0).sum()) / n_years,
        "dias_calor": g["T2M_MAX"].apply(lambda s: (s >= p90).sum()) / n_years,
    })
    clim.index.name = "mes"
    fuente = "NASA POWER (MERRA-2)"
    clim["lluvia_power_mm"] = clim["lluvia_mm"]
    if lluvia is not None and len(lluvia) == 12:
        clim["lluvia_mm"] = lluvia.reindex(clim.index).values
        fuente = FUENTES_LLUVIA[fuente_lluvia][3] if fuente_lluvia in FUENTES_LLUVIA else "satélite"
    clim.attrs["p90"] = float(p90)
    clim.attrs["fuente_lluvia"] = fuente
    info = dict(elev_celda=df.attrs["elevacion"], elev_real=elev_real, ajuste_c=df.attrs["ajuste_c"], p90=float(p90),
                fuente_lluvia=fuente, inversion_costera=df.attrs["inversion_costera"])
    return clim, info


def tendencia_anual(lat: float, lon: float, elev_real: float | None = None, y0: int = 2005, y1: int = 2024) -> pd.DataFrame:
    df = _derivar(power_daily(lat, lon, f"{y0}0101", f"{y1}1231"), elev_real)
    p90 = df["T2M_MAX"].quantile(0.90)
    g = df.groupby(df.index.year)
    return pd.DataFrame({
        "lluvia_mm": g["PRECTOTCORR"].sum(),
        "tmax_media": g["T2M_MAX"].mean(),
        "dias_helada": g["T2M_MIN"].apply(lambda s: (s <= 2.0).sum()),
        "dias_calor": g["T2M_MAX"].apply(lambda s: (s >= p90).sum()),
    })


def lluvia_temporada(lat: float, lon: float, y0: int = 2005, y1: int = 2024) -> dict:
    """Lluvia de la última temporada (sep-abr) vs el promedio histórico.
    Para Ica: la lluvia de la cuenca alta (Huancavelica) es el agua que luego baja por el río Ica."""
    hist = power_daily(lat, lon, f"{y0}0101", f"{y1}1231")["PRECTOTCORR"]
    totales = [hist[f"{y}-09-01":f"{y + 1}-04-30"].sum() for y in range(y0, y1)]
    prom = sum(totales) / len(totales)
    hoy = date.today()
    y_fin = hoy.year if hoy.month >= 5 else hoy.year - 1
    reciente = power_daily(lat, lon, f"{y_fin - 1}0901", f"{y_fin}0430")["PRECTOTCORR"].sum()
    return {"temporada": f"sep {y_fin - 1} – abr {y_fin}", "lluvia_mm": reciente,
            "promedio_mm": prom, "porcentaje": 100 * reciente / prom if prom else float("nan"),
            "historico": totales}


# ------------------------------------------------------------------ Lluvia satelital (NASA SERVIR ClimateSERV)
CS_URL = "https://climateserv.servirglobal.net/api"
# fuente: (datatype ClimateSERV, años, nombre)
FUENTES_LLUVIA = {
    "chirps": (0, 2005, 2024, "CHIRPS 2005–2024 (satélite + estaciones, vía NASA SERVIR)"),
    "imerg": (26, 2015, 2024, "GPM IMERG 2015–2024 (NASA)"),
}


def _cache_lluvia(fuente: str, lat: float, lon: float) -> Path:
    dt, y0, y1, _ = FUENTES_LLUVIA[fuente]
    return _cache_path(f"{fuente}_{round(lat, 2)}_{round(lon, 2)}_{y0}_{y1}")


def lluvia_en_cache(fuente: str, lat: float, lon: float) -> bool:
    return _cache_lluvia(fuente, lat, lon).exists()


def lluvia_diaria(fuente: str, lat: float, lon: float, espera_max: int = 300) -> pd.Series:
    """Lluvia diaria desde NASA SERVIR ClimateSERV (sin cuenta). Pedido asíncrono: enviar → esperar → descargar.
    Se envía un pedido por año, todos en paralelo (CHIRPS ~8 s/año; GPM IMERG ~45 s/año en el servidor)."""
    dt, y0, y1, _ = FUENTES_LLUVIA[fuente]
    lat, lon = round(lat, 2), round(lon, 2)
    p = _cache_lluvia(fuente, lat, lon)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    else:
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        jobs = {}
        for a in range(y0, y1 + 1):
            params = dict(datatype=dt, begintime=f"01/01/{a}", endtime=f"12/31/{a}", intervaltype=0, operationtype=5,
                          dateType_Category="default", isZip_CurrentDataType="false", geometry=geom)
            r = requests.get(f"{CS_URL}/submitDataRequest/", params=params, timeout=60, headers=UA)
            r.raise_for_status()
            jobs[a] = r.json()[0]
        t0 = time.time()
        res: dict = {}
        while len(res) < len(jobs) and time.time() - t0 < espera_max:
            time.sleep(4)
            for a, job in jobs.items():
                if a in res:
                    continue
                try:
                    prog = requests.get(f"{CS_URL}/getDataRequestProgress/", params={"id": job}, timeout=30, headers=UA).json()
                    if prog and prog[0] >= 100:
                        js = requests.get(f"{CS_URL}/getDataFromRequest/", params={"id": job}, timeout=60, headers=UA).json()
                        if isinstance(js, dict) and js.get("data"):
                            res[a] = js["data"]
                except Exception:
                    pass
        if len(res) < len(jobs):
            raise RuntimeError(f"ClimateSERV ({fuente.upper()}) no respondió a tiempo")
        data = [d for a in sorted(res) for d in res[a]]
        p.write_text(json.dumps(data), encoding="utf-8")
    s = pd.Series({pd.Timestamp(year=d["year"], month=d["month"], day=d["day"]): d["raw_value"] for d in data})
    return s.where(s >= 0).sort_index()


def lluvia_mensual(fuente: str, lat: float, lon: float) -> pd.Series:
    dt, y0, y1, _ = FUENTES_LLUVIA[fuente]
    s = lluvia_diaria(fuente, lat, lon)
    return s.groupby(s.index.month).sum() / (y1 - y0 + 1)


# compatibilidad
def lluvia_imerg_diaria(lat, lon):
    return lluvia_diaria("imerg", lat, lon)


def lluvia_mensual_imerg(lat, lon):
    return lluvia_mensual("imerg", lat, lon)


def imerg_en_cache(lat, lon):
    return lluvia_en_cache("imerg", lat, lon)


# ------------------------------------------------------------------ Humedad del suelo
def humedad_suelo(lat: float, lon: float, y0: int = 2005, y1: int = 2024) -> pd.DataFrame:
    """Humedad del suelo mensual de NASA POWER (modelo de superficie MERRA-2, ~50 km), sin cuenta.
    GWETTOP = capa superficial (0–5 cm) · GWETROOT = zona de raíces (0–100 cm). 0 = seco, 1 = saturado.
    Cumple el papel de SMAP/GLDAS (que requieren cuenta Earthdata): es un MODELO, no una medición directa."""
    lat, lon = round(lat, 3), round(lon, 3)
    params = dict(parameters="GWETTOP,GWETROOT", community="AG", latitude=lat, longitude=lon,
                  start=str(y0), end=str(y1), format="JSON")
    js = _get_json("https://power.larc.nasa.gov/api/temporal/monthly/point", params, f"gwet_{lat}_{lon}_{y0}_{y1}")
    prm = js["properties"]["parameter"]
    filas = {}
    for var in ("GWETTOP", "GWETROOT"):
        for k, v in prm[var].items():
            if k[4:] == "13" or v is None or v < 0:  # "13" = promedio anual
                continue
            filas.setdefault((int(k[:4]), int(k[4:])), {})[var] = v
    df = pd.DataFrame.from_dict(filas, orient="index")
    df.index = pd.MultiIndex.from_tuples(df.index, names=["anio", "mes"])
    return df


def humedad_climatologia(lat: float, lon: float) -> pd.DataFrame:
    df = humedad_suelo(lat, lon)
    return df.groupby(level="mes").mean()


# ------------------------------------------------------------------ SRTM
def relieve_srtm(lat: float, lon: float) -> dict:
    """Altitud real (SRTM 90 m) y pendiente (%) con una grilla 3×3 alrededor de la chacra (~110 m)."""
    lat, lon = round(lat, 4), round(lon, 4)
    d = 0.001
    pts = [(lat + dy, lon + dx) for dy in (d, 0, -d) for dx in (-d, 0, d)]
    locs = "|".join(f"{a:.5f},{b:.5f}" for a, b in pts)
    js = _get_json(SRTM_URL, {"locations": locs}, f"srtm_{lat}_{lon}", timeout=30)
    z = [r["elevation"] for r in js["results"]]
    if any(v is None for v in z):
        raise RuntimeError("SRTM sin dato (¿punto en el mar?)")
    dx_m = d * 111320 * math.cos(math.radians(lat))
    dy_m = d * 110540
    dzdx = (z[5] - z[3]) / (2 * dx_m)
    dzdy = (z[1] - z[7]) / (2 * dy_m)
    pend = 100 * math.hypot(dzdx, dzdy)
    return {"elevacion": float(z[4]), "pendiente_pct": pend}


# ------------------------------------------------------------------ SoilGrids
TEXTURAS = ["arenoso", "franco-arenoso", "franco", "franco-arcilloso", "arcilloso"]


def textura_desde(arena: float, arcilla: float) -> str:
    """Clase de textura simplificada (5 clases, basada en el triángulo USDA)."""
    if arcilla >= 40:
        return "arcilloso"
    if arcilla >= 27:
        return "franco-arcilloso"
    if arena >= 70:
        return "arenoso"
    if arena >= 50:
        return "franco-arenoso"
    return "franco"


def _soil_punto(lat: float, lon: float) -> dict | None:
    lat, lon = round(lat, 3), round(lon, 3)
    p = _cache_path(f"soil_{lat}_{lon}")
    if p.exists():
        js = json.loads(p.read_text(encoding="utf-8"))
    else:
        params = [("lon", lon), ("lat", lat), ("value", "mean"), ("depth", "0-5cm"), ("depth", "5-15cm")]
        params += [("property", q) for q in ("clay", "sand", "silt", "phh2o", "soc")]
        r = requests.get(SOIL_URL, params=params, timeout=20, headers=UA)
        r.raise_for_status()
        js = r.json()
    out = {}
    for layer in js["properties"]["layers"]:
        vals = [dd["values"]["mean"] for dd in layer["depths"] if dd["values"]["mean"] is not None]
        if vals:
            out[layer["name"]] = sum(vals) / len(vals) / layer["unit_measure"]["d_factor"]
    if "sand" not in out or "clay" not in out:
        return None  # sin dato (zona urbana, agua o roca): no se guarda en caché
    p.write_text(json.dumps(js), encoding="utf-8")
    out.update(lat=lat, lon=lon)
    return out


def suelo_soilgrids(lat: float, lon: float) -> dict:
    """Estimación de suelo (0–15 cm) de SoilGrids 250 m (ISRIC, CC BY 4.0, servicio beta).
    SoilGrids no tiene dato en pueblos y ciudades: si el punto no tiene, se prueba ~1 km alrededor."""
    d = 0.01
    for dy, dx in [(0, 0), (d, 0), (-d, 0), (0, d), (0, -d)]:
        try:
            out = _soil_punto(lat + dy, lon + dx)
        except requests.RequestException as e:
            raise RuntimeError(f"SoilGrids no responde (servicio beta): {type(e).__name__}") from e
        if out:
            out["textura"] = textura_desde(out["sand"], out["clay"])
            out["desplazado"] = (dy, dx) != (0, 0)
            return out
        time.sleep(1.2)  # respetar el uso justo del servicio (~5 consultas/minuto)
    raise RuntimeError("SoilGrids no tiene dato en este punto ni alrededor")


def clase_ph(ph: float) -> str:
    return "ácido" if ph < 5.5 else "ligeramente ácido" if ph < 6.5 else "neutro" if ph <= 7.5 else "alcalino"


# ------------------------------------------------------------------ Buscador
def buscar_lugar(q: str) -> list[dict]:
    js = _get_json(GEO_URL, {"q": q, "format": "json", "limit": 8, "countrycodes": "pe"},
                   f"geo8_{q.lower().strip()}", timeout=20)
    # Primero pueblos y ciudades: el centro de una provincia o región puede caer en un cerro
    prioridad = {"hamlet": 0, "village": 0, "town": 0, "city": 1, "suburb": 1, "neighbourhood": 1, "locality": 0,
                 "farm": 0, "municipality": 2, "county": 3, "region": 4, "state": 5}
    js = sorted(js, key=lambda x: prioridad.get(x.get("addresstype", ""), 2))
    return [{"nombre": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"])} for x in js[:5]]


# ------------------------------------------------------------------ MODIS NDVI
def _modis_date(d: date) -> str:
    return f"A{d.year}{d.timetuple().tm_yday:03d}"


NDVI_PRODUCTOS = {
    # producto ORNL: (banda, prefijo de caché, descripción)
    "MOD13Q1": ("250m_16_days_NDVI", "ndvi", "MODIS Terra, 250 m"),
    "VNP13A1": ("500_m_16_days_NDVI", "viirs", "VIIRS Suomi-NPP, 500 m (sucesor de MODIS)"),
}


def ndvi_serie(lat: float, lon: float, start: date, end: date, producto: str = "MOD13Q1") -> pd.Series:
    """NDVI cada 16 días vía ORNL DAAC (sin cuenta). Máx. 10 compuestos por pedido y no se cruza el cambio de año.
    MOD13Q1 = MODIS 250 m · VNP13A1 = VIIRS 500 m (continuidad: los satélites Terra/Aqua de MODIS están al final de su vida útil)."""
    banda, pref, _ = NDVI_PRODUCTOS[producto]
    url = f"https://modis.ornl.gov/rst/api/v1/{producto}/subset"
    lat, lon = round(lat, 4), round(lon, 4)
    out = {}
    d = start
    while d <= end:
        bloque = 127 if producto == "MOD13Q1" else 63  # VIIRS publica compuestos cada 8 días
        e = min(d + timedelta(days=bloque), end, date(d.year, 12, 31))
        params = dict(latitude=lat, longitude=lon, band=banda,
                      startDate=_modis_date(d), endDate=_modis_date(e), kmAboveBelow=0, kmLeftRight=0)
        if producto == "MOD13Q1":
            js = _get_json(url, params, f"{pref}_{lat}_{lon}_{d}_{e}")
        else:  # opcional: sin reintentos largos para no bloquear la app si ORNL está lento
            try:
                js = _get_json(url, params, f"{pref}_{lat}_{lon}_{d}_{e}", retries=1, timeout=40)
            except RuntimeError as ex:
                if "400" in str(ex):  # bloque sin datos (VNP13A1 en ORNL llega hasta ~mayo 2024): se omite
                    d = e + timedelta(days=1)
                    continue
                raise
        for item in js.get("subset", []):
            v = item["data"][0]
            if v > -2000:  # -3000 = sin dato
                out[pd.Timestamp(item["calendar_date"])] = v * 0.0001
        d = e + timedelta(days=1)
    return pd.Series(out, name="NDVI").sort_index()
