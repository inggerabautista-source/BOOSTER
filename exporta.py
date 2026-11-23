# -*- coding: utf-8 -*-
"""exporta.py - Esquema, perfiles, tablas y exportación (PDF, Excel, ZIP de PNG). Requiere booster_cdt.py"""
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

equipo = bc.equipo
_diseno = bc.diseno


def _nodo_critico(red, dis):
    c = dis["crit"]
    if "@" in c:
        tid = c.split("@")[0]
        return next(t.hasta for t in red.tramos if t.id == tid)
    return c


# ----------------------------------------------------------------------------- tablas
def tablas(cfg, res, con_alternativas=False):
    red, esc, dis, nueva = res["red"], res["esc"], _diseno(res), res["nueva"]
    lim, nb, eq, s = cfg["limites"], cfg.get("n_bombas", 1), equipo(cfg, res), cfg["succion"]
    tr, zj = [], []
    for t in red.tramos:
        d = dis["det"][t.id]
        obs = "V alta" if d["V"] > lim["v_max"] else ("V baja" if d["Q"] > 0 and d["V"] < lim["v_min"] else "")
        tr.append({"Tramo": t.id, "Desde": t.desde, "Hasta": t.hasta, "Material": t.material, "Clase": t.clase,
                   "DN pulg": t.dn_pulg, "D int mm": t.D_mm, "L m": t.L, "Q lps": d["Q"],
                   "% del gasto": 100 * d["Q"] / dis["Q"] if dis["Q"] else 0, "V m/s": d["V"],
                   "hf m": d["hf"], "hm m": d["hm"], "ΣK": t.Ksum, "Obs": obs})
        zj.append({"Tramo": t.id, "Prof. mínima m": t.prof_min, "Prof. usada m": t.prof,
                   "Colchón m": t.prof - 0.05 - t.D_ext / 1000, "Ancho zanja m": bc.ancho_zanja(t.D_ext),
                   "Cota eje inicio": t.eje[0][1], "Cota eje fin": t.eje[-1][1],
                   "Terreno inicio": t.terreno[0][1], "Terreno fin": t.terreno[-1][1],
                   "Puntos de perfil": len(t.terreno) if t.perfil else 0,
                   "Obs": "" if t.prof >= t.prof_min - 1e-9 else "PROFUNDIDAD MENOR A LA MÍNIMA"})
    nd = []
    for nid, p in dis["pres"].items():
        p_new = dis["H_desc"] - nueva["acum"][nid] - red.z_tub[nid]
        p_est = s["nivel_agua"] - red.z_tub[nid]
        obs = ""
        if nid != red.raiz:
            if p < lim["p_min"] - 1e-6:
                obs = "BAJA"
            elif max(p, p_new, p_est) > lim["p_max"]:
                obs = "ALTA: válvula reductora"
            elif nid in red.terminales() and p - cfg["presion_deseada_m"] > 1:
                obs = f"exceso {p - cfg['presion_deseada_m']:.1f} m: regular ramal"
        nd.append({"Nodo": nid, "Cota terreno": red.z_terr[nid], "Cota tubería": red.z_tub[nid],
                   "P m.c.a.": p, "P nueva (máx.)": p_new, "P estática": p_est, "Obs": obs})
    coefs = bc.ajusta_curva(cfg["curva_bomba"]) if cfg.get("curva_bomba") else None
    cu = []
    for r in esc:
        f = {"Q lps": r["Q"], "Q/bomba": r["Q"] / nb, "CDT m": r["CDT"],
             "P consigna descarga": r["H_desc"] - red.z_tub[red.raiz], "Crítico": r["crit"]}
        if coefs and r["Q"] > 0:
            rr = bc.velocidad_relativa(coefs, r["Q"] / nb, r["CDT"])
            f["N/Nnom %"] = round(rr * 100) if rr else None
        cu.append(f)
    ar = pd.DataFrame(bc.ariete(cfg, res))
    dm = []
    for t in red.tramos:
        d = dis["det"][t.id]
        dn_o = bc.dn_optimo(d["Q"], t.material, t.clase, cfg)
        row = {"Tramo": t.id, "Q lps": d["Q"], "DN actual": t.dn_pulg, "V actual": d["V"], "DN óptimo": dn_o}
        if dn_o:
            Do = bc.geometria(t.material, dn_o, t.clase)["D_int"] / 1000
            row["V óptimo"] = d["Q"] / 1000 / (3.14159265 * Do ** 2 / 4)
        row["Criterio"] = cfg.get("criterio_dn", "bresse")
        dm.append(row)
    edad = cfg.get("edad_anios", 0) or 0
    filas = [
        ("Capacidad total", f"{eq['Q']:.2f} lps ({eq['Q'] / nb:.2f} lps/bomba, {nb} en paralelo)"),
        ("CDT de diseño", f"{eq['H']:.2f} m.c.a." + (f"  (tubería nueva: {nueva['CDT']:.2f} m)" if edad else "")),
        ("Punto crítico", eq["crit"]),
        ("Potencia hidráulica", f"{eq['p_hid']:.2f} kW"),
        ("Potencia al motor", f"{eq['p_kw']:.2f} kW = {eq['hp']:.1f} HP"),
        ("Motor comercial sugerido", f"{eq['hp_com']:g} HP por bomba"),
        ("NPSH disponible", f"{eq['npshd']:.2f} m"),
        ("Método de fricción", "Darcy-Weisbach" if cfg["metodo"].upper() == "DW" or edad else "Hazen-Williams"),
        ("Envejecimiento", f"{edad} años, grupo {cfg.get('grupo_agua')}" if edad else "tubería nueva"),
        ("Pérdidas menores", f"{cfg['pct_menores'] * 100:.0f}% de fricción" if cfg.get("modo_menores") != "K"
         else "por accesorios (ΣK)"),
        ("Presión deseada", f"{cfg['presion_deseada_m']} m.c.a."),
        ("Tiempo de paro/cierre (ariete)", f"{cfg.get('tc_s', 0)} s"),
    ]
    vn, vt, ok_global = bc.verifica_presiones(cfg, res)
    filas.append(("Verificación de presiones", "CUMPLE" if ok_global else "NO CUMPLE"))
    filas.append(("Presión de suministro (proyecto)",
                  f"mín. {lim['p_min'] / 10:.2f} / máx. {lim['p_max'] / 10:.2f} kg/cm²"))
    perf = [{"Tramo": t.id, "Estación m": x, "Cota": z} for t in red.tramos if t.perfil for x, z in t.terreno]
    acc = pd.DataFrame(cfg.get("accesorios") or [], columns=["tramo", "tipo", "cant", "K"])
    out = {"Resumen": pd.DataFrame(filas, columns=["Concepto", "Valor"]), "Tramos": pd.DataFrame(tr),
           "Nodos": pd.DataFrame(nd), "Zanja y cotas": pd.DataFrame(zj), "Curva sistema": pd.DataFrame(cu),
           "Golpe de ariete": ar, "Diámetros": pd.DataFrame(dm), "Accesorios": acc,
           "Perfiles": pd.DataFrame(perf, columns=["Tramo", "Estación m", "Cota"]),
           "Verif. nodos": pd.DataFrame(vn), "Verif. tuberías": pd.DataFrame(vt)}
    g = cfg.get("gastos")
    if g:
        out["Gastos"] = pd.DataFrame([
            ("Qmed", g["Qmed"]), ("QMD", g["QMD"]), ("QMH", g["QMH"]), ("Qmín", g["Qmin"]),
            ("Qmáx instantáneo", g["Qmax_inst"]), ("Qmáx extraordinario", g["Qmax_ext"]),
            ("Q de bombeo (usado)", g["Qbomba"])], columns=["Gasto", "lps"])
    if con_alternativas:
        alts = []
        for t in red.tramos:
            if dis["det"][t.id]["Q"] > 0:
                a = bc.alternativas(cfg, t.id)
                alts += [{"Tramo": t.id, **f} for f in a["filas"]]
        out["Alternativas DN"] = pd.DataFrame(alts)
    return out


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
    red, dis, lim, s = res["red"], _diseno(res), cfg["limites"], cfg["succion"]
    nb, eq, pos = cfg.get("n_bombas", 1), equipo(cfg, res), _layout(res["red"])
    maxd, nh = max(p[0] for p in pos.values()), len(red.terminales())
    fig, ax = plt.subplots(figsize=(max(10, 3.0 * (maxd + 3)), max(4.2, 1.9 * nh + 1.6)))
    crit = _nodo_critico(red, dis)
    ruta = {t.id for t in red.camino(crit)} if crit != red.raiz else set()
    y0 = pos[red.raiz][1]
    ax.add_patch(Rectangle((-3.3, y0 - 0.35), 1.0, 0.7, fc="#cfe8ff", ec="k"))
    ax.text(-2.8, y0, f"Tanque\nnivel {s['nivel_agua']:.2f}", ha="center", va="center", fontsize=7)
    ax.annotate("", xy=(-1.15, y0), xytext=(-2.3, y0), arrowprops=dict(arrowstyle="->", lw=1.5))
    ax.text(-1.72, y0 + 0.12, f"Succión {s['dn_pulg']}\" {s.get('material', 'PVC')}\nL={s['L']:.0f} m", ha="center", va="bottom", fontsize=6.5)
    ax.add_patch(Circle((-0.85, y0), 0.3, fc="#ffd966", ec="k", zorder=3))
    ax.text(-0.85, y0, "B", ha="center", va="center", fontsize=11, fontweight="bold", zorder=4)
    ax.text(-0.85, y0 - 0.42, f"CDT = {eq['H']:.1f} m\nQ = {eq['Q']:.1f} lps" + (f"\n({nb} bombas)" if nb > 1 else ""),
            ha="center", va="top", fontsize=7)
    ax.plot([-0.55, pos[red.raiz][0]], [y0, y0], "-", color="#1f4e79", lw=2)
    for t in red.tramos:
        (x1, y1), (x2, y2), d = pos[t.desde], pos[t.hasta], dis["det"][t.id]
        er = t.id in ruta
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", lw=3 if er else 1.8, color="#c00000" if er else "#1f4e79"))
        ax.text((x1 + x2) / 2, (y1 + y2) / 2,
                f"{t.id}  ({t.material} {t.clase})\n{d['Q']:.1f} lps | {t.dn_pulg:g}\" (Øi {t.D_mm:.0f} mm)\n"
                f"L={t.L:.0f} m | V={d['V']:.2f} m/s\nhf+hm={d['hf'] + d['hm']:.1f} m | prof={t.prof:.2f} m"
                + ("\nperfil cargado" if t.perfil else ""),
                ha="center", va="center", fontsize=6.3, bbox=dict(fc="white", ec="0.7", alpha=0.95, pad=2), zorder=5)
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
        ax.text(x, y - 0.27, f"{nid}\nterreno {red.z_terr[nid]:.2f}\ntubería {red.z_tub[nid]:.2f}\nP={p:.1f} m"
                + (f"\nconsumo {dem:g} lps" if dem else ""), ha="center", va="top", fontsize=6.8)
    ax.legend(handles=[Patch(fc="#9be29b", ec="k", label="Presión dentro de límites"),
                       Patch(fc="#ffcc80", ec="k", label="Exceso: regular ramal / VRP"),
                       Patch(fc="#ff9a9a", ec="k", label="Presión baja"),
                       Patch(fc="white", ec="#c00000", lw=2, label=f"Nodo/camino crítico ({eq['crit']})")],
              loc="upper left", fontsize=7)
    ax.set_xlim(-3.5, maxd + 0.9)
    ax.set_ylim(-1.2, nh + 0.2)
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


