#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
booster_cdt.py  -  Dimensionamiento de equipo de bombeo tipo BOOSTER
                   con red ramificada (varios ramales) y presión constante.

Calcula:
  * Gastos de diseño (QMD, QMH) con coeficientes de variación tipo MAPAS-CONAGUA
  * Pérdidas por fricción (Darcy-Weisbach/Swamee-Jain o Hazen-Williams) y menores
  * Carga dinámica total (CDT) para mantener la presión deseada en el punto
    más desfavorable (nodo crítico), para varios gastos (curva del sistema)
  * Presión resultante en cada nodo (detecta excesos -> válvulas reductoras)
  * Presión de consigna en la descarga de la bomba (control por velocidad variable)
  * Potencia hidráulica y de motor, NPSH disponible
  * (Opcional) velocidad relativa del variador a partir de la curva de la bomba

USO:
    python booster_cdt.py                  # corre el ejemplo incluido
    python booster_cdt.py mi_red.json      # usa tu propia configuración
    python booster_cdt.py mi_red.json --plot

Los valores por defecto (coeficientes, límites de presión y velocidad, rugosidades)
son valores TÍPICOS de los Manuales MAPAS de CONAGUA. Verifícalos contra la
edición vigente del manual y contra la norma de tu organismo operador.

Unidades: longitudes en m, diámetros en mm (INTERNO), caudales en lps,
          cargas y presiones en m.c.a., cotas en msnm.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from typing import Optional

G = 9.81            # m/s2
NU = 1.004e-6       # viscosidad cinemática del agua a 20 °C, m2/s
HP_KW = 0.7457

# Valores típicos (verificar con MAPAS / proveedor de tubería)
RUGOSIDAD_MM = {"PVC": 0.0015, "PEAD": 0.0015, "ACERO": 0.10, "FOFO": 0.25, "CONCRETO": 0.30}
C_HAZEN = {"PVC": 150, "PEAD": 150, "ACERO": 120, "FOFO": 100, "CONCRETO": 120}
MOTORES_HP = [1, 1.5, 2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300]


# ----------------------------------------------------------------------------
# 1. GASTOS DE DISEÑO
# ----------------------------------------------------------------------------
def calcula_gastos(poblacion: float, dotacion_lphd: float, cvd: float = 1.4, cvh: float = 1.55) -> dict:
    """Gastos de diseño. cvd/cvh típicos MAPAS: 1.2-1.4 y 1.5-1.8 (según localidad)."""
    qmed = poblacion * dotacion_lphd / 86400.0
    qmd = qmed * cvd
    qmh = qmd * cvh
    return {"Qmed": qmed, "QMD": qmd, "QMH": qmh}


def gasto_bombeo(qmd_lps: float, horas_bombeo: float = 24.0) -> float:
    """Con tanque regulador se bombea QMD distribuido en las horas de bombeo.
    Si el booster alimenta directo a la red (sin tanque), la bomba debe dar QMH."""
    return qmd_lps * 24.0 / horas_bombeo


# ----------------------------------------------------------------------------
# 2. HIDRÁULICA DE TUBERÍAS
# ----------------------------------------------------------------------------
@dataclass
class Nodo:
    id: str
    cota: float
    demanda_lps: float = 0.0            # gasto de diseño que sale en este nodo
    p_req_m: Optional[float] = None     # presión deseada propia (si None usa la global)


@dataclass
class Tramo:
    id: str
    desde: str
    hasta: str
    L: float                            # m
    D_mm: float                         # diámetro interno, mm
    material: str = "PVC"
    K: float = 0.0                      # suma de coef. de pérdidas menores (codos, válvulas...)


