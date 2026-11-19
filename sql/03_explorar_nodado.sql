-- Exploracion 3: nodar la fibra en sus intersecciones y ajustar (snap) extremos cercanos
SET client_min_messages = warning;
DROP TABLE IF EXISTS tmp_fb CASCADE;
CREATE TABLE tmp_fb AS SELECT row_number() OVER () AS id, d.geom::geometry(LineString,4326) AS geom
FROM infra.fibra_optica f, LATERAL ST_Dump(f.geom) d;
CREATE INDEX ON tmp_fb USING gist(geom);

CREATE OR REPLACE FUNCTION tmp_nodar(p_snap float, p_tol float) RETURNS text AS $$
DECLARE n_edges int; verts int; comps int; mayor_edges int;
BEGIN
  DROP TABLE IF EXISTS tmp_s, tmp_n CASCADE;
  -- 1) snap de cada linea a sus vecinas cercanas (si p_snap = 0 no se ajusta)
  CREATE TABLE tmp_s AS
  SELECT a.id,
         CASE WHEN p_snap > 0 THEN
           ST_Snap(a.geom, (SELECT ST_Collect(b.geom) FROM tmp_fb b
                             WHERE b.id <> a.id AND ST_DWithin(a.geom, b.geom, p_snap)), p_snap)
         ELSE a.geom END AS geom
  FROM tmp_fb a;
  -- 2) nodado en intersecciones
  CREATE TABLE tmp_n AS
  SELECT row_number() OVER () AS id, d.geom::geometry(LineString,4326) AS geom
  FROM (SELECT ST_Dump(ST_Node(ST_Collect(geom))) d FROM tmp_s) q, LATERAL (SELECT (q.d).geom) d(geom);
  ALTER TABLE tmp_n ADD COLUMN source int, ADD COLUMN target int;
  SELECT count(*) INTO n_edges FROM tmp_n;
  PERFORM pgr_createTopology('tmp_n', p_tol, 'geom', 'id', 'source', 'target', clean := true);
  SELECT count(*) INTO verts FROM tmp_n_vertices_pgr;
  SELECT count(DISTINCT component), max(c) INTO comps, mayor_edges FROM
    (SELECT component, count(*) OVER (PARTITION BY component) c
       FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM tmp_n')) x;
  RETURN format('snap=%s tol_topo=%s -> aristas=%s vertices=%s componentes=%s vertices_en_mayor=%s',
                p_snap, p_tol, n_edges, verts, comps, mayor_edges);
END $$ LANGUAGE plpgsql;

SELECT tmp_nodar(0, 0.00001);
SELECT tmp_nodar(0.0005, 0.00001);
SELECT tmp_nodar(0.002, 0.00001);
SELECT tmp_nodar(0.005, 0.00001);
SELECT tmp_nodar(0.01, 0.00001);
