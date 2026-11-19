-- Exploracion 4: por combinacion de capas, cuantos nodos clave quedan en la misma componente que Santiago
SET client_min_messages = warning;
DROP TABLE IF EXISTS tmp_u CASCADE;
CREATE TABLE tmp_u AS
SELECT row_number() OVER () AS id, capa, d.geom::geometry(LineString,4326) AS geom
FROM (SELECT 'fibra' capa, geom FROM infra.fibra_optica
      UNION ALL SELECT 'poste', geom FROM infra.postacion_electrica
      UNION ALL SELECT 'camino', geom FROM infra.camino_sin_postacion
      UNION ALL SELECT 'ferry', geom FROM infra.trayecto_ferry) x, LATERAL ST_Dump(x.geom) d;

CREATE OR REPLACE FUNCTION tmp_combo(p_capas text[], p_snap float) RETURNS text AS $$
DECLARE r text;
BEGIN
  DROP TABLE IF EXISTS tmp_c, tmp_cs, tmp_cn CASCADE;
  CREATE TABLE tmp_c AS SELECT id, geom FROM tmp_u WHERE capa = ANY(p_capas);
  CREATE INDEX ON tmp_c USING gist(geom);
  CREATE TABLE tmp_cs AS
    SELECT a.id, ST_Snap(a.geom, (SELECT ST_Collect(b.geom) FROM tmp_c b
        WHERE b.id<>a.id AND ST_DWithin(a.geom,b.geom,p_snap)), p_snap) geom FROM tmp_c a;
  CREATE TABLE tmp_cn AS
    SELECT row_number() OVER () AS id, (d.g).geom::geometry(LineString,4326) AS geom
    FROM (SELECT ST_Dump(ST_Node(ST_Collect(geom))) g FROM tmp_cs) d;
  ALTER TABLE tmp_cn ADD COLUMN source int, ADD COLUMN target int;
  PERFORM pgr_createTopology('tmp_cn', 0.00001, 'geom', 'id', 'source', 'target', clean := true);
  DROP TABLE IF EXISTS tmp_comp;
  CREATE TABLE tmp_comp AS SELECT node, component FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM tmp_cn');
  SELECT format('capas=%s snap=%s | en_comp_de_Santiago=%s de 16 | max_dist_a_red_km=%s | comp_sin_conectar=%s',
         p_capas, p_snap,
         count(*) FILTER (WHERE x.component = s.component),
         round(max(x.dist)/1000), count(*) FILTER (WHERE x.component <> s.component))
  INTO r
  FROM (SELECT n.nombre, c.component, ST_Distance(n.geom::geography, v.the_geom::geography) dist
        FROM red.nodos_clave n
        CROSS JOIN LATERAL (SELECT id, the_geom FROM tmp_cn_vertices_pgr ORDER BY the_geom <-> n.geom LIMIT 1) v
        JOIN tmp_comp c ON c.node = v.id) x,
       (SELECT c.component FROM red.nodos_clave n
        CROSS JOIN LATERAL (SELECT id FROM tmp_cn_vertices_pgr ORDER BY the_geom <-> n.geom LIMIT 1) v
        JOIN tmp_comp c ON c.node=v.id WHERE n.nombre='Santiago') s;
  RETURN r;
END $$ LANGUAGE plpgsql;

SELECT tmp_combo(ARRAY['fibra'], 0.0005);
SELECT tmp_combo(ARRAY['fibra','poste'], 0.0005);
SELECT tmp_combo(ARRAY['fibra','poste','ferry'], 0.0005);
SELECT tmp_combo(ARRAY['fibra','poste','camino','ferry'], 0.0005);
SELECT tmp_combo(ARRAY['fibra','poste','camino','ferry'], 0.002);