def perdida_tramo(t: Tramo, q_lps: float, metodo: str = "DW", pct_menores: float = 0.0):
    """Devuelve (hf, hm, V). metodo: 'DW' Darcy-Weisbach (Swamee-Jain) o 'HW' Hazen-Williams."""
    if q_lps <= 0 or t.L <= 0:
        return 0.0, 0.0, (q_lps / 1000.0) / (math.pi * (t.D_mm / 1000.0) ** 2 / 4) if q_lps > 0 else 0.0
    Q = q_lps / 1000.0
    D = t.D_mm / 1000.0
    V = Q / (math.pi * D ** 2 / 4.0)
    mat = t.material.upper()
    if metodo.upper() == "HW":
        C = C_HAZEN.get(mat, 130)
        hf = 10.67 * t.L * Q ** 1.852 / (C ** 1.852 * D ** 4.87)
    else:
        eps = RUGOSIDAD_MM.get(mat, 0.1) / 1000.0
        Re = V * D / NU
        if Re < 2000:
            f = 64.0 / Re
        else:
            f = 0.25 / (math.log10(eps / (3.7 * D) + 5.74 / Re ** 0.9)) ** 2
        hf = f * (t.L / D) * V ** 2 / (2 * G)
    hm = t.K * V ** 2 / (2 * G) + pct_menores * hf
    return hf, hm, V


# ----------------------------------------------------------------------------
# 3. RED RAMIFICADA (árbol con raíz en la descarga de la bomba)
# ----------------------------------------------------------------------------
class Red:
    def __init__(self, nodos: list[Nodo], tramos: list[Tramo], raiz: str):
        self.nodos = {n.id: n for n in nodos}
        self.tramos = tramos
        self.raiz = raiz
        self.hijos: dict[str, list[Tramo]] = {}
        for t in tramos:
            for x in (t.desde, t.hasta):
                if x not in self.nodos:
                    raise ValueError(f"Tramo {t.id}: el nodo '{x}' no existe.")
            self.hijos.setdefault(t.desde, []).append(t)
        if raiz not in self.nodos:
            raise ValueError("La raíz (descarga de la bomba) no existe en los nodos.")
        visto, pila = set(), [raiz]
        while pila:
            n = pila.pop()
            if n in visto:
                raise ValueError("La red tiene ciclos; este script resuelve redes RAMIFICADAS (árbol).")
            visto.add(n)
            pila += [t.hasta for t in self.hijos.get(n, [])]
        faltan = set(self.nodos) - visto
        if faltan:
            raise ValueError(f"Nodos no conectados a la bomba: {sorted(faltan)}")

    def terminales(self) -> list[str]:
        return [n for n in self.nodos if n != self.raiz and not self.hijos.get(n)]

    def caudales(self, k: float):
        """Gasto por tramo (lps) con las demandas multiplicadas por k."""
        q: dict[str, float] = {}

        def rec(nid: str) -> float:
            tot = self.nodos[nid].demanda_lps * k
            for t in self.hijos.get(nid, []):
                qt = rec(t.hasta)
                q[t.id] = qt
                tot += qt
            return tot

        return q, rec(self.raiz)


def resolver(red: Red, k: float, cfg: dict) -> dict:
    """Con demandas escaladas por k, obtiene la carga requerida en la descarga de la bomba
    para garantizar la presión deseada en el/los nodos de control."""
    q, qtot = red.caudales(k)
    acum = {red.raiz: 0.0}
    det = {}
    pila = [red.raiz]
    while pila:
        n = pila.pop()
        for t in red.hijos.get(n, []):
            hf, hm, v = perdida_tramo(t, q[t.id], cfg["metodo"], cfg["pct_menores"])
            det[t.id] = {"Q": q[t.id], "V": v, "hf": hf, "hm": hm}
            acum[t.hasta] = acum[n] + hf + hm
            pila.append(t.hasta)
    controles = cfg.get("nodos_control") or red.terminales()
    req = {}
    for nid in controles:
        nd = red.nodos[nid]
        p = nd.p_req_m if nd.p_req_m is not None else cfg["presion_deseada_m"]
        req[nid] = nd.cota + p + acum[nid]
    crit = max(req, key=req.get)
    H = req[crit]                                   # cota piezométrica requerida en la descarga
    pres = {n: H - acum[n] - red.nodos[n].cota for n in acum}
    return {"k": k, "Q": qtot, "H_desc": H, "crit": crit, "det": det, "pres": pres, "acum": acum}


# ----------------------------------------------------------------------------
# 4. SUCCIÓN, NPSH, POTENCIA, CURVA DE BOMBA
# ----------------------------------------------------------------------------
def patm_m(altitud_m: float) -> float:
    return 10.33 * (1 - 2.25577e-5 * altitud_m) ** 5.25588


