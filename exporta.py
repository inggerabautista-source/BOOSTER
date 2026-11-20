# -*- coding: utf-8 -*-
"""
exporta.py - Esquema general, tablas y exportación (PDF, Excel, ZIP de PNG) del cálculo de booster.
Requiere: matplotlib, pandas, openpyxl, booster_cdt.py
"""
import datetime
import io
import zipfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Circle, Patch, Rectangle  # noqa: E402

import booster_cdt as bc  # noqa: E402


# ----------------------------------------------------------------------------- utilidades
def _diseno(res):
    return max(res["esc"], key=lambda r: r["k"])


def _nodo_critico(red, dis):
    c = dis["crit"]
    if "@" in c:                                     # punto intermedio de un perfil
        tid = c.split("@")[0]
        return next(t.hasta for t in red.tramos if t.id == tid)
    return c


def equipo(cfg, res):
    """Resultados de equipo en el punto de diseño."""
    dis, nb, s = _diseno(res), cfg.get("n_bombas", 1), cfg["succion"]
    Q, H = dis["Q"], dis["CDT"]
    p_hid = bc.G * Q / 1000 * H
    p_kw = p_hid / (cfg["eficiencia_bomba"] * cfg["eficiencia_motor"])
    hp = p_kw / bc.HP_KW
    npshd = (bc.patm_m(s["altitud_m"]) - bc.pvap_m(s.get("temp_c", 20))
             + (s["nivel_agua"] - s["cota_eje_bomba"]) - dis["hf_succ"])
    npshr = cfg.get("npsh_requerido")
    return {"Q": Q, "H": H, "p_hid": p_hid, "p_kw": p_kw, "hp": hp,
            "hp_com": bc.motor_comercial(hp * cfg["factor_seguridad_motor"] / nb),
            "npshd": npshd, "marg": (npshd - npshr) if npshr else None, "crit": dis["crit"]}


def tablas(cfg, res):
    """Devuelve dict de DataFrames: Resumen, Tramos, Nodos, Curva sistema, Entradas..."""
    red, esc, dis = res["red"], res["esc"], _diseno(res)
    lim, nb, eq = cfg["limites"], cfg.get("n_bombas", 1), equipo(cfg, res)

    tr = []
    for t in red.tramos:
        d = dis["det"][t.id]
        obs = "V alta" if d["V"] > lim["v_max"] else ("V baja" if d["Q"] > 0 and d["V"] < lim["v_min"] else "")
        tr.append({"Tramo": t.id, "Desde": t.desde, "Hasta": t.hasta, "Q lps": d["Q"], "D mm": t.D_mm,
                   "L m": t.L, "V m/s": d["V"], "hf m": d["hf"], "hm m": d["hm"], "Obs": obs})
    nd = []
    for nid, p in dis["pres"].items():
        obs = ""
        if nid != red.raiz:
            if p < lim["p_min"] - 1e-6:
                obs = "BAJA"
            elif p > lim["p_max"]:
                obs = "ALTA: válvula reductora"
            elif nid in red.terminales() and p - cfg["presion_deseada_m"] > 1:
                obs = f"exceso {p - cfg['presion_deseada_m']:.1f} m: regular ramal"
        nd.append({"Nodo": nid, "Cota": red.nodos[nid].cota, "Demanda lps": red.nodos[nid].demanda_lps,
                   "P m.c.a.": p, "P kg/cm2": p / 10, "Obs": obs})
    coefs = bc.ajusta_curva(cfg["curva_bomba"]) if cfg.get("curva_bomba") else None
    cu = []
    for r in esc:
        f = {"Q lps": r["Q"], "Q/bomba": r["Q"] / nb, "CDT m": r["CDT"],
             "P consigna descarga": r["H_desc"] - red.nodos[red.raiz].cota, "Critico": r["crit"]}
        if coefs and r["Q"] > 0:
            rr = bc.velocidad_relativa(coefs, r["Q"] / nb, r["CDT"])
            f["N/Nnom %"] = round(rr * 100) if rr else None
        cu.append(f)
    filas = [
        ("Capacidad total", f"{eq['Q']:.2f} lps ({eq['Q'] / nb:.2f} lps/bomba, {nb} en paralelo)"),
        ("CDT de diseño", f"{eq['H']:.2f} m.c.a."),
        ("Punto crítico", eq["crit"]),
        ("Potencia hidráulica", f"{eq['p_hid']:.2f} kW"),
        ("Potencia al motor", f"{eq['p_kw']:.2f} kW = {eq['hp']:.1f} HP"),
        ("Motor comercial sugerido", f"{eq['hp_com']:g} HP por bomba"),
        ("NPSH disponible", f"{eq['npshd']:.2f} m"),
        ("NPSH requerido / margen", "sin dato" if eq["marg"] is None
         else f"{cfg['npsh_requerido']:.2f} m / {eq['marg']:.2f} m"),
        ("Método de fricción", "Darcy-Weisbach" if cfg["metodo"].upper() == "DW" else "Hazen-Williams"),
        ("Pérdidas menores", f"{cfg['pct_menores'] * 100:.0f}% de fricción + K"),
        ("Presión deseada", f"{cfg['presion_deseada_m']} m.c.a."),
        ("Margen sobre perfil", f"{cfg.get('presion_min_perfil_m', 0)} m"),
        ("Eficiencias bomba / motor", f"{cfg['eficiencia_bomba']:.0%} / {cfg['eficiencia_motor']:.0%}"),
    ]
    perf = [{"Tramo": t.id, "Estación m": x, "Cota": z} for t in red.tramos if t.perfil for x, z in t.perfil]
    return {
        "Resumen": pd.DataFrame(filas, columns=["Concepto", "Valor"]),
        "Tramos": pd.DataFrame(tr), "Nodos": pd.DataFrame(nd), "Curva sistema": pd.DataFrame(cu),
        "Perfiles": pd.DataFrame(perf, columns=["Tramo", "Estación m", "Cota"]),
    }