def _datos_perfil(red, dis, destino):
    xt, zt, xe, ze, xh, zh, nodos, xc = [], [], [], [], [], [], [], 0.0
    for t in red.camino(destino):
        d = dis["det"][t.id]
        h0 = dis["hgl"][t.desde]
        perd = d["hf"] + d["hm"]
        nodos.append((xc, t.desde))
        xt += [xc + x for x, _ in t.terreno]
        zt += [z for _, z in t.terreno]
        xe += [xc + x for x, _ in t.eje]
        ze += [z for _, z in t.eje]
        xh += [xc, xc + t.L]
        zh += [h0, h0 - perd]
        xc += t.L
    nodos.append((xc, destino))
    return xt, zt, xe, ze, xh, zh, nodos


def perfil_ax(ax, res, destino):
    red, dis = res["red"], _diseno(res)
    xt, zt, xe, ze, xh, zh, nodos = _datos_perfil(red, dis, destino)
    base = min(min(zt), min(ze)) - 4
    ax.fill_between(xt, zt, base, color="#d9c9ad", alpha=.6, label="Terreno")
    ax.plot(xt, zt, color="#7a5c2e", lw=1.2)
    ax.fill_between(xe, ze, [z - 0.25 for z in ze], color="#555", alpha=.2)
    ax.plot(xe, ze, color="k", lw=2.2, label="Tubería (eje)")
    ax.plot(xh, zh, "b-", lw=1.6, label="Línea piezométrica")
    for x, n in nodos:
        ax.axvline(x, color="0.6", ls=":", lw=.8)
        ax.text(x, base, n, rotation=90, va="bottom", ha="right", fontsize=7)
    ax.set_xlabel("Distancia desde la bomba (m)")
    ax.set_ylabel("Cota (msnm)")
    ax.set_title(f"Perfil bomba → {destino}", fontsize=10)
    ax.grid(alpha=.3)
    ax.legend(fontsize=7, loc="best")


