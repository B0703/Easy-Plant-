"""RotaSat — Qué sembrar, cuándo y en qué orden, con datos de la NASA. Funciona en cualquier chacra del Perú.
NASA Space Apps Challenge 2026 · Reto «Field Shift: Adapting Farms with NASA Data»

Ejecutar:  python -m streamlit run app.py
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from folium.plugins import LocateControl
from streamlit_folium import st_folium

import agro
import nasa_data as nd

st.set_page_config(page_title="RotaSat · Datos NASA para tu chacra", page_icon="🌱", layout="wide")
st.markdown("""
<style>
.card {border:1px solid #d9e2ec;border-radius:14px;padding:14px 16px;margin-bottom:8px;background:rgba(127,127,127,.04)}
.big {font-size:2.4rem;font-weight:800;line-height:1}
.tag {display:inline-block;padding:2px 8px;border-radius:8px;font-size:.8rem;margin-right:4px;background:#e8f5e9;color:#1b5e20}
.best {border:2px solid #2e7d32}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ datos (con caché)
@st.cache_data(show_spinner=False)
def cargar_relieve(lat, lon):
    try:
        return nd.relieve_srtm(lat, lon)
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def cargar_clima(lat, lon, elev, fuente):
    lluvia = nd.lluvia_mensual(fuente, lat, lon) if fuente else None
    return nd.climatologia(lat, lon, elev, lluvia, fuente)


@st.cache_data(show_spinner=False)
def cargar_tendencia(lat, lon, elev):
    return nd.tendencia_anual(lat, lon, elev)


@st.cache_data(show_spinner=False)
def cargar_temporada(lat, lon):
    return nd.lluvia_temporada(lat, lon)


@st.cache_data(show_spinner=False)
def cargar_ndvi(lat, lon, producto="MOD13Q1"):
    return nd.ndvi_serie(lat, lon, date(2023, 1, 1), date(2024, 12, 31), producto)


@st.cache_data(show_spinner=False)
def cargar_humedad(lat, lon):
    try:
        return nd.humedad_climatologia(lat, lon)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def buscar(q):
    return nd.buscar_lugar(q)


@st.cache_resource
def tareas():
    """Descargas lentas (GPM IMERG ~1–2 min) en segundo plano, compartidas entre recargas."""
    return ThreadPoolExecutor(max_workers=2), {}


# ------------------------------------------------------------------ ubicación
if "punto" not in st.session_state:
    st.session_state.punto = agro.EJEMPLOS["Ica — Valle (Pueblo Nuevo)"]
    st.session_state.lugar = "Ica — Valle (Pueblo Nuevo)"


def fijar(lat, lon, nombre):
    st.session_state.punto = (round(lat, 4), round(lon, 4))
    st.session_state.lugar = nombre


st.sidebar.title("🌱 RotaSat")
st.sidebar.caption("Qué sembrar, cuándo y en qué orden — con datos satelitales de la NASA.")
st.sidebar.markdown("### 📍 ¿Dónde está su chacra?")
q = st.sidebar.text_input("Buscar pueblo, distrito o provincia", placeholder="Ej.: Pueblo Nuevo, Ica")
if st.sidebar.button("🔍 Buscar", use_container_width=True) and q.strip():
    try:
        st.session_state.resultados = buscar(q.strip())
        if not st.session_state.resultados:
            st.sidebar.warning("No se encontró ese lugar en Perú.")
    except Exception as e:
        st.sidebar.error(f"Buscador no disponible: {e}")
if st.session_state.get("resultados"):
    res_ = st.session_state.resultados
    i = st.sidebar.selectbox("Resultados", range(len(res_)), format_func=lambda k: res_[k]["nombre"][:70])
    if st.sidebar.button("✅ Usar este lugar", use_container_width=True):
        fijar(res_[i]["lat"], res_[i]["lon"], res_[i]["nombre"].split(",")[0] + ", " + res_[i]["nombre"].split(",")[-2].strip())
        st.session_state.resultados = []
        st.rerun()
ej = st.sidebar.selectbox("…o elija un ejemplo", ["—"] + list(agro.EJEMPLOS))
if ej != "—" and ej != st.session_state.get("ej_prev"):
    st.session_state.ej_prev = ej
    fijar(*agro.EJEMPLOS[ej], ej)
    st.rerun()
st.sidebar.caption("También puede hacer **clic en el mapa** o usar el botón **◎ (GPS)** del mapa.")

lat, lon = st.session_state.punto
lugar = st.session_state.lugar

# ------------------------------------------------------------------ relieve + clima
rel = cargar_relieve(lat, lon)
elev_real = rel["elevacion"] if rel else None
ex, futs = tareas()
clave = (round(lat, 2), round(lon, 2))

with st.spinner("Descargando 20 años de clima de NASA POWER…"):
    try:
        clim0, info0 = cargar_clima(lat, lon, elev_real, None)
    except Exception as e:
        st.error(f"No se pudo obtener datos de NASA POWER: {e}")
        st.stop()

# Lluvia: cada producto donde funciona bien (validado contra lo conocido de cada zona)
#  - costa desértica: GPM IMERG (CHIRPS sobreestima mucho en el desierto costero); si no está, POWER (casi no llueve)
#  - sierra, altiplano y selva: CHIRPS (satélite + estaciones; POWER e IMERG subestiman en los Andes)
costa = info0["inversion_costera"]
fuente_lluvia = "imerg" if costa else "chirps"
lluvia_lista = nd.lluvia_en_cache(fuente_lluvia, lat, lon)
kf = (fuente_lluvia, clave)
if not lluvia_lista and not costa and kf not in futs:
    futs[kf] = ex.submit(nd.lluvia_diaria, fuente_lluvia, lat, lon)
if not lluvia_lista and kf in futs and futs[kf].done() and futs[kf].exception() is None:
    lluvia_lista = True
if lluvia_lista:
    clim, info = cargar_clima(lat, lon, elev_real, fuente_lluvia)
else:
    clim, info = clim0, info0
imerg_listo = lluvia_lista or costa  # en la costa la lluvia de POWER basta (casi no llueve)

region_auto = agro.detectar_region(elev_real if elev_real is not None else info["elev_celda"], clim["lluvia_mm"].sum())

# ------------------------------------------------------------------ condiciones del agricultor
st.sidebar.markdown("### 🧭 Su zona")
region = st.sidebar.selectbox("Región (detectada por altitud y lluvia)", list(agro.REGIONES),
                              index=list(agro.REGIONES).index(region_auto), format_func=agro.REGIONES.get,
                              key=f"reg_{clave}")
pend_auto = agro.pendiente_clase(rel["pendiente_pct"]) if rel else "suave"
pendiente = st.sidebar.select_slider("⛰️ Pendiente del terreno", ["plano", "suave", "ladera"], value=pend_auto,
                                     key=f"pend_{clave}",
                                     help=f"SRTM estima {rel['pendiente_pct']:.0f}% de pendiente" if rel else None)
op_riego = {"no": "No, solo lluvia", "limitada": "Sí, pero el agua es limitada", "suficiente": "Sí, agua suficiente"}
riego = st.sidebar.radio("💧 ¿Tiene riego?", list(op_riego), format_func=op_riego.get,
                         index=1 if region == "costa" else 0, key=f"riego_{clave}_{region}")

st.sidebar.markdown("### 🟫 Su suelo")
tx_opts = ["No sé"] + nd.TEXTURAS
textura_m = st.sidebar.selectbox("Tipo de suelo (textura)", tx_opts, key=f"tx_{clave}")
with st.sidebar.expander("❓ ¿Cómo saber mi tipo de suelo? (prueba de la bolita)"):
    st.markdown("""
Tome un puñado de tierra, humedézcala y amásela:
1. **No forma bolita**, se desmorona → **arenoso**
2. Forma bolita pero **se rompe** al apretarla → **franco-arenoso**
3. Forma bolita y una **cinta corta** (menos de 2 cm) → **franco**
4. Forma una **cinta de 2 a 5 cm** → **franco-arcilloso**
5. Forma una **cinta larga**, pegajosa y brillante → **arcilloso**
""")
ph_opts = {"No sé": None, "Ácido (menos de 5.5)": 5.0, "Ligeramente ácido (5.5–6.5)": 6.0,
           "Neutro (6.5–7.5)": 7.0, "Alcalino (más de 7.5)": 8.0}
ph_m = st.sidebar.selectbox("pH (si tiene análisis de suelo)", list(ph_opts), key=f"ph_{clave}")
op_sal = {"baja": "No", "media": "Un poco", "alta": "Mucho"}
sal = st.sidebar.radio("🧂 ¿Ve costra blanca (salitre) o el agua es salobre?", list(op_sal), format_func=op_sal.get,
                       index=1 if region == "costa" else 0, horizontal=True, key=f"sal_{clave}_{region}")

est = st.session_state.get("suelo_est", {}).get(clave)
if st.sidebar.button("🛰️ Estimar mi suelo con satélite (SoilGrids)", use_container_width=True):
    with st.spinner("Consultando SoilGrids…"):
        try:
            est = nd.suelo_soilgrids(lat, lon)
            st.session_state.setdefault("suelo_est", {})[clave] = est
        except Exception as e:
            st.sidebar.warning(f"{e}. Ingrese su suelo manualmente arriba.")
if est:
    st.sidebar.info(f"Estimación satelital: **{est['textura']}**, pH {est['phh2o']:.1f} ({nd.clase_ph(est['phh2o'])}) · "
                    f"arena {est['sand']:.0f}%, arcilla {est['clay']:.0f}%"
                    + (" · *(punto cercano: en pueblos no hay dato)*" if est.get("desplazado") else "")
                    + "\n\nSi usted conoce su suelo, su respuesta manda.")

textura = textura_m if textura_m != "No sé" else (est["textura"] if est else None)
ph = ph_opts[ph_m] if ph_opts[ph_m] is not None else (est["phh2o"] if est else None)
fuente_suelo = "manual" if textura_m != "No sé" else ("satélite" if est else "desconocido")
cond = dict(riego=riego, sal=sal, textura=textura, ph=ph, pendiente=pendiente, fuente_suelo=fuente_suelo)

st.sidebar.markdown("### 🎯 ¿Qué es más importante para usted?")
pesos = {k: st.sidebar.slider(agro.CRITERIOS[k], 0, 5, v) for k, v in agro.PESOS_DEF.items()}

# ------------------------------------------------------------------ cálculos
apt = agro.tabla_aptitud(clim, cond)
hum = cargar_humedad(lat, lon)

# ------------------------------------------------------------------ ENCABEZADO
st.title("🌱 RotaSat")
st.markdown(f"**{lugar}** · {agro.REGIONES[region]} · lat {lat:.3f}, lon {lon:.3f}"
            + (f" · altitud **{elev_real:,.0f} m** (SRTM)" if elev_real is not None else ""))
imerg_fallo = (not imerg_listo and kf in futs and futs[kf].done() and futs[kf].exception() is not None)
preliminar = not imerg_listo
if imerg_fallo:
    st.warning("⚠️ El servicio de lluvia satelital (ClimateSERV) no respondió. Se usa la lluvia de NASA POWER, que "
               "**subestima la lluvia en sierra y selva**: tome la aptitud por falta de agua con cautela.")
    if st.button("🔄 Reintentar lluvia satelital"):
        futs.pop(kf, None)
        st.rerun()
elif not imerg_listo:
    @st.fragment(run_every=5)
    def esperar_lluvia():
        f = futs.get(kf)
        if f is not None and f.done():
            st.rerun()
        st.info("⏳ Descargando 20 años de lluvia satelital **CHIRPS** de este punto (de 1 a 5 minutos, según la carga del servidor de la NASA). "
                "Mientras tanto se usa la lluvia de NASA POWER, que es menos precisa en sierra y selva. "
                "**Los resultados son preliminares** y se actualizarán solos.")
    esperar_lluvia()

t1, t2, t3, t4, t5, t6 = st.tabs(["🗺️ Mi chacra y su clima", "🌱 ¿Qué puedo sembrar aquí?", "🔄 Comparar rotaciones",
                                  "🛰️ ¿Cuándo está pelado el campo?", "📱 WhatsApp", "ℹ️ Cómo funciona"])

# ================================================================== 1. CHACRA Y CLIMA
with t1:
    c1, c2 = st.columns([1, 1.3])
    with c1:
        st.markdown("**Haga clic en su parcela** (o use ◎ para su ubicación GPS)")
        m = folium.Map(location=[lat, lon], zoom_start=12, tiles=None)
        folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                         attr="Esri World Imagery", name="Satélite").add_to(m)
        folium.CircleMarker([lat, lon], radius=9, color="#ffeb3b", fill=True, fill_opacity=0.6, tooltip="Parcela").add_to(m)
        LocateControl(strings={"title": "Usar mi ubicación"}, flyTo=True).add_to(m)
        out = st_folium(m, height=380, use_container_width=True, key="mapa")
        if out and out.get("last_clicked"):
            p = (round(out["last_clicked"]["lat"], 4), round(out["last_clicked"]["lng"], 4))
            if p != st.session_state.punto and p != st.session_state.get("ultimo_click"):
                st.session_state.ultimo_click = p
                fijar(p[0], p[1], f"Punto elegido ({p[0]:.3f}, {p[1]:.3f})")
                st.rerun()
    with c2:
        st.markdown("**Calendario de riesgos (promedio histórico, datos NASA)**")
        filas = {"❄️ Días riesgo helada (mín ≤ 2 °C)": clim["dias_helada"],
                 "🌡️ Días de calor extremo (p90 local)": clim["dias_calor"],
                 "🌡️ Temperatura media (°C)": clim["t_media"],
                 "🌧️ Lluvia (mm)": clim["lluvia_mm"],
                 "💨 Evaporación ET0 (mm)": clim["et0_mm"]}
        if hum is not None:
            filas["🟫 Humedad suelo raíces (%)"] = hum["GWETROOT"].reindex(range(1, 13)) * 100
            filas["🟫 Humedad suelo superficie (%)"] = hum["GWETTOP"].reindex(range(1, 13)) * 100
        z = [list(v.values) for v in filas.values()]
        zn = [[(x - min(r)) / ((max(r) - min(r)) or 1) for x in r] for r in z]
        fig = go.Figure(go.Heatmap(z=zn, x=nd.MESES, y=list(filas), text=[[f"{x:.0f}" for x in r] for r in z],
                                   texttemplate="%{text}", colorscale="YlOrRd", showscale=False, hoverinfo="skip"))
        fig.update_layout(height=320 + (90 if hum is not None else 0), margin=dict(l=0, r=0, t=10, b=0),
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        if hum is not None:
            seco = int(hum["GWETTOP"].idxmin())
            st.caption(f"🟫 Humedad del suelo: modelo MERRA-2 de NASA POWER (cumple el papel de SMAP/GLDAS sin requerir cuenta). "
                       f"Superficie más seca en **{agro.MESES_LARGO[seco - 1]}**: sembrar cuando la humedad de superficie sube."
                       + (" En la costa el modelo **no ve el riego**: úselo solo como referencia." if region == "costa" else ""))
        k1, k2, k3 = st.columns(3)
        k1.metric("Lluvia anual", f"{clim['lluvia_mm'].sum():,.0f} mm", help=f"Fuente: {info['fuente_lluvia']}")
        k2.metric("Evaporación anual (ET0)", f"{clim['et0_mm'].sum():,.0f} mm")
        k3.metric("Días con riesgo de helada", f"{clim['dias_helada'].sum():.0f} /año")

    with st.expander("🔬 Cómo se ajustaron los datos a SU chacra", expanded=False):
        a, b = st.columns(2)
        with a:
            st.markdown("**Temperatura corregida por altitud**")
            if elev_real is not None:
                st.markdown(f"La celda de NASA POWER (~50 km) tiene una altitud promedio de **{info['elev_celda']:,.0f} m**, "
                            f"pero su chacra está a **{elev_real:,.0f} m** (NASA SRTM, 90 m). Con el gradiente térmico "
                            f"de 6.5 °C por km, se corrigieron las temperaturas en **{info['ajuste_c']:+.1f} °C**."
                            + (" En la **costa** hay *inversión térmica* (la capa de nubes del mar hace que en invierno "
                               "el aire de altura no sea más frío): el ajuste se aplica completo en verano y nada en invierno."
                               if info.get("inversion_costera") else ""))
            else:
                st.markdown("No se obtuvo la altitud SRTM: se usan las temperaturas de la celda sin corregir.")
        with b:
            st.markdown("**Lluvia**")
            st.markdown(f"Fuente usada: **{info['fuente_lluvia']}** → **{clim['lluvia_mm'].sum():,.0f} mm/año**. "
                        f"NASA POWER estima {clim['lluvia_power_mm'].sum():,.0f} mm/año en este punto.")
            comp = []
            for f_ in ("chirps", "imerg"):
                if nd.lluvia_en_cache(f_, lat, lon):
                    comp.append(f"{f_.upper()}: {nd.lluvia_mensual(f_, lat, lon).sum():,.0f} mm/año")
            if comp:
                st.caption("Comparación de productos en este punto: " + " · ".join(comp) + ". "
                           "Usamos CHIRPS en sierra y selva (calibrado con estaciones; base del producto PISCO del SENAMHI) "
                           "y GPM IMERG en la costa (CHIRPS sobreestima la lluvia en el desierto costero).")

    en_cuenca_ica = region == "costa" and -15.2 <= lat <= -13.4 and -76.4 <= lon <= -75.2
    if en_cuenca_ica:
        cu = agro.CUENCA_ICA
        st.subheader(f"💧 ¿Traerá agua el río? Lluvia en la {cu['nombre']}")
        st.caption("El agua del río Ica nace como lluvia en la sierra de Huancavelica. Si allá llueve poco, el río trae poco "
                   "y los agricultores dependen más de pozos de un acuífero ya sobreexplotado.")
        try:
            t = cargar_temporada(cu["lat"], cu["lon"])
            a, b = st.columns([1, 2])
            delta = t["porcentaje"] - 100
            a.metric(f"Temporada {t['temporada']}", f"{t['lluvia_mm']:,.0f} mm", f"{delta:+.0f}% vs promedio")
            a.write("🔴 **Año seco**: priorizar cultivos de bajo consumo de agua." if delta < -15 else
                    ("🟢 **Año normal o húmedo**." if delta > -5 else "🟡 **Ligeramente seco**."))
            hist = pd.Series(t["historico"], index=[f"{y}-{str(y + 1)[2:]}" for y in range(2005, 2024)])
            fig = go.Figure(go.Bar(x=hist.index, y=hist.values, marker_color="#4f8bc9"))
            fig.add_hline(y=t["promedio_mm"], line_dash="dash", annotation_text="promedio")
            fig.update_layout(height=230, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="mm (sep–abr)")
            b.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"No se pudo calcular la lluvia de la cuenca alta: {e}")

    with st.expander("📈 ¿Está cambiando el clima aquí? (tendencia 2005–2024)"):
        tend = cargar_tendencia(lat, lon, elev_real)
        cA, cB = st.columns(2)
        f1 = go.Figure(go.Scatter(x=tend.index, y=tend["tmax_media"], mode="lines+markers"))
        f1.update_layout(height=250, title="Temperatura máxima media anual (°C)", margin=dict(l=0, r=0, t=40, b=0))
        cA.plotly_chart(f1, use_container_width=True)
        frio = region in ("sierra", "altoandina")
        f2 = go.Figure(go.Bar(x=tend.index, y=tend["dias_helada"] if frio else tend["dias_calor"]))
        f2.update_layout(height=250, margin=dict(l=0, r=0, t=40, b=0),
                         title="Días con riesgo de helada por año" if frio else "Días de calor extremo por año")
        cB.plotly_chart(f2, use_container_width=True)

# ================================================================== 2. ¿QUÉ PUEDO SEMBRAR?
with t2:
    st.markdown("Para **cada cultivo** RotaSat prueba los **12 meses de siembra** con el clima histórico de su chacra "
                "y calcula una aptitud de 0 a 100 según temperatura, heladas, agua, sal y suelo.")
    if preliminar or imerg_fallo:
        st.warning("⏳ **Resultado preliminar**: en sierra y selva la lluvia de NASA POWER es mucho menor que la real, "
                   "por eso muchos cultivos aparecen limitados por agua. Espere la lluvia de GPM IMERG.")
    aptos = apt[apt["Aptitud"] >= 65]
    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 Aptos", len(aptos))
    c2.metric("🟡 Con cuidado", int(((apt["Aptitud"] >= 40) & (apt["Aptitud"] < 65)).sum()))
    c3.metric("🔴 No recomendados", int((apt["Aptitud"] < 40).sum()))
    if fuente_suelo == "desconocido":
        st.caption("ℹ️ No indicó su tipo de suelo: la aptitud solo considera clima, agua y sal. "
                   "Indíquelo en la barra lateral (o estímelo con satélite) para mayor precisión.")
    cols = ["Cultivo", "Aptitud", "Nivel", "Mejor mes de siembra", "Ciclo", "Lluvia cubre",
            "Riego necesario (m³/ha)", "Factor limitante"]
    st.dataframe(apt[cols], hide_index=True, use_container_width=True,
                 column_config={"Aptitud": st.column_config.ProgressColumn("Aptitud", min_value=0, max_value=100, format="%d"),
                                "Riego necesario (m³/ha)": st.column_config.NumberColumn(format="%d")})
    st.markdown("#### 📆 ¿En qué mes conviene sembrar?")
    sel = st.selectbox("Elija un cultivo", list(apt["id"]), format_func=lambda c: agro.CULTIVOS[c]["nombre"])
    curva = [agro.aptitud_cultivo(sel, mm, clim, cond)["score"] for mm in range(1, 13)]
    fig = go.Figure(go.Bar(x=nd.MESES, y=curva, marker_color=["#2e7d32" if v >= 65 else "#f9a825" if v >= 40 else "#c62828" for v in curva]))
    fig.update_layout(height=260, yaxis=dict(range=[0, 100], title="Aptitud si siembra ese mes"), margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
    st.info("🤝 Es una **orientación**: las variedades locales, el manejo y el mercado pueden cambiar la decisión. "
            "Converse con su técnico agrario.")

# ================================================================== 3. ROTACIONES
with t3:
    base = agro.ACTUAL_TIPICA[region]
    todos = [c for c in agro.CULTIVOS]
    st.markdown("**1. Escriba su rotación actual** (lo que siembra hoy, campaña por campaña):")
    cols = st.columns(3)
    actual = []
    for i, (cid, mes) in enumerate(base):
        with cols[i]:
            c_sel = st.selectbox(f"Campaña {i + 1}", todos, index=todos.index(cid),
                                 format_func=lambda c: agro.CULTIVOS[c]["nombre"], key=f"c{i}_{region}")
            m_sel = st.selectbox("Mes de siembra", range(1, 13), index=mes - 1,
                                 format_func=lambda x: nd.MESES[x - 1], key=f"m{i}_{region}")
            actual.append((c_sel, m_sel))
    st.markdown("**2. RotaSat arma automáticamente 2 alternativas** con los cultivos más aptos de su chacra "
                "(sin repetir familia seguida e incluyendo una leguminosa):")
    auto = agro.rotaciones_auto(apt, actual, clim, cond, pesos)
    rot = {"Actual: " + " → ".join(agro.CULTIVOS[c]["nombre"] for c, _ in actual): actual, **auto}
    res = agro.comparar(rot, clim, cond, pesos)
    mejor = max(res, key=lambda r: r.total)

    cols = st.columns(len(res))
    for col, r in zip(cols, res):
        with col:
            cls = "card best" if r is mejor else "card"
            st.markdown(f"<div class='{cls}'><div style='opacity:.7'>{r.nombre.split(':')[0]}</div>"
                        f"<b>{r.nombre.split(':', 1)[-1]}</b><div class='big'>{r.total:.0f}<span style='font-size:1rem'>/100</span></div>"
                        f"{'<span class=tag>⭐ Recomendada según sus prioridades</span>' if r is mejor else ''}</div>",
                        unsafe_allow_html=True)
            for crit, v in r.puntajes.items():
                st.progress(int(v), text=f"{agro.CRITERIOS[crit]}: {v:.0f}")

    st.markdown("#### 📅 Línea de tiempo (3 años) — los huecos son meses con suelo desnudo")
    colores = {c: f"hsl({(i * 47) % 360},55%,55%)" for i, c in enumerate(agro.CULTIVOS)}
    fig = go.Figure()
    for r in res:
        cal = r.calendario
        i = 0
        while i < len(cal):
            j = i
            while j < len(cal) and cal[j] == cal[i]:
                j += 1
            if cal[i]:
                fig.add_trace(go.Bar(y=[r.nombre.split(":")[0]], x=[j - i], base=i, orientation="h",
                                     marker_color=colores[cal[i]], text=agro.CULTIVOS[cal[i]]["nombre"],
                                     textposition="inside", showlegend=False, hovertemplate="%{text}<extra></extra>"))
            i = j
    fig.update_layout(barmode="overlay", height=230, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis=dict(tickvals=list(range(0, 36, 3)), ticktext=[f"{nd.MESES[x % 12]} A{x // 12 + 1}" for x in range(0, 36, 3)],
                                 range=[0, 36]), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 🔍 Explicación de cada opción")
    for r in res:
        with st.expander(f"{'⭐ ' if r is mejor else ''}{r.nombre} — {r.total:.0f}/100", expanded=r is mejor):
            a, b = st.columns(2)
            with a:
                st.markdown("**🤔 Por qué se sugiere**")
                for x in r.porque or ["Opción de comparación."]:
                    st.markdown(f"- {x}")
                st.markdown("**🛰️ Evidencia (datos NASA)**")
                for x in r.evidencia:
                    st.markdown(f"- {x}")
                st.markdown("**✅ Beneficios frente a la rotación actual**")
                for x in r.beneficios or ["—"]:
                    st.markdown(f"- {x}")
            with b:
                st.markdown("**⚠️ Riesgos a revisar con un experto local**")
                for x in r.riesgos:
                    st.markdown(f"- {x}")
                st.markdown("**❓ Vacíos de información**")
                for x in r.vacios:
                    st.markdown(f"- {x}")
    st.info("🤝 RotaSat **apoya, no reemplaza** la experiencia del agricultor ni la asesoría de un agrónomo.")

# ================================================================== 4. NDVI
with t4:
    st.markdown("El índice **NDVI** (MODIS, 250 m, cada 16 días) mide qué tan verde está el terreno. "
                "Cuando baja de **0.2**, el suelo está prácticamente **desnudo**.")
    if st.button("🛰️ Consultar NDVI de mi parcela (2023–2024)"):
        st.session_state.ver_ndvi = True
    if st.session_state.get("ver_ndvi"):
        with st.spinner("Consultando MODIS (ORNL DAAC)…"):
            try:
                crudo = cargar_ndvi(lat, lon)
                s = crudo.rolling(3, center=True, min_periods=1).median()  # quita picos por nubes
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=crudo.index, y=crudo.values, mode="markers", name="dato crudo",
                                         marker=dict(color="#9e9e9e", size=5)))
                fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name="NDVI filtrado", line_color="#2e7d32"))
                fig.add_hrect(y0=-0.1, y1=0.2, fillcolor="#c8a27a", opacity=0.25, line_width=0,
                              annotation_text="suelo desnudo", annotation_position="top left")
                fig.update_layout(height=320, yaxis_title="NDVI", margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig, use_container_width=True)
                meses_p = sorted({d.month for d in s[s < 0.2].index})
                st.markdown(f"**Meses con campo pelado:** {', '.join(nd.MESES[x - 1] for x in meses_p) or 'ninguno'} "
                            f"· NDVI promedio {s.mean():.2f}")
                st.caption("Un píxel de 250 m puede mezclar varias parcelas, caminos o cerros. Elija el centro del campo.")
            except Exception as e:
                st.error(f"No se pudo consultar MODIS: {e}")
        st.markdown("**Comparar con VIIRS** (500 m): el sucesor de MODIS. Los satélites Terra y Aqua de MODIS están al final "
                    "de su vida útil; VIIRS garantiza que la herramienta siga funcionando en el futuro. "
                    "*Nota: en el servicio de ORNL, VIIRS (Suomi-NPP) tiene datos hasta mayo de 2024.*")
        if st.button("🛰️ Agregar VIIRS (opcional; el servidor de ORNL puede estar lento)"):
            st.session_state.ver_viirs = True
        if st.session_state.get("ver_viirs"):
            with st.spinner("Consultando VIIRS (ORNL DAAC)…"):
                try:
                    v = cargar_ndvi(lat, lon, "VNP13A1").rolling(3, center=True, min_periods=1).median()
                    m_ = cargar_ndvi(lat, lon).rolling(3, center=True, min_periods=1).median()
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=m_.index, y=m_.values, mode="lines", name="MODIS 250 m", line_color="#2e7d32"))
                    fig.add_trace(go.Scatter(x=v.index, y=v.values, mode="lines", name="VIIRS 500 m", line_color="#1565c0"))
                    fig.update_layout(height=280, yaxis_title="NDVI", margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig, use_container_width=True)
                    comun = pd.concat([m_.rename("m"), v.rename("v")], axis=1).dropna()
                    if len(comun) > 5:
                        st.caption(f"Correlación MODIS–VIIRS en fechas comunes: **{comun['m'].corr(comun['v']):.2f}** "
                                   "(cerca de 1 = ambos satélites ven lo mismo).")
                except Exception as e:
                    st.warning(f"VIIRS no disponible ahora ({type(e).__name__}). La app sigue funcionando con MODIS.")

