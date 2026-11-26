# -*- coding: utf-8 -*-
"""
App de cálculo de BOOSTER (ramales, presión constante) - criterios tipo MAPAS-CONAGUA.

Instalación:   pip install streamlit pdfplumber pandas matplotlib openpyxl pillow
Ejecución:     streamlit run app_booster.py
Requiere en la misma carpeta: booster_cdt.py (motor) y exporta.py (esquema/exportación).
"""
import copy
import hashlib
import json
import math
import re

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import booster_cdt as bc
import exporta as ex

st.set_page_config(page_title="Booster - CDT a presión constante", layout="wide")

APP_VERSION = "6"
if getattr(bc, "VERSION", None) != APP_VERSION or getattr(ex, "VERSION", None) != APP_VERSION:
    # Streamlit puede conservar en memoria una versión vieja de los módulos: se recargan desde disco (en este orden)
    import importlib
    for _m in (bc, ex):
        importlib.reload(_m)
if getattr(bc, "VERSION", None) != APP_VERSION or getattr(ex, "VERSION", None) != APP_VERSION:
    import os
    st.error(
        f"Los archivos no son de la misma versión (se requiere la {APP_VERSION}). "
        f"booster_cdt = {getattr(bc, 'VERSION', 'antigua')}, exporta = {getattr(ex, 'VERSION', 'antigua')}.")
    st.code(f"app            : {os.path.abspath(__file__)}\n"
            f"booster_cdt.py : {getattr(bc, '__file__', '?')}\n"
            f"exporta.py     : {getattr(ex, '__file__', '?')}\n"
            f"carpeta actual : {os.getcwd()}")
    st.info("Revisa que booster_cdt.py y exporta.py de la versión 6 estén en la MISMA carpeta que este archivo y que no exista "
            "otra copia de esos archivos en otra carpeta del repositorio (por ejemplo, en la raíz). Después usa "
            "«Manage app → Reboot app». Las rutas de arriba muestran de dónde se está leyendo cada archivo.")
    st.stop()

MATCOLS = ["nombre", "C", "eps0_mm", "E_kgcm2", "espesor_mm", "P_trab_kgcm2"]
MAT_BASE = ["PVC C-900", "PVC IPS", "PEAD", "ACERO", "FODU"]
COLS_N = ["id", "cota", "demanda_lps", "p_req_m"]
COLS_T = ["id", "desde", "hasta", "L", "dn_pulg", "material", "clase", "prof_m", "K", "modo", "valor"]
NAN = float("nan")
ESQ_DIRECTO = "Directo a la bomba (un solo tramo)"
ESQ_CONSUMOS = "Línea con consumos intermedios"
ESQ_TY = "T o Y (dos ramales)"
ESQ_VARIOS = "Varios ramales desde un nodo (3 o más)"
ESQ_SUB = "T o Y con subramal (un ramal que se vuelve a dividir)"
ESQ_ESPINA = "Troncal con derivaciones (espina de pescado)"
ESQUEMAS = [ESQ_DIRECTO, ESQ_CONSUMOS, ESQ_TY, ESQ_VARIOS, ESQ_SUB, ESQ_ESPINA]
ESQ_AYUDA = {
    ESQ_DIRECTO: "Lo más común: la bomba alimenta una sola línea hasta el punto de entrega.",
    ESQ_CONSUMOS: "Una sola línea con tomas o consumos a lo largo (se captura el gasto de cada toma en `demanda_lps`).",
    ESQ_TY: "La línea de la bomba se divide en dos ramales en un nodo. T e Y son iguales para el cálculo hidráulico.",
    ESQ_VARIOS: "Un nodo del que salen 3 o más ramales (trifurcación, pata de ganso).",
    ESQ_SUB: "Un nodo se divide en un ramal simple y otro que vuelve a dividirse en dos.",
    ESQ_ESPINA: "Una línea troncal con varias derivaciones, cada una con su ramal.",
}


def plantilla(tipo, n=2):
    """Devuelve (filas de nodos, filas de tramos) con la estructura del esquema y los datos por capturar vacíos."""
    N, T = [["B", NAN, 0.0, NAN]], []

    def nd(i):
        N.append([i, NAN, 0.0, NAN])

    def tr(d, h):
        T.append([f"{d}-{h}", d, h, NAN, 6.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0])

    if tipo == ESQ_DIRECTO:
        nd("F"); tr("B", "F")
    elif tipo == ESQ_CONSUMOS:
        ids = [f"C{k}" for k in range(1, n + 1)] + ["F"]
        prev = "B"
        for x in ids:
            nd(x); tr(prev, x); prev = x
    elif tipo in (ESQ_TY, ESQ_VARIOS):
        nd("N"); tr("B", "N")
        for k in range(1, (2 if tipo == ESQ_TY else n) + 1):
            nd(f"R{k}"); tr("N", f"R{k}")
    elif tipo == ESQ_SUB:
        nd("N1"); tr("B", "N1"); nd("R1"); tr("N1", "R1"); nd("N2"); tr("N1", "N2")
        nd("R2"); tr("N2", "R2"); nd("R3"); tr("N2", "R3")
    else:                                            # espina de pescado
        prev = "B"
        for k in range(1, n + 1):
            nd(f"N{k}"); tr(prev, f"N{k}"); prev = f"N{k}"
        nd("F"); tr(prev, "F")
        for k in range(1, n + 1):
            nd(f"D{k}"); tr(f"N{k}", f"D{k}")
    return N, T


