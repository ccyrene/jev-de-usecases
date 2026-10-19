# E06 data dictionary (English)

Synthetic data only. Identifiers are ASCII; province and category names are stored data values in English and Thai.

## Tables

### provinces

Thai provinces (public geography, 77 rows).

PK: `province_id`

| column | type | nullable | description |
|---|---|---|---|
| `province_id` | INTEGER | no | Province key. |
| `name_en` | VARCHAR | no | English province name (stored data value). |
| `name_th` | VARCHAR | no | Thai province name (stored data value). |
| `region` | VARCHAR | no | Region: north, northeast, central, east, west or south. |

### product_categories

Product category tree with 2 levels.

PK: `category_id`; FK: `parent_category_id` -> `product_categories.category_id`

| column | type | nullable | description |
|---|---|---|---|
| `category_id` | INTEGER | no | Category key. |
| `name_en` | VARCHAR | no | English category name. |
| `name_th` | VARCHAR | no | Thai category name. |
| `parent_category_id` | INTEGER | yes | Parent category; NULL for top-level categories. |

### products

Products on the catalogue; each belongs to a leaf category.

PK: `product_id`; FK: `category_id` -> `product_categories.category_id`

| column | type | nullable | description |
|---|---|---|---|
| `product_id` | INTEGER | no | Product key. |
| `product_name` | VARCHAR | no | Product name. |
| `category_id` | INTEGER | no | Leaf category of the product. |
| `list_price` | DECIMAL(10,2) | no | List price in THB. |
| `cost_price` | DECIMAL(10,2) | no | Unit cost in THB. |
| `is_active` | BOOLEAN | no | True while the product is on sale. |

### customers

Synthetic customers (no real people).

PK: `customer_id`; FK: `province_id` -> `provinces.province_id`

| column | type | nullable | description |
|---|---|---|---|
| `customer_id` | INTEGER | no | Customer key. |
| `customer_code` | VARCHAR | no | Synthetic customer code. |
| `email` | VARCHAR | no | Placeholder email at example.com. |
| `province_id` | INTEGER | no | Home province of the customer. |
| `segment` | VARCHAR | no | Customer segment: retail, wholesale or vip. |
| `signup_date` | DATE | no | Sign-up date. |
| `is_test` | BOOLEAN | no | True for internal test accounts. |

### campaigns

Marketing campaigns with a date window.

PK: `campaign_id`

| column | type | nullable | description |
|---|---|---|---|
| `campaign_id` | INTEGER | no | Campaign key. |
| `campaign_name` | VARCHAR | no | Campaign name. |
| `channel` | VARCHAR | no | Marketing channel: email, social, search or display. |
| `start_date` | DATE | no | First day of the campaign (inclusive). |
| `end_date` | DATE | no | Last day of the campaign (inclusive). |
| `budget` | DECIMAL(12,2) | no | Budget in THB. |

### calendar

Calendar dimension, one row per day (Asia/Bangkok dates).

PK: `date_key`

| column | type | nullable | description |
|---|---|---|---|
| `date_key` | DATE | no | Calendar date. |
| `year` | INTEGER | no | Calendar year. |
| `month` | INTEGER | no | Month number 1-12. |
| `day` | INTEGER | no | Day of month. |
| `quarter` | INTEGER | no | Quarter 1-4. |
| `iso_dow` | INTEGER | no | ISO weekday, 1 = Monday. |
| `is_weekend` | BOOLEAN | no | True on Saturday and Sunday. |
| `is_holiday` | BOOLEAN | no | True on listed public holidays. |

### orders

Customer orders; one row per order.

PK: `order_id`; FK: `customer_id` -> `customers.customer_id`; FK: `province_id` -> `provinces.province_id`; FK: `campaign_id` -> `campaigns.campaign_id`

| column | type | nullable | description |
|---|---|---|---|
| `order_id` | INTEGER | no | Order key. |
| `customer_id` | INTEGER | no | Customer who placed the order. |
| `province_id` | INTEGER | no | Shipping province of the order. |
| `campaign_id` | INTEGER | yes | Attributed campaign; NULL when none. |
| `order_ts` | TIMESTAMP | no | Order time stored in UTC. |
| `status` | VARCHAR | no | completed, cancelled or pending. |
| `channel` | VARCHAR | no | Sales channel: web, app or store. |
| `total_amount` | DECIMAL(12,2) | no | Order total in THB, net of line discounts. |

