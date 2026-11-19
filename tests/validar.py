"""Pruebas de validacion de todo el flujo (datos -> topologia -> ATTR -> sismo -> modelo de falla -> mapa).

Uso: python tests/validar.py [--externo]
  --externo : ademas compara las coordenadas de los nodos clave con un geocodificador externo (OpenStreetMap).
Sale con codigo 0 si todas las pruebas pasan.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import shapefile

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
import calcular_attr as ca  # noqa: E402
import modelo_falla as mf  # noqa: E402
import metricas_resiliencia as mr  # noqa: E402

resultados = []
# nombres con tilde para el geocodificador externo (sin tilde "Valparaiso" devuelve otro lugar)
CON_TILDE = {"Copiapo": "Copiapó", "Valparaiso": "Valparaíso", "Chillan": "Chillán", "Concepcion": "Concepción",
             "Coyhaique": "Coyhaique"}


def check(nombre, ok, detalle=""):
    resultados.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}" + (f"  -> {detalle}" if detalle else ""))


def aviso(nombre, detalle):
    print(f"  [INFO] {nombre}  -> {detalle}")


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest().upper()


def main(externo):
    conn = ca.conectar()
    cur = conn.cursor()
    q = lambda s, *a: (cur.execute(s, a), cur.fetchall())[1]  # noqa: E731
    q1 = lambda s, *a: q(s, *a)[0][0]  # noqa: E731

    print("A. Datos de entrada")
    check("SHA-256 del GeoJSON de USGS coincide con el registrado",
          sha256(RAIZ / "datos_crudos/sismo/usgs_maule_2010_m88.geojson")
          == "D2B1D268810D9BD7D92CCCAC4D854C14EC759FB29ED43AF548EE5624D2082C77")
    capas = {"fibra_optica_detectada": "fibra_optica", "postacion_electrica_detectada": "postacion_electrica",
             "camino_sin_postacion_detectado": "camino_sin_postacion", "trayecto_en_ferry": "trayecto_ferry"}
    shp = RAIZ / "datos_crudos/zip_extraido/Respuesta_T5610"
    for archivo, tabla in capas.items():
        n_shp = len(shapefile.Reader(str(shp / archivo)))
        n_db = q1(f"SELECT count(*) FROM infra.{tabla}")
        check(f"Filas en BD = entidades del shapefile ({tabla})", n_shp == n_db, f"{n_db} vs {n_shp}")
    km_db = q1("SELECT sum(ST_Length(geom::geography))/1000 FROM infra.fibra_optica")
    # longitud independiente: haversine sobre los vertices del shapefile
    r = shapefile.Reader(str(shp / "fibra_optica_detectada"))
    tot = 0.0
    for s in r.shapes():
        pts, par = s.points, list(s.parts) + [len(s.points)]
        for a, b in zip(par[:-1], par[1:]):
            for (x1, y1), (x2, y2) in zip(pts[a:b - 1], pts[a + 1:b]):
                p1, p2 = math.radians(y1), math.radians(y2)
                h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(x2 - x1) / 2) ** 2
                tot += 2 * 6371.0088 * math.asin(math.sqrt(h))
    check("Longitud de fibra: PostGIS vs calculo independiente (haversine) dentro de 0,5 %",
          abs(km_db - tot) / tot < 0.005, f"{km_db:.0f} km vs {tot:.0f} km")

    print("B. Topologia (pgr_createTopology)")
    n_ar = q1("SELECT count(*) FROM red.aristas")
    check("Aristas con source/target no nulos", q1("SELECT count(*) FROM red.aristas WHERE source IS NULL OR target IS NULL") == 0,
          f"{n_ar} aristas")
    check("IDs de arista unicos", q1("SELECT count(DISTINCT id) FROM red.aristas") == n_ar)
    check("Geometrias validas y no vacias", q1("SELECT count(*) FROM red.aristas WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom)") == 0)
    n_mal = q1("""SELECT count(*) FROM red.aristas a JOIN red.aristas_vertices_pgr s ON s.id=a.source
                  JOIN red.aristas_vertices_pgr t ON t.id=a.target
                  WHERE a.capa <> 'puente' AND (ST_Distance(ST_StartPoint(a.geom), s.the_geom) > 0.0001
                  OR ST_Distance(ST_EndPoint(a.geom), t.the_geom) > 0.0001)""")
    check("Extremos de cada arista coinciden con sus vertices (tol. 1e-4 grados)", n_mal == 0, f"{n_mal} discrepancias")
    km_red = q1("SELECT sum(longitud_m)/1000 FROM red.aristas WHERE capa='fibra'")
    check("Longitud de fibra tras nodar ~ original (1 %)", abs(km_red - km_db) / km_db < 0.01, f"{km_red:.0f} vs {km_db:.0f} km")
    check("Exactamente 1 puente inferido, de menos de 5 km",
          q("SELECT count(*), max(longitud_m)/1000 FROM red.aristas WHERE tipo='inferido'")[0][0] == 1
          and q1("SELECT max(longitud_m)/1000 FROM red.aristas WHERE tipo='inferido'") < 5)

    print("C. Nodos clave")
    check("16 nodos clave, nombres unicos", q1("SELECT count(DISTINCT nombre) FROM red.nodos_clave") == 16)
    check("Todos asignados a un vertice", q1("SELECT count(*) FROM red.nodos_clave WHERE vertice_id IS NULL") == 0)
    check("Cada rol (generador, intercambio, receptor) tiene al menos un nodo",
          all(q1("SELECT count(*) FROM red.nodos_clave WHERE %s = ANY(rol)", r_) > 0 for r_ in ("generador", "intercambio", "receptor")))
    check("Distancia nodo-red < 10 km en todos", q1("SELECT max(dist_a_red_km) FROM red.nodos_clave") < 10,
          f"max {q1('SELECT max(dist_a_red_km) FROM red.nodos_clave'):.1f} km")
    if externo:
        fallos = []
        for nombre, lon, lat in q("SELECT nombre, ST_X(geom), ST_Y(geom) FROM red.nodos_clave ORDER BY id"):
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": f"{CON_TILDE.get(nombre, nombre)}, Chile", "format": "json", "limit": 1})
            req = urllib.request.Request(url, headers={"User-Agent": "validacion-tarea-ruteo/1.0"})
            d = json.load(urllib.request.urlopen(req, timeout=30))[0]
            dx = (float(d["lon"]) - lon) * 111.32 * math.cos(math.radians(lat))
            dy = (float(d["lat"]) - lat) * 110.57
            dist = math.hypot(dx, dy)
            print(f"      {nombre:13s} dif. con OSM: {dist:5.1f} km")
            if dist > 15:
                fallos.append(nombre)
            time.sleep(1.1)
        check("Coordenadas de los nodos a menos de 15 km de un geocodificador externo (OSM)", not fallos, str(fallos))

    print("D. Calculo del ATTR (ec. 8 del paper) y de las metricas de resiliencia")
    # Ciclo de 3 nodos (todos clave), p de falla por arista. Valores exactos:
    #   par (i,j) conectado si sobrevive su arista directa o las otras dos: P = (1-p) + p(1-p)^2  -> ATTR = P
    #   todos conectados: (1-p)^3 + 3p(1-p)^2
    ar = np.array([[0, 0, 1], [1, 1, 2], [2, 2, 0]], dtype=np.int64)
    p = 0.2
    rng = np.random.default_rng(1)
    n = 20000
    ms = [ca.metricas_estado(ca.componentes(ar, 3, rng.random(3) >= p), np.array([0, 1, 2])) for _ in range(n)]
    ex_attr = (1 - p) + p * (1 - p) ** 2
    ex_todos = (1 - p) ** 3 + 3 * p * (1 - p) ** 2
    mc_attr = np.mean([m["attr"] for m in ms])
    mc_todos = np.mean([m["todos_conectados"] for m in ms])
    check("Monte Carlo reproduce el ATTR exacto de un ciclo de 3 nodos (pares)", abs(mc_attr - ex_attr) < 4 * math.sqrt(0.25 / n),
          f"MC={mc_attr:.4f} exacto={ex_attr:.4f}")
    check("Monte Carlo reproduce P(todos conectados) exacta del ciclo", abs(mc_todos - ex_todos) < 4 * math.sqrt(0.25 / n),
          f"MC={mc_todos:.4f} exacto={ex_todos:.4f}")
    cad = np.array([[0, 0, 1], [1, 1, 2], [2, 2, 3]], dtype=np.int64)   # cadena de 3 aristas, 2 terminales: ATTR = (1-p)^3
    ok = np.mean([ca.metricas_estado(ca.componentes(cad, 4, rng.random(3) >= p), np.array([0, 3]))["attr"] for _ in range(n)])
    check("Monte Carlo reproduce el ATTR exacto de una cadena (1-p)^3", abs(ok - (1 - p) ** 3) < 4 * math.sqrt(0.5 * 0.5 / n),
          f"MC={ok:.4f} exacto={(1 - p) ** 3:.4f}")
    aristas, claves, n_vert = ca.cargar_red(cur)
    vert = np.array([c[2] for c in claves])
    m0 = ca.metricas_estado(ca.componentes(aristas, n_vert), vert)
    comp_sql = dict(q("SELECT node, component FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM red.aristas')"))
    n_comp_sql = len({comp_sql[int(v)] for v in vert})
    check("Componentes de los nodos clave: SciPy = pgr_connectedComponents (con puente)",
          n_comp_sql == m0["n_componentes_con_claves"] == 1 and m0["attr"] == 1.0, f"ATTR operacion={m0['attr']}")
    a2, _, _ = ca.cargar_red(cur, sin_puentes=True)
    m1 = ca.metricas_estado(ca.componentes(a2, n_vert), vert)
    comp_sql2 = dict(q("SELECT node, component FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM red.aristas WHERE tipo=''detectado''')"))
    _, cuentas = np.unique([comp_sql2[int(v)] for v in vert], return_counts=True)
    attr_sql = (cuentas * (cuentas - 1) // 2).sum() / 120
    check("Sin puente: SciPy = pgRouting (2 bloques; ATTR = pares dentro de cada bloque / 120)",
          len(cuentas) == m1["n_componentes_con_claves"] == 2 and abs(m1["attr"] - attr_sql) < 1e-12
          and m1["todos_conectados"] == 0.0, f"ATTR={m1['attr']:.4f}")
    p0 = np.zeros(len(aristas))
    a0 = np.mean([ca.metricas_estado(ca.componentes(aristas, n_vert, np.random.default_rng(s).random(len(p0)) >= p0), vert)["attr"] for s in range(20)])
    a1 = np.mean([ca.metricas_estado(ca.componentes(aristas, n_vert, np.random.default_rng(s).random(len(p0)) >= np.ones(len(p0))), vert)["attr"] for s in range(20)])
    check("Caso limite p=0 en todas las aristas -> ATTR = 1 ; p=1 -> ATTR = 0", a0 == 1.0 and a1 == 0.0)
    # metricas de topologia (Fig. 2 y ec. 4 del paper)
    tri = mr.adyacencia(np.array([[0, 0, 1], [1, 1, 2], [2, 2, 0]]), 3)
    estrella = mr.adyacencia(np.array([[0, 0, 1], [1, 0, 2], [2, 0, 3]]), 4)
    _, t_tri = mr.metricas_topologia(tri, np.arange(3))
    _, t_est = mr.metricas_topologia(estrella, np.arange(4))
    check("Agrupamiento: triangulo = 1, estrella = 0; grado medio triangulo = 2, estrella = 1,5",
          t_tri["agrupamiento"] == 1.0 and t_est["agrupamiento"] == 0.0 and t_tri["grado_medio"] == 2.0 and t_est["grado_medio"] == 1.5)
    vred = np.unique(np.concatenate([aristas[:, 1], aristas[:, 2], vert]))
    _, top = mr.metricas_topologia(mr.adyacencia(aristas, n_vert), vred)
    n_pares_simples = q1("SELECT count(DISTINCT (least(source,target), greatest(source,target))) FROM red.aristas WHERE source <> target")
    n_v_sql = q1("SELECT count(*) FROM (SELECT source v FROM red.aristas UNION SELECT target FROM red.aristas) x")
    check("Grado medio = 2|L|/|V| calculado en SQL", abs(top["grado_medio"] - 2 * n_pares_simples / n_v_sql) < 1e-9,
          f"{top['grado_medio']:.4f}")
    n_comp_total = q1("SELECT count(DISTINCT component) FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM red.aristas')")
    check("Componentes conexas N: SciPy = pgr_connectedComponents (toda la red)", top["n_componentes"] == n_comp_total,
          f"N={n_comp_total}")
    # distancias por pgRouting (pgr_dijkstraCost) vs camino minimo independiente (SciPy, sin pesos)
    from scipy.sparse.csgraph import shortest_path
    d_pgr = mr.distancias_pgrouting(cur, "todas", vert)
    sp = shortest_path(mr.adyacencia(aristas, n_vert), unweighted=True, directed=False, indices=vert)[:, vert]
    iu = np.triu_indices(len(vert), 1)
    check("Distancias entre nodos clave: pgr_dijkstraCost = camino minimo independiente (SciPy)",
          abs(d_pgr["dist_media_saltos"] - sp[iu].mean()) < 1e-9 and d_pgr["pares_alcanzables"] == 120,
          f"{d_pgr['dist_media_saltos']:.2f} enlaces")
    check("Centralidad por distancia (ec. 12) = promedio de 1/d sobre los 120 pares",
          abs(d_pgr["centralidad"] - (1 / sp[iu]).mean()) < 1e-12, f"{d_pgr['centralidad']:.4f}")

    print("E. Modelo de falla (recalculo independiente)")
    h = float(q1("SELECT prof_km FROM sismo.evento"))
    mag = float(q1("SELECT magnitud FROM sismo.evento"))
    filas = q("SELECT arista_id, longitud_m, dist_fuente_km, pga_g, mmi, pgv_cm_s, p_falla FROM red.prob_falla ORDER BY random() LIMIT 25")
    peor = 0.0
    for aid, lon_m, d, pga, mmi, pgv, pf in filas:
        rrup = math.hypot(d, h)
        y = math.exp(0.2418 + 1.414 * mag - 2.552 * math.log(rrup + 1.7818 * math.exp(0.554 * mag)) + 0.00607 * h)
        mm = 3.66 * math.log10(y * 981) - 1.66
        vv = 10 ** ((mm - 2.35) / 3.47)
        p_ = 1 - math.exp(-0.002416 * vv * lon_m / 1000)
        peor = max(peor, abs(p_ - pf))
    check("p_falla de 25 aristas al azar coincide con el recalculo independiente (|dif| < 1e-4)", peor < 1e-4, f"max dif {peor:.1e}")
    check("p_falla en [0,1] y una fila por arista", q1("SELECT count(*) FROM red.prob_falla WHERE p_falla<0 OR p_falla>1") == 0
          and q1("SELECT count(*) FROM red.prob_falla") == n_ar)
    corr = q1("SELECT corr(dist_fuente_km, pga_g) FROM red.prob_falla WHERE dist_fuente_km > 0")
    check("PGA decrece con la distancia a la fuente (correlacion negativa)", corr < 0, f"corr={corr:.2f}")
    check("Aristas dentro de la ruptura (dist=0) tienen el PGA maximo",
          abs(q1("SELECT max(pga_g) FROM red.prob_falla WHERE dist_fuente_km=0") - q1("SELECT max(pga_g) FROM red.prob_falla")) < 1e-9)
    r_c = float(q1("SELECT radio_km FROM sismo.zonas WHERE tipo='ruptura'"))
    check("Radio del circulo = sqrt(A/pi), A = 10^(-3,476+0,952 Mw) (Strasser 2010)",
          abs(r_c - math.sqrt(10 ** (-3.476 + 0.952 * mag) / math.pi)) < 0.06, f"R = {r_c} km")
    area_bd = q1("SELECT ST_Area(geom::geography)/1e6 FROM sismo.zonas WHERE tipo='ruptura'")
    check("Zona de ruptura: circulo centrado en el epicentro con el area de Strasser (1 %)",
          q1("SELECT ST_Contains(z.geom, e.geom) FROM sismo.zonas z, sismo.evento e WHERE z.tipo='ruptura'")
          and abs(area_bd - math.pi * r_c ** 2) / (math.pi * r_c ** 2) < 0.01, f"area {area_bd:,.0f} km2")
    dif_d = q1("""SELECT max(abs(f.dist_fuente_km - greatest(0, ST_Distance(a.geom::geography, e.geom::geography)/1000 - %s)))
                  FROM red.prob_falla f JOIN red.aristas a ON a.id = f.arista_id, sismo.evento e""", r_c)
    check("Distancia a la fuente = max(0, distancia al epicentro - R) en todas las aristas", dif_d < 0.06, f"max dif {dif_d:.3f} km")
    r_d = float(q1("SELECT radio_km FROM sismo.zonas WHERE tipo='dano_mmi6'"))
    mmi_dentro, mmi_fuera = q("""SELECT min(f.mmi) FILTER (WHERE ST_DWithin(a.geom::geography, e.geom::geography, %s)),
                                        max(f.mmi) FILTER (WHERE NOT ST_DWithin(a.geom::geography, e.geom::geography, %s))
                                 FROM red.prob_falla f JOIN red.aristas a ON a.id = f.arista_id, sismo.evento e""",
                              (r_d - 0.1) * 1000, (r_d + 0.1) * 1000)[0]
    check("Perimetro de dano: MMI >= VI dentro del circulo y < VI fuera", mmi_dentro >= 5.995 and mmi_fuera < 6.005,
          f"radio {r_d} km; MMI min dentro {mmi_dentro}, max fuera {mmi_fuera}")
    mmi_max = q1("SELECT max(mmi) FROM red.prob_falla")
    mmi_usgs = float(q1("SELECT mmi_usgs FROM sismo.evento"))
    aviso("Contraste con USGS", f"MMI maximo del modelo {mmi_max:.2f} vs MMI maximo USGS {mmi_usgs:.2f} "
          f"(el modelo subestima ~{mmi_usgs - mmi_max:.1f} unidades: PGA mediana en suelo rigido, sin efectos de sitio)")
    check("MMI maximo del modelo no se desvia mas de 1,5 unidades del de USGS", abs(mmi_max - mmi_usgs) < 1.5)

    print("F. main.sql")
    txt = (RAIZ / "main.sql").read_text(encoding="utf-8")
    filas_sql = re.findall(r"^\((\d+),'(\w+)',([\d.e+-]+),([\d.e+-]+),([\d.e+-]+),([\d.e+-]+),([\d.e+-]+),([\d.e+-]+),([\d.e+-]+)\)", txt, re.M)
    check("main.sql contiene una fila por arista", len(filas_sql) == n_ar, f"{len(filas_sql)} filas")
    dif = 0.0
    bd = {r_[0]: r_[1] for r_ in q("SELECT arista_id, p_falla FROM red.prob_falla")}
    for f in filas_sql:
        dif = max(dif, abs(bd[int(f[0])] - float(f[8])))
    check("Probabilidades de main.sql = las de la tabla en la BD", dif < 1e-9, f"max dif {dif:.1e}")

    print("G. ATTR despues del sismo (replica con otra semilla)")
    p_arr = np.array([bd.get(int(i), 0.0) for i in aristas[:, 0]])
    rng = np.random.default_rng(2024)
    ms = [ca.metricas_estado(ca.componentes(aristas, n_vert, rng.random(len(p_arr)) >= p_arr), vert) for _ in range(3000)]
    v_attr = np.array([m["attr"] for m in ms])
    guardado, ic = q("SELECT valor, ic95 FROM red.metricas_resiliencia WHERE red='todas' AND escenario='despues' AND metrica='attr'")[0]
    err = 1.96 * v_attr.std(ddof=1) / math.sqrt(3000)
    check("ATTR post-sismo con otra semilla (n=3000) coincide con el guardado (dentro de los IC95 %)",
          abs(v_attr.mean() - guardado) < 2 * (ic + err), f"{v_attr.mean():.4f} vs {guardado:.4f}")
    check("P(todos los nodos clave conectados) post-sismo ~ 0 (< 0,005)",
          np.mean([m["todos_conectados"] for m in ms]) < 0.005)
    check("Metricas guardadas para ambas redes (antes y despues)",
          q1("SELECT count(DISTINCT red || escenario) FROM red.metricas_resiliencia WHERE escenario IN ('antes','despues')") == 4)
    # cota teorica: ATTR <= probabilidad de que sobreviva al menos un camino entre extremos; usamos un corte simple:
    cur.execute("""SELECT count(*) FROM red.aristas a JOIN red.prob_falla f ON f.arista_id=a.id
                   WHERE f.p_falla>0.5""")
    aviso("Aristas con p>0,5", str(cur.fetchone()[0]))

    print("H. Interfaz web (Flask + Leaflet)")
    env = dict(os.environ, PUERTO="5056")
    srv = subprocess.Popen([sys.executable, str(RAIZ / "main.py"), "mapa"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):  # espera a que el servidor responda (hasta ~30 s)
            try:
                urllib.request.urlopen("http://127.0.0.1:5056/", timeout=2).read()
                break
            except OSError:
                time.sleep(1)
        get = lambda ruta: json.load(urllib.request.urlopen("http://127.0.0.1:5056" + ruta, timeout=120))  # noqa: E731
        n_vis = q1("SELECT count(*) FROM red.aristas WHERE geom && ST_MakeEnvelope(-78,-57,-65,-17,4326)")
        check("/api/red entrega las aristas del Chile continental (las islas oceanicas quedan fuera del recuadro del mapa)",
              len(get("/api/red")["features"]) == n_vis, f"{n_vis} de {n_ar}")
        check("/api/nodos entrega 16 nodos", len(get("/api/nodos")["features"]) == 16)
        check("/api/sismo entrega epicentro, circulo de ruptura y perimetro de dano",
              {f["properties"]["tipo"] for f in get("/api/sismo")["features"]} == {"epicentro", "ruptura", "dano_mmi6"})
        a = get("/api/metricas?red=todas")
        check("/api/metricas (red completa): ATTR antes = 1 y despues = valor guardado",
              a["antes"]["attr"] == 1.0 and abs(a["despues"]["attr"] - guardado) < 1e-12)
        b = get("/api/metricas?red=fibra")
        check("/api/metricas (solo fibra) entrega las mismas metricas", set(b["antes"]) == set(a["antes"]))
        s = get("/api/simulacion?semilla=1&red=todas")
        check("/api/simulacion devuelve enlaces caidos y metricas coherentes",
              s["n_caidas"] == len(s["caidas"]) > 0 and 0 <= s["attr"] <= 1 and s["pares_alcanzables"] == round(s["attr"] * 120))
        html = urllib.request.urlopen("http://127.0.0.1:5056/", timeout=30).read().decode()
        check("La pagina carga Leaflet", "leaflet" in html.lower())
    finally:
        srv.terminate()
    conn.close()

    n_ok = sum(resultados)
    print(f"\nRESUMEN: {n_ok} de {len(resultados)} pruebas aprobadas")
    sys.exit(0 if n_ok == len(resultados) else 1)


if __name__ == "__main__":
    main("--externo" in sys.argv)
