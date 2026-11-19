"""Mapa (imagen) de la red topologica: aristas por capa, vertices, nodos clave y puentes inferidos.
Uso: python mapa_red.py <salida.png>
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psycopg
from shapely import wkt

conn = psycopg.connect(host="127.0.0.1", port=5433, dbname="resiliencia", user="postgres",
                       password=os.getenv("PGPASSWORD", "postgres"))
cur = conn.cursor()
estilo = {"camino": ("#aaaaaa", 0.5, "Camino sin postación"), "poste": ("#e8a317", 0.6, "Postación eléctrica"),
          "ferry": ("#1f77b4", 0.7, "Trayecto en ferry"), "fibra": ("#d62728", 0.9, "Fibra óptica"),
          "puente": ("#000000", 2.5, "Puente inferido")}
fig, ax = plt.subplots(figsize=(6.5, 11))
for capa, (color, lw, nombre) in estilo.items():
    cur.execute("SELECT ST_AsText(geom) FROM red.aristas WHERE capa=%s", (capa,))
    primero = True
    for (w,) in cur.fetchall():
        g = wkt.loads(w)
        x, y = g.xy
        ax.plot(x, y, color=color, lw=lw, label=nombre if primero else None, solid_capstyle="round")
        primero = False
cur.execute("SELECT nombre, ST_X(geom), ST_Y(geom), rol FROM red.nodos_clave")
for n, x, y, rol in cur.fetchall():
    ax.plot(x, y, "o", ms=6, mfc="#00a859", mec="black", zorder=5)
    ax.annotate(n, (x, y), xytext=(8, -3), textcoords="offset points", fontsize=7,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))
ax.plot([], [], "o", mfc="#00a859", mec="black", label="Nodo clave")
ax.set_xlim(-76, -66); ax.set_ylim(-55.5, -17.5); ax.set_aspect(1.15)
ax.set_xlabel("Longitud"); ax.set_ylabel("Latitud")
ax.set_title("Red topológica (pgr_createTopology) y nodos clave")
ax.legend(loc="center left", bbox_to_anchor=(0.0, 0.62), fontsize=7)
fig.tight_layout()
fig.savefig(sys.argv[1], dpi=130)
print("guardada", sys.argv[1])
