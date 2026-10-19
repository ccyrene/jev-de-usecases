-- E03 views and column documentation. Consumers read some data through these views.
SET search_path = shop;

CREATE VIEW v_order_totals AS
SELECT o.order_id, o.customer_id, o.placed_at, sum(i.quantity * i.unit_price) AS items_total
FROM orders o JOIN order_items i ON i.order_id = o.order_id
GROUP BY o.order_id, o.customer_id, o.placed_at;

CREATE VIEW v_customer_orders AS
SELECT c.customer_id, c.email, count(o.order_id) AS orders_count, sum(o.order_total) AS lifetime_total
FROM customers c JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.email;

CREATE VIEW v_payment_status AS
SELECT p.order_id, p.amount, p.status FROM payments p;

CREATE VIEW v_low_stock AS
SELECT s.product_id, s.warehouse_id, s.qty_on_hand, s.reorder_level
FROM inventory s WHERE s.qty_on_hand < s.reorder_level;

CREATE VIEW v_daily_revenue AS
SELECT date_trunc('day', o.placed_at)::date AS sales_day, sum(o.order_total) AS revenue
FROM orders o GROUP BY 1;

COMMENT ON COLUMN orders.order_total IS 'Order total including VAT.';
COMMENT ON COLUMN products.unit_price IS 'List price in THB.';
COMMENT ON COLUMN inventory.qty_on_hand IS 'Units on hand, single units.';
COMMENT ON COLUMN customers.status IS 'Account lifecycle status.';
