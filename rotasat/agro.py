"""Base de conocimiento de cultivos + aptitud por lugar + motor de comparación de rotaciones.

IMPORTANTE: los valores agronómicos son aproximados (FAO-56, FAO ECOCROP y prácticas comunes en Perú).
Deben validarse con un agrónomo local (INIA, Agencia Agraria, universidades) antes de decisiones reales.
El motor es deliberadamente transparente: cada puntaje se explica.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import pandas as pd

# kc: coeficiente de cultivo promedio (FAO-56) · t_opt / t_lim: temperatura media mensual óptima / límite (°C)
# sens_helada, sens_calor: 0..1 · tol_sal: 0 (nada) .. 1 (muy tolerante) · nitrogeno: + fija N, - consume N
# texturas: suelos preferidos · ph: rango tolerado · mercado: 1..5 referencial (actualizar con MIDAGRI/SISAP)
CULTIVOS: dict[str, dict] = {
    "algodon": dict(nombre="Algodón", familia="malváceas", meses=7, kc=0.85, t_opt=(21, 30), t_lim=(15, 38),
                    sens_helada=0.6, sens_calor=0.2, tol_sal=0.9, nitrogeno=-2, cobertura=0.6, mercado=3, forraje=False,
                    texturas=["franco", "franco-arenoso", "franco-arcilloso"], ph=(5.8, 8.0),
                    nota="Muy tolerante a la sal pero consume mucha agua; atención al gusano rosado y picudo."),
    "maiz": dict(nombre="Maíz amarillo duro", familia="gramíneas", meses=5, kc=0.85, t_opt=(18, 30), t_lim=(12, 35),
                 sens_helada=0.8, sens_calor=0.5, tol_sal=0.3, nitrogeno=-3, cobertura=0.6, mercado=3, forraje=True,
                 texturas=["franco", "franco-arenoso", "franco-arcilloso"], ph=(5.5, 7.8),
                 nota="Gran demandante de nitrógeno; el rastrojo sirve de forraje. Sensible a sal."),
    "pallar": dict(nombre="Pallar", familia="leguminosas", meses=6, kc=0.75, t_opt=(17, 27), t_lim=(12, 32),
                   sens_helada=0.7, sens_calor=0.5, tol_sal=0.3, nitrogeno=+2, cobertura=0.7, mercado=4, forraje=False,
                   texturas=["franco-arenoso", "franco", "arenoso"], ph=(6.0, 8.0),
                   nota="Leguminosa (en Ica con Denominación de Origen): fija nitrógeno y tiene buen precio. Sensible a sal."),
    "frijol": dict(nombre="Frijol", familia="leguminosas", meses=4, kc=0.75, t_opt=(16, 25), t_lim=(10, 30),
                   sens_helada=0.8, sens_calor=0.6, tol_sal=0.2, nitrogeno=+2, cobertura=0.6, mercado=3, forraje=False,
                   texturas=["franco", "franco-arenoso"], ph=(5.8, 7.5),
                   nota="Ciclo corto y bajo consumo de agua; fija nitrógeno. Muy sensible a sal."),
    "camote": dict(nombre="Camote", familia="convolvuláceas", meses=5, kc=0.80, t_opt=(20, 28), t_lim=(14, 34),
                   sens_helada=0.8, sens_calor=0.3, tol_sal=0.5, nitrogeno=-1, cobertura=0.9, mercado=2, forraje=True,
                   texturas=["arenoso", "franco-arenoso", "franco"], ph=(5.2, 7.5),
                   nota="Cubre muy bien el suelo (menos erosión y evaporación); el follaje es forraje."),
    "sorgo": dict(nombre="Sorgo", familia="gramíneas", meses=4, kc=0.75, t_opt=(22, 32), t_lim=(14, 38),
                  sens_helada=0.7, sens_calor=0.2, tol_sal=0.7, nitrogeno=-2, cobertura=0.6, mercado=2, forraje=True,
                  texturas=["franco", "franco-arcilloso", "arcilloso", "franco-arenoso"], ph=(5.5, 8.5),
                  nota="Tolerante a sequía, calor y sal moderada; buen forraje. Mercado más pequeño."),
    "quinua": dict(nombre="Quinua", familia="amarantáceas", meses=5, kc=0.70, t_opt=(10, 20), t_lim=(4, 28),
                   sens_helada=0.35, sens_calor=0.6, tol_sal=0.9, nitrogeno=-1, cobertura=0.5, mercado=4, forraje=False,
                   texturas=["franco-arenoso", "franco", "arenoso"], ph=(5.5, 8.5),
                   nota="Tolera helada leve, sequía y sal; precio variable. En costa vigilar mildiu y aves."),
    "papa": dict(nombre="Papa", familia="solanáceas", meses=6, kc=0.85, t_opt=(10, 20), t_lim=(5, 25),
                 sens_helada=0.9, sens_calor=0.5, tol_sal=0.3, nitrogeno=-3, cobertura=0.6, mercado=4, forraje=False,
                 texturas=["franco-arenoso", "franco"], ph=(5.0, 7.0),
                 nota="Base de la alimentación; repetirla multiplica gorgojo de los Andes, nematodos y rancha."),
    "haba": dict(nombre="Haba", familia="leguminosas", meses=6, kc=0.80, t_opt=(10, 20), t_lim=(5, 26),
                 sens_helada=0.5, sens_calor=0.5, tol_sal=0.4, nitrogeno=+3, cobertura=0.6, mercado=3, forraje=True,
                 texturas=["franco", "franco-arcilloso", "arcilloso"], ph=(6.0, 8.0),
                 nota="Fija nitrógeno; tolera heladas leves mejor que la papa."),
    "tarwi": dict(nombre="Tarwi (chocho)", familia="leguminosas", meses=7, kc=0.70, t_opt=(10, 20), t_lim=(5, 25),
                  sens_helada=0.4, sens_calor=0.4, tol_sal=0.4, nitrogeno=+3, cobertura=0.6, mercado=4, forraje=False,
                  texturas=["franco-arenoso", "arenoso", "franco"], ph=(5.0, 7.0),
                  nota="Leguminosa andina de raíz profunda: fija mucho nitrógeno y aguanta sequía. Requiere desamargado."),
    "cebada": dict(nombre="Cebada", familia="gramíneas", meses=5, kc=0.75, t_opt=(10, 20), t_lim=(4, 28),
                   sens_helada=0.3, sens_calor=0.5, tol_sal=0.8, nitrogeno=-2, cobertura=0.7, mercado=2, forraje=True,
                   texturas=["franco", "franco-arenoso", "franco-arcilloso"], ph=(6.0, 8.5),
                   nota="Rústica y tolerante al frío; grano y forraje."),
    "avena": dict(nombre="Avena forrajera", familia="gramíneas", meses=5, kc=0.80, t_opt=(10, 22), t_lim=(4, 28),
                  sens_helada=0.25, sens_calor=0.5, tol_sal=0.5, nitrogeno=-2, cobertura=0.8, mercado=3, forraje=True,
                  texturas=["franco", "franco-arcilloso", "franco-arenoso", "arcilloso"], ph=(5.0, 7.5),
                  nota="Buen forraje para ganado lechero; cubre bien el suelo."),
    "maiz_amilaceo": dict(nombre="Maíz amiláceo", familia="gramíneas", meses=7, kc=0.85, t_opt=(13, 22), t_lim=(9, 28),
                          sens_helada=0.8, sens_calor=0.4, tol_sal=0.3, nitrogeno=-3, cobertura=0.6, mercado=3, forraje=True,
                          texturas=["franco", "franco-arenoso", "franco-arcilloso"], ph=(5.5, 7.5),
                          nota="Ciclo largo: más expuesto a heladas al final de campaña."),
    "olluco": dict(nombre="Olluco", familia="baseláceas", meses=7, kc=0.80, t_opt=(9, 17), t_lim=(5, 22),
                   sens_helada=0.6, sens_calor=0.5, tol_sal=0.3, nitrogeno=-1, cobertura=0.6, mercado=3, forraje=False,
                   texturas=["franco", "franco-arenoso"], ph=(5.0, 7.0),
                   nota="Tubérculo andino de otra familia: rompe el ciclo de plagas de la papa."),
    "yuca": dict(nombre="Yuca", familia="euforbiáceas", meses=10, kc=0.80, t_opt=(22, 30), t_lim=(16, 36),
                 sens_helada=0.9, sens_calor=0.2, tol_sal=0.3, nitrogeno=-1, cobertura=0.7, mercado=2, forraje=True,
                 texturas=["arenoso", "franco-arenoso", "franco"], ph=(4.5, 7.5),
                 nota="Muy rústica y tolera suelos ácidos y pobres; ciclo largo."),
    "arroz": dict(nombre="Arroz", familia="gramíneas", meses=5, kc=1.15, t_opt=(22, 30), t_lim=(15, 36),
                  sens_helada=0.9, sens_calor=0.4, tol_sal=0.4, nitrogeno=-3, cobertura=0.8, mercado=3, forraje=False,
                  texturas=["arcilloso", "franco-arcilloso"], ph=(5.0, 7.5),
                  nota="El cultivo que más agua consume: solo donde el agua sobra."),
    "mani": dict(nombre="Maní", familia="leguminosas", meses=4, kc=0.75, t_opt=(22, 30), t_lim=(16, 35),
                 sens_helada=0.9, sens_calor=0.3, tol_sal=0.4, nitrogeno=+2, cobertura=0.6, mercado=3, forraje=True,
                 texturas=["arenoso", "franco-arenoso"], ph=(5.5, 7.5),
                 nota="Leguminosa de clima cálido para suelos sueltos; fija nitrógeno."),
    "descanso": dict(nombre="Descanso / pasto natural", familia="descanso", meses=12, kc=0.30, t_opt=(-50, 50), t_lim=(-60, 60),
                     sens_helada=0.0, sens_calor=0.0, tol_sal=1.0, nitrogeno=+1, cobertura=0.5, mercado=0, forraje=True,
                     texturas=list(), ph=(0, 14),
                     nota="Recupera el suelo (como en los laymes), pero no genera ingresos ese año."),
}

# Lugares de ejemplo para la demo (cualquier punto del mapa también funciona)
EJEMPLOS = {
    "Ica — Valle (Pueblo Nuevo)": (-14.13, -75.70),
    "Junín — Valle del Mantaro (Chupaca)": (-12.06, -75.29),
    "Huancavelica — zona altoandina": (-12.79, -74.97),
    "Ayacucho — Huamanga": (-13.16, -74.22),
    "Apurímac — Andahuaylas": (-13.66, -73.39),
    "Puno — Altiplano (Juliaca)": (-15.50, -70.13),
    "Junín — Selva central (Satipo)": (-11.25, -74.64),
}
CUENCA_ICA = dict(lat=-13.15, lon=-75.07, nombre="cuenca alta de Huancavelica (Choclococha)")

REGIONES = {"costa": "🏜️ Costa desértica", "sierra": "⛰️ Sierra (valles interandinos)",
            "altoandina": "🏔️ Zona altoandina / altiplano", "selva": "🌴 Selva / ceja de selva"}

ACTUAL_TIPICA = {
    "costa": [("algodon", 9), ("maiz", 4), ("algodon", 9)],
    "sierra": [("papa", 10), ("papa", 10), ("cebada", 12)],
    "altoandina": [("papa", 10), ("papa", 10), ("cebada", 12)],
    "selva": [("maiz", 10), ("maiz", 3), ("yuca", 10)],
}

PESOS_DEF = dict(aptitud=3, agua=3, suelo=3, plagas=2, clima=3, ingresos=3, erosion=2)
CRITERIOS = {"aptitud": "🌍 Aptitud al lugar", "agua": "💧 Agua", "suelo": "🌱 Suelo (nitrógeno)", "plagas": "🐛 Plagas",
             "clima": "❄️🌡️ Riesgo climático", "ingresos": "💰 Ingresos", "erosion": "⛰️ Erosión / suelo desnudo",
             "sal": "🧂 Salinidad"}
HORIZONTE = 36  # meses analizados (3 años)
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre",
               "octubre", "noviembre", "diciembre"]


def _mes(m: int) -> str:
    return MESES_LARGO[m - 1]


def detectar_region(elev: float, lluvia_anual: float) -> str:
    if elev >= 3700:
        return "altoandina"
    if elev >= 2000:
        return "sierra"
    if lluvia_anual < 300:
        return "costa"
    return "selva"


def pendiente_clase(pct: float) -> str:
    return "plano" if pct < 3 else "suave" if pct < 12 else "ladera"


def _pe(p: float) -> float:
    """Lluvia efectiva mensual (método simplificado FAO/AGLW)."""
    return max(0.0, 0.6 * p - 10) if p <= 70 else 0.8 * p - 24


# ================================================================ APTITUD
def _f_temp(t: float, opt, lim) -> float:
    if opt[0] <= t <= opt[1]:
        return 1.0
    if t < opt[0]:
        return max(0.0, (t - lim[0]) / (opt[0] - lim[0]))
    return max(0.0, (lim[1] - t) / (lim[1] - opt[1]))


def _f_textura(tx: str | None, prefs: list[str]) -> float:
    if not tx or not prefs or tx in prefs:
        return 1.0
    i = TEXTURAS_IDX[tx]
    dist = min(abs(i - TEXTURAS_IDX[p]) for p in prefs)
    return 0.75 if dist == 1 else 0.5


TEXTURAS_IDX = {t: i for i, t in enumerate(["arenoso", "franco-arenoso", "franco", "franco-arcilloso", "arcilloso"])}


def _f_ph(ph: float | None, rng) -> float:
    if ph is None or rng[0] <= ph <= rng[1]:
        return 1.0
    fuera = rng[0] - ph if ph < rng[0] else ph - rng[1]
    return 0.75 if fuera <= 0.5 else 0.5


def aptitud_cultivo(cid: str, mes: int, clim: pd.DataFrame, cond: dict) -> dict:
    cu = CULTIVOS[cid]
    meses = [(mes - 1 + k) % 12 + 1 for k in range(cu["meses"])]
    ft = [_f_temp(clim.loc[m, "t_media"], cu["t_opt"], cu["t_lim"]) for m in meses]
    f_t = 0.6 * sum(ft) / len(ft) + 0.4 * min(ft)
    expo_h = sum(clim.loc[m, "dias_helada"] + 2 * clim.loc[m, "dias_helada_fuerte"] for m in meses) * cu["sens_helada"]
    f_h = math.exp(-0.06 * expo_h)
    etc = sum(clim.loc[m, "et0_mm"] * cu["kc"] for m in meses)
    pe = sum(min(_pe(clim.loc[m, "lluvia_mm"]), clim.loc[m, "et0_mm"] * cu["kc"]) for m in meses)
    cubre = pe / etc if etc else 1.0
    riego_mm = max(0.0, etc - pe)
    if cond["riego"] == "no":
        f_w = min(1.0, max(0.3, cubre / 0.5))
    else:
        castigo = {"limitada": 0.45, "suficiente": 0.12}[cond["riego"]]
        f_w = 1 - castigo * min(1.0, riego_mm / 900)
        if cond["riego"] == "limitada" and cu["kc"] > 0.9:  # cultivos de inundación (arroz) con poca agua
            f_w *= 0.55
    nivel_sal = {"baja": 0.0, "media": 0.5, "alta": 1.0}[cond["sal"]]
    f_s = 1 - 0.8 * nivel_sal * (1 - cu["tol_sal"])
    f_tx = _f_textura(cond.get("textura"), cu["texturas"])
    f_ph = _f_ph(cond.get("ph"), cu["ph"])
    score = 100 * f_t * f_h * f_w * f_s * f_tx * f_ph
    factores = {"temperatura": f_t, "heladas": f_h, "agua": f_w, "sal": f_s, "textura del suelo": f_tx, "pH del suelo": f_ph}
    return dict(cultivo=cid, mes=mes, score=score, factores=factores, riego_m3ha=riego_mm * 10, lluvia_cubre=cubre,
                t_min_ciclo=min(clim.loc[m, "t_media"] for m in meses), t_max_ciclo=max(clim.loc[m, "t_media"] for m in meses),
                expo_helada=expo_h)


def tabla_aptitud(clim: pd.DataFrame, cond: dict) -> pd.DataFrame:
    """Para cada cultivo prueba los 12 meses de siembra y se queda con el mejor."""
    filas = []
    for cid in CULTIVOS:
        if cid == "descanso":
            continue
        # desempate: el mes en que la lluvia cubre más y hay menos heladas
        mejor = max((aptitud_cultivo(cid, m, clim, cond) for m in range(1, 13)),
                    key=lambda r: (round(r["score"]), round(min(r["lluvia_cubre"], 1.5), 2), -r["expo_helada"]))
        cu = CULTIVOS[cid]
        lim = min(mejor["factores"], key=mejor["factores"].get)
        motivo = _motivo(lim, mejor, cu, cond) if mejor["factores"][lim] < 0.85 else "Sin limitaciones importantes"
        nivel = "🟢 Apto" if mejor["score"] >= 65 else "🟡 Con cuidado" if mejor["score"] >= 40 else "🔴 No recomendado"
        filas.append(dict(id=cid, Cultivo=cu["nombre"], Aptitud=round(mejor["score"]), Nivel=nivel,
                          **{"Mejor mes de siembra": _mes(mejor["mes"]).capitalize()},
                          Ciclo=f"{cu['meses']} meses",
                          **{"Riego necesario (m³/ha)": round(mejor["riego_m3ha"], -1)},
                          **{"Lluvia cubre": f"{min(mejor['lluvia_cubre'], 1) * 100:.0f}%"},
                          **{"Factor limitante": motivo}, Familia=cu["familia"], mes=mejor["mes"]))
    return pd.DataFrame(filas).sort_values("Aptitud", ascending=False).reset_index(drop=True)


def _motivo(lim: str, r: dict, cu: dict, cond: dict) -> str:
    if lim == "temperatura":
        if r["t_min_ciclo"] < cu["t_opt"][0]:
            return f"Muy frío para este cultivo (media {r['t_min_ciclo']:.0f} °C; prefiere {cu['t_opt'][0]}–{cu['t_opt'][1]} °C)"
        return f"Muy caluroso (media {r['t_max_ciclo']:.0f} °C; prefiere {cu['t_opt'][0]}–{cu['t_opt'][1]} °C)"
    if lim == "heladas":
        return f"Riesgo de heladas durante la campaña ({r['expo_helada']:.0f} puntos de exposición)"
    if lim == "agua":
        if cond["riego"] == "no":
            return f"La lluvia solo cubre {r['lluvia_cubre'] * 100:.0f}% de lo que necesita: requiere riego"
        return f"Necesita mucha agua de riego (~{r['riego_m3ha']:,.0f} m³/ha)"
    if lim == "sal":
        return "Poco tolerante a la sal"
    if lim == "textura del suelo":
        return f"Prefiere suelos {', '.join(cu['texturas'])}"
    return f"Prefiere pH {cu['ph'][0]}–{cu['ph'][1]}"


# ================================================================ ROTACIONES
@dataclass
class Resultado:
    nombre: str
    pasos: list
    calendario: list = field(default_factory=list)
    crudo: dict = field(default_factory=dict)
    puntajes: dict = field(default_factory=dict)
    total: float = 0.0
    porque: list = field(default_factory=list)
    evidencia: list = field(default_factory=list)
    beneficios: list = field(default_factory=list)
    vacios: list = field(default_factory=list)
    riesgos: list = field(default_factory=list)


def calendario(pasos: list[tuple[str, int]]) -> list[str | None]:
    """Coloca los cultivos en una línea de tiempo de 36 meses (empieza en enero del año 1)."""
    cal: list[str | None] = [None] * HORIZONTE
    t = 0
    for cid, mes in pasos:
        m = mes - 1
        while m < t:
            m += 12
        for k in range(CULTIVOS[cid]["meses"]):
            if m + k < HORIZONTE:
                cal[m + k] = cid
        t = m + CULTIVOS[cid]["meses"]
    return cal


def evaluar(nombre: str, pasos, clim: pd.DataFrame, cond: dict, apt: dict | None = None) -> Resultado:
    r = Resultado(nombre, pasos)
    cal = calendario(pasos)
    r.calendario = cal
    pend = {"plano": 0.3, "suave": 0.7, "ladera": 1.0}[cond["pendiente"]]
    nivel_sal = {"baja": 0.0, "media": 0.5, "alta": 1.0}[cond["sal"]]
    agua_mm = helada = calor = erosion = 0.0
    inicio = next(i for i, x in enumerate(cal) if x is not None)
    for i, cid in enumerate(cal):
        if i < inicio:  # antes de la primera siembra: campaña anterior (desconocida)
            continue
        c = clim.loc[i % 12 + 1]
        if cid is None:
            erosion += (c.lluvia_mm / 100.0) * pend + 0.15
            continue
        cu = CULTIVOS[cid]
        agua_mm += max(0.0, c.et0_mm * cu["kc"] - _pe(c.lluvia_mm))
        helada += c.dias_helada * cu["sens_helada"] + 2 * c.dias_helada_fuerte * cu["sens_helada"]
        calor += c.dias_calor * cu["sens_calor"]
        erosion += (c.lluvia_mm / 100.0) * pend * (1 - cu["cobertura"])
    cultivos = [p[0] for p in pasos]
    repet = sum(1 for a, b in zip(cultivos, cultivos[1:]) if CULTIVOS[a]["familia"] == CULTIVOS[b]["familia"])
    repet += 0.5 * sum(1 for a, b in zip(cultivos, cultivos[2:]) if CULTIVOS[a]["familia"] == CULTIVOS[b]["familia"])
    # aptitud real de cada cultivo en SU mes de siembra en este lugar
    apt_vals = [aptitud_cultivo(c, m, clim, cond)["score"] if c != "descanso" else 70 for c, m in pasos]
    r.crudo = dict(agua_m3ha=agua_mm * 10, helada=helada, calor=calor, erosion=erosion,
                   n_bal=sum(CULTIVOS[c]["nitrogeno"] for c in cultivos), repet=repet,
                   ingreso=sum(CULTIVOS[c]["mercado"] for c in cultivos) + 0.5 * sum(CULTIVOS[c]["forraje"] for c in cultivos),
                   sal=sum((1 - CULTIVOS[c]["tol_sal"]) * nivel_sal for c in cultivos),
                   meses_desnudo=sum(1 for x in cal[inicio:] if x is None),
                   aptitud=sum(apt_vals) / len(apt_vals), apt_min=min(apt_vals))
    return r


def _escala(vals, menor_mejor: bool):
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return [100.0] * len(vals)
    return [(100 - 60 * (v - lo) / (hi - lo)) if menor_mejor else (40 + 60 * (v - lo) / (hi - lo)) for v in vals]


def _escala_rel(vals):
    """Menor es mejor, proporcional a la magnitud: una diferencia de 3% no se exagera."""
    lo, hi = min(vals), max(vals)
    if hi <= 1e-9:
        return [100.0] * len(vals)
    return [100 - 60 * (v - lo) / hi for v in vals]


def _puntuar(res: list[Resultado], cond: dict, pesos: dict):
    k = lambda key: [r.crudo[key] for r in res]
    esc = {
        "aptitud": [max(0.0, min(100.0, v)) for v in k("aptitud")],   # absoluto (0–100)
        "agua": _escala_rel(k("agua_m3ha")),
        "suelo": _escala(k("n_bal"), False),
        "plagas": _escala(k("repet"), True),
        "clima": _escala_rel([r.crudo["helada"] + r.crudo["calor"] for r in res]),
        "ingresos": _escala(k("ingreso"), False),
        "erosion": _escala_rel(k("erosion")),
    }
    pw = dict(pesos)
    if cond["sal"] != "baja":
        esc["sal"] = _escala_rel(k("sal"))
        pw.setdefault("sal", 2)
    for i, r in enumerate(res):
        r.puntajes = {c: esc[c][i] for c in esc}
        tot = sum(pw.get(c, 0) for c in esc) or 1
        r.total = sum(r.puntajes[c] * pw.get(c, 0) for c in esc) / tot
        if r.crudo["apt_min"] < 40:  # un cultivo no apto arrastra toda la rotación
            r.total *= 0.85


def comparar(rotaciones: dict, clim: pd.DataFrame, cond: dict, pesos: dict) -> list[Resultado]:
    res = [evaluar(n, p, clim, cond) for n, p in rotaciones.items()]
    _puntuar(res, cond, pesos)
    for i, r in enumerate(res):
        _explicar(r, res[0], clim, cond, i == 0)
    return res


def rotaciones_auto(apt: pd.DataFrame, actual: list, clim: pd.DataFrame, cond: dict, pesos: dict,
                    n: int = 2, max_cand: int = 8) -> dict:
    """Arma automáticamente las mejores rotaciones de 3 campañas con los cultivos aptos del lugar.
    Reglas agronómicas: no repetir familia seguida, incluir al menos una leguminosa si alguna es apta."""
    cand = apt[apt["Aptitud"] >= 45].head(max_cand)
    if len(cand) < 3:
        cand = apt.head(max(3, len(cand)))
    ids = list(cand["id"])
    mes_de = dict(zip(cand["id"], cand["mes"]))
    hay_leg = any(CULTIVOS[c]["familia"] == "leguminosas" for c in ids)
    combos = []
    for trio in itertools.permutations(ids, 3):
        fams = [CULTIVOS[c]["familia"] for c in trio]
        if fams[0] == fams[1] or fams[1] == fams[2]:
            continue
        if hay_leg and "leguminosas" not in fams:
            continue
        combos.append([(c, mes_de[c]) for c in trio])
    if not combos:
        return {}
    res = [evaluar("x", actual, clim, cond)] + [evaluar("x", p, clim, cond) for p in combos]
    _puntuar(res, cond, pesos)
    orden = sorted(res[1:], key=lambda r: r.total, reverse=True)
    elegidas, out = [], {}
    # A: la mejor de todas. B: la mejor que CONSERVA el cultivo principal del agricultor (si es apto),
    # para no proponerle abandonar lo que come y vende.
    principal = max({c for c, _ in actual}, key=lambda c: [x for x, _ in actual].count(c))
    if principal in ids and orden:
        elegidas.append(orden[0])
        if principal not in {p[0] for p in orden[0].pasos}:
            con = [r for r in orden if principal in {p[0] for p in r.pasos}]
            if con:
                elegidas.append(con[0])
    for r in orden:
        if len(elegidas) >= n:
            break
        s = {p[0] for p in r.pasos}
        if any(len(s & {p[0] for p in e.pasos}) >= 3 for e in elegidas):  # evitar misma combinación reordenada
            continue
        elegidas.append(r)
    for letra, r in zip("ABCDE", elegidas):
        out[f"{letra}: " + " → ".join(CULTIVOS[c]["nombre"] for c, _ in r.pasos)] = r.pasos
    return out


def _explicar(r: Resultado, base: Resultado, clim, cond, es_actual: bool):
    c, b = r.crudo, base.crudo
    familias = {CULTIVOS[p[0]]["familia"] for p in r.pasos}
    if es_actual:
        r.porque.append("Es la rotación que el agricultor usa hoy: sirve como punto de comparación.")
    else:
        r.porque.append("Armada automáticamente con los cultivos más aptos para el clima y suelo de esta chacra, "
                        "cada uno en su mejor mes de siembra.")
    legs = [CULTIVOS[p[0]]["nombre"] for p in r.pasos if CULTIVOS[p[0]]["nitrogeno"] > 0]
    if legs:
        r.porque.append(f"Incluye leguminosas ({', '.join(legs)}) que fijan nitrógeno del aire y reducen la necesidad de fertilizante.")
    if len(familias) == len(r.pasos):
        r.porque.append("Ninguna familia de cultivo se repite: se corta el ciclo de plagas y enfermedades.")
    meses_txt = ", ".join(f"{CULTIVOS[cid]['nombre']} en {_mes(m)}" for cid, m in r.pasos)
    r.evidencia.append(f"Siembras: {meses_txt}.")
    r.evidencia.append(f"Aptitud promedio de sus cultivos en este lugar: **{c['aptitud']:.0f}/100**.")
    if cond["riego"] != "no":
        r.evidencia.append(f"Necesidad de riego estimada en 3 años: **{c['agua_m3ha']:,.0f} m³/ha** "
                           "(ET0 de NASA POWER × coeficiente de cultivo − lluvia efectiva).")
    else:
        r.evidencia.append(f"Agua que la lluvia no cubre en 3 años: **{c['agua_m3ha']:,.0f} m³/ha** (NASA POWER).")
    mh = clim["dias_helada"].idxmax()
    if clim["dias_helada"].max() > 0.5:
        r.evidencia.append(f"Mes con más riesgo de helada: **{_mes(mh)}** ({clim.loc[mh, 'dias_helada']:.0f} días/año con mínima ≤ 2 °C).")
    mc = clim["dias_calor"].idxmax()
    r.evidencia.append(f"Mes con más calor extremo: **{_mes(mc)}** ({clim.loc[mc, 'dias_calor']:.0f} días/año sobre "
                       f"{clim.attrs.get('p90', 0):.1f} °C, el percentil 90 local).")
    r.evidencia.append(f"Meses con suelo desnudo en 3 años: **{c['meses_desnudo']}**.")
    if not es_actual:
        d = b["agua_m3ha"] - c["agua_m3ha"]
        if d > 100:
            r.beneficios.append(f"Ahorra ~**{d:,.0f} m³ de agua por hectárea** en 3 años frente a la rotación actual "
                                f"(≈ {d / 30:,.0f} camiones cisterna de 30 m³).")
        elif d < -100:
            r.riesgos.append(f"Requiere ~{-d:,.0f} m³/ha MÁS agua que la rotación actual.")
        if c["aptitud"] > b["aptitud"] + 3:
            r.beneficios.append(f"Cultivos mejor adaptados a este lugar (aptitud {c['aptitud']:.0f} vs {b['aptitud']:.0f}).")
        if c["n_bal"] > b["n_bal"]:
            r.beneficios.append(f"Mejor balance de nitrógeno ({c['n_bal']:+d} vs {b['n_bal']:+d}): menos urea que comprar.")
        if c["repet"] < b["repet"]:
            r.beneficios.append("Menos repetición de familias → menor presión de plagas del suelo.")
        if c["helada"] + c["calor"] < (b["helada"] + b["calor"]) * 0.9:
            r.beneficios.append("Menor exposición a heladas/calor según el clima histórico del lugar.")
        if c["erosion"] < b["erosion"] * 0.9:
            r.beneficios.append("Suelo más protegido en meses de lluvia → menos erosión.")
        if c["ingreso"] < b["ingreso"]:
            r.riesgos.append("Valor de mercado referencial menor: verificar precios y compradores locales antes de cambiar.")
    if c["apt_min"] < 40:
        r.riesgos.append("⚠️ Al menos un cultivo tiene baja aptitud para este lugar (ver pestaña «¿Qué puedo sembrar aquí?»).")
    for nota in {CULTIVOS[p[0]]["nota"] for p in r.pasos}:
        r.riesgos.append(nota)
    r.vacios += [
        "NASA POWER tiene celdas de ~50 km; la temperatura se corrigió por altitud con SRTM, pero en quebradas el microclima puede diferir. "
        "Validar con estación SENAMHI o un termómetro en campo.",
        "Coeficientes de cultivo, rangos de temperatura y fechas de siembra son referenciales; varían según variedad.",
        "No se conocen precios actuales ni acceso a mercados: el puntaje de ingresos es indicativo.",
    ]
    if cond.get("fuente_suelo") == "satélite":
        r.vacios.append("El tipo de suelo es una estimación de SoilGrids (250 m): confírmelo con la prueba de la bolita o un análisis de suelo.")
    if cond["sal"] != "baja":
        r.vacios.append("La salinidad real requiere un análisis de suelo y agua (conductividad eléctrica).")


def mensaje_whatsapp(lugar: str, res: list[Resultado], apt: pd.DataFrame | None = None) -> str:
    mejor = max(res, key=lambda r: r.total)
    actual = res[0]
    lineas = [f"🌱 *RotaSat* — {lugar}", ""]
    if apt is not None and len(apt):
        top = apt.head(3)
        lineas.append("Cultivos más aptos para su chacra:")
        lineas += [f"• {r.Cultivo}: sembrar en {r['Mejor mes de siembra'].lower()}" for _, r in top.iterrows()]
        lineas.append("")
    lineas.append(f"Su rotación actual obtiene *{actual.total:.0f}/100*.")
    if mejor is not actual:
        lineas.append(f"La opción *{mejor.nombre.split(':', 1)[-1].strip()}* obtiene *{mejor.total:.0f}/100*.")
        if mejor.beneficios:
            lineas += ["", "¿Por qué?"] + [f"• {x.replace('**', '')}" for x in mejor.beneficios[:3]]
    else:
        lineas.append("Su rotación actual ya es la mejor de las comparadas. ¡Bien hecho!")
    lineas += ["", "⚠️ Orientación con datos satelitales de la NASA. Converse con su técnico agrario antes de decidir."]
    return "\n".join(lineas)