### order_items

Order lines; one row per product per order.

PK: `order_item_id`; FK: `order_id` -> `orders.order_id`; FK: `product_id` -> `products.product_id`

| column | type | nullable | description |
|---|---|---|---|
| `order_item_id` | INTEGER | no | Order line key. |
| `order_id` | INTEGER | no | Parent order. |
| `product_id` | INTEGER | no | Product sold. |
| `quantity` | INTEGER | no | Units sold. |
| `unit_price` | DECIMAL(10,2) | no | Price per unit in THB. |
| `discount` | DECIMAL(10,2) | yes | Line discount in THB; NULL means no discount. |

### payments

Payment attempts against orders.

PK: `payment_id`; FK: `order_id` -> `orders.order_id`

| column | type | nullable | description |
|---|---|---|---|
| `payment_id` | INTEGER | no | Payment key. |
| `order_id` | INTEGER | no | Order being paid. |
| `method` | VARCHAR | no | card, qr, bank_transfer or cod. |
| `amount` | DECIMAL(12,2) | no | Amount in THB. |
| `paid_ts` | TIMESTAMP | no | Payment time stored in UTC. |
| `status` | VARCHAR | no | captured or failed. |

### returns

Returned order lines with refunds.

PK: `return_id`; FK: `order_item_id` -> `order_items.order_item_id`; FK: `order_id` -> `orders.order_id`

| column | type | nullable | description |
|---|---|---|---|
| `return_id` | INTEGER | no | Return key. |
| `order_item_id` | INTEGER | no | Returned order line. |
| `order_id` | INTEGER | no | Order of the returned line. |
| `return_ts` | TIMESTAMP | no | Return time stored in UTC. |
| `reason` | VARCHAR | no | damaged, wrong_item, not_needed or late. |
| `refund_amount` | DECIMAL(12,2) | no | Refund in THB. |

### daily_fact_sales

Pre-aggregated completed-order sales per Asia/Bangkok day and shipping province.

PK: `sales_date, province_id`; FK: `sales_date` -> `calendar.date_key`; FK: `province_id` -> `provinces.province_id`

| column | type | nullable | description |
|---|---|---|---|
| `sales_date` | DATE | no | Asia/Bangkok business date. |
| `province_id` | INTEGER | no | Shipping province. |
| `order_count` | INTEGER | no | Completed orders that day. |
| `units` | INTEGER | no | Units sold that day. |
| `revenue` | DECIMAL(14,2) | no | Sum of total_amount of completed orders. |

### inventory_snapshots

Weekly stock snapshot, one row per product per Monday.

PK: `snapshot_date, product_id`; FK: `product_id` -> `products.product_id`

| column | type | nullable | description |
|---|---|---|---|
| `snapshot_date` | DATE | no | Snapshot Monday. |
| `product_id` | INTEGER | no | Product in stock. |
| `on_hand` | INTEGER | no | Units on hand. |
| `reorder_level` | INTEGER | no | Reorder threshold in units. |

## Business rules

- `tz`: orders.order_ts is stored in UTC; business days and months use Asia/Bangkok (UTC+7).
- `sales`: Sales means SUM(orders.total_amount) over orders with status 'completed'.
- `province_names`: provinces.name_en and name_th hold the stored province names; match the stored value.
- `exclude_test`: Exclude test accounts (customers.is_test = true).
- `active_only`: Only products with is_active = true are on sale.
- `captured_only`: Only payments with status 'captured' count as money received.
- `campaign_null`: orders.campaign_id is NULL when no campaign applies; keep campaigns that have no orders.
- `ship_province`: Province of an order means orders.province_id (shipping), not the customer's home province.
- `category_names`: product_categories.name_en and name_th hold the stored category names; match the stored value.
- `snapshots`: inventory_snapshots has one row per product per Monday.
- `net_lines`: Line revenue is quantity * unit_price minus discount; a NULL discount means 0.
- `inclusive_dates`: Date ranges and campaign windows include both end dates.
- `refunds`: Refund totals use returns.refund_amount, bucketed by Asia/Bangkok return date.
