# -*- coding: utf-8 -*-
"""
App de cálculo de BOOSTER (varios ramales, presión constante) con perfil de terreno opcional.

Instalación:   pip install streamlit pdfplumber pandas matplotlib
Ejecución:     streamlit run app_booster.py
Requiere booster_cdt.py en la misma carpeta (motor de cálculo).
"""
import hashlib
import io
import json
import math
import re

import pandas as pd
import streamlit as st

import booster_cdt as bc

st.set_page_config(page_title="Booster - CDT a presión constante", layout="wide")

NODOS0 = pd.DataFrame([
    {"id": "B", "cota": 290.41, "demanda_lps": 0.0, "p_req_m": float("nan")},
    {"id": "T", "cota": 290.96, "demanda_lps": 0.0, "p_req_m": float("nan")},
    {"id": "Pte", "cota": 303.00, "demanda_lps": 12.0, "p_req_m": float("nan")},
    {"id": "Ote", "cota": 279.65, "demanda_lps": 6.0, "p_req_m": float("nan")},
])
TRAMOS0 = pd.DataFrame([
    {"id": "B-T", "desde": "B", "hasta": "T", "L": 42.0, "D_mm": 152.4, "material": "PVC", "K": 0.0},
    {"id": "T-Pte", "desde": "T", "hasta": "Pte", "L": 3474.55, "D_mm": 152.4, "material": "PVC", "K": 0.0},
    {"id": "T-Ote", "desde": "T", "hasta": "Ote", "L": 2007.40, "D_mm": 101.6, "material": "PVC", "K": 0.0},
])
PARAMS = {"metodo": "DW", "pct_menores": 0.10, "presion_deseada_m": 15.0, "presion_min_perfil_m": 0.0,
          "n_bombas": 1, "eficiencia_bomba": 0.65, "eficiencia_motor": 0.92, "factor_seguridad_motor": 1.15,
          "npsh_requerido": 0.0, "p_min": 10.0, "p_max": 50.0, "v_min": 0.3, "v_max": 5.0,
          "suc_nivel": 291.33, "suc_cota_eje": 290.41, "suc_L": 6.0, "suc_D": 203.2, "suc_K": 1.5,
          "suc_alt": 2240.0, "suc_temp": 20.0, "escenarios": "0, 0.25, 0.5, 0.75, 1.0"}

ss = st.session_state
ss.setdefault("ver", 0)
ss.setdefault("nodos_base", NODOS0)
ss.setdefault("tramos_base", TRAMOS0)
ss.setdefault("perfiles", {})
for k, v in PARAMS.items():
    ss.setdefault(k, v)


# ------------------------------------------------------------------ lectura de perfiles
NUM = re.compile(r"\d+\+\d{3}(?:\.\d+)?|\d+(?:\.\d+)?")


def _a_float(tok):
    if "+" in tok:                                   # estación tipo 1+250.00 -> 1250.00 m
        km, m = tok.split("+")
        return float(km) * 1000 + float(m)
    return float(tok)


def texto_a_filas(texto):
    filas = []
    for linea in texto.replace(",", " ").replace(";", " ").splitlines():
        toks = NUM.findall(linea)
        if len(toks) >= 2:
            filas.append([_a_float(t) for t in toks])
    return filas


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


def filas_a_perfil(filas, c_est, c_cota, zmin, zmax):
    pts, ultimo = [], -1e18
    for f in filas:
        if len(f) < max(c_est, c_cota):
            continue
        x, z = f[c_est - 1], f[c_cota - 1]
        if zmax > zmin and not (zmin <= z <= zmax):
            continue
        if x > ultimo:                               # estaciones crecientes; descarta ruido y duplicados
            pts.append({"estacion_m": x, "cota": z})
            ultimo = x
    return pd.DataFrame(pts, columns=["estacion_m", "cota"])


# ------------------------------------------------------------------ barra lateral
sb = st.sidebar
sb.header("Parámetros")
cfg_up = sb.file_uploader("Cargar configuración (.json)", type="json")
if cfg_up is not None:
    raw = cfg_up.getvalue()
    h = hashlib.md5(raw).hexdigest()
    if ss.get("_h") != h:
        d = json.loads(raw)
        ss["_h"] = h
        ss.nodos_base = pd.DataFrame(d["nodos"]).reindex(columns=NODOS0.columns).astype({"p_req_m": float})
        tr = pd.DataFrame(d["tramos"])
        ss.perfiles = {t["id"]: t["perfil"] for t in d["tramos"] if t.get("perfil")}
        ss.tramos_base = tr.reindex(columns=TRAMOS0.columns)
        for k in PARAMS:
            if k in d.get("_ui", {}):
                ss[k] = d["_ui"][k]
        ss.ver += 1
        st.rerun()

