// Run repeatedly until it returns 0 (small graph: one pass is enough).
MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS;