_N0, _T0 = plantilla(ESQ_DIRECTO)
NODOS0 = pd.DataFrame(_N0, columns=COLS_N)
TRAMOS0 = pd.DataFrame(_T0, columns=COLS_T)
EJEMPLOS = {
    "Red en T (2 ramales)": {
        "nodos": [["B", 290.41, 0.0, NAN], ["T", 290.96, 0.0, NAN], ["Pte", 303.00, 0.0, NAN], ["Ote", 279.65, 0.0, NAN]],
        "tramos": [["B-T", "B", "T", 33.0, 6.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0],
                   ["T-Pte", "T", "Pte", 3474.55, 6.0, "PVC C-900", "RD 25", NAN, 0.0, "lps", 12.0],
                   ["T-Ote", "T", "Ote", 2007.40, 4.0, "PVC C-900", "RD 25", NAN, 0.0, "lps", 6.0]],
        "params": {"g_modo": "qbomba", "g_qbomba": 18.0, "suc_nivel": 291.33, "suc_cota_eje": 290.41, "suc_alt": 2240.0}},
    "Línea simple (un solo tramo)": {
        "nodos": [["B", 100.0, 0.0, NAN], ["N", 130.0, 0.0, NAN]],
        "tramos": [["B-N", "B", "N", 1200.0, 4.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0]],
        "params": {"g_modo": "qbomba", "g_qbomba": 8.0, "suc_nivel": 101.0, "suc_cota_eje": 100.0, "suc_alt": 500.0}},
    "Tres ramales con reparto automático": {
        "nodos": [["B", 500.0, 0.0, NAN], ["N", 502.0, 0.0, NAN], ["R1", 520.0, 0.0, NAN],
                  ["R2", 495.0, 0.0, NAN], ["R3", 510.0, 0.0, NAN]],
        "tramos": [["B-N", "B", "N", 80.0, 8.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0],
                   ["N-R1", "N", "R1", 1500.0, 6.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0],
                   ["N-R2", "N", "R2", 900.0, 4.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0],
                   ["N-R3", "N", "R3", 2200.0, 6.0, "PVC C-900", "RD 25", NAN, 0.0, "auto", 0.0]],
        "params": {"g_modo": "qbomba", "g_qbomba": 30.0, "suc_nivel": 501.0, "suc_cota_eje": 500.0, "suc_alt": 1000.0}},
}
ACC0 = pd.DataFrame([["succion", "Entrada de borde vivo", 1.0, 0.5], ["succion", "Válvula compuerta abierta", 1.0, 0.2],
                     ["succion", "Codo 90° radio largo", 1.0, 0.6]], columns=["tramo", "tipo", "cant", "K"])
CLASES_TODAS = sorted({c for m in bc.CLASES.values() for c in m} | {"Única"})

PARAMS = {
    "g_modo": "qbomba", "g_pob": 0.0, "g_dot": 150.0, "g_qmed": 0.0, "g_qmd": 0.0, "g_qmh": 0.0,
    "g_qbomba": 0.0, "g_mapas": True, "g_cvd": 1.4, "g_cvh": 1.55, "g_kmin": 0.5, "g_cs": 1.5, "g_M": 2.0,
    "g_horas": 24.0, "g_tanque": True,
    "metodo": "DW", "edad_anios": 0, "grupo_agua": "II", "a_usuario": 0.0,
    "modo_menores": "pct", "pct_menores": 0.10, "colchon_min_m": 0.90,
    "presion_deseada_m": 15.0, "presion_min_perfil_m": 0.0, "escenarios": "0, 0.25, 0.5, 0.75, 1.0",
    "criterio_dn": "bresse", "v_obj": 1.5, "J_max_m_km": 4.0, "usar_opt": False, "tc_s": 0.0,
    "n_bombas": 1, "eficiencia_bomba": 0.65, "eficiencia_motor": 0.92, "factor_seguridad_motor": 1.15,
    "npsh_requerido": 0.0, "p_min_kgcm2": 1.5, "p_max_kgcm2": 5.0, "v_min": 0.3, "v_max": 5.0,
    "suc_nivel": 0.0, "suc_cota_eje": 0.0, "suc_L": 10.0, "suc_auto": True, "suc_vmax": 1.5, "suc_dn": 8.0, "suc_mat": "PVC C-900",
    "suc_clase": "RD 25", "suc_alt": 0.0, "suc_temp": 20.0,
}
ss = st.session_state
ss.setdefault("ver", 0)
ss.setdefault("nodos_base", NODOS0)
ss.setdefault("tramos_base", TRAMOS0)
ss.setdefault("acc_base", ACC0)
ss.setdefault("mat_base", pd.DataFrame(columns=MATCOLS))
ss.setdefault("perfiles", {})
for k, v in PARAMS.items():
    ss.setdefault(k, v)

# ----------------------------------------------------------------------------- lectura de perfiles
NUM = re.compile(r"\d+\+\d{3}(?:\.\d+)?|\d+(?:\.\d+)?")


def _f(tok):
    if "+" in tok:
        km, m = tok.split("+")
        return float(km) * 1000 + float(m)
    return float(tok)


def texto_a_filas(texto):
    out = []
    for linea in texto.replace(",", " ").replace(";", " ").splitlines():
        toks = NUM.findall(linea)
        if len(toks) >= 2:
            out.append([_f(t) for t in toks])
    return out


def texto_pdf(archivo):
    import pdfplumber
    partes = []
    with pdfplumber.open(archivo) as pdf:
        for pg in pdf.pages:
            partes.append(pg.extract_text() or "")
            for tabla in pg.extract_tables():
                for fila in tabla:
                    partes.append(" ".join(str(c) for c in fila if c))
    return "\n".join(partes)


def filas_a_perfil(filas, ce, cz, zmin, zmax):
    pts, ult = [], -1e18
    for f in filas:
        if len(f) < max(ce, cz):
            continue
        x, z = f[ce - 1], f[cz - 1]
        if zmax > zmin and not (zmin <= z <= zmax):
            continue
        if x > ult:
            pts.append({"estacion_m": x, "cota": z})
            ult = x
    return pd.DataFrame(pts, columns=["estacion_m", "cota"])


def nn(x, default=None):
    return default if x is None or (isinstance(x, float) and math.isnan(x)) else x


# ----------------------------------------------------------------------------- barra lateral
sb = st.sidebar
sb.header("Parámetros")
up = sb.file_uploader("Cargar configuración (.json)", type="json")
if up is not None:
    raw = up.getvalue()
    h = hashlib.md5(raw).hexdigest()
    if ss.get("_h") != h:
        d = json.loads(raw)
        ss["_h"] = h
        ss.nodos_base = pd.DataFrame(d["nodos"]).reindex(columns=NODOS0.columns).astype({"p_req_m": float})
        ss.tramos_base = pd.DataFrame([{k: v for k, v in t.items() if k != "perfil"} for t in d["tramos"]]).reindex(columns=COLS_T)
        ss.acc_base = pd.DataFrame(d.get("accesorios") or [], columns=ACC0.columns)
        ss.mat_base = pd.DataFrame(d.get("materiales_personalizados") or [], columns=MATCOLS)
        ss.perfiles = {t["id"]: t["perfil"] for t in d["tramos"] if t.get("perfil")}
        for k in PARAMS:
            if k in d.get("_ui", {}):
                ss[k] = d["_ui"][k]
        ss.ver += 1
        st.rerun()

sb.number_input("Presión deseada en el punto crítico (m.c.a.)", 0.0, 200.0, step=1.0, key="presion_deseada_m",
                help="Se mantiene constante en los extremos; cada nodo puede tener la suya en la tabla de nodos.")
sb.number_input("Margen de presión sobre el perfil (m)", 0.0, 100.0, step=1.0, key="presion_min_perfil_m",
                help="Presión mínima en cualquier punto intermedio de la línea (puntos altos).")
