"""Metricas de resiliencia de la red antes y despues del sismo, segun el paper del curso:

  N. Boettcher, Y. Prieto, S. E. Restrepo y J. E. Pezoa, "Resilient Multiculture Network Design in the
  Presence of Exploit-Triggered Correlated Failures", IEEE Latin America Transactions 16(9), 2018.

Las cuatro metricas del paper, adaptadas a fallas de enlaces por sismo:
  1) ATTR, fiabilidad promedio de dos terminales (ec. 8): fraccion de pares de nodos clave conectados.
  2) Numero de componentes conexas N (ec. 4): cuantas "islas" deja la falla (y cuantas contienen nodos clave).
  3) Centralidad por distancia (Apendice, ec. 12): promedio, sobre los pares de nodos clave, del inverso
     de la distancia en numero de enlaces (un par desconectado aporta 1/inf = 0, de modo que la metrica
     baja cuando la red se fragmenta). Las distancias se calculan con pgRouting en la base de datos
     (pgr_dijkstraCost); tambien se informa la distancia media (enlaces y km) entre los pares conectados.
  4) Grado medio y coeficiente de agrupamiento medio (Fig. 2): descriptores de la topologia.

Despues del sismo cada arista falla de forma independiente con su p_falla (red.prob_falla, main.sql) y
cada metrica es el promedio de n realizaciones Monte Carlo (la centralidad, de las primeras n_dist, porque
cada una requiere una consulta de rutas en la base de datos).

Uso: python metricas_resiliencia.py [--red todas|fibra|ambas] [--n 2000] [--n-dist 200] [--semilla 42]
Guarda los resultados en la tabla red.metricas_resiliencia.
"""
import argparse
from datetime import datetime

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

import calcular_attr as ca

NOMBRES = [
    ("attr", "ATTR (fiabilidad promedio de dos terminales, ec. 8)"),
    ("p_todos", "  P(todos los nodos clave conectados)"),
    ("claves_en_mayor", "  Nodos clave en la mayor componente"),
    ("n_componentes", "Componentes conexas de la red N (ec. 4)"),
    ("islas_con_claves", "  Componentes que contienen nodos clave"),
    ("centralidad", "Centralidad por distancia (ec. 12, prom. 1/saltos)"),
    ("dist_media_saltos", "  Dist. media entre pares conectados [enlaces]"),
    ("dist_media_km", "  Dist. media entre pares conectados [km]"),
    ("grado_medio", "Grado medio (Fig. 2)"),
    ("agrupamiento", "Coeficiente de agrupamiento medio (Fig. 2)"),
]


def adyacencia(aristas, n_vert, mascara=None):
    """Matriz de adyacencia simple (binaria, simetrica, sin lazos) de las aristas activas."""
    src, tgt = aristas[:, 1], aristas[:, 2]
    if mascara is not None:
        src, tgt = src[mascara], tgt[mascara]
    sel = src != tgt
    src, tgt = src[sel], tgt[sel]
    a = coo_matrix((np.ones(len(src)), (src, tgt)), shape=(n_vert, n_vert)).tocsr()
    a = (a + a.T).tocsr()
    a.data[:] = 1.0
    return a


def metricas_topologia(a, vertices_red):
    """N, grado medio y agrupamiento medio sobre los vertices de la red (los aislados cuentan)."""
    n_comp, etiquetas = connected_components(a, directed=False)
    grado = np.asarray(a.sum(axis=1)).ravel()
    triangulos = np.asarray((a @ a).multiply(a).sum(axis=1)).ravel() / 2.0
    pos = grado >= 2
    c = np.zeros_like(grado)
    c[pos] = 2.0 * triangulos[pos] / (grado[pos] * (grado[pos] - 1))
    v = vertices_red
    return etiquetas, {
        "n_componentes": float(len(np.unique(etiquetas[v]))),
        "grado_medio": float(grado[v].mean()),
        "agrupamiento": float(c[v].mean()),
    }


def distancias_pgrouting(cur, red, vert_claves, caidas=None):
    """Distancias entre nodos clave con pgr_dijkstraCost (en saltos y en km) dentro de la base de datos."""
    excl = ""
    if caidas is not None and len(caidas):
        excl = " AND NOT (id = ANY('{%s}'::int[]))" % ",".join(str(int(x)) for x in caidas)
    filtro = ca.filtro_red(red)
    vids = sorted({int(v) for v in vert_claves})
    res = {}
    for clave, costo in (("saltos", "1.0::float8"), ("km", "longitud_m / 1000.0")):
        sql_aristas = f"SELECT id, source, target, {costo} AS cost FROM red.aristas WHERE {filtro}{excl}"
        cur.execute("SELECT start_vid, end_vid, agg_cost FROM pgr_dijkstraCost(%s, %s::bigint[], %s::bigint[], "
                    "directed := false)", (sql_aristas, vids, vids))
        res[clave] = {(int(s), int(t)): float(c) for s, t, c in cur.fetchall()}
    d_saltos, d_km = [], []
    k = len(vert_claves)
    for i in range(k):
        for j in range(i + 1, k):
            vi, vj = int(vert_claves[i]), int(vert_claves[j])
            if vi == vj:
                d_saltos.append(0.0); d_km.append(0.0)
            elif (vi, vj) in res["saltos"]:
                d_saltos.append(res["saltos"][(vi, vj)]); d_km.append(res["km"][(vi, vj)])
    total = k * (k - 1) // 2
    return {
        "pares_alcanzables": len(d_saltos),
        # ec. (12): suma de inversos de distancias; un par desconectado aporta 1/inf = 0
        "centralidad": float(sum(1.0 / max(d, 1.0) for d in d_saltos)) / total,
        "dist_media_saltos": float(np.mean(d_saltos)) if d_saltos else float("nan"),
        "dist_media_km": float(np.mean(d_km)) if d_km else float("nan"),
    }