def perfil_fig(res, destino):
    fig, ax = plt.subplots(figsize=(9, 4.4))
    perfil_ax(ax, res, destino)
    fig.tight_layout()
    return fig


def perfiles_fig(res):
    """Un panel por ramal (terminal): terreno, tubería, línea piezométrica."""
    ts = res["red"].terminales()
    fig, axs = plt.subplots(len(ts), 1, figsize=(10, 4.2 * len(ts)), squeeze=False)
    for ax, t in zip(axs[:, 0], ts):
        perfil_ax(ax, res, t)
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
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, f in todas_las_figuras(cfg, res).items():
            z.writestr(f"{nombre}.png", _png(f).getvalue())
            plt.close(f)
    return out.getvalue()


def a_excel(cfg, res):
    tb, figs = tablas(cfg, res, True), todas_las_figuras(cfg, res)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        for nombre, df in tb.items():
            df.round(3).to_excel(w, sheet_name=nombre[:31], index=False)
            ws = w.sheets[nombre[:31]]
            from openpyxl.styles import PatternFill
            verde, rojo = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(
                    45, max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3)
                for c in col:
                    if c.value in ("CUMPLE", "NO CUMPLE"):
                        c.fill = verde if c.value == "CUMPLE" else rojo
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


def _pag_tabla(pdf, titulo, df, filas=30):
    df = df.copy()
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(2)
    df = df.fillna("")
    for i in range(0, max(len(df), 1), filas):
        ch = df.iloc[i:i + filas]
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.suptitle(titulo + (f" ({i // filas + 1})" if len(df) > filas else ""), fontsize=12)
        ax = fig.add_axes([0.02, 0.05, 0.96, 0.84])
        ax.axis("off")
        tb = ax.table(cellText=ch.astype(str).values.tolist(), colLabels=list(ch.columns), loc="upper center", cellLoc="center")
        tb.auto_set_font_size(False)
        tb.set_fontsize(6.8)
        tb.auto_set_column_width(list(range(len(ch.columns))))
        tb.scale(1, 1.35)
        for (r_, c_), cell in tb.get_celld().items():
            txt = cell.get_text().get_text()
            if r_ > 0 and txt == "CUMPLE":
                cell.set_facecolor("#c6efce")
            elif r_ > 0 and txt == "NO CUMPLE":
                cell.set_facecolor("#ffc7ce")
        pdf.savefig(fig)
        plt.close(fig)


