"""Descarga por adelantado todos los datos de los lugares de ejemplo (guarda en data_cache/).
Ejecútalo con internet ANTES del evento: así la demo funciona aunque falle el wifi.
    python precarga.py
GPM IMERG tarda ~1–2 min por lugar; se descargan varios en paralelo.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import agro
import nasa_data as nd


def un_lugar(nombre, lat, lon):
    log = [f"→ {nombre}"]
    rel = None
    try:
        rel = nd.relieve_srtm(lat, lon)
        log.append(f"   SRTM {rel['elevacion']:.0f} m, pendiente {rel['pendiente_pct']:.0f}%")
    except Exception as e:
        log.append(f"   SRTM no disponible: {e}")
    lluvias = {}
    for f in ("chirps", "imerg"):
        try:
            lluvias[f] = nd.lluvia_mensual(f, lat, lon)
            log.append(f"   {f.upper()} {lluvias[f].sum():.0f} mm/año")
        except Exception as e:
            log.append(f"   {f.upper()} no disponible: {e}")
    elev = rel["elevacion"] if rel else None
    clim, info = nd.climatologia(lat, lon, elev)
    fuente = "imerg" if info["inversion_costera"] else "chirps"
    if fuente in lluvias:
        clim, info = nd.climatologia(lat, lon, elev, lluvias[fuente], fuente)
    nd.tendencia_anual(lat, lon, elev)
    try:
        nd.humedad_climatologia(lat, lon)
        log.append("   Humedad del suelo (POWER/MERRA-2) ok")
    except Exception as e:
        log.append(f"   Humedad del suelo no disponible: {e}")
    log.append(f"   POWER ok (ajuste {info['ajuste_c']:+.1f} °C)")
    try:
        nd.ndvi_serie(lat, lon, date(2023, 1, 1), date(2024, 12, 31))
        log.append("   NDVI ok")
    except Exception as e:
        log.append(f"   NDVI no disponible: {e}")
    return "\n".join(log)


with ThreadPoolExecutor(max_workers=3) as ex:
    futs = [ex.submit(un_lugar, n, la, lo) for n, (la, lo) in agro.EJEMPLOS.items()]
    for f in futs:
        print(f.result(), flush=True)

nd.lluvia_temporada(agro.CUENCA_ICA["lat"], agro.CUENCA_ICA["lon"])
print("→ Suelo (SoilGrids, servicio beta: puede fallar)")
for n, (la, lo) in agro.EJEMPLOS.items():
    try:
        s = nd.suelo_soilgrids(la, lo)
        print(f"   {n}: {s['textura']}, pH {s['phh2o']:.1f}")
    except Exception as e:
        print(f"   {n}: sin dato ({e})")
    time.sleep(12)  # uso justo: ~5 consultas por minuto
print("Listo. Datos guardados en data_cache/")
