"""Dibuja las 4 capas cargadas en PostGIS (lee directamente desde la BD) en una imagen PNG."""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psycopg
from shapely import wkt

salida = sys.argv[1]
conn = psycopg.connect(host="127.0.0.1", port=5433, dbname="resiliencia", user="postgres",
                       password=os.getenv("PGPASSWORD", "postgres"))
capas = [("camino_sin_postacion", "#999999", "Camino sin postación"),
         ("postacion_electrica", "#e8a317", "Postación eléctrica"),
         ("trayecto_ferry", "#1f77b4", "Trayecto en ferry"),
         ("fibra_optica", "#d62728", "Fibra óptica")]
fig, axs = plt.subplots(1, 2, figsize=(11, 10), gridspec_kw={"width_ratios": [1, 1]})
cur = conn.cursor()
for ax, (xlim, tit) in zip(axs, [((-110, -66), "Territorio completo"), ((-76, -66), "Chile continental")]):
    for tabla, color, nombre in capas:
        cur.execute(f"SELECT ST_AsText(geom) FROM infra.{tabla}")
        first = True
        for (w,) in cur.fetchall():
            for ln in wkt.loads(w).geoms:
                x, y = ln.xy
                ax.plot(x, y, color=color, lw=0.8 if tabla == "fibra_optica" else 0.5,
                        label=nombre if first else None)
                first = False
    ax.set_title(tit)
    ax.set_xlim(*xlim)
    ax.set_ylim(-56, -17)
    ax.set_aspect(1.15)
    ax.set_xlabel("Longitud")
axs[0].set_ylabel("Latitud")
axs[0].legend(loc="lower left", fontsize=8)
fig.suptitle("Capas de infraestructura cargadas en PostGIS (esquema infra)")
fig.tight_layout()
fig.savefig(salida, dpi=130)
print("guardada", salida)
