"""Calculo del ATTR (Average Two-Terminal Reliability) de la red de telecomunicaciones.

Definicion (Boettcher, Prieto, Restrepo y Pezoa, IEEE Latin America Transactions 16(9), 2018, ec. 8,
"fiabilidad promedio de dos terminales"; ref. original Rai y Agrawal 1990):

    ATTR = C(n,2)^-1 * sum_{i<j} Z_ij ,  Z_ij = 1 si existe un camino entre los nodos clave i y j

es decir, la fraccion de PARES de nodos clave que pueden comunicarse.
 - Escenario "operacion": red sin fallas -> ATTR determinista (cada par esta conectado o no).
 - Escenario "sismo": cada arista falla de forma independiente con p_falla (tabla red.prob_falla);
   ATTR = valor esperado de la fraccion de pares conectados, estimado por Monte Carlo.
Ademas se informa, como dato complementario, la probabilidad de que TODOS los nodos clave queden
conectados (confiabilidad de todos los terminales).

Redes:  --red todas  : las 4 capas + puente inferido (red de caminos documentada)
        --red fibra  : solo la fibra optica detectada (infraestructura realmente tendida)

Uso:
  python calcular_attr.py --escenario operacion [--red todas|fibra]
  python calcular_attr.py --escenario sismo --n 10000 --semilla 42 [--red todas|fibra]
Conexion: PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (por defecto 127.0.0.1:5433).
"""
import argparse
import os
from datetime import datetime

import numpy as np
import psycopg
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

REDES = {
    "todas": ("Red completa: 4 capas + puente inferido", "TRUE", "vertice_id"),
    "fibra": ("Solo fibra optica detectada", "capa = 'fibra'", "vertice_fibra"),
}