def a_pdf(cfg, res):
    tb, out = tablas(cfg, res, True), io.BytesIO()
    with PdfPages(out) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.94, "Memoria de cálculo - Sistema booster a presión constante", fontsize=15, fontweight="bold")
        fig.text(0.07, 0.915, datetime.date.today().strftime("%d/%m/%Y"), fontsize=9)
        y = 0.87
        for c, v in tb["Resumen"].values:
            fig.text(0.07, y, f"{c}:", fontsize=10, fontweight="bold")
            fig.text(0.38, y, str(v), fontsize=10)
            y -= 0.03
        for av in res.get("avisos", [])[:6]:
            fig.text(0.07, y, "Aviso: " + av, fontsize=8, color="#c00000")
            y -= 0.025
        nota = ("Notas: coeficientes, límites, rugosidades, K de accesorios y módulos de elasticidad son valores típicos "
                "(MAPAS-CONAGUA y bibliografía);\nverificar contra la edición vigente, la norma del organismo operador y el "
                "fabricante. El golpe de ariete es una estimación\n(Joukowsky/Michaud) y no sustituye un análisis de transitorios. "
                "Resultados preliminares de diseño.")
        fig.text(0.07, y - 0.03, nota, fontsize=8, va="top", color="0.3")
        pdf.savefig(fig)
        plt.close(fig)
        for nombre, f in todas_las_figuras(cfg, res).items():
            pdf.savefig(f, bbox_inches="tight")
            plt.close(f)
        for nombre in ("Gastos", "Tramos", "Zanja y cotas", "Nodos", "Verif. nodos", "Verif. tuberías", "Curva sistema",
                       "Golpe de ariete", "Diámetros", "Alternativas DN"):
            if nombre in tb and len(tb[nombre]):
                _pag_tabla(pdf, nombre, tb[nombre])
        if len(tb["Accesorios"]):
            _pag_tabla(pdf, "Accesorios (K)", tb["Accesorios"])
        if len(tb["Perfiles"]):
            _pag_tabla(pdf, "Perfiles cargados", tb["Perfiles"], 40)
    return out.getvalue()
