// Bounded neighbourhood: every lineage edge (any status) leaving the changed asset or an asset one
// downstream step away, in flow orientation, capped by $limit.
MATCH (c:Asset {asset_id: $asset_id})
CALL (c) {
  MATCH p = (c) ( (a)-[r:PRODUCES|CONSUMES|DEPENDS_ON]-(b)
                  WHERE (type(r) = 'PRODUCES' AND startNode(r) = a)
                     OR (type(r) <> 'PRODUCES' AND endNode(r) = a) ){0,__EXCERPT_UP_HOPS__} (u:Asset)
  RETURN u, min(length(p)) AS ud
}
MATCH (u)-[r:PRODUCES|CONSUMES|DEPENDS_ON]-(v:Asset)
WHERE (type(r) = 'PRODUCES' AND startNode(r) = u) OR (type(r) <> 'PRODUCES' AND endNode(r) = u)
RETURN u.asset_id AS up, v.asset_id AS down, type(r) AS rel, r.status AS status,
       labels(v) AS down_labels, v.criticality AS down_criticality, ud
ORDER BY ud, up, down
LIMIT $limit;