def estado(cur, red, aristas, n_vert, vertices_red, vert_claves, mascara=None, con_distancias=True):
    a = adyacencia(aristas, n_vert, mascara)
    etiquetas, top = metricas_topologia(a, vertices_red)
    m = ca.metricas_estado(etiquetas, vert_claves)
    r = {"attr": m["attr"], "p_todos": m["todos_conectados"], "claves_en_mayor": float(m["claves_en_mayor"]),
         "islas_con_claves": float(m["n_componentes_con_claves"]), **top}
    if con_distancias:
        caidas = None if mascara is None else aristas[~mascara, 0]
        r.update(distancias_pgrouting(cur, red, vert_claves, caidas))
    return r


def calcular(cur, red="todas", n=2000, n_dist=200, semilla=42, tabla="prob_falla", verbose=True):
    aristas, claves, n_vert = ca.cargar_red(cur, red=red)
    vert = np.array([c[2] for c in claves])
    vertices_red = np.unique(np.concatenate([aristas[:, 1], aristas[:, 2], vert]))
    antes = estado(cur, red, aristas, n_vert, vertices_red, vert)
    k = len(vert)
    if antes["pares_alcanzables"] != round(antes["attr"] * k * (k - 1) / 2):
        raise RuntimeError("pgRouting y SciPy no coinciden en los pares conectados")

    cur.execute(f"SELECT arista_id, p_falla FROM red.{tabla}")
    pf = dict(cur.fetchall())
    p = np.array([pf.get(int(i), 0.0) for i in aristas[:, 0]])
    rng = np.random.default_rng(semilla)
    muestras = {c: [] for c, _ in NOMBRES}
    for i in range(n):
        activa = rng.random(len(p)) >= p
        r = estado(cur, red, aristas, n_vert, vertices_red, vert, activa, con_distancias=i < n_dist)
        for c in muestras:
            if c in r and not (isinstance(r[c], float) and np.isnan(r[c])):
                muestras[c].append(r[c])
        if verbose and (i + 1) % 500 == 0:
            print(f"   {red}: {i + 1}/{n} realizaciones")
    despues = {c: float(np.mean(v)) for c, v in muestras.items() if v}
    ic95 = {c: float(1.96 * np.std(v, ddof=1) / np.sqrt(len(v))) for c, v in muestras.items() if len(v) > 1}
    return {"red": red, "descripcion": ca.REDES[red][0], "n_aristas": int(len(aristas)),
            "n_vertices": int(len(vertices_red)), "n_claves": k, "p_media": float(p.mean()),
            "fallas_esperadas": float(p.sum()), "n_sim": n, "n_dist": min(n, n_dist), "semilla": semilla,
            "antes": antes, "despues": despues, "ic95": ic95}


def guardar(cur, res):
    cur.execute("""CREATE TABLE IF NOT EXISTS red.metricas_resiliencia(
        red text, escenario text, metrica text, valor double precision, ic95 double precision,
        n_sim int, fecha timestamp, PRIMARY KEY (red, escenario, metrica))""")
    cur.execute("DELETE FROM red.metricas_resiliencia WHERE red = %s", (res["red"],))
    ahora = datetime.now()
    for esc in ("antes", "despues"):
        for c, v in res[esc].items():
            if isinstance(v, float) and np.isnan(v):
                continue
            n_sim = 0 if esc == "antes" else (res["n_dist"] if c in ("centralidad", "dist_media_saltos", "dist_media_km",
                                                                         "pares_alcanzables") else res["n_sim"])
            cur.execute("INSERT INTO red.metricas_resiliencia VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (res["red"], esc, c, float(v), res["ic95"].get(c) if esc == "despues" else None, n_sim, ahora))
    for c in ("n_aristas", "n_vertices", "n_claves", "p_media", "fallas_esperadas"):
        cur.execute("INSERT INTO red.metricas_resiliencia VALUES (%s,'datos',%s,%s,NULL,%s,%s)",
                    (res["red"], c, float(res[c]), res["n_sim"], ahora))


def imprimir(res):
    print(f"\n{res['descripcion']}  ({res['n_aristas']} aristas, {res['n_vertices']} vertices, "
          f"{res['n_claves']} nodos clave)")
    print(f"Sismo: p_falla media {res['p_media']:.4f}, fallas esperadas {res['fallas_esperadas']:.0f}; "
          f"Monte Carlo n={res['n_sim']} (distancias: {res['n_dist']}), semilla {res['semilla']}")
    print(f"{'Metrica':56s} {'Antes':>10s} {'Despues':>10s} {'IC95%':>9s}")
    for c, nombre in NOMBRES:
        a, d = res["antes"].get(c), res["despues"].get(c)
        if a is None or d is None:
            continue
        e = res["ic95"].get(c)
        print(f"{nombre:56s} {a:10.4f} {d:10.4f} {('+/-%.4f' % e) if e is not None else '':>9s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--red", choices=["todas", "fibra", "ambas"], default="ambas")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--n-dist", type=int, default=200)
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--tabla", default="prob_falla")
    a = ap.parse_args()
    with ca.conectar() as conn, conn.cursor() as cur:
        for red in (["todas", "fibra"] if a.red == "ambas" else [a.red]):
            res = calcular(cur, red, a.n, a.n_dist, a.semilla, a.tabla)
            if a.tabla == "prob_falla":
                guardar(cur, res)
                conn.commit()
            imprimir(res)


if __name__ == "__main__":
    main()
