-- Base tables. Field columns under test are added by the loader (ALTER TABLE ... ADD COLUMN),
-- JSONB keys live inside attrs.
create table crm.customer (
    id bigint primary key, created_at timestamptz not null, status text not null,
    row_version int not null default 1, attrs jsonb not null default '{}'
);
create table hr.employee (
    id bigint primary key, created_at timestamptz not null, status text not null,
    row_version int not null default 1, attrs jsonb not null default '{}'
);
create table proc.supplier (
    id bigint primary key, created_at timestamptz not null, status text not null,
    row_version int not null default 1, attrs jsonb not null default '{}'
);
create table web.web_event (
    id bigint primary key, created_at timestamptz not null, status text not null,
    row_version int not null default 1, attrs jsonb not null default '{}'
);
create table audit.audit (
    id bigint primary key, created_at timestamptz not null, status text not null,
    row_version int not null default 1, attrs jsonb not null default '{}'
);
