// Unverified (pending) lineage edges leaving the changed asset or any actively reached asset.
MATCH (c:Asset {asset_id: $asset_id})
CALL (c) {
  MATCH (c) ( (a)-[r:PRODUCES|CONSUMES|DEPENDS_ON]-(b)
              WHERE r.status = 'active'
                AND ((type(r) = 'PRODUCES' AND startNode(r) = a)
                  OR (type(r) <> 'PRODUCES' AND endNode(r) = a)) ){0,__MAX_HOPS__} (u:Asset)
  RETURN DISTINCT u
}
MATCH (u)-[r:PRODUCES|CONSUMES|DEPENDS_ON {status: 'pending'}]-(v:Asset)
WHERE (type(r) = 'PRODUCES' AND startNode(r) = u) OR (type(r) <> 'PRODUCES' AND endNode(r) = u)
RETURN count(r) AS pending_edges;