def pvap_m(temp_c: float) -> float:
    mmhg = 10 ** (8.07131 - 1730.63 / (233.426 + temp_c))
    return mmhg * 0.013595


def ajusta_curva(puntos):
    """Ajusta H = c0 + c1*Q + c2*Q^2 a 3 puntos (Q lps, H m) de UNA bomba a velocidad nominal."""
    pts = [tuple(p) for p in puntos[:3]]
    c0 = c1 = c2 = 0.0
    for i, (xi, yi) in enumerate(pts):
        xj, xk = [p[0] for j, p in enumerate(pts) if j != i]
        den = (xi - xj) * (xi - xk)
        c2 += yi / den
        c1 += -yi * (xj + xk) / den
        c0 += yi * xj * xk / den
    return c0, c1, c2


def velocidad_relativa(coefs, q_bomba, h_req):
    """Relación de velocidad r (n/n_nominal) para que una bomba entregue (Q,H). Leyes de afinidad."""
    a, b, c = coefs
    disc = (b * q_bomba) ** 2 - 4 * a * (c * q_bomba ** 2 - h_req)
    if disc < 0 or a <= 0:
        return None
    return (-b * q_bomba + math.sqrt(disc)) / (2 * a)


def motor_comercial(hp: float) -> float:
    for m in MOTORES_HP:
        if m >= hp:
            return m
    return math.ceil(hp)


# ----------------------------------------------------------------------------
# 5. ANÁLISIS COMPLETO
# ----------------------------------------------------------------------------
def construir_red(cfg: dict) -> Red:
    nodos = [Nodo(**n) for n in cfg["nodos"]]
    tramos = [Tramo(**t) for t in cfg["tramos"]]
    return Red(nodos, tramos, cfg["raiz"])


def analizar(cfg: dict) -> dict:
    red = construir_red(cfg)
    s = cfg["succion"]
    tsuc = Tramo("succion", "tanque", "bomba", s.get("L", 0), s["D_mm"], s.get("material", "PVC"), s.get("K", 0))
    escenarios = []
    for k in cfg["escenarios"]:
        r = resolver(red, k, cfg)
        hf, hm, vs = perdida_tramo(tsuc, r["Q"], cfg["metodo"], cfg["pct_menores"])
        r["hf_succ"], r["V_succ"] = hf + hm, vs
        r["H_succ"] = s["nivel_agua"] - r["hf_succ"]       # cota piezométrica a la entrada de la bomba
        r["CDT"] = r["H_desc"] - r["H_succ"]
        escenarios.append(r)
    return {"red": red, "esc": escenarios, "tsuc": tsuc}


