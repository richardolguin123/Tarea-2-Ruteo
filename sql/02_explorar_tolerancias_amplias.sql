-- Exploracion 2: tolerancias amplias, solo fibra vs. fibra + otras capas como respaldo de continuidad
SET client_min_messages = warning;
DROP TABLE IF EXISTS tmp_a CASCADE;
CREATE TABLE tmp_a AS
SELECT row_number() OVER () AS id, capa, d.geom::geometry(LineString,4326) AS geom, 1 AS cost
FROM (
  SELECT 'fibra' capa, geom FROM infra.fibra_optica
  UNION ALL SELECT 'poste', geom FROM infra.postacion_electrica
  UNION ALL SELECT 'camino', geom FROM infra.camino_sin_postacion
  UNION ALL SELECT 'ferry', geom FROM infra.trayecto_ferry
) u, LATERAL ST_Dump(u.geom) d;
ALTER TABLE tmp_a ADD COLUMN source int, ADD COLUMN target int;
CREATE INDEX ON tmp_a USING gist(geom);

CREATE OR REPLACE FUNCTION tmp_probar(p_capas text[], p_tol float) RETURNS text AS $$
DECLARE comps int; mayor int; verts int;
BEGIN
  DROP TABLE IF EXISTS tmp_b CASCADE;
  CREATE TABLE tmp_b AS SELECT id, geom FROM tmp_a WHERE capa = ANY(p_capas);
  ALTER TABLE tmp_b ADD COLUMN source int, ADD COLUMN target int;
  PERFORM pgr_createTopology('tmp_b', p_tol, 'geom', 'id', 'source', 'target', clean := true);
  SELECT count(*) INTO verts FROM tmp_b_vertices_pgr;
  SELECT count(DISTINCT component), max(c) INTO comps, mayor FROM
    (SELECT component, count(*) OVER (PARTITION BY component) c
       FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM tmp_b')) x;
  RETURN format('capas=%s tol=%s grados (~%s km): vertices=%s componentes=%s mayor=%s',
         p_capas, p_tol, round((p_tol*111)::numeric,1), verts, comps, mayor);
END $$ LANGUAGE plpgsql;

SELECT tmp_probar(ARRAY['fibra'], t) FROM unnest(ARRAY[0.01, 0.02, 0.05, 0.1]) t;
SELECT tmp_probar(ARRAY['fibra','poste','camino','ferry'], t) FROM unnest(ARRAY[0.0005, 0.005, 0.01, 0.02, 0.05]) t;
