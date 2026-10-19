-- Deterministic synthetic seed data (no random()). Prices end in .99 so rounding never flips a filter.
SET search_path = shop;

INSERT INTO categories (name, parent_id)
SELECT 'Category ' || i, CASE WHEN i > 3 THEN i - 3 END FROM generate_series(1, 6) AS g(i) ORDER BY i;

INSERT INTO suppliers (name, contact_email, lead_time_days)
SELECT 'Supplier ' || i, 'supplier' || i || '@example.com', 3 + i * 2 FROM generate_series(1, 5) AS g(i) ORDER BY i;

INSERT INTO warehouses (code, city, capacity_units)
SELECT 'WH-' || lpad(i::text, 2, '0'), (ARRAY['Bangkok', 'Chiang Mai', 'Phuket', 'Khon Kaen'])[i], 1000 + i * 2000
FROM generate_series(1, 4) AS g(i) ORDER BY i;

INSERT INTO customers (email, full_name, phone, country_code, status, loyalty_points, created_at, legacy_segment)
SELECT 'user' || lpad(i::text, 4, '0') || '@example.com',
       'Customer ' || i,
       CASE WHEN i % 5 = 0 THEN NULL ELSE '08' || lpad(((i * 7919) % 100000000)::text, 8, '0') END,
       (ARRAY['TH', 'TH', 'TH', 'SG', 'JP'])[1 + i % 5],
       (ARRAY['active', 'active', 'active', 'suspended', 'closed'])[1 + i % 5],
       CASE WHEN i % 20 = 0 THEN 50000 ELSE (i * 37) % 1000 END,
       timestamptz '2026-01-01 00:00:00+00' + i * interval '1 day',
       'seg' || (i % 4)
FROM generate_series(1, 60) AS g(i) ORDER BY i;

INSERT INTO customer_addresses (customer_id, line1, city, postal_code, is_default, legacy_region)
SELECT 1 + i % 60, i || ' Example Road', (ARRAY['Bangkok', 'Nonthaburi', 'Phuket'])[1 + i % 3],
       '10' || lpad((i * 13 % 1000)::text, 3, '0'), i % 2 = 0, 'R' || (i % 3)
FROM generate_series(1, 80) AS g(i) ORDER BY i;

INSERT INTO products (sku, name, category_id, supplier_id, unit_price, weight_kg, is_active, legacy_code)
SELECT 'SKU-' || lpad(i::text, 4, '0'), 'Product ' || i, 1 + i % 6, 1 + i % 5,
       (5 + (i * 37) % 400) + 0.99,
       CASE WHEN i % 7 = 0 THEN NULL ELSE ((i % 9) + 1) * 0.375 END,
       i % 10 <> 0, 'L' || i
FROM generate_series(1, 40) AS g(i) ORDER BY i;

INSERT INTO inventory (product_id, warehouse_id, qty_on_hand, reorder_level, legacy_bin)
SELECT p, 1 + (p + k) % 4, (p * 13 + k * 7) % 90, 10, 'B' || p
FROM generate_series(1, 40) AS a(p), generate_series(0, 1) AS b(k) ORDER BY p, k;

INSERT INTO inventory_movements (product_id, warehouse_id, delta, reason, moved_at)
SELECT 1 + i % 40, 1 + i % 4, (i % 7) * 5 - 10, CASE WHEN i % 2 = 0 THEN 'receipt' ELSE 'sale' END,
       timestamptz '2026-02-01 00:00:00+00' + i * interval '3 hours'
FROM generate_series(1, 100) AS g(i) ORDER BY i;

INSERT INTO purchase_orders (supplier_id, status, ordered_at, total_amount)
SELECT 1 + i % 5, 'received', timestamptz '2026-02-01 00:00:00+00' + i * interval '1 day', 1000 + i * 10.5
FROM generate_series(1, 20) AS g(i) ORDER BY i;

INSERT INTO purchase_order_lines (po_id, product_id, qty, unit_cost)
SELECT 1 + i % 20, 1 + i % 40, 1 + i % 20, 3.25 + i * 1.5
FROM generate_series(1, 60) AS g(i) ORDER BY i;

