SELECT
    customer_id,
    region,
    SUM(amount) AS total_amount,
    ROW_NUMBER() OVER (PARTITION BY region ORDER BY SUM(amount) DESC) AS rank_in_region
FROM transactions
WHERE status = 'completed'
GROUP BY customer_id, region
HAVING SUM(amount) > 500
ORDER BY total_amount DESC;
