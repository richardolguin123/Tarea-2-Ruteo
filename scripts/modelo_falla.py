"""Probabilidad de falla de cada enlace (arista) de la red ante el sismo cargado en sismo.evento.

Cadena del modelo (todas las relaciones son de la literatura, ver informe, seccion 6):
  1) Fuente sismica  : zona de ruptura CIRCULAR centrada en el epicentro, con el area de ruptura de
                       Strasser, Arango y Bommer (2010) para sismos de interfaz de subduccion:
                       log10 A[km2] = -3.476 + 0.952 Mw   ->   radio R = sqrt(A / pi)
                       (falla regional circular, como los "cortes circulares" de Neumayer y Modiano 2011,
                       ref. [5] del paper de Boettcher et al. 2018).
                       Opcion --fuente epicentro: fuente puntual (R = 0), solo para comparar.
  2) Distancia       : d = max(0, distancia(arista, epicentro) - R) ; rrup = sqrt(d^2 + h^2), h = prof. foco.
                       Como la fuente es circular y la atenuacion solo depende de d, las curvas de igual
                       probabilidad de falla son circulos concentricos al epicentro.
  3) PGA             : Youngs et al. (1997), subduccion interplaca, suelo rigido:
                       ln PGA[g] = 0.2418 + 1.414 M + C3 ln(rrup + 1.7818 exp(0.554 M)) + 0.00607 H ,  C3 = -2.552
  4) Intensidad/PGV  : Wald et al. (1999):  MMI = 3.66 log10(PGA[cm/s2]) - 1.66 ;  log10 PGV[cm/s] = (MMI - 2.35)/3.47
  5) Tasa reparacion : ALA (2001), propagacion de ondas:  RR[1/km] = K * 0.002416 * PGV[cm/s]
  6) Falla de enlace : Poisson, P = 1 - exp(-RR * L_km)   (L = longitud de la arista)
  Perimetro de dano  : circulo donde el modelo da MMI >= VI (inicio de dano, escala de Wald et al. 1999).

Uso: python modelo_falla.py [--fuente circular|epicentro] [--k 1.0] [--sql main.sql] [--tabla prob_falla]
Escribe sismo.zonas (circulo de ruptura y perimetro de dano), la tabla red.<tabla>, la columna
red.aristas.p_falla (solo con la tabla base prob_falla) y, si se pide, main.sql.

main.sql es autocontenido: crea las extensiones, los esquemas, la red (aristas, vertices y nodos clave),
el sismo y sus zonas, y las probabilidades de falla. Se puede cargar en una base vacia con
  psql -v ON_ERROR_STOP=1 -f main.sql
"""
import argparse
import math
import os
from datetime import datetime

import psycopg

A_YOUNGS, B_YOUNGS, C3_YOUNGS, D_YOUNGS, E_YOUNGS, F_YOUNGS = 0.2418, 1.414, -2.552, 1.7818, 0.554, 0.00607
MMI_DANO = 6.0   # MMI VI: "dano leve" (inicio del dano), Wald et al. (1999)


def area_ruptura_km2(m):
    """Area de ruptura mediana, Strasser et al. (2010), interfaz de subduccion."""
    return 10 ** (-3.476 + 0.952 * m)


def radio_ruptura_km(m):
    """Radio del circulo con la misma area de ruptura."""
    return math.sqrt(area_ruptura_km2(m) / math.pi)


def pga_youngs97(m, rrup_km, h_km):
    """PGA mediana [g], Youngs et al. (1997), interplaca, roca/suelo rigido."""
    return math.exp(A_YOUNGS + B_YOUNGS * m + C3_YOUNGS * math.log(rrup_km + D_YOUNGS * math.exp(E_YOUNGS * m))
                    + F_YOUNGS * h_km)


def mmi_wald99(pga_g):
    return 3.66 * math.log10(max(pga_g * 981.0, 1e-9)) - 1.66


def pgv_wald99(mmi):
    return 10 ** ((mmi - 2.35) / 3.47)


def radio_mmi_km(m, h_km, r_fuente_km, mmi_obj=MMI_DANO):
    """Distancia al epicentro hasta donde el modelo da MMI >= mmi_obj (solucion cerrada de Youngs + Wald)."""
    pga_obj = 10 ** ((mmi_obj + 1.66) / 3.66) / 981.0
    ln_arg = (A_YOUNGS + B_YOUNGS * m + F_YOUNGS * h_km - math.log(pga_obj)) / -C3_YOUNGS
    rrup = math.exp(ln_arg) - D_YOUNGS * math.exp(E_YOUNGS * m)
    if rrup <= h_km:
        return r_fuente_km if pga_youngs97(m, h_km, h_km) >= pga_obj else 0.0
    return r_fuente_km + math.sqrt(rrup ** 2 - h_km ** 2)


