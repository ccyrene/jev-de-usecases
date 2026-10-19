# พจนานุกรมข้อมูล E06 (ภาษาไทย)

ข้อมูลสังเคราะห์เท่านั้น ตัวระบุเป็น ASCII ส่วนชื่อจังหวัดและหมวดหมู่เป็นค่าข้อมูลที่เก็บเป็นภาษาอังกฤษและภาษาไทย

## ตาราง

### provinces

จังหวัดของประเทศไทย (ข้อมูลภูมิศาสตร์สาธารณะ 77 แถว)

PK: `province_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `province_id` | INTEGER | no | รหัสจังหวัด |
| `name_en` | VARCHAR | no | ชื่อจังหวัดภาษาอังกฤษ (ค่าข้อมูลที่เก็บไว้) |
| `name_th` | VARCHAR | no | ชื่อจังหวัดภาษาไทย (ค่าข้อมูลที่เก็บไว้) |
| `region` | VARCHAR | no | ภูมิภาค: north, northeast, central, east, west หรือ south |

### product_categories

ต้นไม้หมวดหมู่สินค้า 2 ระดับ

PK: `category_id`; FK: `parent_category_id` -> `product_categories.category_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `category_id` | INTEGER | no | รหัสหมวดหมู่ |
| `name_en` | VARCHAR | no | ชื่อหมวดหมู่ภาษาอังกฤษ |
| `name_th` | VARCHAR | no | ชื่อหมวดหมู่ภาษาไทย |
| `parent_category_id` | INTEGER | yes | หมวดหมู่แม่ เป็น NULL สำหรับหมวดหมู่ระดับบนสุด |

### products

สินค้าในแคตตาล็อก แต่ละรายการอยู่ในหมวดหมู่ระดับย่อย

PK: `product_id`; FK: `category_id` -> `product_categories.category_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `product_id` | INTEGER | no | รหัสสินค้า |
| `product_name` | VARCHAR | no | ชื่อสินค้า |
| `category_id` | INTEGER | no | หมวดหมู่ย่อยของสินค้า |
| `list_price` | DECIMAL(10,2) | no | ราคาตั้งต้นเป็นบาท |
| `cost_price` | DECIMAL(10,2) | no | ต้นทุนต่อหน่วยเป็นบาท |
| `is_active` | BOOLEAN | no | เป็น true เมื่อสินค้ายังวางขาย |

### customers

ลูกค้าสังเคราะห์ (ไม่ใช่บุคคลจริง)

PK: `customer_id`; FK: `province_id` -> `provinces.province_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `customer_id` | INTEGER | no | รหัสลูกค้า |
| `customer_code` | VARCHAR | no | รหัสลูกค้าสังเคราะห์ |
| `email` | VARCHAR | no | อีเมลสมมติที่ example.com |
| `province_id` | INTEGER | no | จังหวัดที่อยู่ของลูกค้า |
| `segment` | VARCHAR | no | กลุ่มลูกค้า: retail, wholesale หรือ vip |
| `signup_date` | DATE | no | วันที่สมัคร |
| `is_test` | BOOLEAN | no | เป็น true สำหรับบัญชีทดสอบภายใน |

### campaigns

แคมเปญการตลาดที่มีช่วงวันที่