sb.radio("Método de fricción", ["DW", "HW"], key="metodo",
         format_func=lambda x: "Darcy-Weisbach" if x == "DW" else "Hazen-Williams")
sb.slider("Pérdidas menores (% de fricción)", 0.0, 0.5, step=0.01, key="pct_menores")
sb.number_input("Presión deseada en nodos de control (m.c.a.)", 0.0, 200.0, step=1.0, key="presion_deseada_m")
sb.number_input("Presión mínima sobre el perfil (m)", 0.0, 100.0, step=1.0, key="presion_min_perfil_m",
                help="Margen de la línea piezométrica sobre cada punto del perfil cargado.")
sb.text_input("Escenarios (fracciones del gasto de diseño)", key="escenarios")
with sb.expander("Bomba y motor"):
    st.number_input("Bombas en paralelo", 1, 8, key="n_bombas")
    st.number_input("Eficiencia bomba", 0.2, 0.95, step=0.01, key="eficiencia_bomba")
    st.number_input("Eficiencia motor", 0.5, 0.99, step=0.01, key="eficiencia_motor")
    st.number_input("Factor de seguridad motor", 1.0, 1.5, step=0.05, key="factor_seguridad_motor")
    st.number_input("NPSH requerido (m, 0 = sin dato)", 0.0, 30.0, step=0.1, key="npsh_requerido")
    usa_curva = st.checkbox("Ingresar curva de UNA bomba (3 puntos) para estimar velocidad del VFD")
    curva = None
    if usa_curva:
        cdf = st.data_editor(pd.DataFrame({"Q_lps": [0.0, 10.0, 20.0], "H_m": [45.0, 40.0, 28.0]}),
                             num_rows="fixed", key="curva_ed")
        curva = cdf.values.tolist()
with sb.expander("Succión"):
    st.number_input("Nivel de agua mínimo (msnm)", key="suc_nivel", format="%.2f")
    st.number_input("Cota del eje de la bomba (msnm)", key="suc_cota_eje", format="%.2f")
    st.number_input("Longitud succión (m)", 0.0, 500.0, key="suc_L")
    st.number_input("Diámetro interno succión (mm)", 20.0, 1500.0, key="suc_D")
    st.number_input("Σ K menores succión", 0.0, 50.0, key="suc_K")
    st.number_input("Altitud del sitio (msnm)", 0.0, 5000.0, key="suc_alt")
    st.number_input("Temperatura del agua (°C)", 0.0, 60.0, key="suc_temp")
with sb.expander("Límites de verificación (típicos MAPAS; ajusta a tu norma)"):
    st.number_input("Presión mínima (m.c.a.)", 0.0, 100.0, key="p_min")
    st.number_input("Presión máxima (m.c.a.)", 0.0, 200.0, key="p_max")
    st.number_input("Velocidad mínima (m/s)", 0.0, 2.0, key="v_min")
    st.number_input("Velocidad máxima (m/s)", 0.5, 10.0, key="v_max")

# ------------------------------------------------------------------ cuerpo
st.title("Booster con presión constante")
st.caption("Edita los datos, carga (opcional) el perfil del terreno de cada tramo y revisa los resultados.")

tab_dem, tab_red, tab_perf, tab_res = st.tabs(["1. Gastos", "2. Red", "3. Perfil del terreno", "4. Resultados"])

