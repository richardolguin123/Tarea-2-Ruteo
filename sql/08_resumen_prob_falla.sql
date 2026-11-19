-- Resumen por capa de las probabilidades de falla cargadas por main.sql
SELECT capa, count(*) AS aristas, round(avg(p_falla)::numeric,4) AS p_media,
       round(max(p_falla)::numeric,4) AS p_max, round(max(pga_g)::numeric,3) AS pga_max_g
FROM red.prob_falla GROUP BY capa ORDER BY capa;
SELECT count(*) AS total, count(*) FILTER (WHERE p_falla > 0.5) AS p_mayor_05,
       round(avg(p_falla)::numeric,4) AS p_media_total FROM red.prob_falla;