sb.text_input("Escenarios (fracciones del gasto de diseño)", key="escenarios")
with sb.expander("Fricción y envejecimiento", expanded=True):
    st.radio("Método", ["DW", "HW"], key="metodo", horizontal=True,
             format_func=lambda x: "Darcy-Weisbach" if x == "DW" else "Hazen-Williams")
    st.number_input("Años de servicio (tubería envejecida)", 0, 60, key="edad_anios",
                    help="MAPAS: ε_t = ε_0 + a·t. Con años > 0 se usa Darcy-Weisbach y la CDT de diseño es la envejecida.")
    st.selectbox("Grupo de agua", list(bc.GRUPOS_AGUA), key="grupo_agua",
                 format_func=lambda g: f"{g}: {bc.GRUPOS_AGUA[g][0]} (a={bc.GRUPOS_AGUA[g][1]} mm/año)")
    st.number_input("a propio (mm/año, 0 = usar el del grupo)", 0.0, 3.0, step=0.01, key="a_usuario")
with sb.expander("Pérdidas menores"):
    st.radio("Cálculo", ["pct", "K"], key="modo_menores", horizontal=True,
             format_func=lambda x: "% de la fricción" if x == "pct" else "Por accesorios (K)")
    st.slider("% de la fricción", 0.0, 0.5, step=0.01, key="pct_menores")
with sb.expander("Zanja y diámetros"):
    st.number_input("Colchón mínimo (m)", 0.3, 3.0, step=0.05, key="colchon_min_m",
                    help="Valor de la tabla de colchón mínimo de MAPAS según tu diámetro y tráfico: ajústalo.")
    st.selectbox("Criterio de diámetro óptimo", ["bresse", "velocidad", "gradiente"], key="criterio_dn",
                 format_func={"bresse": "Bresse (D=1.3·X^0.25·√Q)", "velocidad": "Velocidad objetivo",
                              "gradiente": "Pérdida unitaria máx."}.get)
    st.number_input("Velocidad objetivo (m/s)", 0.3, 5.0, step=0.1, key="v_obj")
    st.number_input("Pérdida unitaria máx. (m/km)", 0.5, 50.0, step=0.5, key="J_max_m_km")
    st.checkbox("Usar en el cálculo los diámetros óptimos", key="usar_opt")
with sb.expander("Golpe de ariete"):
    st.number_input("Tiempo de paro / cierre tc (s, 0 = instantáneo)", 0.0, 600.0, step=1.0, key="tc_s")
with sb.expander("Bomba y motor"):
    st.number_input("Bombas en paralelo", 1, 8, key="n_bombas")
    st.number_input("Eficiencia bomba", 0.2, 0.95, step=0.01, key="eficiencia_bomba")
    st.number_input("Eficiencia motor", 0.5, 0.99, step=0.01, key="eficiencia_motor")
    st.number_input("Factor de seguridad motor", 1.0, 1.5, step=0.05, key="factor_seguridad_motor")
    st.number_input("NPSH requerido (m, 0 = sin dato)", 0.0, 30.0, step=0.1, key="npsh_requerido")
    usa_curva = st.checkbox("Ingresar curva de UNA bomba (3 puntos) para estimar velocidad del VFD")
    curva = None
    if usa_curva:
        curva = st.data_editor(pd.DataFrame({"Q_lps": [0.0, 10.0, 20.0], "H_m": [45.0, 40.0, 28.0]}),
                               num_rows="fixed", key="curva_ed").values.tolist()
with sb.expander("Otros materiales (personalizados)"):
    st.caption("Define un material con sus coeficientes y la presión que soporta; luego elígelo por su nombre en la tabla de tramos "
               "(clase «Única»). `espesor_mm` vacío = estimado; DN se toma como diámetro interior.")
    mat_df = st.data_editor(ss.mat_base, num_rows="dynamic", key=f"mat_{ss.ver}", width="stretch")
MATS = MAT_BASE + [str(n).upper().strip() for n in mat_df["nombre"].dropna() if str(n).strip()]
with sb.expander("Succión"):
    st.number_input("Nivel de agua mínimo (msnm)", key="suc_nivel", format="%.2f")
    st.number_input("Cota del eje de la bomba (msnm)", key="suc_cota_eje", format="%.2f")
    st.number_input("Longitud tanque → bomba (m)", 0.0, 500.0, key="suc_L",
                    help="MAPAS no fija una longitud: la succión debe ser lo más corta posible. Valor preliminar 10 m: "
                         "captura la distancia real del tanque (o cárcamo) a la bomba.")
    st.selectbox("Material", MATS, key="suc_mat")
    st.selectbox("Clase", CLASES_TODAS, key="suc_clase")
    st.checkbox("DN de succión automático", key="suc_auto",
                help="Elige el menor DN comercial con velocidad ≤ la máxima de succión (1.5 m/s recomendada en MAPAS, "
                     "diseño de instalaciones mecánicas). Cada bomba lleva su propia succión.")
    st.number_input("Velocidad máxima en la succión (m/s)", 0.3, 3.0, step=0.1, key="suc_vmax")
    if not ss.suc_auto:
        st.selectbox("DN (pulg)", bc.DN_LISTA, key="suc_dn")
    st.number_input("Altitud del sitio (msnm)", 0.0, 5000.0, key="suc_alt")
    st.number_input("Temperatura del agua (°C)", 0.0, 60.0, key="suc_temp")
    st.caption("Accesorios de la succión (entrada, válvula, codo...): tabla de accesorios en la pestaña Red, tramo «succion». "
               "Ya trae valores típicos; edítalos con tu arreglo real.")
with sb.expander("Presión de suministro y velocidades", expanded=True):
    st.number_input("Presión mínima de suministro (kg/cm²)", 0.0, 20.0, step=0.1, key="p_min_kgcm2",
                    help="Predeterminado 1.5 kg/cm² (15 m.c.a.).")
    st.number_input("Presión máxima de suministro (kg/cm²)", 0.0, 30.0, step=0.1, key="p_max_kgcm2",
                    help="Predeterminado 5.0 kg/cm² (50 m.c.a.).")
    if ss.presion_deseada_m < ss.p_min_kgcm2 * 10:
        st.warning("La presión deseada en el punto crítico es menor que la presión mínima de suministro.")
    st.number_input("Velocidad mínima (m/s)", 0.0, 2.0, key="v_min")
    st.number_input("Velocidad máxima (m/s)", 0.5, 10.0, key="v_max")

# ----------------------------------------------------------------------------- cuerpo
st.title("Booster con presión constante")
st.caption("Gastos → red y ramales → perfil y zanja → resultados, diámetros y golpe de ariete → exportar.")
tabs = st.tabs(["1. Gastos", "2. Red", "3. Perfil y zanja", "4. Resultados", "5. Verificación de presiones",
                "6. Diámetros", "7. Golpe de ariete", "8. Exportar"])