def reporte(cfg: dict, res: dict) -> None:
    red, esc = res["red"], res["esc"]
    dis = max(esc, key=lambda r: r["k"])             # escenario de diseño = mayor k
    nb = cfg.get("n_bombas", 1)
    lim = cfg["limites"]
    linea = "=" * 78

    print(linea)
    print(" BOOSTER CON PRESIÓN CONSTANTE - MEMORIA DE CÁLCULO")
    print(linea)
    print(f" Método: {'Darcy-Weisbach (Swamee-Jain)' if cfg['metodo'].upper() == 'DW' else 'Hazen-Williams'}"
          f" | menores: {cfg['pct_menores'] * 100:.0f}% de hf + K | bombas en paralelo: {nb}")
    print(f" Presión deseada en nodos de control: {cfg['presion_deseada_m']} m.c.a.")

    print("\n--- Tramos en condición de diseño ---")
    print(f" {'Tramo':<10}{'Q lps':>8}{'D mm':>8}{'L m':>10}{'V m/s':>8}{'hf m':>8}{'hm m':>8}  Obs")
    for t in red.tramos:
        d = dis["det"][t.id]
        obs = ""
        if d["Q"] > 0 and d["V"] < lim["v_min"]:
            obs = "V baja"
        if d["V"] > lim["v_max"]:
            obs = "V ALTA"
        print(f" {t.id:<10}{d['Q']:>8.2f}{t.D_mm:>8.1f}{t.L:>10.2f}{d['V']:>8.2f}{d['hf']:>8.2f}{d['hm']:>8.2f}  {obs}")

    print("\n--- Presiones en nodos (condición de diseño, sin válvulas reguladoras) ---")
    print(f" {'Nodo':<10}{'Cota':>9}{'P m.c.a.':>10}{'P kg/cm2':>10}  Obs")
    for nid, p in dis["pres"].items():
        obs = ""
        if nid != red.raiz:
            if p < lim["p_min"] - 1e-6:
                obs = "BAJA"
            elif p > lim["p_max"]:
                obs = f"ALTA: instalar VRP/válvula, disipar ~{p - cfg['presion_deseada_m']:.1f} m"
            elif nid in red.terminales() and p - cfg["presion_deseada_m"] > 1.0:
                obs = f"exceso {p - cfg['presion_deseada_m']:.1f} m (regular el ramal)"
        print(f" {nid:<10}{red.nodos[nid].cota:>9.2f}{p:>10.2f}{p / 10:>10.2f}  {obs}")

    print(f"\n Nodo crítico (define la carga): {dis['crit']}")

    print("\n--- Curva del sistema a presión constante ---")
    coefs = ajusta_curva(cfg["curva_bomba"]) if cfg.get("curva_bomba") else None
    enc = f" {'Q lps':>8}{'Q/bomba':>9}{'CDT m':>9}{'P descarga':>12}{'Crítico':>10}"
    if coefs:
        enc += f"{'N/Nnom':>9}"
    print(enc)
    for r in esc:
        p_desc = r["H_desc"] - red.nodos[red.raiz].cota
        linea_r = f" {r['Q']:>8.2f}{r['Q'] / nb:>9.2f}{r['CDT']:>9.2f}{p_desc:>12.2f}{r['crit']:>10}"
        if coefs:
            rr = velocidad_relativa(coefs, r["Q"] / nb, r["CDT"]) if r["Q"] > 0 else None
            linea_r += f"{rr * 100:>8.0f}%" if rr else f"{'n/d':>9}"
        print(linea_r)
    print(" P descarga = consigna de presión en la descarga para sostener la presión deseada en el nodo")
    print(" crítico. Si el sensor del VFD está en la descarga, la consigna debe COMPENSARSE con el gasto")
    print(" (aumentar al subir Q). Si el sensor está en el nodo crítico, la consigna es fija.")

    # Potencia y NPSH en el punto de diseño
    Q = dis["Q"]
    H = dis["CDT"]
    eta_b, eta_m, fs = cfg["eficiencia_bomba"], cfg["eficiencia_motor"], cfg["factor_seguridad_motor"]
    p_hid = G * (Q / 1000.0) * H                        # kW
    p_motor_kw = p_hid / (eta_b * eta_m)
    hp_req = p_motor_kw / HP_KW
    hp_com = motor_comercial(hp_req * fs / nb)
    s = cfg["succion"]
    npshd = (patm_m(s["altitud_m"]) - pvap_m(s.get("temp_c", 20)) + (s["nivel_agua"] - s["cota_eje_bomba"]) - dis["hf_succ"])
    print("\n--- Equipo de bombeo (punto de diseño) ---")
    print(f" Capacidad total .............. {Q:.2f} lps  ({Q / nb:.2f} lps por bomba, {nb} en paralelo)")
    print(f" CDT de diseño ................ {H:.2f} m.c.a.  (estática+fricción+menores+presión residual)")
    print(f" Potencia hidráulica .......... {p_hid:.2f} kW")
    print(f" Potencia al motor ............ {p_motor_kw:.2f} kW = {hp_req:.1f} HP (η bomba {eta_b:.0%}, η motor {eta_m:.0%})")
    print(f" Motor comercial sugerido ..... {hp_com:g} HP por bomba (F.S. {fs:.2f})")
    print(f" NPSH disponible .............. {npshd:.2f} m (Patm {patm_m(s['altitud_m']):.2f} m a {s['altitud_m']:.0f} msnm)")
    if cfg.get("npsh_requerido"):
        marg = npshd - cfg["npsh_requerido"]
        print(f" NPSH requerido (fabricante) .. {cfg['npsh_requerido']:.2f} m -> margen {marg:.2f} m "
              f"{'OK' if marg >= 1.0 else 'INSUFICIENTE (recomendado >= 1 m)'}")
    else:
        print(" NPSH requerido ............... captura el del fabricante y compara (margen >= 1 m).")
    print(linea)
    print(" Verificar: perfil real del terreno (puntos altos), golpe de ariete, válvulas de aire,")
    print(" curva del fabricante y operación a gasto mínimo (bomba piloto / jockey).")
    print(linea)