INSERT INTO orders (customer_id, status, order_total, currency, placed_at, channel, internal_note)
SELECT 1 + i % 60, (ARRAY['paid', 'shipped', 'cancelled'])[1 + i % 3],
       25.50 + (i * 17) % 900,
       'THB', timestamptz '2026-03-01 00:00:00+00' + i * interval '7 hours',
       (ARRAY['web', 'app', 'store'])[1 + i % 3], 'note ' || i
FROM generate_series(1, 200) AS g(i) ORDER BY i;

INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_pct)
SELECT 1 + i % 200, 1 + i % 40, 1 + i % 5, 9.99 + i % 50, CASE WHEN i % 4 = 0 THEN 5.00 ELSE 0 END
FROM generate_series(1, 600) AS g(i) ORDER BY i;

INSERT INTO shipments (order_id, carrier, tracking_no, shipped_at, delivered_at)
SELECT i, (ARRAY['kerry', 'thaipost', 'flash'])[1 + i % 3],
       CASE WHEN i % 10 = 0 THEN NULL ELSE '66' || lpad(i::text, 9, '0') END,
       timestamptz '2026-03-02 00:00:00+00' + i * interval '7 hours',
       CASE WHEN i % 4 = 0 THEN NULL
            ELSE timestamptz '2026-03-02 00:00:00+00' + i * interval '7 hours' + (i % 5 + 1) * interval '1 day' + interval '3 hours' END
FROM generate_series(1, 150) AS g(i) ORDER BY i;

INSERT INTO payment_methods (code, label) VALUES ('card', 'Card'), ('qr', 'QR payment'), ('transfer', 'Bank transfer');

INSERT INTO payments (order_id, method, amount, currency, status, paid_at, reference)
SELECT i, (ARRAY['card', 'qr', 'transfer'])[1 + i % 3],
       (SELECT order_total FROM orders o WHERE o.order_id = i) + CASE WHEN i % 10 = 0 THEN 1.25 ELSE 0 END,
       'THB', (ARRAY['captured', 'captured', 'authorized', 'failed'])[1 + i % 4],
       CASE WHEN i % 4 = 3 THEN NULL ELSE timestamptz '2026-03-01 06:00:00+00' + i * interval '7 hours' END,
       'PAY-' || lpad(i::text, 6, '0')
FROM generate_series(1, 200) AS g(i) ORDER BY i;

INSERT INTO refunds (payment_id, amount, reason, refunded_at)
SELECT i, 5.55 + i * 7, 'customer request', timestamptz '2026-04-01 00:00:00+00' + i * interval '1 day'
FROM generate_series(1, 15) AS g(i) ORDER BY i;

INSERT INTO coupons (code, discount_pct, valid_until)
SELECT 'SAVE' || i, i * 2.5 + 2.5, date '2026-05-01' + i * 30 FROM generate_series(1, 6) AS g(i) ORDER BY i;

INSERT INTO order_coupons (order_id, coupon_id)
SELECT i, 1 + i % 6 FROM generate_series(1, 20) AS g(i);

INSERT INTO reviews (product_id, customer_id, rating, body, created_at)
SELECT 1 + i % 40, 1 + i % 60, 1 + i % 5, CASE WHEN i % 6 = 0 THEN NULL ELSE 'Review text ' || i END,
       timestamptz '2026-04-01 00:00:00+00' + i * interval '5 hours'
FROM generate_series(1, 120) AS g(i) ORDER BY i;

INSERT INTO daily_sales_summary (sales_date, orders_count, gross_amount, refunded_amount)
SELECT date '2026-03-01' + i, 5 + i % 7, i * 1234.56, i * 3.5 FROM generate_series(1, 30) AS g(i);

INSERT INTO report_customer_ltv (customer_id, lifetime_value, last_order_at)
SELECT i, i * 321.45, timestamptz '2026-04-01 00:00:00+00' + i * interval '1 day' FROM generate_series(1, 60) AS g(i);

ANALYZE;
