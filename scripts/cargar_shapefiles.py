"""Carga los shapefiles del proyecto Yafun a PostGIS (esquema 'infra').

Uso: python cargar_shapefiles.py <carpeta_shapefiles>
Conexion: variables PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (por defecto 127.0.0.1:5433).
"""
import os
import sys
from pathlib import Path

import psycopg
import shapefile  # pyshp

CAPAS = {
    "fibra_optica_detectada": "fibra_optica",
    "postacion_electrica_detectada": "postacion_electrica",
    "camino_sin_postacion_detectado": "camino_sin_postacion",
    "trayecto_en_ferry": "trayecto_ferry",
}


def wkt_multilinea(shape):
    pts, partes = shape.points, list(shape.parts) + [len(shape.points)]
    lineas = []
    for a, b in zip(partes[:-1], partes[1:]):
        seg = pts[a:b]
        if len(seg) >= 2:
            lineas.append("(" + ",".join(f"{x:.9f} {y:.9f}" for x, y in seg) + ")")
    return "MULTILINESTRING(" + ",".join(lineas) + ")" if lineas else None


def main(carpeta):
    conn = psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        dbname=os.getenv("PGDATABASE", "resiliencia"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )
    with conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS infra")
        for archivo, tabla in CAPAS.items():
            r = shapefile.Reader(str(Path(carpeta) / archivo), encoding="latin-1")
            cur.execute(f"DROP TABLE IF EXISTS infra.{tabla} CASCADE")
            cur.execute(
                f"CREATE TABLE infra.{tabla} ("
                "gid serial PRIMARY KEY, id_origen text, "
                "geom geometry(MultiLineString,4326))"
            )
            filas = []
            for sr in r.iterShapeRecords():
                wkt = wkt_multilinea(sr.shape)
                if wkt:
                    filas.append((str(sr.record[0]).strip(), wkt))
            cur.executemany(
                f"INSERT INTO infra.{tabla}(id_origen, geom) "
                "VALUES (%s, ST_GeomFromText(%s, 4326))",
                filas,
            )
            cur.execute(f"CREATE INDEX ON infra.{tabla} USING gist(geom)")
            print(f"{archivo:35s} -> infra.{tabla:22s} {len(filas):5d} filas (origen: {len(r)})")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
