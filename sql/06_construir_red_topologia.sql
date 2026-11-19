-- =====================================================================
-- Construccion de la red y su topologia (pgr_createTopology)
-- Entrada : infra.* (4 capas de lineas)  y  red.nodos_clave
-- Salida  : red.aristas, red.aristas_vertices_pgr, red.nodos_clave.vertice_id
-- Pasos   : (1) lineas simples  (2) snap 56 m  (3) nodado en intersecciones
--           (4) topologia  (5) asignar nodos clave a vertices
--           (6) puentes inferidos entre fragmentos que contienen nodos clave
-- =====================================================================
SET client_min_messages = warning;
CREATE SCHEMA IF NOT EXISTS red;
DROP TABLE IF EXISTS red.aristas, red.aristas_vertices_pgr, red.tmp_lineas, red.tmp_snap, red.tmp_nodado CASCADE;

-- (1) lineas simples con su capa de origen y prioridad (fibra manda sobre las demas)
CREATE TABLE red.tmp_lineas AS
SELECT row_number() OVER () AS id, x.capa, x.prio, d.geom::geometry(LineString,4326) AS geom
FROM (SELECT 'fibra' AS capa, 1 AS prio, geom FROM infra.fibra_optica
      UNION ALL SELECT 'poste', 2, geom FROM infra.postacion_electrica
      UNION ALL SELECT 'ferry', 3, geom FROM infra.trayecto_ferry
      UNION ALL SELECT 'camino', 4, geom FROM infra.camino_sin_postacion) x,
     LATERAL ST_Dump(x.geom) d;
CREATE INDEX ON red.tmp_lineas USING gist(geom);

-- (2) snap de cada linea a sus vecinas dentro de 0.0005 grados (~56 m)
CREATE TABLE red.tmp_snap AS
SELECT a.id, a.capa, a.prio,
       COALESCE(ST_Snap(a.geom,
          (SELECT ST_Collect(b.geom) FROM red.tmp_lineas b
            WHERE b.id <> a.id AND ST_DWithin(a.geom, b.geom, 0.0005)), 0.0005), a.geom) AS geom
FROM red.tmp_lineas a;
CREATE INDEX ON red.tmp_snap USING gist(geom);

-- (3) nodado: partir las lineas en cada interseccion
CREATE TABLE red.tmp_nodado AS
SELECT row_number() OVER () AS id, (d.g).geom::geometry(LineString,4326) AS geom
FROM (SELECT ST_Dump(ST_Node(ST_Collect(geom))) AS g FROM red.tmp_snap) d;

CREATE TABLE red.aristas AS
SELECT n.id, COALESCE(c.capa, 'desconocido') AS capa, 'detectado'::text AS tipo,
       ST_Length(n.geom::geography) AS longitud_m, n.geom,
       NULL::int AS source, NULL::int AS target
FROM red.tmp_nodado n
LEFT JOIN LATERAL (SELECT s.capa FROM red.tmp_snap s
                    WHERE ST_DWithin(s.geom, ST_LineInterpolatePoint(n.geom, 0.5), 0.00001)
                    ORDER BY s.prio LIMIT 1) c ON true
WHERE ST_Length(n.geom::geography) > 0;
ALTER TABLE red.aristas ADD PRIMARY KEY (id);
CREATE INDEX ON red.aristas USING gist(geom);

-- (4) TOPOLOGIA con pgRouting (tolerancia 0.00001 grados ~ 1 m)
SELECT pgr_createTopology('red.aristas', 0.00001, 'geom', 'id', 'source', 'target', clean := true);
CREATE INDEX ON red.aristas(source);
CREATE INDEX ON red.aristas(target);

-- (5) asignar cada nodo clave a su vertice mas cercano
ALTER TABLE red.nodos_clave ADD COLUMN IF NOT EXISTS vertice_id int, ADD COLUMN IF NOT EXISTS dist_a_red_km numeric;
UPDATE red.nodos_clave k
   SET vertice_id = (SELECT v.id FROM red.aristas_vertices_pgr v ORDER BY v.the_geom <-> k.geom LIMIT 1);