t_gas, t_red, t_perf, t_res, t_ver, t_dn, t_ar, t_exp = tabs

with t_gas:
    LBL = {"poblacion": "Población y dotación", "qmed": "Gasto medio (Qmed)", "qmd": "Gasto máx. diario (QMD)",
           "qmh": "Gasto máx. horario (QMH)", "qbomba": "Caudal a bombear"}
    st.radio("¿Qué dato conoces?", list(LBL), key="g_modo", horizontal=True, format_func=LBL.get)
    c = st.columns(4)
    if ss.g_modo == "poblacion":
        c[0].number_input("Población (hab)", 0.0, 1e7, key="g_pob")
        c[1].number_input("Dotación (l/hab/día)", 0.0, 1000.0, key="g_dot")
        valor = 0.0
    else:
        k_ = {"qmed": "g_qmed", "qmd": "g_qmd", "qmh": "g_qmh", "qbomba": "g_qbomba"}[ss.g_modo]
        c[0].number_input(f"{LBL[ss.g_modo]} (lps)", 0.0, 1e5, key=k_)
        valor = ss[k_]
        c[1].number_input("Población (opcional, para Harmon)", 0.0, 1e7, key="g_pob")
    c[2].number_input("Horas de bombeo", 1.0, 24.0, key="g_horas")
    c[3].checkbox("Hay tanque regulador", key="g_tanque",
                  help="Con tanque se bombea QMD·24/horas; sin tanque (directo a la red) la bomba da QMH.")
    st.checkbox("Usar coeficientes predeterminados de MAPAS (CVD 1.40, CVH 1.55, Qmín 0.5·Qmed, CS 1.5)", key="g_mapas")
    if ss.g_mapas:
        cvd, cvh, kmin, cs = 1.4, 1.55, 0.5, 1.5
    else:
        d_ = st.columns(4)
        cvd = d_[0].number_input("CVD (variación diaria)", 1.0, 2.5, key="g_cvd")
        cvh = d_[1].number_input("CVH (variación horaria)", 1.0, 3.0, key="g_cvh")
        kmin = d_[2].number_input("Qmín / Qmed", 0.1, 1.0, key="g_kmin")
        cs = d_[3].number_input("CS (Qmáx ext / Qmáx inst)", 1.0, 3.0, key="g_cs")
    if ss.g_pob <= 0 and ss.g_modo != "poblacion":
        st.number_input("Coeficiente de Harmon M (sin población no se puede calcular)", 1.0, 5.0, key="g_M")
    gas = bc.calcula_gastos(ss.g_modo, valor, ss.g_pob, ss.g_dot, cvd, cvh, ss.g_horas, ss.g_tanque, kmin, cs, ss.g_M)
    M_ = gas["M"]
    tab_g = pd.DataFrame([
        ("Qmed", gas["Qmed"], "Gasto medio diario"),
        ("QMD", gas["QMD"], f"Gasto máximo diario = CVD ({cvd:g}) · Qmed"),
        ("QMH", gas["QMH"], f"Gasto máximo horario = CVH ({cvh:g}) · QMD"),
        ("Qmín", gas["Qmin"], f"Gasto mínimo = {kmin:g} · Qmed"),
        ("Qmáx inst.", gas["Qmax_inst"], f"Gasto máximo instantáneo = M ({M_:.2f}) · Qmed (Harmon)" if M_ else "requiere población o M"),
        ("Qmáx ext.", gas["Qmax_ext"], f"Gasto máximo extraordinario = CS ({cs:g}) · Qmáx inst." if M_ else "requiere población o M"),
        ("Q de bombeo", gas["Qbomba"], "USADO EN EL CÁLCULO (QMD·24/h con tanque; QMH sin tanque; o el dato directo)"),
    ], columns=["Gasto", "lps", "Descripción"])
    tab_g["m³/día"] = tab_g["lps"] * 86.4
    st.dataframe(tab_g[["Gasto", "lps", "m³/día", "Descripción"]].round(2), width="stretch", hide_index=True)
    st.caption("Qmed, QMD y QMH son los gastos de agua potable. Qmín, Qmáx inst. y Qmáx ext. se muestran como referencia "
               "(definiciones de MAPAS de alcantarillado); no gobiernan el cálculo del booster. Verifica coeficientes con tu edición de MAPAS.")

def nuevo_proyecto(nodos=None, tramos=None, params=None):
    """Se ejecuta como callback (on_click): puede modificar las claves de los widgets antes de que se dibujen."""
    ss.nodos_base = pd.DataFrame(nodos, columns=COLS_N) if nodos else NODOS0.copy()
    ss.tramos_base = pd.DataFrame(tramos, columns=COLS_T) if tramos else TRAMOS0.copy()
    ss.acc_base, ss.perfiles = ACC0, {}
    ss.mat_base = pd.DataFrame(columns=MATCOLS)
    for k, v in PARAMS.items():
        ss[k] = (params or {}).get(k, v)
    ss.pop("exp_cfg", None)
    ss.ver += 1


def cb_plantilla():
    """Solo reemplaza las tablas de nodos y tramos (conserva gastos, succión y demás parámetros)."""
    k_ = {ESQ_CONSUMOS: "esq_n_consumos", ESQ_VARIOS: "esq_n_varios", ESQ_ESPINA: "esq_n_espina"}.get(ss.esq_tipo)
    n = ss[k_] if k_ and k_ in ss else 2
    N, T = plantilla(ss.esq_tipo, int(n))
    ss.nodos_base, ss.tramos_base = pd.DataFrame(N, columns=COLS_N), pd.DataFrame(T, columns=COLS_T)
    ss.acc_base, ss.perfiles = ACC0, {}
    ss.pop("exp_cfg", None)
    ss.ver += 1


def cb_ejemplo():
    e = EJEMPLOS[ss.ej_sel]
    nuevo_proyecto(e["nodos"], e["tramos"], e["params"])