# ----------------------------------------------------------------------------- figuras
def _layout(red):
    pos, cont = {}, [0]

    def rec(n, d):
        hs = red.hijos.get(n, [])
        if not hs:
            y = float(cont[0])
            cont[0] += 1
        else:
            ys = [rec(t.hasta, d + 1) for t in hs]
            y = sum(ys) / len(ys)
        pos[n] = (d, y)
        return y

    rec(red.raiz, 0)
    return pos


def esquema_fig(cfg, res):
    """Esquema unifilar (no a escala): tanque, bomba, tramos con gasto/diámetro/longitud/velocidad,
    nodos con cota y presión, resaltando el camino crítico."""
    red, dis, lim, s = res["red"], _diseno(res), cfg["limites"], cfg["succion"]
    nb, eq, pos = cfg.get("n_bombas", 1), equipo(cfg, res), _layout(res["red"])
    maxd, nh = max(p[0] for p in pos.values()), len(red.terminales())
    fig, ax = plt.subplots(figsize=(max(10, 3.0 * (maxd + 3)), max(4.2, 1.9 * nh + 1.6)))

    crit, ruta, padre = _nodo_critico(red, dis), set(), {t.hasta: t for t in red.tramos}
    n = crit
    while n != red.raiz:
        ruta.add(padre[n].id)
        n = padre[n].desde

    y0 = pos[red.raiz][1]
    ax.add_patch(Rectangle((-3.3, y0 - 0.35), 1.0, 0.7, fc="#cfe8ff", ec="k"))
    ax.text(-2.8, y0, f"Tanque\nnivel {s['nivel_agua']:.2f}", ha="center", va="center", fontsize=7)
    ax.annotate("", xy=(-1.15, y0), xytext=(-2.3, y0), arrowprops=dict(arrowstyle="->", lw=1.5))
    ax.text(-1.72, y0 + 0.12, f"Succión Ø{s['D_mm']:.0f} mm\nL={s['L']:.0f} m", ha="center", va="bottom", fontsize=6.5)
    ax.add_patch(Circle((-0.85, y0), 0.3, fc="#ffd966", ec="k", zorder=3))
    ax.text(-0.85, y0, "B", ha="center", va="center", fontsize=11, fontweight="bold", zorder=4)
    ax.text(-0.85, y0 - 0.42, f"CDT = {eq['H']:.1f} m\nQ = {eq['Q']:.1f} lps" + (f"\n({nb} bombas)" if nb > 1 else ""),
            ha="center", va="top", fontsize=7)
    ax.plot([-0.55, pos[red.raiz][0]], [y0, y0], "-", color="#1f4e79", lw=2)

    for t in red.tramos:
        (x1, y1), (x2, y2), d = pos[t.desde], pos[t.hasta], dis["det"][t.id]
        enruta = t.id in ruta
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", lw=3 if enruta else 1.8, color="#c00000" if enruta else "#1f4e79"))
        ax.text((x1 + x2) / 2, (y1 + y2) / 2,
                f"{t.id}\n{d['Q']:.1f} lps | Ø{t.D_mm:.0f} mm\nL={t.L:.0f} m | V={d['V']:.2f} m/s\n"
                f"hf+hm={d['hf'] + d['hm']:.1f} m" + ("\nperfil cargado" if t.perfil else ""),
                ha="center", va="center", fontsize=6.5, bbox=dict(fc="white", ec="0.7", alpha=0.95, pad=2), zorder=5)

    for nid, (x, y) in pos.items():
        p = dis["pres"][nid]
        term = nid in red.terminales()
        if nid == red.raiz:
            col = "#ffd966"
        elif p < lim["p_min"] - 1e-6:
            col = "#ff9a9a"
        elif p > lim["p_max"] or (term and p - cfg["presion_deseada_m"] > 1):
            col = "#ffcc80"
        else:
            col = "#9be29b"
        ax.add_patch(Circle((x, y), 0.17, fc=col, ec="#c00000" if nid == crit else "k", lw=3 if nid == crit else 1, zorder=4))
        dem = red.nodos[nid].demanda_lps
        ax.text(x, y - 0.27, f"{nid}\nz={red.nodos[nid].cota:.2f}\nP={p:.1f} m" + (f"\nconsumo {dem:g} lps" if dem else ""),
                ha="center", va="top", fontsize=7)

    ax.legend(handles=[Patch(fc="#9be29b", ec="k", label="Presión dentro de límites"),
                       Patch(fc="#ffcc80", ec="k", label="Exceso: regular ramal / VRP"),
                       Patch(fc="#ff9a9a", ec="k", label="Presión baja"),
                       Patch(fc="white", ec="#c00000", lw=2, label=f"Nodo/camino crítico ({eq['crit']})")],
              loc="upper left", fontsize=7)
    ax.set_xlim(-3.5, maxd + 0.9)
    ax.set_ylim(-1.2, nh - 0.2 + 0.4)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    ax.set_title("Esquema general del sistema (no está a escala)", fontsize=11)
    fig.tight_layout()
    return fig


