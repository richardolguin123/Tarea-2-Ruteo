"""Tarea 2 - Resiliencia de la infraestructura de Internet de Chile frente al terremoto M8.8.

Uso:
  python main.py            # flujo completo: levanta la base si no responde (docker compose up -d),
                            # carga la BD, calcula los ATTR antes y despues, y abre el mapa Leaflet
  python main.py cargar     # carga shapefiles, construye la topologia (pgRouting), carga el sismo
                            # (GeoJSON USGS), calcula las probabilidades de falla y ejecuta main.sql
  python main.py metricas   # ATTR y demas metricas de resiliencia antes/despues del sismo (ambas redes)
  python main.py attr       # (igual que metricas)
  python main.py mapa       # servidor web con el mapa Leaflet (http://127.0.0.1:5055, variable PUERTO)
  python main.py todo       # cargar + metricas, sin abrir el mapa

Conexion: PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (por defecto 127.0.0.1:5433, resiliencia, postgres/postgres).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "scripts"))
import calcular_attr as ca  # noqa: E402
import metricas_resiliencia as mr  # noqa: E402

SHP = RAIZ / "datos_crudos" / "zip_extraido" / "Respuesta_T5610"
GEOJSON_SISMO = RAIZ / "datos_crudos" / "sismo" / "usgs_maule_2010_m88.geojson"
PY = sys.executable
N_SIM, N_DIST = 2000, 200


def base_responde():
    try:
        with psycopg.connect(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
                             dbname=os.getenv("PGDATABASE", "resiliencia"), user=os.getenv("PGUSER", "postgres"),
                             password=os.getenv("PGPASSWORD", "postgres"), connect_timeout=3) as c:
            c.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def asegurar_base(espera_max_s=180):
    """Si PostgreSQL no responde, levanta el contenedor con docker compose y espera a que acepte conexiones."""
    if base_responde():
        print("0) Base de datos: responde")
        return
    print("0) Base de datos: no responde, se levanta con docker compose up -d")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=RAIZ, check=True)
    inicio = time.time()
    seguidas = 0
    while time.time() - inicio < espera_max_s:
        seguidas = seguidas + 1 if base_responde() else 0
        if seguidas == 2:      # dos respuestas seguidas: el servidor ya termino de iniciar
            print(f"   lista en {time.time() - inicio:.0f} s")
            return
        time.sleep(2)
    sys.exit("La base de datos no respondio a tiempo")


def ejecutar_sql(ruta):
    with ca.conectar() as conn, conn.cursor() as cur:
        cur.execute(Path(ruta).read_text(encoding="utf-8-sig"))
        conn.commit()
    print(f"  ok  {Path(ruta).relative_to(RAIZ)}")


def cargar():
    print("1) Extensiones y shapefiles")
    ejecutar_sql(RAIZ / "sql" / "00_extensiones.sql")
    subprocess.run([PY, str(RAIZ / "scripts" / "cargar_shapefiles.py"), str(SHP)], check=True)
    print("2) Nodos clave y topologia (pgr_createTopology)")
    ejecutar_sql(RAIZ / "sql" / "04_nodos_clave.sql")
    ejecutar_sql(RAIZ / "sql" / "06_construir_red_topologia.sql")
    print("3) Sismo M8.8 (GeoJSON USGS)")
    subprocess.run([PY, str(RAIZ / "scripts" / "cargar_sismo.py"), str(GEOJSON_SISMO)], check=True)
    print("4) Zona de ruptura circular y probabilidades de falla (main.sql)")
    subprocess.run([PY, str(RAIZ / "scripts" / "modelo_falla.py"), "--fuente", "circular", "--k", "1.0",
                    "--sql", str(RAIZ / "main.sql")], check=True)
    ejecutar_sql(RAIZ / "main.sql")


def metricas(n=N_SIM, n_dist=N_DIST):
    with ca.conectar() as conn, conn.cursor() as cur:
        for red in ("todas", "fibra"):
            res = mr.calcular(cur, red, n, n_dist)
            mr.guardar(cur, res)
            conn.commit()
            mr.imprimir(res)


def leer_metricas(cur, red):
    cur.execute("SELECT to_regclass('red.metricas_resiliencia')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute("SELECT escenario, metrica, valor, ic95, n_sim FROM red.metricas_resiliencia WHERE red = %s", (red,))
    filas = cur.fetchall()
    if not filas:
        return None
    r = {"red": red, "descripcion": ca.REDES[red][0], "antes": {}, "despues": {}, "ic95": {}, "datos": {}}
    for esc, met, val, ic, n_sim in filas:
        r[esc][met] = val
        if esc == "despues":
            r["ic95"][met] = ic
            r["n_sim" if met == "attr" else "_"] = n_sim
            if met == "centralidad":
                r["n_dist"] = n_sim
    r.pop("_", None)
    return r


def servidor():
    from flask import Flask, jsonify, request, send_from_directory

    app = Flask(__name__, static_folder=str(RAIZ / "static"))

    def consulta_geojson(sql):
        with ca.conectar() as conn, conn.cursor() as cur:
            cur.execute(sql)
            valor = cur.fetchone()[0]
            return jsonify(json.loads(valor) if isinstance(valor, str) else valor)

    def red_param():
        red = request.args.get("red", "todas")
        return red if red in ca.REDES else "todas"

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/red")
    def api_red():
        return consulta_geojson("""
            SELECT json_build_object('type','FeatureCollection','features', json_agg(json_build_object(
              'type','Feature','geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(a.geom, 0.0008), 4)::json,
              'properties', json_build_object('id', a.id, 'capa', a.capa, 'tipo', a.tipo,
                 'km', round((a.longitud_m/1000)::numeric,2), 'p', f.p_falla, 'pga', f.pga_g, 'mmi', f.mmi,
                 'src', a.source, 'tgt', a.target)))) FROM red.aristas a JOIN red.prob_falla f ON f.arista_id = a.id
            WHERE a.geom && ST_MakeEnvelope(-78, -57, -65, -17, 4326)""")

    @app.route("/api/nodos")
    def api_nodos():
        return consulta_geojson("""
            SELECT json_build_object('type','FeatureCollection','features', json_agg(json_build_object(
              'type','Feature','geometry', ST_AsGeoJSON(geom, 5)::json,
              'properties', json_build_object('nombre', nombre, 'rol', rol, 'vertice', vertice_id,
                 'dist_a_red_km', dist_a_red_km, 'dist_a_fibra_km', dist_a_fibra_km)))) FROM red.nodos_clave""")

    @app.route("/api/sismo")
    def api_sismo():
        with ca.conectar() as conn, conn.cursor() as cur:
            cur.execute("""SELECT json_build_object('type','Feature','geometry', ST_AsGeoJSON(geom,5)::json,
                 'properties', json_build_object('tipo','epicentro','titulo', titulo, 'magnitud', magnitud,
                 'fecha', fecha_utc, 'prof_km', prof_km)) FROM sismo.evento""")
            feats = [r[0] for r in cur.fetchall()]
            cur.execute("""SELECT json_build_object('type','Feature','geometry', ST_AsGeoJSON(geom,5)::json,
                 'properties', json_build_object('tipo', tipo, 'descripcion', descripcion, 'radio_km', radio_km,
                 'area_km2', area_km2)) FROM sismo.zonas ORDER BY radio_km DESC""")
            feats += [r[0] for r in cur.fetchall()]
        return jsonify({"type": "FeatureCollection", "features": feats})

    @app.route("/api/metricas")
    def api_metricas():
        red = red_param()
        with ca.conectar() as conn, conn.cursor() as cur:
            r = leer_metricas(cur, red)
            if r is None:   # si aun no se calcularon, se calculan y se guardan
                res = mr.calcular(cur, red, N_SIM, N_DIST, verbose=False)
                mr.guardar(cur, res)
                conn.commit()
                r = leer_metricas(cur, red)
        return jsonify(r)

    @app.route("/api/simulacion")
    def api_simulacion():
        semilla = int(request.args.get("semilla", 1))
        red = red_param()
        with ca.conectar() as conn, conn.cursor() as cur:
            aristas, claves, n_vert = ca.cargar_red(cur, red=red)
            vert = np.array([c[2] for c in claves])
            cur.execute("SELECT arista_id, p_falla FROM red.prob_falla")
            pf = dict(cur.fetchall())
            p = np.array([pf.get(int(i), 0.0) for i in aristas[:, 0]])
            activa = np.random.default_rng(semilla).random(len(p)) >= p
            vertices_red = np.unique(np.concatenate([aristas[:, 1], aristas[:, 2], vert]))
            m = mr.estado(cur, red, aristas, n_vert, vertices_red, vert, activa)
        return jsonify({"semilla": semilla, "red": red, "caidas": [int(x) for x in aristas[~activa, 0]],
                        "n_caidas": int((~activa).sum()), **m})

    puerto = int(os.getenv("PUERTO", "5055"))
    print(f"Mapa en http://127.0.0.1:{puerto}")
    app.run(host="127.0.0.1", port=puerto)


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "completo"
    if accion == "completo":
        asegurar_base()
        cargar()
        metricas()
        servidor()
    elif accion == "cargar":
        cargar()
    elif accion in ("metricas", "attr"):
        metricas(int(sys.argv[2]) if len(sys.argv) > 2 else N_SIM)
    elif accion == "todo":
        asegurar_base()
        cargar()
        metricas()
    elif accion == "mapa":
        servidor()
    else:
        print(__doc__)