with t_red:
    st.subheader("Esquema de la red")
    st.caption("Elige cómo sale el agua de la bomba. Los ramales son opcionales: lo más común es una línea directa. "
               "Después puedes agregar o quitar filas libremente en las tablas.")
    u1, u2 = st.columns([2, 3])
    with u1:
        st.selectbox("Tipo de esquema", ESQUEMAS, key="esq_tipo")
        st.caption(ESQ_AYUDA[ss.esq_tipo])
        n_esq = 2
        if ss.esq_tipo == ESQ_CONSUMOS:
            n_esq = st.number_input("Número de consumos intermedios", 1, 10, 2, key="esq_n_consumos")
        elif ss.esq_tipo == ESQ_VARIOS:
            n_esq = st.number_input("Número de ramales", 3, 8, 3, key="esq_n_varios")
        elif ss.esq_tipo == ESQ_ESPINA:
            n_esq = st.number_input("Número de derivaciones", 2, 8, 3, key="esq_n_espina")
        st.button("Crear tablas con este esquema", type="primary", on_click=cb_plantilla, width="stretch")
        with st.expander("Ejemplos con datos / limpiar"):
            st.selectbox("Ejemplo", list(EJEMPLOS), key="ej_sel")
            st.button("Cargar ejemplo con datos", width="stretch", on_click=cb_ejemplo)
            st.button("Limpiar todo (esquema directo)", width="stretch", on_click=nuevo_proyecto)
    with u2:
        pN, pT = plantilla(ss.esq_tipo, int(n_esq))
        fm = ex.esquema_topologia(pN, pT)
        st.pyplot(fm)
        plt.close(fm)
    st.caption("No se resuelven redes malladas (con circuitos cerrados): el cálculo es para redes ramificadas.")
    st.subheader("Nodos")
    st.caption("`cota` = terreno (msnm). `demanda_lps` = consumo fijo en un nodo intermedio (opcional). "
               "`p_req_m` = presión deseada propia del nodo (vacío = la global).")
    nodos_df = st.data_editor(ss.nodos_base, num_rows="dynamic", key=f"nodos_{ss.ver}", width="stretch")
    st.subheader("Tramos")
    st.caption("`prof_m` = profundidad de zanja (vacío = mínima MAPAS). **Reparto de gasto** en cada derivación: "
               "`auto` = equilibrio hidráulico (calcula cuánto gasto toma cada ramal), `pct` = % del gasto que llega al nodo, "
               "`lps` = gasto fijo (en `valor`). Si mezclas, los `auto` reparten lo que sobra.")
    tramos_df = st.data_editor(
        ss.tramos_base, num_rows="dynamic", key=f"tramos_{ss.ver}", width="stretch",
        column_config={
            "dn_pulg": st.column_config.SelectboxColumn("dn_pulg", options=bc.DN_LISTA),
            "material": st.column_config.SelectboxColumn(options=MATS),
            "clase": st.column_config.SelectboxColumn(options=CLASES_TODAS),
            "modo": st.column_config.SelectboxColumn(options=["auto", "pct", "lps"])})
    ids_t = [str(x) for x in tramos_df["id"].dropna()]
    ids_n = [str(x) for x in nodos_df["id"].dropna()]
    st.subheader("Accesorios (K)")
    st.caption("Se usan si en la barra lateral eliges pérdidas menores «Por accesorios (K)». "
               "K vacío = valor típico del catálogo; edítalo con el dato de tu fabricante.")
    acc_df = st.data_editor(
        ss.acc_base, num_rows="dynamic", key=f"acc_{ss.ver}", width="stretch",
        column_config={"tramo": st.column_config.SelectboxColumn(options=ids_t + ["succion"]),
                       "tipo": st.column_config.SelectboxColumn(options=list(bc.ACCESORIOS_K))})
    raiz = st.selectbox("Nodo raíz (descarga de la bomba)", ids_n, index=0 if ids_n else None)


def build_cfg():
    nodos = [{"id": str(r.id), "cota": float(r.cota), "demanda_lps": float(nn(r.demanda_lps, 0.0)),
              "p_req_m": nn(r.p_req_m)} for r in nodos_df.dropna(subset=["id", "cota"]).itertuples()]
    tramos = []
    for r in tramos_df.dropna(subset=["id", "desde", "hasta", "L", "dn_pulg"]).itertuples():
        t = {"id": str(r.id), "desde": str(r.desde), "hasta": str(r.hasta), "L": float(r.L), "dn_pulg": float(r.dn_pulg),
             "material": nn(r.material, "PVC C-900"), "clase": nn(r.clase, "RD 25"), "prof_m": nn(r.prof_m),
             "K": float(nn(r.K, 0.0)), "modo": nn(r.modo, "auto"), "valor": float(nn(r.valor, 0.0))}
        if str(r.id) in ss.perfiles:
            t["perfil"] = ss.perfiles[str(r.id)]
        tramos.append(t)
    acc = []
    for r in acc_df.dropna(subset=["tramo", "tipo"]).itertuples():
        acc.append({"tramo": str(r.tramo), "tipo": str(r.tipo), "cant": float(nn(r.cant, 1.0)),
                    "K": float(nn(r.K, bc.ACCESORIOS_K.get(str(r.tipo), 1.0)))})
    esc = sorted({float(x) for x in re.split(r"[,\s]+", ss.escenarios.strip()) if x} | {1.0})
    cfg = {
        "raiz": raiz, "Q_bombeo_lps": gas["Qbomba"], "nodos": nodos, "tramos": tramos, "accesorios": acc,
        "metodo": ss.metodo, "edad_anios": ss.edad_anios, "grupo_agua": ss.grupo_agua,
        "a_envejec_mm_anio": ss.a_usuario or None, "modo_menores": ss.modo_menores, "pct_menores": ss.pct_menores,
        "colchon_min_m": ss.colchon_min_m, "presion_deseada_m": ss.presion_deseada_m,
        "presion_min_perfil_m": ss.presion_min_perfil_m, "escenarios": esc, "n_bombas": int(ss.n_bombas),
        "eficiencia_bomba": ss.eficiencia_bomba, "eficiencia_motor": ss.eficiencia_motor,
        "factor_seguridad_motor": ss.factor_seguridad_motor, "npsh_requerido": ss.npsh_requerido or None,
        "curva_bomba": curva, "tc_s": ss.tc_s, "criterio_dn": ss.criterio_dn, "v_obj": ss.v_obj,
        "J_max_m_km": ss.J_max_m_km, "horas_bombeo": ss.g_horas,
        "limites": {"p_min": ss.p_min_kgcm2 * 10, "p_max": ss.p_max_kgcm2 * 10, "v_min": ss.v_min, "v_max": ss.v_max},
        "materiales_personalizados": [
            {"nombre": str(r.nombre).upper().strip(), "C": nn(r.C), "eps0_mm": nn(r.eps0_mm), "E_kgcm2": nn(r.E_kgcm2),
             "espesor_mm": nn(r.espesor_mm), "P_trab_kgcm2": nn(r.P_trab_kgcm2)}
            for r in mat_df.dropna(subset=["nombre"]).itertuples() if str(r.nombre).strip()],
        "gastos": gas,
        "succion": {"nivel_agua": ss.suc_nivel, "cota_eje_bomba": ss.suc_cota_eje, "L": ss.suc_L,
                    "dn_pulg": ss.suc_dn, "material": ss.suc_mat, "clase": ss.suc_clase, "K": 0.0,
                    "auto_dn": ss.suc_auto, "v_max": ss.suc_vmax,
                    "altitud_m": ss.suc_alt, "temp_c": ss.suc_temp},
        "_ui": {k: ss[k] for k in PARAMS},
    }
    return cfg