def curva_fig(cfg, res):
    nb, esc = cfg.get("n_bombas", 1), res["esc"]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    qs, hs = [r["Q"] for r in esc], [r["CDT"] for r in esc]
    ax.plot(qs, hs, "o-", label="Sistema (presión constante)")
    if cfg.get("curva_bomba"):
        c = bc.ajusta_curva(cfg["curva_bomba"])
        xs = [max(qs) * 1.3 * i / 50 for i in range(51)]
        ax.plot(xs, [c[0] + c[1] * x / nb + c[2] * (x / nb) ** 2 for x in xs], "--", label="Bomba 100 %")
    ax.set_xlabel("Q (lps)")
    ax.set_ylabel("CDT (m.c.a.)")
    ax.grid(alpha=.3)
    ax.legend()
    ax.set_title("Curva del sistema")
    fig.tight_layout()
    return fig


def perfil_fig(res, destino):
    """Perfil longitudinal (terreno y línea piezométrica de diseño) desde la bomba hasta 'destino'."""
    red, dis = res["red"], _diseno(res)
    padre, camino, n = {t.hasta: t for t in red.tramos}, [], destino
    while n != red.raiz:
        camino.insert(0, padre[n])
        n = padre[n].desde
    xt, zt, xh, zh, xc = [], [], [], [], 0.0
    for t in camino:
        d = dis["det"][t.id]
        h0 = dis["H_desc"] - dis["acum"][t.desde]
        pts = sorted(map(tuple, t.perfil)) if t.perfil else [(0, red.nodos[t.desde].cota), (t.L, red.nodos[t.hasta].cota)]
        x0, span = pts[0][0], (pts[-1][0] - pts[0][0]) or 1.0
        for x, z in pts:
            xt.append(xc + (x - x0) / span * t.L)
            zt.append(z)
        xh += [xc, xc + t.L]
        zh += [h0, h0 - d["hf"] - d["hm"]]
        xc += t.L
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    ax.fill_between(xt, zt, min(zt) - 5, color="#c9b79c", alpha=.6, label="Terreno")
    ax.plot(xh, zh, "b-", label="Línea piezométrica")
    ax.set_xlabel("Distancia desde la bomba (m)")
    ax.set_ylabel("Cota (msnm)")
    ax.set_title(f"Perfil hacia {destino}")
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    return fig


