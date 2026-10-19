// Uniqueness (also gives index-backed lookups by asset_id).
CREATE CONSTRAINT asset_id_unique IF NOT EXISTS FOR (n:Asset) REQUIRE n.asset_id IS UNIQUE;
CREATE CONSTRAINT owner_id_unique IF NOT EXISTS FOR (n:Owner) REQUIRE n.asset_id IS UNIQUE;
