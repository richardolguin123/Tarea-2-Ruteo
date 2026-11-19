"""Carga el GeoJSON del sismo (USGS ComCat) a PostGIS, esquema 'sismo'.

Uso: python cargar_sismo.py <archivo.geojson>
Conexion: PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (por defecto 127.0.0.1:5433).
"""
import json
import os
import sys
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb


def main(ruta):
    with open(ruta, encoding="utf-8") as f:
        fc = json.load(f)
    conn = psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
        dbname=os.getenv("PGDATABASE", "resiliencia"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"))
    with conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS sismo")
        cur.execute("DROP TABLE IF EXISTS sismo.evento CASCADE")
        cur.execute("""CREATE TABLE sismo.evento(
            id_usgs text PRIMARY KEY, titulo text, magnitud numeric(3,1), tipo_magnitud text,
            fecha_utc timestamptz, prof_km numeric, mmi_usgs numeric, cdi_usgs numeric,
            reportes_sentido int, url text, propiedades jsonb,
            geom geometry(Point,4326) NOT NULL)""")
        for ft in fc["features"]:
            p, (lon, lat, prof) = ft["properties"], ft["geometry"]["coordinates"]
            cur.execute(
                "INSERT INTO sismo.evento VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "ST_SetSRID(ST_MakePoint(%s,%s),4326))",
                (ft["id"], p["title"], p["mag"], p["magType"],
                 datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc), prof,
                 p.get("mmi"), p.get("cdi"), p.get("felt"), p["url"], Jsonb(p), lon, lat))
        cur.execute("CREATE INDEX ON sismo.evento USING gist(geom)")
        cur.execute("""SELECT id_usgs, titulo, magnitud, tipo_magnitud, fecha_utc, prof_km,
                       round(ST_X(geom)::numeric,3), round(ST_Y(geom)::numeric,3) FROM sismo.evento""")
        for r in cur.fetchall():
            print(f"Evento cargado : {r[0]}\n  Titulo       : {r[1]}\n  Magnitud     : {r[2]} ({r[3]})"
                  f"\n  Fecha (UTC)  : {r[4]}\n  Profundidad  : {r[5]} km\n  Epicentro    : lon {r[6]}, lat {r[7]}")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
