// Fixed downstream oracle. Edge direction (as stored):
//   (job)-[:PRODUCES]->(data)        data flows job -> data
//   (job|dashboard|process)-[:CONSUMES]->(data)   data flows data -> consumer
//   (dependent)-[:DEPENDS_ON]->(upstream)         data flows upstream -> dependent
// A step from node a is "downstream" when it follows PRODUCES forward or CONSUMES / DEPENDS_ON
// backward. Only verified (status = 'active') edges count; the repeated segment is a trail, so
// cycles terminate, and the hop bound is a literal because quantifiers cannot be parameters.
MATCH (c:Asset {asset_id: $asset_id})
CALL (c) {
  MATCH p = (c) ( (a)-[r:PRODUCES|CONSUMES|DEPENDS_ON]-(b)
                  WHERE r.status = 'active'
                    AND ((type(r) = 'PRODUCES' AND startNode(r) = a)
                      OR (type(r) <> 'PRODUCES' AND endNode(r) = a)) ){1,__MAX_HOPS__} (x:Asset)
  WHERE x <> c AND (x:Dataset OR x:Dashboard OR x:CriticalBusinessProcess)
  RETURN x, min(length(p)) AS hops
}
OPTIONAL MATCH (x)-[:OWNED_BY]->(o:Owner)
RETURN properties(x) AS props, labels(x) AS labels, hops, collect(o.domain) AS owner_domains
ORDER BY hops, props.asset_id;
