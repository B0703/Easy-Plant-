# 🌱 RotaSat — Qué sembrar, cuándo y en qué orden, con datos de la NASA

Proyecto para el **NASA Space Apps Challenge 2026** · Reto *Field Shift: Adapting Farms with NASA Data*.

RotaSat funciona en **cualquier chacra del Perú**: el agricultor busca su pueblo, hace clic en el mapa o usa el GPS.
Con datos satelitales de la NASA, RotaSat:

1. **Detecta la zona** (costa, sierra, zona altoandina o selva) por altitud y lluvia, y la pendiente del terreno.
2. **Recomienda qué cultivos sembrar** (aptitud de 0 a 100) y **en qué mes**, según temperatura, heladas, agua, sal y suelo.
3. **Arma automáticamente 2 rotaciones** con los cultivos más aptos y las compara con la rotación actual,
   explicando **por qué, con qué evidencia, qué beneficios, qué vacíos de información y qué riesgos** debe revisar un experto.
4. Resume todo en un **mensaje de WhatsApp**.

## Datos
| Dato | Uso |
|---|---|
| **NASA POWER** (diario 2005–2024) | Temperatura, radiación → heladas, calor extremo, evapotranspiración (ET0 Hargreaves, FAO-56) |
| **NASA SRTM** (90 m) vía OpenTopoData | Altitud real (corrige la temperatura de la celda de ~50 km) y pendiente |
| **GPM IMERG** (NASA, ~10 km, 2015–2024) vía NASA SERVIR ClimateSERV | Lluvia: mucho más precisa que POWER en sierra y selva |
| **MODIS MOD13Q1 NDVI** (250 m) vía ORNL DAAC | Meses con suelo desnudo |
| **VIIRS VNP13A1 NDVI** (500 m) vía ORNL DAAC, opcional | Continuidad de MODIS (Terra/Aqua al final de su vida útil) |
| **Humedad del suelo NASA POWER** (MERRA-2: superficie y raíces) | Cuándo está seco el suelo; sustituye a SMAP/GLDAS (que piden cuenta Earthdata) |
| **SoilGrids** (ISRIC, CC BY 4.0, beta, opcional) | Estimación de textura y pH; **el dato que ingresa el agricultor siempre manda** |
| OpenStreetMap Nominatim | Buscar pueblos por nombre |

Todos son gratuitos y no requieren cuenta.

## Recursos del reto no incluidos (y por qué)
- **SMAP:** requiere cuenta Earthdata; 9–36 km; pierde precisión en relieve abrupto, suelo congelado y selva densa → sustituido por la humedad de MERRA-2 (POWER). Próximo paso: SMAP L4 vía AppEEARS.
- **NLDAS:** solo cubre Norteamérica. **GLDAS:** requiere cuenta; mismo papel que MERRA-2.
- **ECOSTRESS:** requiere cuenta; pasa a horas irregulares desde la Estación Espacial, datos solo desde 2018 y con nubes → útil para puntos calientes en la parcela, no para el clima de 20 años de una rotación. Próximo paso.
- **Landsat:** 30 m ideal, pero nubes en la sierra y procesamiento pesado → próximo paso con HLS (Landsat + Sentinel-2).

## Ejecutar
```bash
pip install -r requirements.txt
python precarga.py          # descarga y guarda los datos de los lugares de ejemplo (con internet, antes de la demo)
python -m streamlit run app.py
```

## Estructura
- `app.py` — interfaz (Streamlit)
- `nasa_data.py` — descargas (POWER, SRTM, IMERG, MODIS, SoilGrids, buscador) con caché local
- `agro.py` — 17 cultivos, aptitud por lugar, rotaciones automáticas y motor de comparación transparente
- `precarga.py` — precarga los datos para trabajar sin internet

## Ajustes científicos
- **Altitud:** la temperatura de la celda POWER se corrige a la altitud real (SRTM) con 6.5 °C/km.
- **Inversión térmica costera:** en la costa el ajuste se aplica completo en verano y nada en invierno
  (la capa de nubes del mar hace que el aire de altura no sea más frío en invierno).
- **Calor extremo** relativo al lugar (percentil 90), robusto al sesgo de la celda.

## Limitaciones
- Microclimas de quebrada no captados; calibrar con estaciones SENAMHI.
- Parámetros de cultivos referenciales (FAO ECOCROP / FAO-56): **validar con agrónomos locales**.
- GPM IMERG tarda 1–2 min por lugar nuevo (ClimateSERV); SoilGrids es beta y no tiene datos en pueblos.
- RotaSat **apoya, no reemplaza** la decisión del agricultor.

## Uso de Super Intelligence (SI)
Se usó un asistente de SI (Claude) para acelerar la programación, revisar fórmulas agronómicas y redactar textos.
Todas las decisiones de diseño, datos y validación fueron revisadas por el equipo.
# proyectoNasa
