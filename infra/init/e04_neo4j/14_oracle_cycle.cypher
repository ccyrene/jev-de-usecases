// Is the changed asset on a cycle of verified lineage edges? (Cycle-safe: trail, bounded.)
MATCH (c:Asset {asset_id: $asset_id})
RETURN EXISTS {
  MATCH (c) ( (a)-[r:PRODUCES|CONSUMES|DEPENDS_ON]-(b)
              WHERE r.status = 'active'
                AND ((type(r) = 'PRODUCES' AND startNode(r) = a)
                  OR (type(r) <> 'PRODUCES' AND endNode(r) = a)) ){1,__MAX_HOPS__} (c)
} AS in_cycle;