err, cfg, res = None, None, None
blanco = not [x for x in nodos_df["id"].dropna()] or not [x for x in tramos_df["id"].dropna()]
faltan = []
for r_ in nodos_df.dropna(subset=["id"]).itertuples():
    if nn(r_.cota) is None:
        faltan.append(f"nodo {r_.id}: cota")
for r_ in tramos_df.dropna(subset=["id"]).itertuples():
    faltan += [f"tramo {r_.id}: {c_}" for c_ in ("desde", "hasta", "L", "dn_pulg") if nn(getattr(r_, c_)) in (None, "")]
if blanco:
    err = "Proyecto en blanco: elige un esquema en la pestaña Red o carga un ejemplo."
elif faltan:
    blanco = True
    err = "Faltan datos por capturar en la pestaña Red: " + "; ".join(faltan[:12]) + (" ..." if len(faltan) > 12 else "")
else:
    try:
        cfg = build_cfg()
        if ss.usar_opt:
            cfg = bc.optimiza_diametros(cfg)
        res = bc.analizar(cfg)
    except Exception as e:                           # noqa: BLE001
        err = e

def persistir_tablas(tid, L_nueva):
    """Conserva lo capturado en las tablas y cambia la L de un tramo (reinicia los editores con ver+1)."""
    df = tramos_df.copy()
    df.loc[df["id"] == tid, "L"] = round(float(L_nueva), 2)
    ss.tramos_base, ss.nodos_base, ss.acc_base = df, nodos_df.copy(), acc_df.copy()
    ss.ver += 1


with t_perf:
    ids_t2 = [str(x) for x in tramos_df["id"].dropna()]
    st.write("Carga el perfil del terreno de un tramo desde **PDF** (tabla/texto de estación y cota) o **CSV/TXT**. "
             "Si el PDF es solo dibujo, expórtalo a tabla desde tu software de topografía/CAD. "
             "Sin perfil, el tramo usa la línea recta entre las cotas de sus nodos.")
    st.checkbox("Al asignar un perfil, ajustar la longitud L del tramo a la longitud del perfil", key="aj_L", value=True)
    t_sel = st.selectbox("Tramo", ids_t2) if ids_t2 else None
    upf = st.file_uploader("Perfil del terreno", type=["pdf", "csv", "txt"], key=f"perf_{t_sel}")
    if t_sel and upf is not None:
        try:
            texto = texto_pdf(upf) if upf.name.lower().endswith(".pdf") else upf.getvalue().decode("utf-8", "ignore")
            filas = texto_a_filas(texto)
        except Exception as e:                       # noqa: BLE001
            filas = []
            st.error(f"No se pudo leer el archivo: {e}")
        if not filas:
            st.warning("No se encontraron pares (estación, cota). Prueba con CSV o captura los puntos a mano.")
        else:
            nmax = max(len(f) for f in filas)
            a, b, c, d = st.columns(4)
            ce = a.number_input("Columna de estación", 1, nmax, 1)
            cz = b.number_input("Columna de cota", 1, nmax, min(2, nmax))
            zmin = c.number_input("Cota mínima válida", value=0.0)
            zmax = d.number_input("Cota máxima válida (0 = sin filtro)", value=0.0)
            prev = filas_a_perfil(filas, int(ce), int(cz), zmin, zmax)
            st.write(f"{len(prev)} puntos detectados. Edita o borra los que estén mal antes de asignar:")
            prev = st.data_editor(prev, num_rows="dynamic", key=f"prev_{t_sel}_{upf.name}", width="stretch")
            if st.button("Asignar perfil al tramo", type="primary") and len(prev) >= 2:
                ss.perfiles[t_sel] = prev.dropna().values.tolist()
                if ss.aj_L:
                    persistir_tablas(t_sel, ss.perfiles[t_sel][-1][0] - ss.perfiles[t_sel][0][0])
                st.rerun()
    if ss.perfiles:
        Ltab = {str(r_.id): nn(r_.L) for r_ in tramos_df.dropna(subset=["id"]).itertuples()}
        for tid, pts in list(ss.perfiles.items()):
            c1, c2 = st.columns([5, 1])
            span = pts[-1][0] - pts[0][0]
            c1.write(f"**{tid}**: {len(pts)} puntos, estaciones {pts[0][0]:.0f} a {pts[-1][0]:.0f} m "
                     f"(longitud del perfil {span:.2f} m), cota máx {max(p[1] for p in pts):.2f}")
            if c2.button("Quitar", key=f"q_{tid}"):
                del ss.perfiles[tid]
                st.rerun()
            L_ = Ltab.get(tid)
            if L_ is not None and span > 0 and abs(L_ - span) / span > 0.01:
                w1, w2 = st.columns([5, 1])
                w1.warning(f"Tramo {tid}: L de la tabla = {L_:g} m, pero el perfil mide {span:.2f} m. "
                           "El perfil se reescala a L; corrige el dato que esté mal.")
                if w2.button(f"Usar {span:.2f} m", key=f"ajL_{tid}"):
                    persistir_tablas(tid, span)
                    st.rerun()
    if err:
        (st.info if blanco else st.error)(str(err) if blanco else f"Revisa los datos de la red: {err}")
    else:
        tb0 = ex.tablas(cfg, res)
        st.subheader("Profundidad de zanja y cotas de tubería")
        st.caption("MAPAS: profundidad mínima = 0.70 m (tubería ≤ 51 mm) o D ext. + 0.05 m + colchón mínimo. "
                   "La cota de eje de la tubería = terreno − profundidad + 0.05 m (cama) + radio exterior; las presiones se miden sobre el eje.")
        st.dataframe(tb0["Zanja y cotas"].round(2), width="stretch", hide_index=True)
        for _, r in tb0["Zanja y cotas"].iterrows():
            if r["Obs"]:
                st.warning(f"Tramo {r['Tramo']}: {r['Obs']}")
        st.subheader("Esquema de la zanja")
        st.caption("Sección transversal del tramo elegido con los datos que usa la app: profundidad de zanja (`prof_m`), cama de 5 cm, "
                   "colchón sobre la clave, ancho de zanja (D ext. + 0.50 m si D ext. < 0.50 m; + 0.60 m en los demás) y cotas resultantes.")
        tz = st.selectbox("Tramo para la sección de zanja", [t.id for t in res["red"].tramos], key="zanja_sel")
        fz = ex.zanja_fig(next(t for t in res["red"].tramos if t.id == tz))
        st.pyplot(fz)
        plt.close(fz)
        st.subheader("Perfiles de los ramales (terreno, tubería y línea piezométrica)")
        fp = ex.perfiles_fig(res)
        st.pyplot(fp)
        plt.close(fp)