def graficar(cfg: dict, res: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib no instalado: pip install matplotlib")
        return
    esc = res["esc"]
    qs = [r["Q"] for r in esc]
    hs = [r["CDT"] for r in esc]
    plt.figure(figsize=(7, 4.5))
    plt.plot(qs, hs, "o-", label="Sistema (presión constante)")
    if cfg.get("curva_bomba"):
        nb = cfg.get("n_bombas", 1)
        c0, c1, c2 = ajusta_curva(cfg["curva_bomba"])
        qmax = max(qs) * 1.3
        xs = [qmax * i / 50 for i in range(51)]
        plt.plot(xs, [c0 + c1 * x / nb + c2 * (x / nb) ** 2 for x in xs], "--", label=f"Bomba 100% ({nb} en paralelo)")
    plt.xlabel("Q (lps)")
    plt.ylabel("CDT (m.c.a.)")
    plt.grid(alpha=.3)
    plt.legend()
    plt.title("Curva del sistema booster")
    plt.tight_layout()
    plt.savefig("curva_sistema.png", dpi=150)
    print("Gráfica guardada: curva_sistema.png")


# ----------------------------------------------------------------------------
# 6. CONFIGURACIÓN (EJEMPLO = esquema en T del problema anterior)
# ----------------------------------------------------------------------------
DEFAULTS = {
    "metodo": "DW",                     # "DW" Darcy-Weisbach  |  "HW" Hazen-Williams
    "pct_menores": 0.10,                # pérdidas menores como % de la fricción
    "presion_deseada_m": 15.0,          # presión constante deseada en nodos de control (m.c.a.)
    "nodos_control": None,              # None = todos los nodos terminales; o lista ["Pte","Ote"]
    "escenarios": [0.0, 0.25, 0.5, 0.75, 1.0],      # fracciones del gasto de diseño
    "n_bombas": 1,
    "eficiencia_bomba": 0.65,
    "eficiencia_motor": 0.92,
    "factor_seguridad_motor": 1.15,
    "npsh_requerido": None,             # m, del fabricante
    "curva_bomba": None,                # 3 puntos [(Q lps, H m)] de UNA bomba a velocidad nominal
    "limites": {"p_min": 10.0, "p_max": 50.0, "v_min": 0.3, "v_max": 5.0},   # típicos MAPAS
}

EJEMPLO = {
    "raiz": "B",                        # descarga de la bomba
    "nodos": [
        {"id": "B", "cota": 290.41},
        {"id": "T", "cota": 290.96},
        {"id": "Pte", "cota": 303.00, "demanda_lps": 12.0},
        {"id": "Ote", "cota": 279.65, "demanda_lps": 6.0},
    ],
    "tramos": [
        {"id": "B-T", "desde": "B", "hasta": "T", "L": 42.0, "D_mm": 152.4},
        {"id": "T-Pte", "desde": "T", "hasta": "Pte", "L": 3474.55, "D_mm": 152.4},
        {"id": "T-Ote", "desde": "T", "hasta": "Ote", "L": 2007.40, "D_mm": 101.6},
    ],
    "succion": {"nivel_agua": 291.33, "cota_eje_bomba": 290.41, "L": 6.0, "D_mm": 203.2,
                "K": 1.5, "altitud_m": 2240, "temp_c": 20},
    "presion_deseada_m": 15.0,
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cfg = dict(DEFAULTS)
    if args:
        with open(args[0], encoding="utf-8") as f:
            user = json.load(f)
    else:
        user = EJEMPLO
        print("(Sin archivo JSON: se usa el ejemplo incluido, esquema en T)\n")
    cfg.update(user)
    cfg["limites"] = {**DEFAULTS["limites"], **user.get("limites", {})}
    res = analizar(cfg)
    reporte(cfg, res)
    if "--plot" in sys.argv:
        graficar(cfg, res)


if __name__ == "__main__":
    main()
