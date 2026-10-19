-- E05 synthetic PII-classification workload. Schema-qualified tables plus a versioned metadata catalog.
drop schema if exists crm, hr, proc, web, audit, meta cascade;
create schema crm;
create schema hr;
create schema proc;
create schema web;
create schema audit;
create schema meta;

create table meta.catalog_release (
    version     int primary key,
    released_on date not null,
    note        text not null
);
insert into meta.catalog_release values
    (1, date '2026-01-15', 'initial catalog: purpose and lineage only'),
    (2, date '2026-06-01', 'adds steward and retention');

create table meta.column_catalog (
    schema_name    text not null,
    table_name     text not null,
    column_path    text not null,   -- scalar column, or attrs.<json path> for JSONB keys
    version        int  not null references meta.catalog_release (version),
    purpose_en     text,
    purpose_th     text,
    lineage_en     text,
    lineage_th     text,
    steward        text,
    retention_days int,
    primary key (schema_name, table_name, column_path, version)
);