with tab_dem:
    st.write("Calculadora de gastos de diseño (informativa). Los gastos que usa el cálculo son los de la columna "
             "`demanda_lps` de la pestaña Red.")
    c1, c2, c3, c4, c5 = st.columns(5)
    pob = c1.number_input("Población", 0.0, 1e7, 5000.0)
    dot = c2.number_input("Dotación (l/hab/día)", 0.0, 1000.0, 150.0)
    cvd = c3.number_input("CVD", 1.0, 2.0, 1.4)
    cvh = c4.number_input("CVH", 1.0, 3.0, 1.55)
    hb = c5.number_input("Horas de bombeo", 1.0, 24.0, 24.0)
    g = bc.calcula_gastos(pob, dot, cvd, cvh)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Q medio", f"{g['Qmed']:.2f} lps")
    m2.metric("QMD", f"{g['QMD']:.2f} lps")
    m3.metric("QMH", f"{g['QMH']:.2f} lps")
    m4.metric("Q bombeo con tanque", f"{bc.gasto_bombeo(g['QMD'], hb):.2f} lps")
    st.info("Con tanque regulador se bombea QMD en las horas de bombeo. Booster directo a la red, sin tanque: "
            "la bomba debe dar QMH.")

with tab_red:
    st.subheader("Nodos")
    st.caption("`B` = descarga de la bomba (raíz). Nodos con demanda = puntos de consumo. "
               "`p_req_m` opcional: presión deseada propia del nodo.")
    nodos_df = st.data_editor(ss.nodos_base, num_rows="dynamic", key=f"nodos_{ss.ver}", width="stretch")
    st.subheader("Tramos")
    tramos_df = st.data_editor(
        ss.tramos_base, num_rows="dynamic", key=f"tramos_{ss.ver}", width="stretch",
        column_config={"material": st.column_config.SelectboxColumn(options=list(bc.RUGOSIDAD_MM))})
    nodos_ids = [str(x) for x in nodos_df["id"].dropna()]
    raiz = st.selectbox("Nodo raíz (descarga de la bomba)", nodos_ids, index=0 if nodos_ids else None)
    ctrl = st.multiselect("Nodos de control (vacío = todos los extremos)", nodos_ids)

with tab_perf:
    ids_t = [str(x) for x in tramos_df["id"].dropna()]
    st.write("Carga el perfil de un tramo desde **PDF** (con tabla o texto de estación y cota) o **CSV/TXT**. "
             "Si el PDF es solo dibujo (sin texto), expórtalo a tabla desde tu software de topografía/CAD.")
    t_sel = st.selectbox("Tramo", ids_t) if ids_t else None
    up = st.file_uploader("Perfil del terreno", type=["pdf", "csv", "txt"], key=f"perf_{t_sel}")
    if t_sel and up is not None:
        try:
            texto = texto_pdf(up) if up.name.lower().endswith(".pdf") else up.getvalue().decode("utf-8", "ignore")
            filas = texto_a_filas(texto)
        except Exception as e:                       # noqa: BLE001
            filas = []
            st.error(f"No se pudo leer el archivo: {e}")
        if not filas:
            st.warning("No se encontraron pares de números (estación, cota). Prueba con CSV o pega los datos abajo.")
        else:
            nmax = max(len(f) for f in filas)
            a, b, c, d = st.columns(4)
            ce = a.number_input("Columna de estación", 1, nmax, 1)
            cz = b.number_input("Columna de cota", 1, nmax, min(2, nmax))
            zmin = c.number_input("Cota mínima válida", value=0.0)
            zmax = d.number_input("Cota máxima válida (0 = sin filtro)", value=0.0)
            prev = filas_a_perfil(filas, int(ce), int(cz), zmin, zmax)
            st.write(f"{len(prev)} puntos detectados. Edita o borra los que estén mal antes de asignar:")
            prev = st.data_editor(prev, num_rows="dynamic", key=f"prev_{t_sel}_{up.name}", width="stretch")
            if st.button("Asignar perfil al tramo", type="primary") and len(prev) >= 2:
                ss.perfiles[t_sel] = prev.dropna().values.tolist()
                st.success(f"Perfil asignado a {t_sel}")
    if ss.perfiles:
        st.subheader("Perfiles asignados")
        for tid, pts in list(ss.perfiles.items()):
            col1, col2 = st.columns([4, 1])
            col1.write(f"**{tid}**: {len(pts)} puntos, estaciones {pts[0][0]:.0f} a {pts[-1][0]:.0f} m, "
                       f"cota máx {max(p[1] for p in pts):.2f}")
            if col2.button("Quitar", key=f"q_{tid}"):
                del ss.perfiles[tid]
                st.rerun()

