"""Exploracion: ATTR post-sismo si los nodos clave fueran un subconjunto de las 16 ciudades (n=3000, semilla 5)."""
import sys, numpy as np
sys.path.insert(0, "scripts")
import calcular_attr as ca
conn = ca.conectar(); cur = conn.cursor()
ar, claves, nv = ca.cargar_red(cur)
nombre = {c[1]: c[2] for c in claves}
cur.execute("select arista_id,p_falla from red.prob_falla order by arista_id")
pf = dict(cur.fetchall()); p = np.array([pf[int(i)] for i in ar[:,0]])
def attr(nombres, n=3000):
    v = np.array([nombre[x] for x in nombres]); rng = np.random.default_rng(5); ok = 0; pares = 0
    for _ in range(n):
        m = ca.metricas_estado(ca.componentes(ar, nv, rng.random(len(p)) >= p), v); ok += m["todos_conectados"]; pares += m["attr"]
    return pares/n, ok/n   # (ATTR de dos terminales, P(todos conectados))
for nom, s in {"4 (Santiago, Valparaiso, Arica, Punta Arenas)": ["Santiago","Valparaiso","Arica","Punta Arenas"],
  "Norte hasta Santiago (7)": ["Arica","Iquique","Antofagasta","Copiapo","La Serena","Valparaiso","Santiago"],
  "Norte hasta La Serena (5)": ["Arica","Iquique","Antofagasta","Copiapo","La Serena"],
  "Santiago + Valparaiso": ["Santiago","Valparaiso"],
  "Sur de Temuco (Valdivia, P.Montt, Coyhaique, P.Arenas)": ["Valdivia","Puerto Montt","Coyhaique","Punta Arenas"]}.items():
    r = attr(s)
    print(f"{nom:60s} ATTR={r[0]:.4f}  P(todos)={r[1]:.4f}")
