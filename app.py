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

MATS = list(bc.MATERIALES)
NODOS0 = pd.DataFrame([
    {"id": "B", "cota": 290.41, "demanda_lps": 0.0, "p_req_m": float("nan")},
    {"id": "T", "cota": 290.96, "demanda_lps": 0.0, "p_req_m": float("nan")},
    {"id": "Pte", "cota": 303.00, "demanda_lps": 0.0, "p_req_m": float("nan")},
    {"id": "Ote", "cota": 279.65, "demanda_lps": 0.0, "p_req_m": float("nan")},
])
COLS_T = ["id", "desde", "hasta", "L", "dn_pulg", "material", "clase", "prof_m", "K", "modo", "valor"]
TRAMOS0 = pd.DataFrame([
    ["B-T", "B", "T", 42.0, 6.0, "PVC", "RD 26", float("nan"), 0.0, "auto", 0.0],
    ["T-Pte", "T", "Pte", 3474.55, 6.0, "PVC", "RD 26", float("nan"), 0.0, "lps", 12.0],
    ["T-Ote", "T", "Ote", 2007.40, 4.0, "PVC", "RD 26", float("nan"), 0.0, "lps", 6.0]], columns=COLS_T)
ACC0 = pd.DataFrame(columns=["tramo", "tipo", "cant", "K"])
CLASES_TODAS = sorted({c for m in bc.CLASES.values() for c in m})

PARAMS = {
    "g_modo": "qbomba", "g_pob": 5000.0, "g_dot": 150.0, "g_qmed": 8.0, "g_qbomba": 18.0, "g_cvd": 1.4,
    "g_cvh": 1.55, "g_horas": 24.0, "g_tanque": True,
    "metodo": "DW", "edad_anios": 0, "grupo_agua": "II", "a_usuario": 0.0,
    "modo_menores": "pct", "pct_menores": 0.10, "colchon_min_m": 0.90,
    "presion_deseada_m": 15.0, "presion_min_perfil_m": 0.0, "escenarios": "0, 0.25, 0.5, 0.75, 1.0",
    "criterio_dn": "bresse", "v_obj": 1.5, "J_max_m_km": 4.0, "usar_opt": False, "tc_s": 0.0,
    "n_bombas": 1, "eficiencia_bomba": 0.65, "eficiencia_motor": 0.92, "factor_seguridad_motor": 1.15,
    "npsh_requerido": 0.0, "p_min": 10.0, "p_max": 50.0, "v_min": 0.3, "v_max": 5.0,
    "suc_nivel": 291.33, "suc_cota_eje": 290.41, "suc_L": 6.0, "suc_dn": 8.0, "suc_mat": "PVC",
    "suc_clase": "RD 26", "suc_alt": 2240.0, "suc_temp": 20.0,
}
ss = st.session_state
ss.setdefault("ver", 0)
ss.setdefault("nodos_base", NODOS0)
ss.setdefault("tramos_base", TRAMOS0)
ss.setdefault("acc_base", ACC0)
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
with sb.expander("Succión"):
    st.number_input("Nivel de agua mínimo (msnm)", key="suc_nivel", format="%.2f")
    st.number_input("Cota del eje de la bomba (msnm)", key="suc_cota_eje", format="%.2f")
    st.number_input("Longitud (m)", 0.0, 500.0, key="suc_L")
    st.selectbox("Material", MATS, key="suc_mat")
    st.selectbox("DN (pulg)", bc.DN_LISTA, key="suc_dn")
    st.selectbox("Clase", CLASES_TODAS, key="suc_clase")
    st.number_input("Altitud del sitio (msnm)", 0.0, 5000.0, key="suc_alt")
    st.number_input("Temperatura del agua (°C)", 0.0, 60.0, key="suc_temp")
    st.caption("Accesorios de la succión: en la pestaña Red, tramo «succion».")
with sb.expander("Límites de verificación (típicos MAPAS; ajusta a tu norma)"):
    st.number_input("Presión mínima (m.c.a.)", 0.0, 100.0, key="p_min")
    st.number_input("Presión máxima (m.c.a.)", 0.0, 200.0, key="p_max")
    st.number_input("Velocidad mínima (m/s)", 0.0, 2.0, key="v_min")
    st.number_input("Velocidad máxima (m/s)", 0.5, 10.0, key="v_max")

# ----------------------------------------------------------------------------- cuerpo
st.title("Booster con presión constante")
st.caption("Gastos → red y ramales → perfil y zanja → resultados, diámetros y golpe de ariete → exportar.")
tabs = st.tabs(["1. Gastos", "2. Red", "3. Perfil y zanja", "4. Resultados", "5. Diámetros",
                "6. Golpe de ariete", "7. Exportar"])
t_gas, t_red, t_perf, t_res, t_dn, t_ar, t_exp = tabs