# ================================================================== 5. WHATSAPP
with t5:
    st.markdown("Muchos agricultores no usan apps complejas, pero **sí WhatsApp**. RotaSat resume el resultado:")
    msg = agro.mensaje_whatsapp(lugar, res, apt[apt["Aptitud"] >= 40])
    st.code(msg, language=None)
    from urllib.parse import quote
    st.link_button("📲 Abrir en WhatsApp", f"https://wa.me/?text={quote(msg)}")

# ================================================================== 6. CÓMO FUNCIONA
with t6:
    st.markdown(f"""
### Datos usados
| Dato | Qué mide | Para qué lo usamos |
|---|---|---|
| **NASA POWER** (diario 2005–2024) | Temperatura, radiación, lluvia | Heladas, calor, evapotranspiración (ET0 Hargreaves, FAO-56) |
| **NASA SRTM** (90 m) | Altitud y pendiente | Corregir la temperatura de la celda de ~50 km a la altitud real; erosión |
| **CHIRPS** (5 km, 2005–2024, satélite + estaciones) vía NASA SERVIR ClimateSERV | Lluvia diaria | Lluvia en sierra, altiplano y selva (POWER e IMERG subestiman en los Andes) |
| **GPM IMERG** (NASA, ~10 km, 2015–2024) vía NASA SERVIR ClimateSERV | Lluvia diaria | Lluvia en la costa (CHIRPS sobreestima en el desierto) y comparación |
| **MODIS MOD13Q1** (250 m) | NDVI | Meses con suelo desnudo |
| **SoilGrids** (ISRIC, 250 m, opcional) | Textura y pH del suelo | Estimación inicial; **el dato del agricultor manda** |

### ¿Qué puedo sembrar aquí? (aptitud)
Para cada cultivo y cada mes de siembra posible: **temperatura** media del ciclo vs. rango óptimo del cultivo ·
**heladas** durante el ciclo × sensibilidad · **agua**: cuánto cubre la lluvia (secano) o cuánto riego necesita ·
**sal** × tolerancia · **textura y pH** del suelo. Se multiplica todo (un factor malo basta para descartar) y se elige el mejor mes.

### Rotaciones automáticas
Se prueban todas las combinaciones de 3 campañas con los cultivos aptos, con dos reglas agronómicas:
no repetir familia de forma seguida e incluir una leguminosa. Se puntúan por aptitud, agua, nitrógeno, plagas,
riesgo climático, ingresos, erosión y sal, **ponderados con las prioridades del agricultor**.

### Recursos de la NASA sugeridos por el reto: qué usamos y por qué
| Recurso | Decisión | Justificación técnica |
|---|---|---|
| **NASA POWER** | ✅ Usado | Clima diario 20 años; humedad del suelo (MERRA-2) |
| **GPM IMERG** | ✅ Usado | Lluvia en la costa y comparación (subestima en los Andes: ver CHIRPS) |
| **MODIS** | ✅ Usado | NDVI 250 m, suelo desnudo |
| **VIIRS** | ✅ Usado (opcional) | NDVI 500 m: continuidad cuando MODIS (Terra/Aqua) deje de operar |
| **SRTM** | ✅ Usado | Altitud real (corrección de temperatura) y pendiente (erosión) |
| **SMAP** | 🔁 Sustituido | Requiere cuenta Earthdata; celdas de 9–36 km; pierde precisión en relieve abrupto, suelo congelado del altiplano y vegetación densa de selva. Usamos la humedad del suelo de POWER (MERRA-2), sin cuenta. **Próximo paso:** SMAP L4 (9 km, superficie y raíces) vía AppEEARS. |
| **NLDAS** | ⛔ No aplica | Solo cubre Norteamérica (25–53° N). |
| **GLDAS** | 🔁 Sustituido | Global (0.25°), pero requiere cuenta Earthdata. Cumple el mismo papel que la humedad de MERRA-2 que ya usamos (ambos son modelos de superficie). |
| **ECOSTRESS** | ⏭️ Próximo paso | Requiere cuenta; va en la Estación Espacial, que pasa a horas irregulares y no todos los días; datos solo desde 2018 y con nubes en la temporada de lluvias andina. Sirve para «puntos calientes» de estrés en la parcela (70 m), no para el clima de 20 años que necesita una rotación. El estrés por calor lo cubrimos con POWER (percentil 90) y la ET0. |
| **Landsat** | ⏭️ Próximo paso | 30 m sería ideal para ver la parcela, pero pasa cada 8–16 días y en la sierra las nubes de la temporada de lluvias dejan muy pocas imágenes útiles; además exige procesar imágenes completas (máscara de nubes). **Próximo paso:** HLS (Landsat + Sentinel-2, 30 m, cada 2–3 días). |

Los recursos del USDA, USGS y PRISM solo cubren EE. UU.; sus equivalentes en Perú son PISCO del SENAMHI (clima),
SIEA y SISAP del MIDAGRI (estadísticas y precios), ANA (agua) y los estudios de suelos del INIA.

### Limitaciones (lo decimos con honestidad)
- POWER tiene celdas de ~50 km: corregimos la temperatura por altitud, pero el microclima de una quebrada puede diferir.
- Los parámetros de los 17 cultivos son referenciales (FAO ECOCROP/FAO-56) y **deben validarse con agrónomos locales**.
- SoilGrids es un servicio en fase beta y no tiene datos en pueblos; por eso el ingreso manual siempre está disponible.
- No incluye precios de mercado actuales ni el conocimiento del agricultor: **RotaSat apoya, no reemplaza**.

*Fuente de lluvia en este punto: {info['fuente_lluvia']}.*
""")
