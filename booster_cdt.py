# -*- coding: utf-8 -*-
"""
booster_cdt.py - Motor de cálculo: booster con ramales, presión constante, criterios tipo MAPAS-CONAGUA.

Incluye: gastos de diseño, geometría comercial (DN, RD), profundidad de zanja y cota de tubería, perfil de
terreno por tramo, reparto de gasto por ramal (automático por equilibrio hidráulico, % o lps), pérdidas por
fricción (Darcy-Weisbach / Hazen-Williams), tubería envejecida (eps_t = eps_0 + a*t), pérdidas menores (% o
por accesorios K), diámetro óptimo y alternativas, CDT, potencia, NPSH y golpe de ariete (Joukowsky/Michaud).

Unidades: m, mm, lps, m.c.a., msnm. Los valores por defecto son TÍPICOS (MAPAS / bibliografía): verifícalos
contra la edición vigente del manual, la norma de tu organismo operador y los datos del fabricante.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Optional

G = 9.81
NU = 1.004e-6
HP_KW = 0.7457
EV_KGCM2 = 2.06e4                   # módulo de compresibilidad del agua (MAPAS), kg/cm2

# ----------------------------------------------------------------------------- catálogos
OD_IPS = {2: 60.3, 2.5: 73.0, 3: 88.9, 4: 114.3, 6: 168.3, 8: 219.1, 10: 273.1, 12: 323.9, 14: 355.6,
          16: 406.4, 18: 457.2, 20: 508.0, 24: 609.6, 30: 762.0, 36: 914.4}       # DN (pulg) -> D ext (mm)
OD_IPS = {float(k): v for k, v in OD_IPS.items()}
DN_LISTA = sorted(OD_IPS)
# PVC AWWA C-900 / C-905 (D ext. controlado por equivalencia a hierro fundido)
OD_C900 = {4.0: 121.9, 6.0: 175.3, 8.0: 229.9, 10.0: 281.9, 12.0: 335.3, 14.0: 388.6, 16.0: 442.0, 18.0: 495.3,
           20.0: 548.6, 24.0: 655.3}
RD_C900 = {"RD 25": (25, 11.6), "RD 18": (18, 16.5), "RD 14": (14, 21.4)}      # (RD, presión de trabajo kg/cm2)
# clase -> (RD, presión de trabajo kg/cm2)
RD_PVC = {"RD 41": (41, 7.0), "RD 32.5": (32.5, 8.8), "RD 26": (26, 11.2), "RD 21": (21, 14.1), "RD 13.5": (13.5, 28.1)}
RD_PEAD = {"RD 26": (26, 6.4), "RD 21": (21, 8.2), "RD 17": (17, 10.2), "RD 13.6": (13.6, 12.7), "RD 11": (11, 16.3)}
CLASES = {"PVC C-900": RD_C900, "PVC IPS": RD_PVC, "PEAD": RD_PEAD, "ACERO": {"Estándar": (None, None)}, "FODU": {"K9": (None, None)}}
# C: Hazen-Williams (nuevo) | eps0: rugosidad tubo nuevo (mm) | E: módulo de elasticidad (kg/cm2)
MATERIALES = {
    "PVC C-900": {"C": 140, "eps0": 0.0015, "E": 2.81e4},
    "PVC IPS": {"C": 140, "eps0": 0.0015, "E": 2.81e4},
    "PEAD": {"C": 140, "eps0": 0.0015, "E": 1.0e4},
    "ACERO": {"C": 120, "eps0": 0.05, "E": 2.1e6},
    "FODU": {"C": 140, "eps0": 0.12, "E": 1.7e6},
}
# coeficiente a (mm/año) por grupo de agua, ec. eps_t = eps_0 + a*t  (valores medios; ver Tabla 2.12 MAPAS)
GRUPOS_AGUA = {
    "I": ("Agua poco mineralizada, no corrosiva", 0.025),
    "II": ("Agua poco mineralizada, algo corrosiva", 0.07),
    "III": ("Agua con residuo mineral apreciable, moderadamente corrosiva", 0.20),
    "IV": ("Agua dura, con tendencia a incrustar", 0.51),
    "V": ("Agua muy dura, con residuo denso (a de 0.6 a más de 1)", 1.00),
}
ACCESORIOS_K = {   # valores típicos; editables en la app
    "Codo 90° radio largo": 0.6, "Codo 90° radio corto": 0.9, "Codo 45°": 0.4, "Tee paso directo": 0.6,
    "Tee salida lateral": 1.8, "Válvula compuerta abierta": 0.2, "Válvula mariposa abierta": 0.5,
    "Válvula de esfera": 0.1, "Válvula de retención (check)": 2.5, "Reducción gradual": 0.15,
    "Ampliación gradual": 0.30, "Entrada de borde vivo": 0.5, "Salida a tanque": 1.0, "Otro": 1.0,
}


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


_BASE_MAT, _BASE_CLASES = set(MATERIALES), set(CLASES)
CUSTOM: dict = {}


def mat_key(m) -> str:
    k = str(m).upper().strip().replace("C900", "C-900")
    return {"PVC": "PVC IPS"}.get(k, k)


def registra_materiales(cfg: dict) -> None:
    """Materiales definidos por el usuario: nombre, C, eps0_mm, E_kgcm2, espesor_mm (opc.), P_trab_kgcm2."""
    for k in [k for k in MATERIALES if k not in _BASE_MAT]:
        del MATERIALES[k]
    for k in [k for k in CLASES if k not in _BASE_CLASES]:
        del CLASES[k]
    CUSTOM.clear()
    for m in cfg.get("materiales_personalizados") or []:
        nom = mat_key(m["nombre"])
        MATERIALES[nom] = {"C": float(m.get("C") or 130), "eps0": float(m.get("eps0_mm") or 0.1),
                           "E": float(m.get("E_kgcm2") or 2.0e5)}
        CUSTOM[nom] = {"e_mm": m.get("espesor_mm"), "P_kgcm2": m.get("P_trab_kgcm2")}
        CLASES[nom] = {"Única": (None, None)}


def clases_de(material) -> dict:
    return CLASES.get(mat_key(material), {"Única": (None, None)})


def dn_lista(material):
    mk = mat_key(material)
    return sorted(OD_C900) if mk == "PVC C-900" else (sorted(OD_IPS) if mk in ("PVC IPS", "PEAD") else DN_LISTA)


# ----------------------------------------------------------------------------- gastos
def harmon(poblacion: float):
    """Coeficiente de Harmon M = 1 + 14/(4+sqrt(P)), P en miles de hab. (3.8 si P<1000; 2.0 si P>63 454)."""
    if poblacion <= 0:
        return None
    if poblacion < 1000:
        return 3.8
    if poblacion > 63454:
        return 2.0
    return 1 + 14 / (4 + math.sqrt(poblacion / 1000.0))


def calcula_gastos(modo, valor=0.0, poblacion=0.0, dotacion=0.0, cvd=1.4, cvh=1.55, horas=24.0,
                   con_tanque=True, k_min=0.5, cs_ext=1.5, M_manual=None) -> dict:
    """A partir del dato conocido (modo: poblacion | qmed | qmd | qmh | qbomba) obtiene todos los gastos.
    QMD = CVD·Qmed; QMH = CVH·QMD; Qmin = k_min·Qmed; Qmáx inst = M·Qmed (Harmon); Qmáx ext = CS·Qmáx inst.
    Con tanque regulador se bombea QMD·24/horas; directo a la red, QMH."""
    if modo == "poblacion":
        qmed = poblacion * dotacion / 86400.0
    elif modo == "qmed":
        qmed = valor
    elif modo == "qmd":
        qmed = valor / cvd
    elif modo == "qmh":
        qmed = valor / (cvd * cvh)
    else:                                            # qbomba
        qmed = (valor * horas / 24.0 if con_tanque else valor / cvh) / cvd
    qmd, qmh = qmed * cvd, qmed * cvd * cvh
    M = harmon(poblacion) or (M_manual or None)
    qmi = M * qmed if M else None
    return {"Qmed": qmed, "QMD": qmd, "QMH": qmh, "Qmin": k_min * qmed, "Qmax_inst": qmi,
            "Qmax_ext": cs_ext * qmi if qmi else None, "M": M,
            "Qbomba": valor if modo == "qbomba" else (qmd * 24.0 / horas if con_tanque else qmh)}


# ----------------------------------------------------------------------------- geometría
def geometria(material: str, dn: float, clase: str) -> dict:
    mk, dn = mat_key(material), float(dn)
    if mk in ("PVC C-900", "PVC IPS", "PEAD"):
        tabla = OD_C900 if mk == "PVC C-900" else OD_IPS
        if dn not in tabla:
            raise ValueError(f"DN {dn:g}\" no disponible en {mk} (disponibles: {sorted(tabla)})")
        tab = CLASES[mk]
        rd, p = tab.get(clase) or next(iter(tab.values()))
        od = tabla[dn]
        e = od / rd
        return {"D_int": od - 2 * e, "D_ext": od, "e": e, "P_m": p * 10.0}
    d = dn * 25.4                                   # otros materiales: DN ≈ diámetro interior
    cu = CUSTOM.get(mk)
    if cu is not None:
        e = float(cu["e_mm"]) if not _nan(cu.get("e_mm")) and cu.get("e_mm") else max(4.8, d / 60)
        pm = float(cu["P_kgcm2"]) * 10 if not _nan(cu.get("P_kgcm2")) and cu.get("P_kgcm2") else None
        return {"D_int": d, "D_ext": d + 2 * e, "e": e, "P_m": pm}
    e = max(4.8, d / 60) if mk == "ACERO" else 9 * (0.5 + 0.001 * d)
    return {"D_int": d, "D_ext": d + 2 * e, "e": e, "P_m": None}


def prof_minima(d_ext_mm: float, dn_pulg: float, colchon_m: float) -> float:
    """MAPAS: 0.70 m en tubería <= 51 mm; en adelante D_ext + 0.05 m + colchón mínimo."""
    if dn_pulg * 25.4 <= 51.0:
        return 0.70
    return d_ext_mm / 1000.0 + 0.05 + colchon_m


def ancho_zanja(d_ext_mm: float) -> float:
    """MAPAS: D_ext + 0.50 m (D_ext < 0.50 m) o D_ext + 0.60 m (D_ext >= 0.50 m)."""
    d = d_ext_mm / 1000.0
    return d + (0.5 if d < 0.5 else 0.6)


@dataclass
class Nodo:
    id: str
    cota: float                                     # cota de terreno, msnm
    demanda_lps: float = 0.0                        # consumo fijo en el nodo (opcional)
    p_req_m: Optional[float] = None                 # presión deseada propia (si None usa la global)


@dataclass
class Tramo:
    id: str
    desde: str
    hasta: str
    L: float
    dn_pulg: float
    material: str = "PVC C-900"
    clase: str = "RD 25"
    prof_m: Optional[float] = None                  # profundidad de zanja (fondo); None = mínima MAPAS
    K: float = 0.0                                  # K adicional manual
    modo: str = "auto"                              # reparto: auto | pct | lps
    valor: float = 0.0
    perfil: Optional[list] = None                   # [(estación m, cota terreno)]
    P_trab_kgcm2: Optional[float] = None
    # derivados
    D_mm: float = field(default=0.0, init=False)
    D_ext: float = field(default=0.0, init=False)
    e_mm: float = field(default=0.0, init=False)
    P_clase_m: Optional[float] = field(default=None, init=False)
    prof: float = field(default=0.0, init=False)
    prof_min: float = field(default=0.0, init=False)
    Ksum: float = field(default=0.0, init=False)
    dz: float = field(default=0.0, init=False)
    terreno: list = field(default_factory=list, init=False)
    eje: list = field(default_factory=list, init=False)
    aviso: str = field(default="", init=False)


def prepara_tramo(t: Tramo, cfg: dict, ksum_acc: float = 0.0) -> None:
    tab = clases_de(t.material)
    if t.clase not in tab:
        nueva = next(iter(tab))
        t.aviso = f"Tramo {t.id}: la clase '{t.clase}' no existe para {t.material}; se usó {nueva}."
        t.clase = nueva
    g = geometria(t.material, t.dn_pulg, t.clase)
    t.D_mm, t.D_ext, t.e_mm = g["D_int"], g["D_ext"], g["e"]
    t.P_clase_m = t.P_trab_kgcm2 * 10 if not _nan(t.P_trab_kgcm2) and t.P_trab_kgcm2 else g["P_m"]
    t.prof_min = prof_minima(t.D_ext, t.dn_pulg, cfg.get("colchon_min_m", 0.90))
    t.prof = float(t.prof_m) if not _nan(t.prof_m) else t.prof_min
    t.dz = -t.prof + 0.05 + t.D_ext / 2000.0        # eje de tubería = fondo de zanja + cama 5 cm + radio exterior
    t.Ksum = (0.0 if _nan(t.K) else t.K) + ksum_acc


# ----------------------------------------------------------------------------- red
class Red:
    def __init__(self, nodos, tramos, raiz, cfg):
        self.nodos = {n.id: n for n in nodos}
        self.tramos, self.raiz, self.cfg = tramos, raiz, cfg
        self.hijos: dict[str, list[Tramo]] = {}
        self.padre: dict[str, Tramo] = {}
        acc: dict[str, float] = {}
        for a in cfg.get("accesorios", []) or []:
            acc[a["tramo"]] = acc.get(a["tramo"], 0.0) + float(a["cant"]) * float(a["K"])
        for t in tramos:
            for x in (t.desde, t.hasta):
                if x not in self.nodos:
                    raise ValueError(f"Tramo {t.id}: el nodo '{x}' no existe.")
            prepara_tramo(t, cfg, acc.get(t.id, 0.0))
            self.hijos.setdefault(t.desde, []).append(t)
            self.padre[t.hasta] = t
        if raiz not in self.nodos:
            raise ValueError("La raíz (descarga de la bomba) no existe en los nodos.")
        vistos, pila = set(), [raiz]
        while pila:
            n = pila.pop()
            if n in vistos:
                raise ValueError("La red tiene ciclos; este programa resuelve redes ramificadas (árbol).")
            vistos.add(n)
            pila += [t.hasta for t in self.hijos.get(n, [])]
        if set(self.nodos) - vistos:
            raise ValueError(f"Nodos no conectados a la bomba: {sorted(set(self.nodos) - vistos)}")
        for t in tramos:                            # perfiles de terreno y de eje de tubería, abscisa 0..L
            if t.perfil and len(t.perfil) >= 2:
                pf = sorted((float(x), float(z)) for x, z in t.perfil)
                x0, span = pf[0][0], (pf[-1][0] - pf[0][0]) or 1.0
                t.terreno = [((x - x0) / span * t.L, z) for x, z in pf]
            else:
                t.terreno = [(0.0, self.nodos[t.desde].cota), (t.L, self.nodos[t.hasta].cota)]
            t.eje = [(x, z + t.dz) for x, z in t.terreno]
        self.z_terr, self.z_tub = {}, {}
        for t in tramos:
            self.z_terr[t.hasta] = t.terreno[-1][1]
            self.z_tub[t.hasta] = t.eje[-1][1]
        h0 = self.hijos.get(raiz, [])
        self.z_terr[raiz] = h0[0].terreno[0][1] if h0 else self.nodos[raiz].cota
        self.z_tub[raiz] = h0[0].eje[0][1] if h0 else self.nodos[raiz].cota

    def terminales(self):
        return [n for n in self.nodos if n != self.raiz and not self.hijos.get(n)]

    def camino(self, destino):
        c, n = [], destino
        while n != self.raiz:
            c.insert(0, self.padre[n])
            n = self.padre[n].desde
        return c


# ----------------------------------------------------------------------------- pérdidas
def eps_tubo_mm(material: str, cfg: dict) -> float:
    a = cfg.get("a_envejec_mm_anio")
    if _nan(a):
        a = GRUPOS_AGUA.get(cfg.get("grupo_agua", "II"), ("", 0.07))[1]
    return MATERIALES.get(mat_key(material), {"eps0": 0.1})["eps0"] + a * float(cfg.get("edad_anios", 0) or 0)


def perdida_tramo(t, q_lps: float, cfg: dict):
    """(hf, hm, V). Darcy-Weisbach/Swamee-Jain con eps_t (envejecida) o Hazen-Williams (solo tubería nueva)."""
    D = t.D_mm / 1000.0
    A = math.pi * D ** 2 / 4.0
    if q_lps <= 0:
        return 0.0, 0.0, 0.0
    Q = q_lps / 1000.0
    V = Q / A
    edad = float(cfg.get("edad_anios", 0) or 0)
    if cfg.get("metodo", "DW").upper() == "HW" and edad <= 0:
        C = MATERIALES.get(mat_key(t.material), {"C": 130})["C"]
        hf = 10.67 * t.L * Q ** 1.852 / (C ** 1.852 * D ** 4.87)
    else:
        eps = eps_tubo_mm(t.material, cfg) / 1000.0
        Re = V * D / NU
        f = 64.0 / Re if Re < 2000 else 0.25 / (math.log10(eps / (3.7 * D) + 5.74 / Re ** 0.9)) ** 2
        hf = f * (t.L / D) * V ** 2 / (2 * G)
    if cfg.get("modo_menores", "pct") == "K":
        hm = t.Ksum * V ** 2 / (2 * G)
    else:
        hm = cfg.get("pct_menores", 0.10) * hf
    return hf, hm, V


# ----------------------------------------------------------------------------- reparto de gasto
class Hidraulica:
    """Calcula el reparto de gasto por ramal (fijo / % / equilibrio hidráulico) y la carga requerida."""

    def __init__(self, red: Red, cfg: dict):
        self.red, self.cfg = red, cfg
        self.pperf = cfg.get("presion_min_perfil_m", 0.0)

    def perd(self, t, Q):
        hf, hm, _ = perdida_tramo(t, Q, self.cfg)
        return hf + hm

    def p_req(self, n):
        p = self.red.nodos[n].p_req_m
        return self.cfg["presion_deseada_m"] if _nan(p) else p

    def H_up(self, t, Q):
        """Carga requerida aguas arriba del tramo para pasar Q (incluye puntos altos del perfil)."""
        perd = self.perd(t, Q)
        h = self.H_req(t.hasta, Q) + perd
        for x, z in t.eje:
            h = max(h, z + self.pperf + (perd * x / t.L if t.L > 0 else 0.0))
        return h

    def H_req(self, n, Q):
        if not self.red.hijos.get(n):
            return self.red.z_tub[n] + self.p_req(n)
        return self.reparto(n, Q)[1]

    def Q_de_H(self, t, H, Qmax):
        if Qmax <= 0 or self.H_up(t, 0.0) >= H:
            return 0.0
        if self.H_up(t, Qmax) <= H:
            return Qmax
        lo, hi = 0.0, Qmax
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if self.H_up(t, mid) < H else (lo, mid)
        return (lo + hi) / 2

    def reparto(self, n, Q):
        red = self.red
        nodo = red.nodos[n]
        d = 0.0 if _nan(nodo.demanda_lps) else nodo.demanda_lps
        Qout = max(Q - d, 0.0)
        hs = red.hijos[n]
        q, autos, Hn = {}, [], -1e9
        for t in hs:
            if t.modo == "lps":
                q[t.id] = max(float(t.valor), 0.0)
            elif t.modo == "pct":
                q[t.id] = Qout * float(t.valor) / 100.0
            else:
                autos.append(t)
        for t in hs:
            if t.id in q:
                Hn = max(Hn, self.H_up(t, q[t.id]))
        Qr = max(Qout - sum(q.values()), 0.0)
        if autos:
            if len(autos) == 1:
                q[autos[0].id] = Qr
                Ha = self.H_up(autos[0], Qr)
            else:
                lo = min(self.H_up(t, 0.0) for t in autos)
                hi = max(self.H_up(t, Qr) for t in autos) + 1e-6
                for _ in range(40):
                    mid = (lo + hi) / 2
                    s = sum(self.Q_de_H(t, mid, Qr) for t in autos)
                    lo, hi = (mid, hi) if s < Qr else (lo, mid)
                Ha = (lo + hi) / 2
                qa = {t.id: self.Q_de_H(t, Ha, Qr) for t in autos}
                tot = sum(qa.values()) or 1.0
                q.update({k: v * Qr / tot for k, v in qa.items()})
            Hn = max(Hn, Ha)
        if d > 0:
            Hn = max(Hn, red.z_tub[n] + self.p_req(n))
        return q, Hn

    def asigna(self, Qtot):
        flows = {}

        def rec(n, Q):
            if not self.red.hijos.get(n):
                return
            q, _ = self.reparto(n, Q)
            for t in self.red.hijos[n]:
                flows[t.id] = q[t.id]
                rec(t.hasta, q[t.id])

        rec(self.red.raiz, Qtot)
        return flows


def resolver(red: Red, k: float, cfg: dict) -> dict:
    hid = Hidraulica(red, cfg)
    Qtot = cfg["Q_bombeo_lps"] * k
    q = hid.asigna(Qtot) if Qtot > 0 else {t.id: 0.0 for t in red.tramos}
    acum, det, pila = {red.raiz: 0.0}, {}, [red.raiz]
    while pila:
        n = pila.pop()
        for t in red.hijos.get(n, []):
            hf, hm, v = perdida_tramo(t, q[t.id], cfg)
            det[t.id] = {"Q": q[t.id], "V": v, "hf": hf, "hm": hm}
            acum[t.hasta] = acum[n] + hf + hm
            pila.append(t.hasta)
    req = {}
    for nid, nd in red.nodos.items():
        if nid == red.raiz:
            continue
        if not red.hijos.get(nid) or (not _nan(nd.demanda_lps) and nd.demanda_lps > 0):
            req[nid] = red.z_tub[nid] + hid.p_req(nid) + acum[nid]
    for t in red.tramos:
        d = det[t.id]
        for x, z in t.eje:
            req[f"{t.id}@{x:.0f}m"] = z + hid.pperf + acum[t.desde] + (d["hf"] + d["hm"]) * x / (t.L or 1.0)
    crit = max(req, key=req.get)
    H = req[crit]
    hgl = {red.raiz: H}
    for nid in acum:
        hgl[nid] = H - acum[nid]
    pres = {n: hgl[n] - red.z_tub[n] for n in acum}
    for t in red.tramos:
        d = det[t.id]
        pm = min(hgl[t.desde] - (d["hf"] + d["hm"]) * x / (t.L or 1.0) - z for x, z in t.eje)
        d["p_min_linea"] = pm
        d["p_max_linea"] = max(hgl[t.desde] - (d["hf"] + d["hm"]) * x / (t.L or 1.0) - z for x, z in t.eje)
    return {"k": k, "Q": Qtot, "H_desc": H, "crit": crit, "det": det, "pres": pres, "acum": acum,
            "req": req, "hgl": hgl}


def analizar(cfg: dict) -> dict:
    registra_materiales(cfg)
    nodos = [Nodo(**n) for n in cfg["nodos"]]
    tramos = [Tramo(**t) for t in cfg["tramos"]]
    red = Red(nodos, tramos, cfg["raiz"], cfg)
    s = cfg["succion"]
    ts = Tramo("succion", "tanque", "bomba", s.get("L", 0.0), s["dn_pulg"], s.get("material", "PVC C-900"),
               s.get("clase", "RD 25"), None, s.get("K", 0.0))
    ksuc = sum(float(a["cant"]) * float(a["K"]) for a in cfg.get("accesorios", []) or [] if a["tramo"] == "succion")
    prepara_tramo(ts, cfg, ksuc)
    avisos = [t.aviso for t in tramos + [ts] if t.aviso]
    if cfg.get("metodo", "DW").upper() == "HW" and (cfg.get("edad_anios", 0) or 0) > 0:
        avisos.append("Con tubería envejecida se usa Darcy-Weisbach (eps_t = eps_0 + a·t); Hazen-Williams solo aplica a tubería nueva.")
    esc = []
    for k in cfg["escenarios"]:
        r = resolver(red, k, cfg)
        hf, hm, vs = perdida_tramo(ts, r["Q"], cfg)
        r["hf_succ"], r["V_succ"] = hf + hm, vs
        r["H_succ"] = s["nivel_agua"] - r["hf_succ"]
        r["CDT"] = r["H_desc"] - r["H_succ"]
        esc.append(r)
    c0 = {**cfg, "edad_anios": 0}
    nueva = resolver(red, 1.0, c0)
    hfn, hmn, _ = perdida_tramo(ts, nueva["Q"], c0)
    nueva["CDT"] = nueva["H_desc"] - (s["nivel_agua"] - hfn - hmn)
    return {"red": red, "esc": esc, "nueva": nueva, "tsuc": ts, "avisos": avisos}


# ----------------------------------------------------------------------------- equipo, curva de bomba
def patm_m(alt):
    return 10.33 * (1 - 2.25577e-5 * alt) ** 5.25588


def pvap_m(T):
    return 10 ** (8.07131 - 1730.63 / (233.426 + T)) * 0.013595


def ajusta_curva(pts):
    p = [tuple(x) for x in pts[:3]]
    c0 = c1 = c2 = 0.0
    for i, (xi, yi) in enumerate(p):
        xj, xk = [q[0] for j, q in enumerate(p) if j != i]
        den = (xi - xj) * (xi - xk)
        c2 += yi / den
        c1 += -yi * (xj + xk) / den
        c0 += yi * xj * xk / den
    return c0, c1, c2


def velocidad_relativa(coefs, q, h):
    a, b, c = coefs
    disc = (b * q) ** 2 - 4 * a * (c * q ** 2 - h)
    return None if disc < 0 or a <= 0 else (-b * q + math.sqrt(disc)) / (2 * a)


MOTORES_HP = [1, 1.5, 2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300]


def motor_comercial(hp):
    return next((m for m in MOTORES_HP if m >= hp), math.ceil(hp))


def diseno(res):
    return max(res["esc"], key=lambda r: r["k"])


def equipo(cfg, res):
    dis, nb, s = diseno(res), cfg.get("n_bombas", 1), cfg["succion"]
    Q, H = dis["Q"], dis["CDT"]
    p_hid = G * Q / 1000 * H
    p_kw = p_hid / (cfg["eficiencia_bomba"] * cfg["eficiencia_motor"])
    hp = p_kw / HP_KW
    npshd = patm_m(s["altitud_m"]) - pvap_m(s.get("temp_c", 20)) + (s["nivel_agua"] - s["cota_eje_bomba"]) - dis["hf_succ"]
    npshr = cfg.get("npsh_requerido")
    return {"Q": Q, "H": H, "p_hid": p_hid, "p_kw": p_kw, "hp": hp,
            "hp_com": motor_comercial(hp * cfg["factor_seguridad_motor"] / nb), "npshd": npshd,
            "marg": (npshd - npshr) if npshr else None, "crit": dis["crit"]}


# ----------------------------------------------------------------------------- golpe de ariete
def celeridad(t) -> float:
    """Allievi (MAPAS): a = 1420 / sqrt(1 + (Ev/E)(D/e))  [m/s]."""
    E = MATERIALES.get(mat_key(t.material), {"E": 2.0e6})["E"]
    return 1420.0 / math.sqrt(1.0 + (EV_KGCM2 / E) * (t.D_mm / t.e_mm))


def ariete(cfg, res):
    """Estimación por tramo para paro de bomba / cierre: Joukowsky (tc <= 2L/a) o Michaud (tc > 2L/a)."""
    red, dis, s = res["red"], diseno(res), cfg["succion"]
    tc = float(cfg.get("tc_s", 0.0) or 0.0)
    filas = []
    for t in red.tramos:
        d = dis["det"][t.id]
        V = d["V"]
        a = celeridad(t)
        T = 2 * t.L / a
        dH = a * V / G
        tipo = "directo (Joukowsky)"
        if tc > T and V > 0:
            dH = min(2 * t.L * V / (G * tc), dH)
            tipo = "indirecto (Michaud)"
        p_op = max(dis["pres"][t.desde], dis["pres"][t.hasta])
        p_est = max(s["nivel_agua"] - red.z_tub[t.desde], s["nivel_agua"] - red.z_tub[t.hasta])
        p_max = max(p_op, p_est) + dH
        p_min = d["p_min_linea"] - dH
        obs = []
        if t.P_clase_m and p_max > t.P_clase_m:
            obs.append(f"EXCEDE clase ({t.P_clase_m:.0f} m)")
        if p_min <= -10:
            obs.append("vacío / separación de columna")
        elif p_min < 0:
            obs.append("depresión")
        filas.append({"Tramo": t.id, "V m/s": V, "a m/s": a, "T=2L/a s": T, "Tipo": tipo, "ΔH m": dH,
                      "P op. máx m": max(p_op, p_est), "P máx. m": p_max, "P mín. m": p_min,
                      "Clase m": t.P_clase_m, "Obs": "; ".join(obs)})
    return filas


# ----------------------------------------------------------------------------- verificación de presiones
def verifica_presiones(cfg, res):
    """Compara resultados contra (a) presiones mínima/máxima de suministro del proyecto en nodos de consumo y
    (b) presión que soporta la tubería (clase) en cada tramo, en operación y con golpe de ariete."""
    red, dis, nueva, lim = res["red"], diseno(res), res["nueva"], cfg["limites"]
    sumin = set(red.terminales()) | {n for n, nd in red.nodos.items() if not _nan(nd.demanda_lps) and nd.demanda_lps > 0}
    k = 10.0
    nodos = []
    for nid, p in dis["pres"].items():
        if nid == red.raiz:
            continue
        p_new = dis["H_desc"] - nueva["acum"][nid] - red.z_tub[nid]
        p_max = max(max(r["pres"][nid] for r in res["esc"]), p_new)
        sup = nid in sumin
        ok_min = p >= lim["p_min"] - 1e-6
        ok_max = p_max <= lim["p_max"] + 1e-6
        nodos.append({"Nodo": nid, "Tipo": "suministro" if sup else "intermedio", "P diseño kg/cm²": p / k,
                      "P máx. operación kg/cm²": p_max / k, "P mín. proyecto kg/cm²": lim["p_min"] / k,
                      "P máx. proyecto kg/cm²": lim["p_max"] / k,
                      "Mínima": ("CUMPLE" if ok_min else "NO CUMPLE") if sup else "n/a",
                      "Máxima": ("CUMPLE" if ok_max else "NO CUMPLE") if sup else "n/a",
                      "Acción": ("" if (not sup or (ok_min and ok_max)) else
                                 ("Aumentar CDT o reducir pérdidas" if not ok_min else "Instalar válvula reductora (VRP)"))})
    ar = {a["Tramo"]: a for a in ariete(cfg, res)}
    tub = []
    for t in red.tramos:
        p_op = max(r["det"][t.id]["p_max_linea"] for r in res["esc"])
        dn_ = nueva["det"][t.id]
        pn = max(dis["H_desc"] - nueva["acum"][t.desde] - (dn_["hf"] + dn_["hm"]) * x / (t.L or 1.0) - z for x, z in t.eje)
        p_tr = max(p_op, pn)
        dH = ar[t.id]["ΔH m"]
        cl = t.P_clase_m
        tub.append({"Tramo": t.id, "Material": t.material, "Clase": t.clase,
                    "P trabajo máx. kg/cm²": p_tr / k, "Soporta kg/cm²": None if cl is None else cl / k,
                    "Operación": "SIN DATO" if cl is None else ("CUMPLE" if p_tr <= cl else "NO CUMPLE"),
                    "ΔH ariete kg/cm²": dH / k, "P con ariete kg/cm²": (p_tr + dH) / k,
                    "Con ariete": "SIN DATO" if cl is None else ("CUMPLE" if p_tr + dH <= cl else "NO CUMPLE"),
                    "Margen kg/cm²": None if cl is None else (cl - p_tr - dH) / k})
    ok = all(r["Mínima"] != "NO CUMPLE" and r["Máxima"] != "NO CUMPLE" for r in nodos) and \
        all(r["Operación"] != "NO CUMPLE" and r["Con ariete"] != "NO CUMPLE" for r in tub)
    return nodos, tub, ok


# ----------------------------------------------------------------------------- diámetros
def dn_optimo(Q_lps, material, clase, cfg):
    """Diámetro óptimo (criterio en cfg['criterio_dn']):
    'bresse'    : D = 1.3 X^0.25 sqrt(Q), X = horas/24; DN comercial más cercano (por diámetro interior)
    'velocidad' : menor DN con V <= v_obj
    'gradiente' : menor DN con pérdida unitaria J <= J_max (m/km), Hazen-Williams con C del material"""
    if Q_lps is None or Q_lps <= 0:
        return None
    crit = cfg.get("criterio_dn", "bresse")
    if crit == "bresse":
        X = cfg.get("horas_bombeo", 24.0) / 24.0
        Db = 1.3 * X ** 0.25 * math.sqrt(Q_lps / 1000.0) * 1000
        return min(dn_lista(material), key=lambda dn: abs(geometria(material, dn, clase)["D_int"] - Db))
    C = MATERIALES.get(mat_key(material), {"C": 130})["C"]
    for dn in dn_lista(material):
        D = geometria(material, dn, clase)["D_int"] / 1000
        if crit == "gradiente":
            J = 10.67 * (Q_lps / 1000) ** 1.852 / (C ** 1.852 * D ** 4.87) * 1000
            if J <= cfg.get("J_max_m_km", 4.0):
                return dn
        elif (Q_lps / 1000) / (math.pi * D ** 2 / 4) <= cfg.get("v_obj", 1.5):
            return dn
    return dn_lista(material)[-1]


def _rapido(cfg):
    c = copy.deepcopy(cfg)
    c["escenarios"] = [1.0]
    return c


def optimiza_diametros(cfg):
    """Con los gastos ya definidos (los del reparto actual, congelados) asigna a cada tramo su DN óptimo."""
    c = copy.deepcopy(cfg)
    dis = diseno(analizar(_rapido(c)))
    for t in c["tramos"]:
        dn = dn_optimo(dis["det"][t["id"]]["Q"], t.get("material", "PVC C-900"), t.get("clase", "RD 25"), c)
        if dn is not None:
            t["dn_pulg"] = dn
    return c


def alternativas(cfg, tramo_id):
    """Para un tramo: DN óptimo -1, óptimo, óptimo +1 y el actual; efecto en CDT, potencia, V y ariete."""
    base = _rapido(cfg)
    res0 = analizar(base)
    Q = diseno(res0)["det"][tramo_id]["Q"]
    t0 = next(t for t in base["tramos"] if t["id"] == tramo_id)
    opt = dn_optimo(Q, t0.get("material", "PVC C-900"), t0.get("clase", "RD 25"), base) or t0["dn_pulg"]
    lista = dn_lista(t0.get("material", "PVC C-900"))
    i = lista.index(opt) if opt in lista else 0
    cand = [lista[j] for j in (i - 1, i, i + 1) if 0 <= j < len(lista)]
    if t0["dn_pulg"] not in cand:
        cand.append(t0["dn_pulg"])
    eq0 = equipo(base, res0)
    filas = []
    for dn in sorted(cand):
        c = copy.deepcopy(base)
        next(t for t in c["tramos"] if t["id"] == tramo_id)["dn_pulg"] = dn
        r = analizar(c)
        eq, d, ar = equipo(c, r), diseno(r)["det"][tramo_id], ariete(c, r)
        fa = next(x for x in ar if x["Tramo"] == tramo_id)
        tr = next(t for t in r["red"].tramos if t.id == tramo_id)
        obs = "" if cfg["limites"]["v_min"] <= d["V"] <= cfg["limites"]["v_max"] else "V fuera de límites"
        filas.append({"DN pulg": dn, "D int mm": tr.D_mm, "V m/s": d["V"], "hf+hm tramo m": d["hf"] + d["hm"],
                      "CDT m": eq["H"], "ΔCDT m": eq["H"] - eq0["H"], "Potencia HP": eq["hp"],
                      "ΔHP": eq["hp"] - eq0["hp"], "ΔH ariete m": fa["ΔH m"],
                      "Etiqueta": ("óptimo" if dn == opt else "") + (" · actual" if dn == t0["dn_pulg"] else ""),
                      "Obs": obs})
    return {"Q": Q, "optimo": opt, "actual": t0["dn_pulg"], "filas": filas}


def advertencia_cambio(alt):
    """Texto de advertencia al comparar el DN actual contra el óptimo."""
    act = next(f for f in alt["filas"] if f["DN pulg"] == alt["actual"])
    opt = next(f for f in alt["filas"] if f["DN pulg"] == alt["optimo"])
    if alt["actual"] == alt["optimo"]:
        return "El diámetro actual coincide con el óptimo para el gasto de diseño."
    dcdt, dhp = act["CDT m"] - opt["CDT m"], act["Potencia HP"] - opt["Potencia HP"]
    if alt["actual"] < alt["optimo"]:
        return (f"Con {alt['actual']}\" (menor que el óptimo {alt['optimo']}\"): V = {act['V m/s']:.2f} m/s, CDT {dcdt:+.1f} m "
                f"y potencia {dhp:+.1f} HP respecto al óptimo (más energía todo el año), ΔH de ariete "
                f"{act['ΔH ariete m'] - opt['ΔH ariete m']:+.1f} m. Ventaja: tubería más barata. Riesgos: mayor presión en la bomba, "
                "mayores pérdidas y desgaste, velocidad alta si supera el límite, y menor holgura para ampliaciones o tubería envejecida.")
    return (f"Con {alt['actual']}\" (mayor que el óptimo {alt['optimo']}\"): V = {act['V m/s']:.2f} m/s, CDT {dcdt:+.1f} m y potencia "
            f"{dhp:+.1f} HP respecto al óptimo (menos energía). Ventaja: menos pérdidas y margen a futuro. Riesgos: mayor costo de tubería y "
            "excavación, y velocidad baja (< límite mínimo) con riesgo de sedimentación y mayor tiempo de residencia del agua.")


# ----------------------------------------------------------------------------- configuración de ejemplo
DEFAULTS = {
    "metodo": "DW", "edad_anios": 0, "grupo_agua": "II", "a_envejec_mm_anio": None,
    "modo_menores": "pct", "pct_menores": 0.10, "accesorios": [], "colchon_min_m": 0.90,
    "presion_deseada_m": 15.0, "presion_min_perfil_m": 0.0, "escenarios": [0.0, 0.25, 0.5, 0.75, 1.0],
    "n_bombas": 1, "eficiencia_bomba": 0.65, "eficiencia_motor": 0.92, "factor_seguridad_motor": 1.15,
    "npsh_requerido": None, "curva_bomba": None, "tc_s": 0.0, "criterio_dn": "bresse", "v_obj": 1.5, "J_max_m_km": 4.0,
    "horas_bombeo": 24.0, "limites": {"p_min": 15.0, "p_max": 50.0, "v_min": 0.3, "v_max": 5.0},      # m.c.a. = 1.5 y 5.0 kg/cm2
}
EJEMPLO = {
    "raiz": "B", "Q_bombeo_lps": 18.0,
    "nodos": [{"id": "B", "cota": 290.41}, {"id": "T", "cota": 290.96},
              {"id": "Pte", "cota": 303.00}, {"id": "Ote", "cota": 279.65}],
    "tramos": [
        {"id": "B-T", "desde": "B", "hasta": "T", "L": 42.0, "dn_pulg": 6.0},
        {"id": "T-Pte", "desde": "T", "hasta": "Pte", "L": 3474.55, "dn_pulg": 6.0, "modo": "lps", "valor": 12},
        {"id": "T-Ote", "desde": "T", "hasta": "Ote", "L": 2007.40, "dn_pulg": 4.0, "modo": "lps", "valor": 6}],
    "succion": {"nivel_agua": 291.33, "cota_eje_bomba": 290.41, "L": 6.0, "dn_pulg": 8, "altitud_m": 2240, "temp_c": 20},
}


def main():
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(EJEMPLO))
    res = analizar(cfg)
    eq = equipo(cfg, res)
    print(f"Q={eq['Q']:.1f} lps  CDT={eq['H']:.2f} m  Potencia={eq['hp']:.1f} HP  crítico={eq['crit']}")


if __name__ == "__main__":
    main()