# ------------------------------------------------------------------ construir config y calcular
def num_or_none(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def build_cfg():
    nodos = [{"id": str(r.id), "cota": float(r.cota), "demanda_lps": float(r.demanda_lps or 0),
              "p_req_m": num_or_none(r.p_req_m)} for r in nodos_df.dropna(subset=["id", "cota"]).itertuples()]
    tramos = []
    for r in tramos_df.dropna(subset=["id", "desde", "hasta", "L", "D_mm"]).itertuples():
        t = {"id": str(r.id), "desde": str(r.desde), "hasta": str(r.hasta), "L": float(r.L), "D_mm": float(r.D_mm),
             "material": r.material or "PVC", "K": float(r.K or 0)}
        if str(r.id) in ss.perfiles:
            t["perfil"] = ss.perfiles[str(r.id)]
        tramos.append(t)
    esc = [float(x) for x in re.split(r"[,\s]+", ss.escenarios.strip()) if x]
    if 1.0 not in esc:
        esc.append(1.0)
    return {
        "raiz": raiz, "nodos": nodos, "tramos": tramos, "metodo": ss.metodo, "pct_menores": ss.pct_menores,
        "presion_deseada_m": ss.presion_deseada_m, "presion_min_perfil_m": ss.presion_min_perfil_m,
        "nodos_control": ctrl or None, "escenarios": sorted(set(esc)), "n_bombas": int(ss.n_bombas),
        "eficiencia_bomba": ss.eficiencia_bomba, "eficiencia_motor": ss.eficiencia_motor,
        "factor_seguridad_motor": ss.factor_seguridad_motor, "npsh_requerido": ss.npsh_requerido or None,
        "curva_bomba": curva,
        "limites": {"p_min": ss.p_min, "p_max": ss.p_max, "v_min": ss.v_min, "v_max": ss.v_max},
        "succion": {"nivel_agua": ss.suc_nivel, "cota_eje_bomba": ss.suc_cota_eje, "L": ss.suc_L, "D_mm": ss.suc_D,
                    "K": ss.suc_K, "altitud_m": ss.suc_alt, "temp_c": ss.suc_temp},
        "_ui": {k: ss[k] for k in PARAMS},
    }


with tab_res:
    try:
        cfg = build_cfg()
        res = bc.analizar(cfg)
    except Exception as e:                           # noqa: BLE001
        st.error(f"Revisa los datos: {e}")
        st.stop()
    red, esc = res["red"], res["esc"]
    dis = max(esc, key=lambda r: r["k"])
    nb, s = cfg["n_bombas"], cfg["succion"]
    Q, H = dis["Q"], dis["CDT"]
    p_hid = bc.G * Q / 1000 * H
    p_kw = p_hid / (cfg["eficiencia_bomba"] * cfg["eficiencia_motor"])
    hp = p_kw / bc.HP_KW
    npshd = (bc.patm_m(s["altitud_m"]) - bc.pvap_m(s["temp_c"]) + (s["nivel_agua"] - s["cota_eje_bomba"])
             - dis["hf_succ"])

    m = st.columns(5)
    m[0].metric("Capacidad", f"{Q:.1f} lps", f"{Q / nb:.1f} lps/bomba" if nb > 1 else None)
    m[1].metric("CDT de diseño", f"{H:.2f} m")
    m[2].metric("Potencia motor", f"{hp:.1f} HP", f"comercial: {bc.motor_comercial(hp * cfg['factor_seguridad_motor'] / nb):g} HP")
    m[3].metric("NPSH disponible", f"{npshd:.2f} m")
    m[4].metric("Punto crítico", dis["crit"])
    if cfg["npsh_requerido"]:
        marg = npshd - cfg["npsh_requerido"]
        (st.success if marg >= 1 else st.error)(f"Margen de NPSH: {marg:.2f} m (recomendado ≥ 1 m)")
    if "@" in dis["crit"]:
        st.warning(f"El punto que gobierna es un punto intermedio del perfil ({dis['crit']}), no un extremo.")

    lim = cfg["limites"]
    filas_t = []
    for t in red.tramos:
        d = dis["det"][t.id]
        obs = "V alta" if d["V"] > lim["v_max"] else ("V baja" if 0 < d["Q"] and d["V"] < lim["v_min"] else "")
        filas_t.append({"Tramo": t.id, "Q lps": d["Q"], "D mm": t.D_mm, "L m": t.L, "V m/s": d["V"],
                        "hf m": d["hf"], "hm m": d["hm"], "Obs": obs})
    st.subheader("Tramos (diseño)")
    st.dataframe(pd.DataFrame(filas_t).round(2), width="stretch", hide_index=True)

    filas_n = []
    for nid, p in dis["pres"].items():
        obs = ""
        if nid != red.raiz:
            if p < lim["p_min"] - 1e-6:
                obs = "BAJA"
            elif p > lim["p_max"]:
                obs = "ALTA: válvula reductora"
            elif nid in red.terminales() and p - cfg["presion_deseada_m"] > 1:
                obs = f"exceso {p - cfg['presion_deseada_m']:.1f} m: regular ramal"
        filas_n.append({"Nodo": nid, "Cota": red.nodos[nid].cota, "P m.c.a.": p, "P kg/cm²": p / 10, "Obs": obs})
    st.subheader("Presiones en nodos (sin válvulas reguladoras)")
    st.dataframe(pd.DataFrame(filas_n).round(2), width="stretch", hide_index=True)

    coefs = bc.ajusta_curva(curva) if curva else None
    filas_c = []
    for r in esc:
        f = {"Q lps": r["Q"], "Q/bomba": r["Q"] / nb, "CDT m": r["CDT"],
             "P consigna descarga": r["H_desc"] - red.nodos[red.raiz].cota, "Crítico": r["crit"]}
        if coefs and r["Q"] > 0:
            rr = bc.velocidad_relativa(coefs, r["Q"] / nb, r["CDT"])
            f["N/Nnom %"] = round(rr * 100) if rr else None
        filas_c.append(f)
    st.subheader("Curva del sistema a presión constante")
    cdf = pd.DataFrame(filas_c).round(2)
    st.dataframe(cdf, width="stretch", hide_index=True)

    import matplotlib.pyplot as plt
    g1, g2 = st.columns(2)
    fig, ax = plt.subplots(figsize=(5.5, 3.8))
    ax.plot(cdf["Q lps"], cdf["CDT m"], "o-", label="Sistema")
    if coefs:
        xs = [max(cdf["Q lps"]) * 1.3 * i / 50 for i in range(51)]
        ax.plot(xs, [coefs[0] + coefs[1] * x / nb + coefs[2] * (x / nb) ** 2 for x in xs], "--", label="Bomba 100%")
    ax.set_xlabel("Q (lps)")
    ax.set_ylabel("CDT (m)")
    ax.grid(alpha=.3)
    ax.legend()
    g1.pyplot(fig)

    # perfil longitudinal + línea piezométrica hacia un nodo
    destino = g2.selectbox("Ver perfil hacia el nodo", red.terminales())
    camino, n = [], destino
    padre = {t.hasta: t for t in red.tramos}
    while n != red.raiz:
        camino.insert(0, padre[n])
        n = padre[n].desde
    xt, zt, xh, zh, xc = [], [], [], [], 0.0
    for t in camino:
        d = dis["det"][t.id]
        h0 = dis["H_desc"] - dis["acum"][t.desde]
        pts = sorted(map(tuple, t.perfil)) if t.perfil else [
            (0, red.nodos[t.desde].cota), (t.L, red.nodos[t.hasta].cota)]
        x0, span = pts[0][0], (pts[-1][0] - pts[0][0]) or 1.0
        for x, z in pts:
            xt.append(xc + (x - x0) / span * t.L)
            zt.append(z)
        xh += [xc, xc + t.L]
        zh += [h0, h0 - d["hf"] - d["hm"]]
        xc += t.L
    fig2, ax2 = plt.subplots(figsize=(5.5, 3.8))
    ax2.fill_between(xt, zt, min(zt) - 5, color="#c9b79c", alpha=.6, label="Terreno")
    ax2.plot(xh, zh, "b-", label="Línea piezométrica")
    ax2.set_xlabel("Distancia (m)")
    ax2.set_ylabel("Cota (msnm)")
    ax2.legend()
    ax2.grid(alpha=.3)
    g2.pyplot(fig2)

    st.download_button("Descargar configuración (.json)", json.dumps(cfg, indent=1), "booster_config.json")
    st.download_button("Descargar curva del sistema (.csv)", cdf.to_csv(index=False), "curva_sistema.csv")
    st.caption("Valores por defecto típicos de MAPAS-CONAGUA; confirma con la edición vigente y tu organismo operador. "
               "Revisa también golpe de ariete, válvulas de aire y operación a gasto mínimo.")