UPDATE red.nodos_clave k
   SET dist_a_red_km = round((ST_Distance(k.geom::geography, v.the_geom::geography)/1000)::numeric, 1)
  FROM red.aristas_vertices_pgr v WHERE v.id = k.vertice_id;

-- (5b) para la variante "solo fibra": vertice mas cercano que toca una arista de fibra
ALTER TABLE red.nodos_clave ADD COLUMN IF NOT EXISTS vertice_fibra int, ADD COLUMN IF NOT EXISTS dist_a_fibra_km numeric;
CREATE TEMP TABLE tmp_v_fibra AS
  SELECT v.id, v.the_geom FROM red.aristas_vertices_pgr v
   WHERE v.id IN (SELECT source FROM red.aristas WHERE capa = 'fibra' UNION SELECT target FROM red.aristas WHERE capa = 'fibra');
CREATE INDEX ON tmp_v_fibra USING gist(the_geom);
UPDATE red.nodos_clave k
   SET vertice_fibra = (SELECT v.id FROM tmp_v_fibra v ORDER BY v.the_geom <-> k.geom LIMIT 1);
UPDATE red.nodos_clave k
   SET dist_a_fibra_km = round((ST_Distance(k.geom::geography, v.the_geom::geography)/1000)::numeric, 1)
  FROM red.aristas_vertices_pgr v WHERE v.id = k.vertice_fibra;
DROP TABLE tmp_v_fibra;

-- (6) puentes inferidos: une, de a pares y por menor distancia, los fragmentos que contienen nodos clave
DO $$
DECLARE r record; nuevo_id int; n int := 0;
BEGIN
  SELECT max(id) INTO nuevo_id FROM red.aristas;
  LOOP
    DROP TABLE IF EXISTS tmp_comp, tmp_rel;
    CREATE TEMP TABLE tmp_comp AS
      SELECT node, component FROM pgr_connectedComponents('SELECT id, source, target, longitud_m AS cost FROM red.aristas');
    CREATE TEMP TABLE tmp_rel AS
      SELECT DISTINCT c.component FROM red.nodos_clave k JOIN tmp_comp c ON c.node = k.vertice_id;
    EXIT WHEN (SELECT count(*) FROM tmp_rel) <= 1;
    SELECT a.id AS va, b.id AS vb, a.the_geom AS ga, b.the_geom AS gb
      INTO r
      FROM red.aristas_vertices_pgr a
      JOIN tmp_comp ca ON ca.node = a.id JOIN tmp_rel ra ON ra.component = ca.component
      JOIN red.aristas_vertices_pgr b ON true
      JOIN tmp_comp cb ON cb.node = b.id JOIN tmp_rel rb ON rb.component = cb.component
     WHERE ca.component < cb.component
     ORDER BY ST_Distance(a.the_geom, b.the_geom) LIMIT 1;
    nuevo_id := nuevo_id + 1; n := n + 1;
    INSERT INTO red.aristas(id, capa, tipo, longitud_m, geom, source, target)
    VALUES (nuevo_id, 'puente', 'inferido',
            ST_Distance(r.ga::geography, r.gb::geography),
            ST_SetSRID(ST_MakeLine(r.ga, r.gb), 4326), r.va, r.vb);
  END LOOP;
  RAISE NOTICE 'puentes inferidos agregados: %', n;
END $$;

DROP TABLE IF EXISTS red.tmp_lineas, red.tmp_snap, red.tmp_nodado CASCADE;
ANALYZE red.aristas;

-- Resumen
SELECT capa, tipo, count(*) AS aristas, round(sum(longitud_m)/1000) AS km
  FROM red.aristas GROUP BY capa, tipo ORDER BY capa;
SELECT (SELECT count(*) FROM red.aristas) AS aristas, (SELECT count(*) FROM red.aristas_vertices_pgr) AS vertices;
SELECT nombre, vertice_id, dist_a_red_km, vertice_fibra, dist_a_fibra_km FROM red.nodos_clave ORDER BY id;
SELECT id AS id_puente, round((longitud_m/1000)::numeric, 1) AS km FROM red.aristas WHERE tipo = 'inferido' ORDER BY longitud_m DESC;