with t_res:
    if err:
        (st.info if blanco else st.error)(str(err) if blanco else f"Revisa los datos: {err}")
        st.stop()
    if ss.suc_nivel == 0 and ss.suc_cota_eje == 0:
        st.warning("Captura el nivel del agua y la cota del eje de la bomba en «Succión» (barra lateral); "
                   "ahora valen 0 y el resultado no es válido.")
    if gas["Qbomba"] <= 0:
        st.warning("El caudal de bombeo es 0: captura el gasto en la pestaña Gastos.")
    red, dis, nb, lim = res["red"], bc.diseno(res), cfg["n_bombas"], cfg["limites"]
    eq, tb = bc.equipo(cfg, res), ex.tablas(cfg, res)
    for av in res["avisos"]:
        st.warning(av)
    m = st.columns(5)
    m[0].metric("Capacidad", f"{eq['Q']:.1f} lps", f"{eq['Q'] / nb:.1f} lps/bomba" if nb > 1 else None)
    m[1].metric("CDT de diseño", f"{eq['H']:.2f} m",
                f"nueva: {res['nueva']['CDT']:.2f} m" if cfg["edad_anios"] else None, delta_color="off")
    m[2].metric("Potencia motor", f"{eq['hp']:.1f} HP", f"comercial: {eq['hp_com']:g} HP", delta_color="off")
    m[3].metric("NPSH disponible", f"{eq['npshd']:.2f} m")
    m[4].metric("Punto crítico", eq["crit"])
    if eq["marg"] is not None:
        (st.success if eq["marg"] >= 1 else st.error)(f"Margen de NPSH: {eq['marg']:.2f} m (recomendado ≥ 1 m)")
    if "@" in eq["crit"]:
        st.warning(f"Gobierna un punto intermedio del perfil ({eq['crit']}), no un extremo.")
    ts_, vs_ = res["tsuc"], dis["V_succ"]
    msg_s = (f"Succión (tanque → bomba): DN {ts_.dn_pulg:g}\" {ts_.material} {ts_.clase}"
             f"{' (automático)' if ss.suc_auto else ''}, L = {ts_.L:g} m, V = {vs_:.2f} m/s, pérdidas = {dis['hf_succ']:.2f} m "
             f"(ΣK = {ts_.Ksum:.2f}). Velocidad recomendada ≤ {ss.suc_vmax:g} m/s.")
    (st.success if vs_ <= ss.suc_vmax + 1e-9 else st.warning)(msg_s)
    dg = bc.desglose_cdt(cfg, res)
    st.subheader("Cómo se calcula la CDT")
    st.markdown("**CDT = H descarga − H succión**, donde *H succión* = nivel del agua − pérdidas de la succión, y "
                "*H descarga* = cota de tubería del punto crítico + presión requerida + pérdidas de la ruta crítica.")
    cc1, cc2 = st.columns([3, 2])
    cc1.dataframe(pd.DataFrame(dg["desglose"]).round(3), width="stretch", hide_index=True)
    cc2.dataframe(pd.DataFrame(dg["componentes"]).round(2), width="stretch", hide_index=True)
    st.markdown("**Ruta crítica** (tramos desde la bomba hasta el punto que gobierna)")
    st.dataframe(pd.DataFrame(dg["ruta"]).round(3), width="stretch", hide_index=True)
    st.markdown("**Pérdidas en la succión (hf y hm)**")
    sdf = pd.DataFrame(dg["succion"])
    sdf["Valor"] = sdf["Valor"].apply(lambda v: f"{v:,.3f}" if isinstance(v, float) else ("" if v is None else str(v)))
    st.dataframe(sdf, width="stretch", hide_index=True)
    with st.expander("Notas y tips sobre la CDT", expanded=False):
        st.markdown("""
**Cómo se obtiene**
1. **Succión:** `H succión = nivel del agua − (hf + hm)`. Cada bomba tiene su propia línea; el gasto es el de una bomba.
2. **Punto crítico:** para cada nodo final (y cada punto alto del perfil) se calcula `cota de tubería + presión requerida + pérdidas del camino desde la bomba`. El mayor valor es la **carga en la descarga** y ese punto gobierna.
3. **Pérdidas:** `hf = f·(L/D)·V²/2g` (Darcy-Weisbach, f por Swamee-Jain; con años de servicio se usa la rugosidad envejecida ε₀ + a·t). `hm` = % de hf **o** `ΣK·V²/2g` según elijas.
4. **CDT** = `H descarga − H succión`. Equivale a: *estática + presión requerida + fricción + menores + pérdidas de succión*.
5. Se calcula para cada escenario de gasto; el **de diseño** es el de mayor gasto. No se incluye la diferencia de carga de velocidad entre entrada y salida de la bomba (suele ser menor a 0.1 m).

**Tips**
- La **tabla de componentes** muestra qué pesa más. Si domina la fricción, aumentar el diámetro del tramo de mayor hf baja la CDT y la potencia (pestaña Diámetros muestra ΔCDT y ΔHP).
- Si el punto crítico dice `tramo@xxx m`, gobierna un **punto alto del perfil**, no el extremo: revisa ese punto o ajusta la profundidad de zanja ahí.
- La **tubería envejecida** y la presión deseada alta aumentan la CDT; revisa que no sean mayores a lo necesario.
- En la **succión** los accesorios (K) y una línea larga o de diámetro chico restan carga y bajan el NPSH disponible: mantén la succión corta, con pocos codos y velocidad ≤ 1.5 m/s.
- Los ramales con **presión sobrante** (nodos en naranja) no suben la CDT, pero requieren válvula reductora o de control de flujo.
- Potencia hidráulica ≈ `Q × CDT / 102` (kW, con Q en lps). Cada metro de CDT en 10 lps son unos 0.1 kW hidráulicos (≈ 0.16 kW al motor con eficiencia global de 60 %).
- La consigna de presión del variador en la descarga es `H descarga − cota de tubería de la bomba`.
- Si cambias la longitud, el diámetro o la cota de un tramo, revisa que el perfil y la tabla coincidan (la app avisa).
""")
    st.subheader("Esquema general")
    st.caption("El gasto de cada ramal se reparte según el modo elegido (auto / % / lps); con ese gasto se calculan velocidad y "
               "pérdidas; la presión deseada en el nodo crítico fija la carga en la bomba; los demás nodos reciben la presión que "
               "resulte (si sobra, hay que regular el ramal).")
    fe = ex.esquema_fig(cfg, res)
    st.pyplot(fe)
    plt.close(fe)
    st.subheader("Distribución del gasto por ramal")
    dist = tb["Tramos"][["Tramo", "Desde", "Hasta", "Q lps", "% del gasto"]]
    st.dataframe(dist.round(2), width="stretch", hide_index=True)
    st.subheader("Tramos (diseño)")
    st.dataframe(tb["Tramos"].round(2), width="stretch", hide_index=True)
    st.subheader("Presiones en nodos")
    st.caption("`P nueva (máx.)` = presión con tubería nueva y la bomba ajustada a la CDT envejecida (caso más alto). "
               "`P estática` = nivel del agua − cota de tubería (bomba parada).")
    st.dataframe(tb["Nodos"].round(2), width="stretch", hide_index=True)
    st.subheader("Curva del sistema a presión constante")
    st.dataframe(tb["Curva sistema"].round(2), width="stretch", hide_index=True)
    fc = ex.curva_fig(cfg, res)
    st.pyplot(fc)
    plt.close(fc)
    st.caption("Valores por defecto típicos de MAPAS-CONAGUA y bibliografía; confirma con la edición vigente, tu organismo operador y el fabricante.")


