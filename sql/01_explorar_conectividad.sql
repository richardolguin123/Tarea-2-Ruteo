-- Exploracion: cuantas componentes conexas resultan segun la tolerancia de union de extremos
-- (solo capa fibra_optica). Se usa una tabla temporal por tolerancia.
\timing off
DROP TABLE IF EXISTS tmp_f CASCADE;
CREATE TABLE tmp_f AS
SELECT row_number() OVER () AS id, d.geom::geometry(LineString,4326) AS geom
FROM infra.fibra_optica f, LATERAL ST_Dump(f.geom) d;
ALTER TABLE tmp_f ADD COLUMN source int, ADD COLUMN target int;

SELECT count(*) AS tramos_simples FROM tmp_f;

DO $$
DECLARE tol float; comps int; verts int; mayor int;
BEGIN
  FOREACH tol IN ARRAY ARRAY[0.00001, 0.0001, 0.0005, 0.002, 0.005] LOOP
    UPDATE tmp_f SET source=NULL, target=NULL;
    DROP TABLE IF EXISTS tmp_f_vertices_pgr;
    PERFORM pgr_createTopology('tmp_f', tol, 'geom', 'id', 'source', 'target', clean := true);
    SELECT count(*) INTO verts FROM tmp_f_vertices_pgr;
    SELECT count(DISTINCT component), max(c) INTO comps, mayor FROM
      (SELECT component, count(*) OVER (PARTITION BY component) c
         FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost FROM tmp_f')) x;
    RAISE NOTICE 'tolerancia=% grados (~% m) -> vertices=% componentes=% mayor_componente=% vertices',
      tol, round(tol*111000), verts, comps, mayor;
  END LOOP;
END $$;