def probabilidad_arista(m, h_km, dist_epicentro_km, r_fuente_km, longitud_km, k=1.0):
    """Devuelve (d, pga, mmi, pgv, rr, p) para una arista."""
    d = max(0.0, dist_epicentro_km - r_fuente_km)
    rrup = math.sqrt(d ** 2 + h_km ** 2)
    pga = pga_youngs97(m, rrup, h_km)
    mmi = mmi_wald99(pga)
    pgv = pgv_wald99(mmi)
    rr = k * 0.002416 * pgv
    return d, pga, mmi, pgv, rr, 1.0 - math.exp(-rr * longitud_km)


def conectar():
    return psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
        dbname=os.getenv("PGDATABASE", "resiliencia"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"))


def guardar_zonas(cur, m, h, r_fuente, r_dano):
    """Circulos (geografia, 64 segmentos por cuarto) del area de ruptura y del perimetro de dano."""
    cur.execute("DROP TABLE IF EXISTS sismo.fuente_ruptura")   # tabla de la version rectangular anterior
    cur.execute("DROP TABLE IF EXISTS sismo.zonas")
    cur.execute("""CREATE TABLE sismo.zonas(tipo text PRIMARY KEY, descripcion text, radio_km numeric,
                   area_km2 numeric, geom geometry(Polygon,4326))""")
    zonas = [("ruptura", "Ruptura circular de area equivalente (Strasser et al. 2010), centrada en el epicentro", r_fuente),
             ("dano_mmi6", "Perimetro de dano: MMI >= VI segun el modelo (Youngs 1997 + Wald 1999)", r_dano)]
    for tipo, desc, r in zonas:
        if r <= 0:
            continue
        cur.execute("""INSERT INTO sismo.zonas
                       SELECT %s, %s, %s, %s,
                              ST_Buffer(e.geom::geography, %s, 'quad_segs=64')::geometry
                       FROM sismo.evento e ORDER BY e.magnitud DESC LIMIT 1""",
                    (tipo, desc, round(r, 1), round(math.pi * r * r), r * 1000.0))


# tablas que main.sql reconstruye en una base vacia: (tabla, llave primaria, indices)
TABLAS_MAIN_SQL = [
    ("red.nodos_clave", "id", []),
    ("red.aristas", "id", ["USING gist(geom)", "(source)", "(target)"]),
    ("red.aristas_vertices_pgr", "id", ["USING gist(the_geom)"]),
    ("sismo.evento", "id_usgs", []),
    ("sismo.zonas", "tipo", []),
]


def volcar_tabla(cur, f, tabla, llave, indices, por_insert=500):
    """Escribe CREATE TABLE + INSERT de una tabla de la BD. Cada valor va como literal de texto
    (las geometrias en EWKB hexadecimal), que PostgreSQL convierte al tipo de la columna al insertar."""
    cur.execute("""SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
                   WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
                (tabla,))
    cols = cur.fetchall()
    f.write(f"\n-- {tabla}\n")
    defs = ",\n  ".join(f'"{c}" {tipo}' for c, tipo in cols)
    f.write(f"CREATE TABLE {tabla}(\n  {defs},\n  PRIMARY KEY ({llave}));\n")
    expr = ", ',', ".join(f'quote_nullable("{c}"::text)' for c, _ in cols)
    cur.execute(f"SELECT '(' || concat({expr}) || ')' FROM {tabla} ORDER BY {llave}")
    filas = [r[0] for r in cur.fetchall()]
    nombres = ", ".join(f'"{c}"' for c, _ in cols)
    for i in range(0, len(filas), por_insert):
        f.write(f"INSERT INTO {tabla} ({nombres}) VALUES\n" + ",\n".join(filas[i:i + por_insert]) + ";\n")
    for ix in indices:
        f.write(f"CREATE INDEX ON {tabla} {ix};\n")
    return len(filas)


def escribir_main_sql(cur, ruta, cabecera, t, filas):
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(cabecera)
        f.write("-- Autocontenido: se puede cargar en una base vacia con  psql -v ON_ERROR_STOP=1 -f main.sql\n\n")
        f.write("SET client_min_messages = warning;\n")
        f.write("CREATE EXTENSION IF NOT EXISTS postgis;\nCREATE EXTENSION IF NOT EXISTS pgrouting;\n")
        f.write("CREATE SCHEMA IF NOT EXISTS red;\nCREATE SCHEMA IF NOT EXISTS sismo;\n")
        f.write(f"DROP TABLE IF EXISTS red.{t}, " + ", ".join(x[0] for x in TABLAS_MAIN_SQL) + " CASCADE;\n")
        f.write("\n-- 1) Red (topologia de pgr_createTopology), nodos clave, sismo y zonas\n")
        for tabla, llave, indices in TABLAS_MAIN_SQL:
            volcar_tabla(cur, f, tabla, llave, indices)
        f.write(f"\n-- 2) Probabilidad de falla de cada enlace\n")
        f.write(f"CREATE TABLE red.{t}(\n  arista_id int PRIMARY KEY, capa text, longitud_m double precision,\n"
                "  dist_fuente_km double precision, pga_g double precision, mmi double precision,\n"
                "  pgv_cm_s double precision, tasa_reparacion_km double precision, p_falla double precision);\n")
        f.write(f"INSERT INTO red.{t} VALUES\n")
        f.write(",\n".join("(%d,'%s',%s,%s,%s,%s,%s,%s,%s)" % x for x in filas) + ";\n")
        f.write("ANALYZE red.aristas;\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuente", choices=["circular", "epicentro"], default="circular")
    ap.add_argument("--k", type=float, default=1.0, help="factor K de ALA (tipo de instalacion)")
    ap.add_argument("--sql", help="archivo .sql de salida con los resultados")
    ap.add_argument("--tabla", default="prob_falla")
    a = ap.parse_args()

    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT magnitud, prof_km FROM sismo.evento ORDER BY magnitud DESC LIMIT 1")
        m, h = (float(x) for x in cur.fetchone())
        r_fuente = radio_ruptura_km(m) if a.fuente == "circular" else 0.0
        r_dano = radio_mmi_km(m, h, r_fuente)
        print(f"Evento: M{m}, profundidad foco {h} km")
        if a.fuente == "circular":
            print(f"Fuente: circular, area Strasser 2010 = {area_ruptura_km2(m):,.0f} km2 -> radio {r_fuente:.1f} km")
        else:
            print("Fuente: puntual (epicentro)")
        print(f"Perimetro de dano (MMI >= VI): radio {r_dano:.1f} km desde el epicentro")
        guardar_zonas(cur, m, h, r_fuente, r_dano)

        cur.execute("""SELECT a.id, a.capa, a.longitud_m, ST_Distance(a.geom::geography, e.geom::geography)
                       FROM red.aristas a, (SELECT geom FROM sismo.evento ORDER BY magnitud DESC LIMIT 1) e
                       ORDER BY a.id""")
        filas = []
        for aid, capa, lon_m, d_m in cur.fetchall():
            d, pga, mmi, pgv, rr, p = probabilidad_arista(m, h, float(d_m) / 1000.0, r_fuente,
                                                          float(lon_m) / 1000.0, a.k)
            filas.append((aid, capa, round(float(lon_m), 1), round(d, 2), round(pga, 4),
                          round(mmi, 2), round(pgv, 2), round(rr, 5), round(p, 6)))

        t = a.tabla
        cur.execute(f"DROP TABLE IF EXISTS red.{t}")
        cur.execute(f"""CREATE TABLE red.{t}(
            arista_id int PRIMARY KEY, capa text, longitud_m double precision, dist_fuente_km double precision,
            pga_g double precision, mmi double precision, pgv_cm_s double precision,
            tasa_reparacion_km double precision, p_falla double precision)""")
        cur.executemany(f"INSERT INTO red.{t} VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", filas)
        if t == "prob_falla":   # la tabla base tambien queda en la columna p_falla de red.aristas
            cur.execute("ALTER TABLE red.aristas ADD COLUMN IF NOT EXISTS p_falla double precision NOT NULL DEFAULT 0.0")
            cur.execute("UPDATE red.aristas a SET p_falla = f.p_falla FROM red.prob_falla f WHERE f.arista_id = a.id")
        conn.commit()

        n = len(filas)
        ps = [f[8] for f in filas]
        print(f"Aristas evaluadas         : {n}")
        print(f"Aristas dentro del circulo: {sum(f[3] == 0 for f in filas)}")
        print(f"Distancia min/max a fuente: {min(f[3] for f in filas):.1f} / {max(f[3] for f in filas):.1f} km")
        print(f"PGA max / min             : {max(f[4] for f in filas):.3f} g / {min(f[4] for f in filas):.4f} g")
        print(f"MMI max                   : {max(f[5] for f in filas):.2f}")
        print(f"p_falla media             : {sum(ps) / n:.4f}")
        print(f"p_falla max               : {max(ps):.4f}")
        print(f"Aristas con p_falla > 0.1 : {sum(p > 0.1 for p in ps)}   (> 0.5: {sum(p > 0.5 for p in ps)})")
        print(f"Fallas esperadas (suma p) : {sum(ps):.0f}")
        for capa in ("fibra", "poste", "camino", "ferry", "puente"):
            pc = [f[8] for f in filas if f[1] == capa]
            if pc:
                print(f"  {capa:7s} n={len(pc):5d}  p media={sum(pc) / len(pc):.4f}")

        if a.sql:
            cabecera = (
                "-- main.sql : red, sismo y probabilidades de falla de cada enlace tras el sismo M%.1f (USGS %s)\n"
                % (m, "official20100227063411530_30")
                + f"-- Generado {datetime.now():%Y-%m-%d %H:%M} por scripts/modelo_falla.py  (fuente={a.fuente}, K={a.k})\n"
                + f"-- Modelo: ruptura circular Strasser 2010 (R = {r_fuente:.1f} km) -> Youngs 1997 (PGA) -> "
                "Wald 1999 (MMI, PGV) -> ALA 2001 (RR) -> Poisson\n")
            escribir_main_sql(cur, a.sql, cabecera, t, filas)
            print(f"main.sql escrito: {a.sql}  ({n} filas de probabilidad, base autocontenida)")


if __name__ == "__main__":
    main()