def pinta(df, cols):
    def c(v):
        return ("background-color:#c6efce;color:#006100" if v == "CUMPLE"
                else "background-color:#ffc7ce;color:#9c0006" if v == "NO CUMPLE" else "")
    sty = df.style.format(precision=2, na_rep="—")
    return sty.map(c, subset=cols) if hasattr(sty, "map") else sty.applymap(c, subset=cols)


with t_ver:
    if err:
        st.stop()
    vn, vt, ok_g = bc.verifica_presiones(cfg, res)
    (st.success if ok_g else st.error)(
        "CUMPLE: las presiones de suministro y de tubería están dentro de los límites." if ok_g
        else "NO CUMPLE: revisa las filas marcadas en rojo.")
    st.subheader("Presiones de suministro: resultado vs datos del proyecto")
    st.caption(f"Proyecto: mínima {ss.p_min_kgcm2:g} kg/cm² y máxima {ss.p_max_kgcm2:g} kg/cm². Se verifican los nodos de consumo "
               "(extremos y nodos con demanda). `P máx. operación` = mayor presión entre los escenarios de gasto y la tubería nueva "
               "con la bomba a la CDT de diseño.")
    st.dataframe(pinta(pd.DataFrame(vn), ["Mínima", "Máxima"]), width="stretch", hide_index=True)
    st.subheader("Presión de la tubería: resultado vs presión que soporta")
    st.caption("`Soporta` = presión de trabajo de la clase (PVC C-900: RD 25 = 11.6, RD 18 = 16.5, RD 14 = 21.4 kg/cm²; "
               "otros materiales según su tabla). «Con ariete» suma la sobrepresión estimada (pestaña Golpe de ariete). "
               "Verifica con el fabricante la sobrepresión transitoria admisible de la clase.")
    st.dataframe(pinta(pd.DataFrame(vt), ["Operación", "Con ariete"]), width="stretch", hide_index=True)


@st.cache_data(show_spinner="Calculando alternativas...")
def alts_cache(cfg_json, tid):
    return bc.alternativas(json.loads(cfg_json), tid)


with t_dn:
    if err:
        st.stop()
    st.write(f"Criterio de diámetro óptimo: **{ss.criterio_dn}**. El óptimo se calcula con el gasto de diseño de cada tramo.")
    st.dataframe(tb["Diámetros"].round(2), width="stretch", hide_index=True)
    tq = [t.id for t in red.tramos if dis["det"][t.id]["Q"] > 0]
    sel = st.selectbox("Ver alternativas del tramo", tq)
    if sel:
        alt = alts_cache(json.dumps({k: v for k, v in cfg.items() if k != "_ui"}), sel)
        adf = pd.DataFrame(alt["filas"]).round(2)
        st.write(f"Gasto del tramo: **{alt['Q']:.2f} lps** · DN óptimo: **{alt['optimo']}\"** · DN actual: **{alt['actual']}\"**")
        st.dataframe(adf, width="stretch", hide_index=True)
        txt = bc.advertencia_cambio(alt)
        (st.info if alt["actual"] == alt["optimo"] else st.warning)(txt)
        st.caption("ΔCDT y ΔHP se calculan con TODO el sistema (los demás tramos no cambian). "
                   "El criterio de óptimo es técnico; un análisis económico real requiere costos de tubería y energía.")

with t_ar:
    if err:
        st.stop()
    st.write("Estimación de sobrepresión y depresión por **paro de bomba o cierre de válvula**: Joukowsky si tc ≤ 2L/a, "
             "Michaud si tc > 2L/a. Celeridad por Allievi. Se evalúa cada tramo con su velocidad de diseño.")
    st.dataframe(tb["Golpe de ariete"].round(2), width="stretch", hide_index=True)
    for _, r in tb["Golpe de ariete"].iterrows():
        if r["Obs"]:
            st.warning(f"Tramo {r['Tramo']}: {r['Obs']}")
    st.caption("`P máx` = presión de operación o estática más ΔH; `P mín` = presión mínima de la línea menos ΔH. "
               "Es un cálculo preliminar: MAPAS (Fenómenos transitorios en líneas de conducción) recomienda simular el transitorio "
               "y definir dispositivos (válvulas de alivio, anticipadoras, de aire, tanques unidireccionales, volante de inercia).")

with t_exp:
    if err:
        st.stop()
    st.write("Exporta la memoria completa: esquema, perfiles, gráficos, tablas de resultados y datos de entrada.")

    @st.cache_data(show_spinner="Generando archivos...")
    def genera_export(cfg_json):
        c = json.loads(cfg_json)
        r = bc.analizar(c)
        return ex.a_pdf(c, r), ex.a_excel(c, r), ex.a_zip_png(c, r)

    if st.button("Generar archivos de exportación", type="primary"):
        ss["exp_cfg"] = json.dumps(cfg)
    if ss.get("exp_cfg"):
        if ss["exp_cfg"] != json.dumps(cfg):
            st.info("Cambiaste datos desde la última generación: vuelve a generar para actualizar los archivos.")
        pdf_b, xls_b, zip_b = genera_export(ss["exp_cfg"])
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button("Memoria PDF", pdf_b, "memoria_booster.pdf", "application/pdf")
        c2.download_button("Excel (tablas + gráficos)", xls_b, "resultados_booster.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        c3.download_button("Gráficos PNG (.zip)", zip_b, "graficos_booster.zip", "application/zip")
        c4.download_button("Configuración (.json)", json.dumps(cfg, indent=1), "booster_config.json")