PK: `campaign_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `campaign_id` | INTEGER | no | รหัสแคมเปญ |
| `campaign_name` | VARCHAR | no | ชื่อแคมเปญ |
| `channel` | VARCHAR | no | ช่องทางการตลาด: email, social, search หรือ display |
| `start_date` | DATE | no | วันแรกของแคมเปญ (รวมวันนี้) |
| `end_date` | DATE | no | วันสุดท้ายของแคมเปญ (รวมวันนี้) |
| `budget` | DECIMAL(12,2) | no | งบประมาณเป็นบาท |

### calendar

มิติปฏิทิน หนึ่งแถวต่อวัน (วันที่ตามเวลา Asia/Bangkok)

PK: `date_key`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `date_key` | DATE | no | วันที่ในปฏิทิน |
| `year` | INTEGER | no | ปี ค.ศ. |
| `month` | INTEGER | no | เดือน 1-12 |
| `day` | INTEGER | no | วันที่ในเดือน |
| `quarter` | INTEGER | no | ไตรมาส 1-4 |
| `iso_dow` | INTEGER | no | วันในสัปดาห์แบบ ISO โดย 1 = วันจันทร์ |
| `is_weekend` | BOOLEAN | no | เป็น true ในวันเสาร์และอาทิตย์ |
| `is_holiday` | BOOLEAN | no | เป็น true ในวันหยุดนักขัตฤกษ์ที่ระบุ |

### orders

คำสั่งซื้อของลูกค้า หนึ่งแถวต่อคำสั่งซื้อ

PK: `order_id`; FK: `customer_id` -> `customers.customer_id`; FK: `province_id` -> `provinces.province_id`; FK: `campaign_id` -> `campaigns.campaign_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `order_id` | INTEGER | no | รหัสคำสั่งซื้อ |
| `customer_id` | INTEGER | no | ลูกค้าที่สั่งซื้อ |
| `province_id` | INTEGER | no | จังหวัดปลายทางจัดส่งของคำสั่งซื้อ |
| `campaign_id` | INTEGER | yes | แคมเปญที่เป็นต้นทาง เป็น NULL เมื่อไม่มี |
| `order_ts` | TIMESTAMP | no | เวลาสั่งซื้อที่เก็บเป็น UTC |
| `status` | VARCHAR | no | completed, cancelled หรือ pending |
| `channel` | VARCHAR | no | ช่องทางขาย: web, app หรือ store |
| `total_amount` | DECIMAL(12,2) | no | ยอดรวมคำสั่งซื้อเป็นบาท หลังหักส่วนลดรายการ |

### order_items

รายการสินค้าในคำสั่งซื้อ หนึ่งแถวต่อสินค้าต่อคำสั่งซื้อ

PK: `order_item_id`; FK: `order_id` -> `orders.order_id`; FK: `product_id` -> `products.product_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `order_item_id` | INTEGER | no | รหัสรายการสินค้า |
| `order_id` | INTEGER | no | คำสั่งซื้อแม่ |
| `product_id` | INTEGER | no | สินค้าที่ขาย |
| `quantity` | INTEGER | no | จำนวนหน่วยที่ขาย |
| `unit_price` | DECIMAL(10,2) | no | ราคาต่อหน่วยเป็นบาท |
| `discount` | DECIMAL(10,2) | yes | ส่วนลดของรายการเป็นบาท NULL หมายถึงไม่มีส่วนลด |

### payments

ความพยายามชำระเงินของคำสั่งซื้อ

PK: `payment_id`; FK: `order_id` -> `orders.order_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `payment_id` | INTEGER | no | รหัสการชำระเงิน |
| `order_id` | INTEGER | no | คำสั่งซื้อที่ชำระ |
| `method` | VARCHAR | no | card, qr, bank_transfer หรือ cod |
| `amount` | DECIMAL(12,2) | no | จำนวนเงินเป็นบาท |
| `paid_ts` | TIMESTAMP | no | เวลาชำระเงินที่เก็บเป็น UTC |
| `status` | VARCHAR | no | captured หรือ failed |

### returns

รายการสินค้าที่ถูกคืนพร้อมการคืนเงิน