def conectar():
    return psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
        dbname=os.getenv("PGDATABASE", "resiliencia"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"))


def filtro_red(red="todas", sin_puentes=False):
    f = REDES[red][1]
    return f + (" AND tipo = 'detectado'" if sin_puentes else "")


def cargar_red(cur, sin_puentes=False, red="todas"):
    """Devuelve (aristas [id, source, target], nodos clave [id, nombre, vertice], n_vertices)."""
    cur.execute(f"SELECT id, source, target FROM red.aristas WHERE {filtro_red(red, sin_puentes)} ORDER BY id")
    aristas = np.array(cur.fetchall(), dtype=np.int64)
    cur.execute(f"SELECT id, nombre, {REDES[red][2]} FROM red.nodos_clave ORDER BY id")
    claves = cur.fetchall()
    cur.execute("SELECT max(id) FROM red.aristas_vertices_pgr")
    n_vert = cur.fetchone()[0] + 1
    return aristas, claves, n_vert


def componentes(aristas_ids_src_tgt, n_vert, mascara_activa=None):
    src, tgt = aristas_ids_src_tgt[:, 1], aristas_ids_src_tgt[:, 2]
    if mascara_activa is not None:
        src, tgt = src[mascara_activa], tgt[mascara_activa]
    g = coo_matrix((np.ones(len(src), dtype=np.int8), (src, tgt)), shape=(n_vert, n_vert))
    return connected_components(g, directed=False)[1]


def metricas_estado(etiquetas, vert_claves):
    """Metricas de conectividad de los nodos clave dado el vector de componentes de una realizacion."""
    et = etiquetas[vert_claves]
    valores, cuentas = np.unique(et, return_counts=True)
    k = len(vert_claves)
    pares = int((cuentas * (cuentas - 1) // 2).sum())
    attr = pares / (k * (k - 1) // 2)
    return {
        "attr": attr,                                   # ec. (8) del paper: fraccion de pares conectados
        "frac_pares_conectados": attr,                  # (alias, por compatibilidad)
        "todos_conectados": 1.0 if len(valores) == 1 else 0.0,
        "n_componentes_con_claves": len(valores),
        "claves_en_mayor": int(cuentas.max()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--escenario", choices=["operacion", "sismo"], required=True)
    ap.add_argument("--n", type=int, default=10000, help="simulaciones Monte Carlo (sismo)")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--sin-puentes", action="store_true")
    ap.add_argument("--red", choices=list(REDES), default="todas")
    ap.add_argument("--tabla", default="prob_falla", help="tabla de probabilidades en esquema red")
    a = ap.parse_args()

    with conectar() as conn, conn.cursor() as cur:
        aristas, claves, n_vert = cargar_red(cur, a.sin_puentes, a.red)
        vert_claves = np.array([c[2] for c in claves])
        nombre = REDES[a.red][0]
        if a.sin_puentes:
            nombre = nombre.replace(" + puente inferido", "") + ", sin el puente inferido"
        print(f"{nombre}: {len(aristas)} aristas, {len(claves)} nodos clave")

        if a.escenario == "operacion":
            et = componentes(aristas, n_vert)
            m = metricas_estado(et, vert_claves)
            print("\nESCENARIO: red en operacion (sin fallas)")
            print(f"  Componentes que contienen nodos clave : {m['n_componentes_con_claves']}")
            print(f"  Nodos clave en la mayor componente    : {m['claves_en_mayor']} de {len(claves)}")
            print(f"  Todos los nodos clave conectados      : {'si' if m['todos_conectados'] else 'no'}")
            if m["n_componentes_con_claves"] > 1:
                for comp in np.unique(et[vert_claves]):
                    nombres = [c[1] for c, e in zip(claves, et[vert_claves]) if e == comp]
                    print(f"    bloque {comp}: {', '.join(nombres)}")
            print(f"  ATTR (operacion) = {m['attr']:.4f}")
            valor, todos, n_sim, err = m["attr"], m["todos_conectados"], 0, 0.0
        else:
            cur.execute(f"SELECT arista_id, p_falla FROM red.{a.tabla} ORDER BY arista_id")
            pf = dict(cur.fetchall())
            p = np.array([pf.get(int(i), 0.0) for i in aristas[:, 0]])
            rng = np.random.default_rng(a.semilla)
            vals, todos_l, mayor = [], [], []
            i_stgo = [c[1] for c in claves].index("Santiago")
            con_stgo = np.zeros(len(claves))           # veces que cada nodo clave sigue conectado con Santiago
            for _ in range(a.n):
                activa = rng.random(len(p)) >= p      # True = el enlace sobrevive
                et = componentes(aristas, n_vert, activa)
                m = metricas_estado(et, vert_claves)
                vals.append(m["attr"]); todos_l.append(m["todos_conectados"]); mayor.append(m["claves_en_mayor"])
                con_stgo += et[vert_claves] == et[vert_claves[i_stgo]]
            valor, todos, n_sim = float(np.mean(vals)), float(np.mean(todos_l)), a.n
            err = 1.96 * float(np.std(vals, ddof=1)) / np.sqrt(a.n)
            print(f"\nESCENARIO: sismo (Monte Carlo, n={a.n}, semilla={a.semilla})")
            print(f"  Probabilidad media de falla de las aristas : {p.mean():.4f}")
            print(f"  Aristas con p_falla > 0.5                  : {(p > 0.5).sum()}")
            print(f"  Nodos clave conectados a la mayor componente (prom.) : {np.mean(mayor):.2f} de {len(claves)}")
            print(f"  P(todos los nodos clave conectados)                  : {todos:.4f}")
            print(f"  ATTR (post-sismo) = {valor:.4f}   (IC95% +/- {err:.4f})")
            print("\n  Probabilidad de seguir conectado con Santiago despues del sismo:")
            for i in np.argsort(con_stgo / a.n):
                if i != i_stgo:
                    print(f"    {claves[i][1]:14s} {con_stgo[i] / a.n:.4f}")

        cur.execute("""CREATE TABLE IF NOT EXISTS red.resultados_attr(
            id serial PRIMARY KEY, fecha timestamp, escenario text, sin_puentes boolean,
            n_sim int, attr double precision, frac_pares double precision, tabla_prob text)""")
        cur.execute("ALTER TABLE red.resultados_attr ADD COLUMN IF NOT EXISTS red text, "
                    "ADD COLUMN IF NOT EXISTS p_todos double precision, ADD COLUMN IF NOT EXISTS definicion text")
        cur.execute("INSERT INTO red.resultados_attr(fecha, escenario, sin_puentes, n_sim, attr, frac_pares, tabla_prob,"
                    " red, p_todos, definicion) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (datetime.now(), a.escenario, a.sin_puentes, n_sim, valor, valor,
                     a.tabla if a.escenario == "sismo" else None, a.red, todos, "dos terminales (pares)"))
        conn.commit()


if __name__ == "__main__":
    main()
