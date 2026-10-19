// Profile and owners of the changed asset.
MATCH (c:Asset {asset_id: $asset_id})
OPTIONAL MATCH (c)-[:OWNED_BY]->(o:Owner)
RETURN properties(c) AS props, labels(c) AS labels, collect(o.domain) AS owner_domains;