with t_gas:
    st.radio("¿Qué dato conoces?", ["poblacion", "qmed", "qbomba"], key="g_modo", horizontal=True,
             format_func={"poblacion": "Población y dotación", "qmed": "Gasto medio (Qmed)",
                          "qbomba": "Caudal a bombear"}.get)
    c = st.columns(5)
    if ss.g_modo == "poblacion":
        c[0].number_input("Población", 0.0, 1e7, key="g_pob")
        c[1].number_input("Dotación (l/hab/día)", 0.0, 1000.0, key="g_dot")
    elif ss.g_modo == "qmed":
        c[0].number_input("Qmed (lps)", 0.0, 1e5, key="g_qmed")
    else:
        c[0].number_input("Caudal a bombear (lps)", 0.0, 1e5, key="g_qbomba")
    if ss.g_modo != "qbomba":
        c[2].number_input("CVD", 1.0, 2.0, key="g_cvd")
        c[3].number_input("CVH", 1.0, 3.0, key="g_cvh")
        st.checkbox("Hay tanque regulador (se bombea QMD en las horas de bombeo; si no, QMH)", key="g_tanque")
        c[4].number_input("Horas de bombeo", 1.0, 24.0, key="g_horas")
    gas = bc.calcula_gastos(ss.g_modo, ss.g_pob, ss.g_dot, ss.g_qmed, ss.g_qbomba, ss.g_cvd, ss.g_cvh,
                            ss.g_horas, ss.g_tanque)
    if ss.g_modo != "qbomba":
        m = st.columns(4)
        m[0].metric("Qmed", f"{gas['Qmed']:.2f} lps")
        m[1].metric("QMD", f"{gas['QMD']:.2f} lps")
        m[2].metric("QMH", f"{gas['QMH']:.2f} lps")
        m[3].metric("Q de bombeo", f"{gas['Qbomba']:.2f} lps")
    else:
        st.metric("Q de bombeo", f"{gas['Qbomba']:.2f} lps")
    st.caption("CVD y CVH típicos de MAPAS: 1.2–1.4 y 1.5–1.8 según la localidad; confirma los de tu proyecto.")

with t_red:
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
             "material": nn(r.material, "PVC"), "clase": nn(r.clase, "RD 26"), "prof_m": nn(r.prof_m),
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
        "limites": {"p_min": ss.p_min, "p_max": ss.p_max, "v_min": ss.v_min, "v_max": ss.v_max},
        "succion": {"nivel_agua": ss.suc_nivel, "cota_eje_bomba": ss.suc_cota_eje, "L": ss.suc_L,
                    "dn_pulg": ss.suc_dn, "material": ss.suc_mat, "clase": ss.suc_clase, "K": 0.0,
                    "altitud_m": ss.suc_alt, "temp_c": ss.suc_temp},
        "_ui": {k: ss[k] for k in PARAMS},
    }
    return cfg


err, cfg, res = None, None, None
try:
    cfg = build_cfg()
    if ss.usar_opt:
        cfg = bc.optimiza_diametros(cfg)
    res = bc.analizar(cfg)
except Exception as e:                               # noqa: BLE001
    err = e

with t_perf:
    ids_t2 = [str(x) for x in tramos_df["id"].dropna()]
    st.write("Carga el perfil del terreno de un tramo desde **PDF** (tabla/texto de estación y cota) o **CSV/TXT**. "
             "Si el PDF es solo dibujo, expórtalo a tabla desde tu software de topografía/CAD. "
             "Sin perfil, el tramo usa la línea recta entre las cotas de sus nodos.")
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
                st.rerun()
    if ss.perfiles:
        for tid, pts in list(ss.perfiles.items()):
            c1, c2 = st.columns([5, 1])
            c1.write(f"**{tid}**: {len(pts)} puntos, estaciones {pts[0][0]:.0f} a {pts[-1][0]:.0f} m, "
                     f"cota máx {max(p[1] for p in pts):.2f}")
            if c2.button("Quitar", key=f"q_{tid}"):
                del ss.perfiles[tid]
                st.rerun()
    if err:
        st.error(f"Revisa los datos de la red: {err}")
    else:
        tb0 = ex.tablas(cfg, res)
        st.subheader("Profundidad de zanja y cotas de tubería")
        st.caption("MAPAS: profundidad mínima = 0.70 m (tubería ≤ 51 mm) o D ext. + 0.05 m + colchón mínimo. "
                   "La cota de eje de la tubería = terreno − profundidad + 0.05 m (cama) + radio exterior; las presiones se miden sobre el eje.")
        st.dataframe(tb0["Zanja y cotas"].round(2), width="stretch", hide_index=True)
        for _, r in tb0["Zanja y cotas"].iterrows():
            if r["Obs"]:
                st.warning(f"Tramo {r['Tramo']}: {r['Obs']}")
        st.subheader("Perfiles de los ramales (terreno, tubería y línea piezométrica)")
        fp = ex.perfiles_fig(res)
        st.pyplot(fp)
        plt.close(fp)

with t_res:
    if err:
        st.error(f"Revisa los datos: {err}")
        st.stop()
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