PK: `return_id`; FK: `order_item_id` -> `order_items.order_item_id`; FK: `order_id` -> `orders.order_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `return_id` | INTEGER | no | รหัสการคืนสินค้า |
| `order_item_id` | INTEGER | no | รายการสินค้าที่ถูกคืน |
| `order_id` | INTEGER | no | คำสั่งซื้อของรายการที่ถูกคืน |
| `return_ts` | TIMESTAMP | no | เวลาคืนสินค้าที่เก็บเป็น UTC |
| `reason` | VARCHAR | no | damaged, wrong_item, not_needed หรือ late |
| `refund_amount` | DECIMAL(12,2) | no | ยอดคืนเงินเป็นบาท |

### daily_fact_sales

ยอดขายสรุปของคำสั่งซื้อที่สำเร็จรายวัน (ตามเวลา Asia/Bangkok) และจังหวัดปลายทาง

PK: `sales_date, province_id`; FK: `sales_date` -> `calendar.date_key`; FK: `province_id` -> `provinces.province_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `sales_date` | DATE | no | วันที่ทางธุรกิจตามเวลา Asia/Bangkok |
| `province_id` | INTEGER | no | จังหวัดปลายทางจัดส่ง |
| `order_count` | INTEGER | no | จำนวนคำสั่งซื้อที่สำเร็จในวันนั้น |
| `units` | INTEGER | no | จำนวนหน่วยที่ขายในวันนั้น |
| `revenue` | DECIMAL(14,2) | no | ผลรวม total_amount ของคำสั่งซื้อที่สำเร็จ |

### inventory_snapshots

ภาพสต็อกรายสัปดาห์ หนึ่งแถวต่อสินค้าต่อวันจันทร์

PK: `snapshot_date, product_id`; FK: `product_id` -> `products.product_id`

| คอลัมน์ | ชนิด | ว่างได้ | คำอธิบาย |
|---|---|---|---|
| `snapshot_date` | DATE | no | วันจันทร์ของภาพสต็อก |
| `product_id` | INTEGER | no | สินค้าในสต็อก |
| `on_hand` | INTEGER | no | จำนวนหน่วยคงเหลือ |
| `reorder_level` | INTEGER | no | จุดสั่งซื้อเพิ่มเป็นจำนวนหน่วย |

## กฎทางธุรกิจ

- `tz`: orders.order_ts เก็บเป็น UTC ส่วนวันและเดือนทางธุรกิจใช้เวลา Asia/Bangkok (UTC+7)
- `sales`: ยอดขายคือ SUM(orders.total_amount) ของคำสั่งซื้อที่มี status เป็น 'completed'
- `province_names`: provinces.name_en และ name_th เก็บชื่อจังหวัดไว้ ต้องเทียบกับค่าที่เก็บจริง
- `exclude_test`: ไม่รวมบัญชีทดสอบ (customers.is_test = true)
- `active_only`: สินค้าที่วางขายคือสินค้าที่ is_active = true เท่านั้น
- `captured_only`: เฉพาะการชำระเงินที่ status เป็น 'captured' เท่านั้นที่นับเป็นเงินที่ได้รับ
- `campaign_null`: orders.campaign_id เป็น NULL เมื่อไม่มีแคมเปญ และต้องแสดงแคมเปญที่ไม่มีคำสั่งซื้อด้วย
- `ship_province`: จังหวัดของคำสั่งซื้อหมายถึง orders.province_id (จัดส่ง) ไม่ใช่จังหวัดที่อยู่ของลูกค้า
- `category_names`: product_categories.name_en และ name_th เก็บชื่อหมวดหมู่ไว้ ต้องเทียบกับค่าที่เก็บจริง
- `snapshots`: inventory_snapshots มีหนึ่งแถวต่อสินค้าต่อวันจันทร์
- `net_lines`: รายได้ต่อรายการคือ quantity * unit_price ลบ discount โดย discount ที่เป็น NULL คือ 0
- `inclusive_dates`: ช่วงวันที่และช่วงแคมเปญรวมทั้งวันเริ่มและวันสิ้นสุด
- `refunds`: ยอดคืนเงินใช้ returns.refund_amount และจัดกลุ่มตามวันที่คืนสินค้าตามเวลา Asia/Bangkok