def todas_las_figuras(cfg, res):
    figs = {"esquema": esquema_fig(cfg, res), "curva_sistema": curva_fig(cfg, res)}
    for t in res["red"].terminales():
        figs[f"perfil_{t}"] = perfil_fig(res, t)
    return figs


# ----------------------------------------------------------------------------- exportación
def _png(fig, dpi=160):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=dpi, bbox_inches="tight")
    b.seek(0)
    return b


def a_zip_png(cfg, res):
    figs = todas_las_figuras(cfg, res)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, f in figs.items():
            z.writestr(f"{nombre}.png", _png(f).getvalue())
            plt.close(f)
    return out.getvalue()


def a_excel(cfg, res):
    tb, figs = tablas(cfg, res), todas_las_figuras(cfg, res)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        for nombre, df in tb.items():
            df.round(3).to_excel(w, sheet_name=nombre, index=False)
            ws = w.sheets[nombre]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(
                    45, max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3)
        pd.DataFrame(cfg["nodos"]).to_excel(w, sheet_name="Entrada nodos", index=False)
        pd.DataFrame([{k: v for k, v in t.items() if k != "perfil"} for t in cfg["tramos"]]).to_excel(
            w, sheet_name="Entrada tramos", index=False)
        from openpyxl.drawing.image import Image as XlImg
        ws = w.book.create_sheet("Gráficos")
        fila = 1
        for nombre, f in figs.items():
            img = XlImg(_png(f, 110))
            ws.add_image(img, f"A{fila}")
            fila += int(img.height / 20) + 3
            plt.close(f)
    return out.getvalue()


def _pag_tabla(pdf, titulo, df, filas=34):
    df = df.round(2).fillna("")
    for i in range(0, max(len(df), 1), filas):
        ch = df.iloc[i:i + filas]
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.suptitle(titulo + (f" ({i // filas + 1})" if len(df) > filas else ""), fontsize=12)
        ax = fig.add_axes([0.03, 0.05, 0.94, 0.84])
        ax.axis("off")
        tb = ax.table(cellText=ch.astype(str).values.tolist(), colLabels=list(ch.columns), loc="upper center", cellLoc="center")
        tb.auto_set_font_size(False)
        tb.set_fontsize(7.5)
        tb.scale(1, 1.35)
        pdf.savefig(fig)
        plt.close(fig)


def a_pdf(cfg, res):
    tb, out = tablas(cfg, res), io.BytesIO()
    with PdfPages(out) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.94, "Memoria de cálculo - Sistema booster a presión constante", fontsize=15, fontweight="bold")
        fig.text(0.07, 0.915, datetime.date.today().strftime("%d/%m/%Y"), fontsize=9)
        y = 0.87
        for c, v in tb["Resumen"].values:
            fig.text(0.07, y, f"{c}:", fontsize=10, fontweight="bold")
            fig.text(0.38, y, str(v), fontsize=10)
            y -= 0.03
        nota = ("Notas: valores de coeficientes, límites de presión/velocidad y rugosidades son típicos de los "
                "manuales MAPAS-CONAGUA;\nverificar contra la edición vigente y la norma del organismo operador. "
                "No incluye análisis de golpe de ariete ni\nválvulas de aire. Cálculo de pérdidas uniforme por tramo "
                "(diámetro constante). Resultados preliminares de diseño.")
        fig.text(0.07, y - 0.03, nota, fontsize=8, va="top", color="0.3")
        pdf.savefig(fig)
        plt.close(fig)
        figs = todas_las_figuras(cfg, res)
        for nombre, f in figs.items():
            pdf.savefig(f, bbox_inches="tight")
            plt.close(f)
        for nombre in ("Tramos", "Nodos", "Curva sistema"):
            _pag_tabla(pdf, nombre, tb[nombre])
        if len(tb["Perfiles"]):
            _pag_tabla(pdf, "Perfiles cargados", tb["Perfiles"], 40)
    return out.getvalue()
