"""E05 - PII / sensitive data classification on PostgreSQL + JSONB (PLAN.md section 8).

Each scenario is one candidate field (a scalar column or a JSONB key) in a synthetic schema of
about 10,000 rows. The model sees only the field's versioned catalog metadata (Thai or English)
and 2-5 minimized, partially masked samples. Gold comes from how the field was constructed and
from the policy below, never from a regex or a model.

Data-handling policy ``e05-policy-v1`` (pre-registered)
-------------------------------------------------------
Truth flags (independent multi-label set, kept in ``facts["_internal"]["truth_flags"]`` for
ancillary evaluation): any of ``sensitive_personal``, ``direct_identifier``, ``quasi_identifier``.
An empty set means the field is not personal data.

* Single primary class (``data_class``): ``sensitive_personal`` > ``direct_identifier`` >
  ``quasi_identifier`` > ``non_personal``. A field with several flags takes the highest. When the
  catalog and samples are insufficient to decide (evidence_sufficiency ``insufficient``) the gold
  is ``unknown`` even if the generator knows the hidden truth. ``ambiguous`` scenarios have thin
  metadata but keep the constructed class.
* Direct identifier: identifies one person alone (email, national or government ID, phone, name,
  full address, exact date of birth, staff number). Quasi identifier: identifies only in
  combination (birth year-month, postcode, province, demographic bands, IP address, tokenized or
  hashed IDs, persistent device UUIDs). Sensitive: health, belief, union, biometric data,
  including inferred health attributes. Non-personal: random transaction/request ids, business
  entity names, catalog and UI values, load-test data under a person-sounding column name.
* Handling tier (``handling_tier``): sensitive 3; direct 3 for government-issued IDs else 2;
  quasi 2 for linkable-by-lookup values (unsalted hash, IP address) else 1; unknown 2 (restricted
  until reviewed); non-personal 0 when public catalog or technical values else 1.
* ``requires_masking_review``: true when the tier is 2 or 3.
* Tokenized IDs (vault held elsewhere) and keyed hashes are quasi; an unsalted hash of a contact
  value can be re-derived by dictionary, so it is quasi at tier 2.

Synthetic-data safety
---------------------
Emails use example.com / example.org / example.net. Phones use ranges reserved for fiction:
NANP +1 xxx 555-0100..0199, UK Ofcom drama mobile +44 7700 900000..900999 and London drama
+44 20 7946 0000..0999. Names come from the fictional pools below (surnames are obvious
placeholders). Every 13-digit Thai-ID lookalike FAILS the mod-11 check-digit rule, government-like
ids carry the unassigned prefix ``XZ``, IPs are RFC 5737 documentation ranges, postcodes start 00
and companies are "Mock/Sample/Demo" names. Thai-script sample values are masked entirely because
an English state may not contain Thai letters.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import ScenarioSpec
from debench.evidence.postgres_pii import PiiSeed, PostgresPiiBackend, mask_value, sample_row_ids

CASE = "E05"
BACKEND = "postgres"
CASE_OFFSET = 505
POLICY_VERSION = "e05-policy-v1"
LABEL_SOURCE = f"scenario_construction+{POLICY_VERSION}"
PRECEDENCE = ("sensitive_personal", "direct_identifier", "quasi_identifier")
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
ROW_COUNTS = {
    "crm.customer": 3000,
    "hr.employee": 800,
    "proc.supplier": 600,
    "web.web_event": 4600,
    "audit.audit": 1000,
}
SCENARIOS = 300

# --- fictional pools -------------------------------------------------------------------------------

GIVEN_EN = ("Alex", "Brinn", "Calla", "Dorian", "Elowen", "Fenn", "Garrick", "Hollis", "Isolde", "Jarrow",
            "Kestrel", "Lyra", "Marlo", "Nerys", "Orlin", "Perrin", "Quill", "Rowan", "Sable", "Tessa")  # fmt: skip
SURNAME_EN = ("Mockwright", "Testley", "Samplewood", "Fakeford", "Exampleton", "Dummerly", "Placeholt",
              "Stubbins", "Trialhurst", "Demoworth")  # fmt: skip
GIVEN_TH = ("ฟ้าใส", "ดาวเรือง", "ลมหนาว", "แสงเช้า", "ชายเขา", "น้ำใจ", "พลอยใส", "รุ้งงาม", "มะลิลา", "ตะวันฉาย")
SURNAME_TH = ("ทดสอบ", "จำลอง", "สมมติ", "ตัวอย่าง", "สาธิต", "ทดลอง")
COMPANY_A = ("Mock", "Sample", "Demo", "Test", "Fake")
COMPANY_B = ("works", "trading", "logistics", "supply", "foods")
PROVINCES = ("Northvale", "Eastmere", "Southfold", "Westhaven", "Centralmoor")
CONDITIONS = ("hypertension_risk_high", "diabetes_risk_medium", "sleep_disorder_suspected",
              "anxiety_signal_moderate", "asthma_risk_low", "migraine_pattern_detected")  # fmt: skip
SICK_REASONS = ("influenza", "back pain", "migraine", "dental surgery", "stomach infection", "sprained ankle")
SENTENCES = ("Asked to change the delivery slot to the afternoon.", "Follow up needed, {name} will call back.",
             "Duplicate ticket merged with the earlier request.", "Wants a refund for the damaged item.",
             "Reviewed by {name} and approved without changes.", "No issue found after the second check.")  # fmt: skip
PHONE_PATTERNS = (
    re.compile(r"^\+1 202 555 01\d\d$"),
    re.compile(r"^\+44 7700 900\d{3}$"),
    re.compile(r"^\+44 20 7946 0\d{3}$"),
)
IP_PREFIXES = ("192.0.2", "198.51.100", "203.0.113")


# --- Thai national ID checksum ------------------------------------------------------------------------


def thai_id_check_digit(first12: str) -> int:
    """Thai 13-digit national ID: check = (11 - sum(d_i * (13 - i)) mod 11) mod 10."""
    return (11 - sum(int(c) * (13 - i) for i, c in enumerate(first12)) % 11) % 10


def is_valid_thai_id(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) == 13 and int(digits[12]) == thai_id_check_digit(digits[:12])


def fake_thai_id(r: random.Random, dashed: bool = False) -> str:
    """13 digits whose check digit is deliberately wrong (never passes ``is_valid_thai_id``)."""
    body = str(r.randrange(1, 9)) + "".join(str(r.randrange(10)) for _ in range(11))
    wrong = (thai_id_check_digit(body) + r.randrange(1, 10)) % 10
    d = body + str(wrong)
    return f"{d[0]}-{d[1:5]}-{d[5:10]}-{d[10:12]}-{d[12]}" if dashed else d


# --- archetypes --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Arche:
    key: str
    gen: str
    truth: tuple[str, ...]  # multi-label truth flags
    locations: tuple[str, ...]  # "schema.table:column" or "schema.table:attrs.a.b"
    purpose: tuple[str, str]  # (en, th)
    lineage: tuple[str, str]
    meta: str = "full"  # full | partial (no lineage) | none
    suff: str = "sufficient"  # sufficient | ambiguous | insufficient
    sqltype: str = "text"
    gov: bool = False
    strong: bool = False
    public: bool = False
    tags: tuple[str, ...] = ()


def primary_class(truth: tuple[str, ...] | list[str], suff: str) -> str:
    if suff == "insufficient":
        return "unknown"
    for cls in PRECEDENCE:
        if cls in truth:
            return cls
    return "non_personal"


def handling_tier(primary: str, gov: bool, strong: bool, public: bool) -> int:
    if primary == "sensitive_personal":
        return 3
    if primary == "direct_identifier":
        return 3 if gov else 2
    if primary == "quasi_identifier":
        return 2 if strong else 1
    if primary == "unknown":
        return 2
    return 0 if public else 1


def gold_for(a: Arche) -> dict[str, Any]:
    primary = primary_class(a.truth, a.suff)
    tier = handling_tier(primary, a.gov, a.strong, a.public)
    return {"data_class": primary, "handling_tier": tier, "requires_masking_review": tier >= 2}


S, D, Q = "sensitive_personal", "direct_identifier", "quasi_identifier"


def _partial(base: Arche, key: str, locations: tuple[str, ...]) -> Arche:
    return dataclasses.replace(
        base, key=key, locations=locations, meta="partial", suff="ambiguous", tags=(*base.tags, "ambiguous")
    )


_EMAIL = Arche(
    "email", "email", (D,),
    ("crm.customer:email", "crm.customer:attrs.contact.email_secondary", "hr.employee:work_email",
     "proc.supplier:attrs.contact.email", "web.web_event:attrs.form.email_input", "audit.audit:attrs.actor.email"),
    ("Contact email address of the record owner, used for notifications.",
     "อีเมลติดต่อของเจ้าของระเบียน ใช้สำหรับส่งการแจ้งเตือน"),
    ("Entered by the user in the sign-up or contact form.",
     "ผู้ใช้กรอกเองในแบบฟอร์มสมัครสมาชิกหรือแบบฟอร์มติดต่อ"),
)  # fmt: skip
_THAI_ID = Arche(
    "thai_national_id", "thai_id", (D,),
    ("crm.customer:national_id", "hr.employee:attrs.gov.citizen_id", "proc.supplier:attrs.owner.id_card_no",
     "crm.customer:attrs.kyc.id_number"),
    ("National identification number captured during identity verification.",
     "เลขประจำตัวประชาชนที่เก็บไว้ระหว่างการยืนยันตัวตน"),
    ("Copied from the KYC form, one value per person.", "คัดลอกจากแบบฟอร์ม KYC หนึ่งค่าต่อหนึ่งคน"),
    gov=True,
)  # fmt: skip
_PHONE = Arche(
    "phone_intl", "phone", (D,),
    ("crm.customer:phone", "crm.customer:attrs.contact.mobile", "hr.employee:emergency_phone",
     "proc.supplier:contact_phone", "web.web_event:attrs.form.phone"),
    ("Phone number in international format used to reach the person.",
     "หมายเลขโทรศัพท์รูปแบบสากลที่ใช้ติดต่อบุคคลนั้น"),
    ("Typed in by the user and normalized to international format.",
     "ผู้ใช้กรอกเอง แล้วระบบแปลงเป็นรูปแบบสากล"),
)  # fmt: skip
_GOVID = Arche(
    "government_id", "govid", (D,),
    ("crm.customer:passport_no", "hr.employee:attrs.gov.work_permit_no", "crm.customer:attrs.kyc.driver_license_no"),
    ("Government-issued document number such as a passport or licence.",
     "หมายเลขเอกสารที่ทางราชการออกให้ เช่น หนังสือเดินทางหรือใบอนุญาต"),
    ("Keyed in by staff from a scanned document.", "เจ้าหน้าที่คีย์จากเอกสารที่สแกนไว้"),
    gov=True,
)  # fmt: skip
_NAME_EN = Arche(
    "name_en", "name_en", (D,),
    ("crm.customer:full_name", "hr.employee:full_name", "proc.supplier:contact_person",
     "web.web_event:attrs.form.name_input"),
    ("Full name of a person written in Latin letters.", "ชื่อและนามสกุลของบุคคลที่เขียนด้วยอักษรละติน"),
    ("Entered by the user or copied from the HR form.", "ผู้ใช้กรอกเอง หรือคัดลอกจากแบบฟอร์มของฝ่ายบุคคล"),
)  # fmt: skip
_NAME_TH = Arche(
    "name_th", "name_th", (D,),
    ("crm.customer:full_name_th", "hr.employee:attrs.profile.name_local", "proc.supplier:attrs.contact.person_th"),
    ("Full name of a person written in Thai script.", "ชื่อและนามสกุลของบุคคลที่เขียนด้วยอักษรไทย"),
    ("Entered by the user or copied from the HR form.", "ผู้ใช้กรอกเอง หรือคัดลอกจากแบบฟอร์มของฝ่ายบุคคล"),
)  # fmt: skip
_ADDRESS = Arche(
    "shipping_address", "address", (D,),
    ("crm.customer:shipping_address", "crm.customer:attrs.shipping.line1", "web.web_event:attrs.checkout.delivery_address"),
    ("Delivery address of the recipient, with street and house number.",
     "ที่อยู่จัดส่งของผู้รับ รวมถึงถนนและเลขที่บ้าน"),
    ("Typed at checkout and stored as one free-form line.", "กรอกตอนชำระเงินและเก็บเป็นข้อความบรรทัดเดียว"),
)  # fmt: skip
_BIRTHDAY = Arche(
    "birthday_exact", "birth_date", (D,),
    ("crm.customer:birth_date", "hr.employee:date_of_birth", "crm.customer:attrs.profile.dob_full"),
    ("Exact date of birth of the person.", "วันเดือนปีเกิดที่แน่นอนของบุคคล"),
    ("Entered in the profile form as a full date.", "กรอกในแบบฟอร์มโปรไฟล์เป็นวันที่แบบเต็ม"),
    sqltype="date",
)  # fmt: skip
_MISLEAD_EMAIL = Arche(
    "misleading_name_email", "email", (D,),
    ("crm.customer:ref_code", "proc.supplier:attrs.misc.contact_ref", "web.web_event:attrs.form.note_key"),
    ("Reference value copied from the sign-up form.", "ค่าอ้างอิงที่คัดลอกมาจากแบบฟอร์มสมัครสมาชิก"),
    ("Holds the email the user typed into the contact box.", "เก็บอีเมลที่ผู้ใช้พิมพ์ลงในช่องติดต่อ"),
    tags=("hard_negative",),
)  # fmt: skip
_EMPLOYEE_NO = Arche(
    "employee_number", "employee_no", (D,),
    ("hr.employee:employee_no", "audit.audit:attrs.actor.staff_no"),
    ("Staff number that identifies an employee inside the company.",
     "รหัสพนักงานที่ใช้ระบุตัวพนักงานภายในบริษัท"),
    ("Issued by HR when the employee is hired.", "ฝ่ายบุคคลออกให้เมื่อรับพนักงานเข้าทำงาน"),
)  # fmt: skip

_HEALTH = Arche(
    "health_inferred", "condition", (S,),
    ("crm.customer:inferred_condition", "crm.customer:attrs.segments.health_risk_band",
     "web.web_event:attrs.profile.wellness_inferred", "crm.customer:attrs.ml.chronic_flag_pred"),
    ("Health condition inferred by a model from purchase behaviour.",
     "ภาวะสุขภาพที่โมเดลอนุมานจากพฤติกรรมการซื้อ"),
    ("Written nightly by the segmentation job, not given by the person.",
     "งานแบ่งกลุ่มลูกค้าเขียนให้ทุกคืน ไม่ได้ให้ข้อมูลโดยบุคคลนั้นเอง"),
)  # fmt: skip
_SICK = Arche(
    "sick_leave_reason", "sick_reason", (S,),
    ("hr.employee:sick_leave_reason", "hr.employee:attrs.leave.medical_note", "audit.audit:attrs.leave.reason_text"),
    ("Medical reason given for a sick-leave request.", "เหตุผลทางการแพทย์ที่ระบุในใบลาป่วย"),
    ("Typed by the employee in the leave form.", "พนักงานพิมพ์ในแบบฟอร์มการลา"),
)  # fmt: skip
_HEALTH_NOTE = Arche(
    "health_note_with_name", "health_note", (D, S),
    ("audit.audit:attrs.claim.member_note", "crm.customer:attrs.claim.health_note"),
    ("Claim note naming the member and the treated condition.", "บันทึกเคลมที่ระบุชื่อสมาชิกและอาการที่รักษา"),
    ("Copied by claim agents from the medical certificate.", "เจ้าหน้าที่เคลมคัดลอกจากใบรับรองแพทย์"),
)  # fmt: skip
_RELIGION = Arche(
    "religion_declared", "religion", (S,),
    ("hr.employee:attrs.profile.religion_declared", "crm.customer:attrs.profile.faith"),
    ("Religion declared by the person for holiday planning.", "ศาสนาที่บุคคลแจ้งไว้เพื่อใช้วางแผนวันหยุด"),
    ("Optional field in the profile form.", "ช่องไม่บังคับในแบบฟอร์มโปรไฟล์"),
)  # fmt: skip
_BIOMETRIC = Arche(
    "biometric_ref", "biometric", (S,),
    ("hr.employee:attrs.access.face_template_ref", "audit.audit:attrs.door.fingerprint_ref"),
    ("Reference to the biometric template in the access-control system.",
     "รหัสอ้างอิงแม่แบบชีวมาตรในระบบควบคุมการเข้าออก"),
    ("Created at enrolment by the door controller.", "ตัวควบคุมประตูสร้างขึ้นตอนลงทะเบียน"),
)  # fmt: skip
_UNION = Arche(
    "union_membership", "union", (S,),
    ("hr.employee:attrs.profile.union_member",),
    ("Whether the employee is a trade union member.", "ระบุว่าพนักงานเป็นสมาชิกสหภาพแรงงานหรือไม่"),
    ("Declared by the employee for payroll deduction.", "พนักงานแจ้งเพื่อใช้หักเงินเดือน"),
)  # fmt: skip

_BIRTH_YM = Arche(
    "birth_year_month", "birth_ym", (Q,),
    ("crm.customer:birth_month", "crm.customer:attrs.profile.birth_ym", "hr.employee:attrs.profile.birth_year_month"),
    ("Birth year and month only, without the day.", "เฉพาะปีและเดือนเกิด ไม่มีวันที่"),
    ("Derived from the profile form by dropping the day.", "แปลงจากแบบฟอร์มโปรไฟล์โดยตัดวันที่ทิ้ง"),
    tags=("hard_negative",),
)  # fmt: skip
_POSTCODE = Arche(
    "postcode", "postcode", (Q,),
    ("crm.customer:shipping_postcode", "web.web_event:attrs.geo.postcode"),
    ("Postal code of the delivery area.", "รหัสไปรษณีย์ของพื้นที่จัดส่ง"),
    ("Split from the checkout address.", "แยกออกมาจากที่อยู่ตอนชำระเงิน"),
    tags=("hard_negative",),
)  # fmt: skip
_PROVINCE = Arche(
    "province", "province", (Q,),
    ("crm.customer:attrs.shipping.province", "web.web_event:attrs.geo.province"),
    ("Province or region of the person.", "จังหวัดหรือภูมิภาคของบุคคล"),
    ("Mapped from the postal code by the geo service.", "บริการภูมิศาสตร์แปลงมาจากรหัสไปรษณีย์"),
    tags=("hard_negative",),
)  # fmt: skip
_ACCOUNT_TOKEN = Arche(
    "account_token", "account_token", (Q,),
    ("crm.customer:account_token", "web.web_event:attrs.session.account_token", "audit.audit:attrs.actor.account_token"),
    ("Token that stands in for the account number.", "โทเค็นที่ใช้แทนหมายเลขบัญชี"),
    ("Issued by the token vault, which another team operates.", "ออกโดย token vault ซึ่งทีมอื่นเป็นผู้ดูแล"),
    tags=("hard_negative",),
)  # fmt: skip
_HASH = Arche(
    "hash_unsalted", "email_hash", (Q,),
    ("crm.customer:email_sha256", "web.web_event:attrs.ids.email_hash", "audit.audit:attrs.actor.phone_hash"),
    ("Hash of a contact value, used to match records across systems.",
     "ค่าแฮชของข้อมูลติดต่อ ใช้จับคู่ระเบียนข้ามระบบ"),
    ("Computed in the ingestion job with plain SHA-256 and no secret key.",
     "คำนวณในงานนำเข้าข้อมูลด้วย SHA-256 ธรรมดา โดยไม่มีรหัสลับ"),
    strong=True, tags=("hard_negative",),
)  # fmt: skip
_HMAC = Arche(
    "hash_keyed", "hmac", (Q,),
    ("web.web_event:attrs.ids.user_hmac", "crm.customer:attrs.ids.loyalty_hmac"),
    ("Keyed hash of the user id, used for analytics joins.", "แฮชแบบมีกุญแจของรหัสผู้ใช้ ใช้เชื่อมข้อมูลวิเคราะห์"),
    ("Computed with HMAC and a secret key that analysts cannot read.",
     "คำนวณด้วย HMAC และกุญแจลับที่นักวิเคราะห์อ่านไม่ได้"),
    tags=("hard_negative", "negation"),
)  # fmt: skip
_DEVICE_UUID = Arche(
    "device_uuid", "uuid", (Q,),
    ("web.web_event:attrs.device.install_uuid", "crm.customer:attrs.device.primary_device_uuid"),
    ("Identifier generated once per app installation and kept across sessions.",
     "รหัสที่สร้างครั้งเดียวต่อการติดตั้งแอปและคงอยู่ข้ามเซสชัน"),
    ("Created by the mobile app and sent with every event.", "แอปมือถือสร้างขึ้นและส่งมาพร้อมทุกเหตุการณ์"),
    tags=("hard_negative",),
)  # fmt: skip
_DEMOGRAPHIC = Arche(
    "demographic_band", "demographic", (Q,),
    ("crm.customer:age_band", "hr.employee:job_grade", "hr.employee:department_code", "crm.customer:attrs.profile.gender"),
    ("Coarse demographic or job attribute used for reporting.", "ข้อมูลประชากรหรือตำแหน่งงานแบบหยาบที่ใช้ในการรายงาน"),
    ("Grouped into bands by the reporting job.", "งานรายงานจัดให้เป็นช่วงหรือกลุ่ม"),
)  # fmt: skip
_IP = Arche(
    "ip_address", "ip", (Q,),
    ("web.web_event:ip_addr", "audit.audit:attrs.actor.ip"),
    ("IP address of the client that made the request.", "ที่อยู่ IP ของเครื่องที่ส่งคำขอ"),
    ("Logged by the web gateway for each request.", "เกตเวย์เว็บบันทึกไว้ทุกคำขอ"),
    strong=True,
)  # fmt: skip

_TXN = Arche(
    "transaction_id", "txn", (),
    ("web.web_event:attrs.txn.transaction_id", "audit.audit:txn_id", "proc.supplier:attrs.po.transaction_ref"),
    ("Random identifier of a payment transaction.", "รหัสสุ่มของธุรกรรมการชำระเงิน"),
    ("Generated by the payment service and not derived from any person.",
     "บริการชำระเงินสร้างขึ้นเอง ไม่ได้มาจากข้อมูลของบุคคลใด"),
    tags=("hard_negative", "negation"),
)  # fmt: skip
_REQ_UUID = Arche(
    "request_uuid", "uuid", (),
    ("web.web_event:attrs.request.request_uuid", "audit.audit:attrs.trace.trace_uuid", "web.web_event:request_id"),
    ("Random UUID v4 created for each request or trace.", "UUID v4 แบบสุ่มที่สร้างขึ้นทุกคำขอหรือทุก trace"),
    ("Generated by the gateway per request and never reused.", "เกตเวย์สร้างต่อคำขอและไม่นำกลับมาใช้ซ้ำ"),
    tags=("hard_negative", "negation"),
)  # fmt: skip
_MISLEAD_CUSTOMER = Arche(
    "misleading_customer_id", "random_customer", (),
    ("web.web_event:customer_id", "audit.audit:customer_id", "proc.supplier:attrs.test.customer_id"),
    ("Customer reference column filled with random test values.",
     "คอลัมน์อ้างอิงลูกค้าที่ใส่ค่าสุ่มเพื่อใช้ทดสอบ"),
    ("Produced by the load-test generator, with no mapping to any customer.",
     "สร้างโดยตัวสร้างข้อมูลทดสอบโหลด ไม่มีการเชื่อมโยงกับลูกค้าจริง"),
    tags=("hard_negative", "negation"),
)  # fmt: skip
_COMPANY = Arche(
    "company_name", "company", (),
    ("proc.supplier:company_name", "proc.supplier:attrs.legal.entity_name"),
    ("Registered name of a supplier company.", "ชื่อนิติบุคคลที่จดทะเบียนของบริษัทผู้ขาย"),
    ("Taken from the company registry extract.", "นำมาจากข้อมูลทะเบียนนิติบุคคล"),
    tags=("hard_negative",),
)  # fmt: skip
_UI = Arche(
    "ui_setting", "ui", (),
    ("web.web_event:attrs.ui.theme", "web.web_event:attrs.browser.language"),
    ("Display setting chosen in the app.", "การตั้งค่าการแสดงผลที่เลือกในแอป"),
    ("Sent by the client with each event.", "ไคลเอนต์ส่งมาพร้อมแต่ละเหตุการณ์"),
    public=True,
)  # fmt: skip
_PAGE = Arche(
    "page_path", "page_path", (),
    ("web.web_event:attrs.page.path",),
    ("URL path of the page that was viewed.", "พาธ URL ของหน้าที่ถูกเปิดดู"),
    ("Logged by the web server with query strings removed.", "เว็บเซิร์ฟเวอร์บันทึกไว้โดยตัดค่า query string ออก"),
)  # fmt: skip
_SKU = Arche(
    "product_sku", "sku", (),
    ("web.web_event:attrs.cart.sku", "proc.supplier:attrs.catalog.sku"),
    ("Product SKU code from the public catalog.", "รหัส SKU ของสินค้าจากแค็ตตาล็อกสาธารณะ"),
    ("Loaded from the product master data.", "โหลดมาจากข้อมูลหลักของสินค้า"),
    public=True,
)  # fmt: skip
_CURRENCY = Arche(
    "currency_code", "currency", (),
    ("web.web_event:currency_code", "proc.supplier:attrs.po.currency"),
    ("ISO currency code of the order.", "รหัสสกุลเงินตามมาตรฐาน ISO ของคำสั่งซื้อ"),
    ("Set by the order system from the shop settings.", "ระบบคำสั่งซื้อกำหนดจากการตั้งค่าร้านค้า"),
    public=True,
)  # fmt: skip
_EVENT_TYPE = Arche(
    "event_type", "event_type", (),
    ("web.web_event:event_type", "audit.audit:action_type"),
    ("Name of the tracked event or action.", "ชื่อเหตุการณ์หรือการกระทำที่ถูกติดตามไว้"),
    ("Chosen from a fixed list in the tracking plan.", "เลือกจากรายการตายตัวในแผนการติดตามเหตุการณ์"),
    public=True,
)  # fmt: skip
_CART = Arche(
    "cart_item_count", "cart_count", (),
    ("web.web_event:attrs.cart.item_count",),
    ("Number of items in the cart at event time.", "จำนวนสินค้าในตะกร้า ณ เวลาที่เกิดเหตุการณ์"),
    ("Counted by the shop front end.", "ส่วนหน้าร้านค้านับให้"),
)  # fmt: skip
_ORDER_REF = Arche(
    "order_ref13", "order_ref", (),
    ("web.web_event:attrs.order.order_ref", "audit.audit:order_ref13"),
    ("Order reference number printed on invoices.", "เลขที่อ้างอิงคำสั่งซื้อที่พิมพ์บนใบแจ้งหนี้"),
    ("Allocated by the order system with 13 digits and unrelated to people.",
     "ระบบคำสั่งซื้อออกให้ ยาว 13 หลัก และไม่เกี่ยวกับตัวบุคคล"),
    tags=("hard_negative", "negation"),
)  # fmt: skip
_FREE_CONSTRAINED = Arche(
    "free_text_constrained", "reason", (),
    ("proc.supplier:attrs.feedback.reason_code", "crm.customer:attrs.survey.reason"),
    ("Reason chosen from a fixed list in the feedback form.", "เหตุผลที่เลือกจากรายการตายตัวในแบบฟอร์มข้อเสนอแนะ"),
    ("Dropdown values only, typing free text is disabled.", "รับเฉพาะค่าจากรายการเลือก ไม่เปิดให้พิมพ์ข้อความเอง"),
    tags=("hard_negative", "negation"),
)  # fmt: skip

_FREE_TEXT = Arche(
    "free_text", "free_text", (D,),
    ("crm.customer:support_note", "web.web_event:attrs.form.message", "audit.audit:attrs.change.comment",
     "hr.employee:attrs.review.free_comment"),
    ("Free-text note typed by staff or users.", "หมายเหตุแบบข้อความอิสระที่เจ้าหน้าที่หรือผู้ใช้พิมพ์เอง"),
    ("No input validation and the content is not restricted.", "ไม่มีการตรวจสอบข้อมูลนำเข้า และไม่จำกัดเนื้อหา"),
    suff="insufficient",
)  # fmt: skip
_OPAQUE_CODE = Arche(
    "opaque_code", "opaque_code", (),
    ("crm.customer:attrs.x.f17", "hr.employee:x_val", "proc.supplier:attrs.misc.k3", "audit.audit:attrs.legacy.ref2",
     "web.web_event:attrs.tmp.v9"),
    ("", ""), ("", ""), meta="none", suff="insufficient",
)  # fmt: skip
_OPAQUE_NUM = Arche(
    "opaque_number", "opaque_num", (),
    ("crm.customer:legacy_num", "web.web_event:attrs.legacy.num_a", "hr.employee:attrs.legacy.code_n"),
    ("", ""), ("", ""), meta="none", suff="insufficient", sqltype="bigint",
)  # fmt: skip
_LOOK_ID13 = Arche(
    "lookalike_id13", "thai_id", (D,),
    ("crm.customer:ident", "hr.employee:attrs.misc.ident_a"),
    ("", ""), ("", ""), meta="none", suff="insufficient", tags=("hard_negative",),
)  # fmt: skip
_LOOK_GOV = Arche(
    "lookalike_govid", "govid", (D,),
    ("proc.supplier:attrs.owner.doc_no",),
    ("", ""), ("", ""), meta="none", suff="insufficient", tags=("hard_negative",),
)  # fmt: skip
_LOOK_HASH = Arche(
    "lookalike_hash", "email_hash", (Q,),
    ("audit.audit:attrs.actor.h_val",),
    ("", ""), ("", ""), meta="none", suff="insufficient", tags=("hard_negative",),
)  # fmt: skip

ARCHETYPES: tuple[Arche, ...] = (
    _HEALTH,
    _partial(_HEALTH, "health_inferred_partial", ("crm.customer:attrs.segments.cohort_h2", "web.web_event:attrs.profile.segment_med")),
    _SICK, _HEALTH_NOTE, _RELIGION, _BIOMETRIC, _UNION,
    _EMAIL, _THAI_ID, _PHONE, _GOVID, _NAME_EN, _NAME_TH, _ADDRESS,
    _partial(_ADDRESS, "shipping_address_partial", ("crm.customer:attrs.shipping.addr_text",)),
    _BIRTHDAY, _MISLEAD_EMAIL, _EMPLOYEE_NO,
    _partial(_THAI_ID, "thai_national_id_partial", ("hr.employee:attrs.gov.id_no_2",)),
    _BIRTH_YM, _POSTCODE, _PROVINCE, _ACCOUNT_TOKEN,
    _partial(_ACCOUNT_TOKEN, "account_token_partial", ("audit.audit:attrs.payment.acct_ref",)),
    _HASH,
    _partial(_HASH, "hash_unsalted_partial", ("web.web_event:attrs.ids.h1",)),
    _HMAC, _DEVICE_UUID,
    _partial(_DEVICE_UUID, "device_uuid_partial", ("web.web_event:attrs.device.client_uuid",)),
    _DEMOGRAPHIC, _IP,
    _TXN, _REQ_UUID,
    _partial(_REQ_UUID, "request_uuid_partial", ("audit.audit:attrs.trace.span_uuid",)),
    _MISLEAD_CUSTOMER,
    _partial(_MISLEAD_CUSTOMER, "misleading_customer_id_partial", ("crm.customer:attrs.test.customer_id",)),
    _COMPANY, _UI, _PAGE, _SKU, _CURRENCY, _EVENT_TYPE, _CART, _ORDER_REF, _FREE_CONSTRAINED,
    _partial(_TXN, "transaction_id_partial", ("audit.audit:attrs.misc.txn_ref",)),
    _FREE_TEXT, _OPAQUE_CODE, _OPAQUE_NUM, _LOOK_ID13, _LOOK_GOV, _LOOK_HASH,
)  # fmt: skip
ARCHE_BY_KEY = {a.key: a for a in ARCHETYPES}


@dataclass(frozen=True)
class FieldDef:
    idx: int
    arche: str
    schema: str
    table: str
    column: str
    path: tuple[str, ...]  # JSONB path inside attrs; () for scalar columns

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def leaf(self) -> str:
        return self.path[-1] if self.path else self.column

    @property
    def field_id(self) -> str:
        if self.path:
            return f"{self.schema}.{self.table}.{self.column}:{'.'.join(self.path)}"
        return f"{self.schema}.{self.table}.{self.column}"

    @property
    def catalog_path(self) -> str:
        return ".".join([self.column, *self.path])


def _build_field_defs() -> list[FieldDef]:
    out = []
    for a in ARCHETYPES:
        for loc in a.locations:
            qt, _, col = loc.partition(":")
            schema, table = qt.split(".")
            if col.startswith("attrs."):
                out.append(FieldDef(len(out), a.key, schema, table, "attrs", tuple(col.split(".")[1:])))
            else:
                out.append(FieldDef(len(out), a.key, schema, table, col, ()))
    return out


FIELD_DEFS = _build_field_defs()
FIELD_BY_ID = {fd.field_id: fd for fd in FIELD_DEFS}
TABLE_NOTES = {
    "crm.customer": ("Customer master table of the online shop.", "ตารางข้อมูลหลักของลูกค้าร้านค้าออนไลน์"),
    "hr.employee": ("Employee records of the HR system.", "ระเบียนพนักงานในระบบทรัพยากรบุคคล"),
    "proc.supplier": ("Supplier register of the procurement team.", "ทะเบียนผู้ขายของทีมจัดซื้อ"),
    "web.web_event": ("Clickstream events from the website and app.", "เหตุการณ์คลิกสตรีมจากเว็บไซต์และแอป"),
    "audit.audit": ("Audit trail of changes and access.", "บันทึกตรวจสอบการเปลี่ยนแปลงและการเข้าถึง"),
}
STEWARDS = {"crm": "crm-data-team", "hr": "hr-systems", "proc": "procurement-ops",
            "web": "web-analytics", "audit": "internal-audit"}  # fmt: skip
RETENTION = {"sensitive_personal": 180, "direct_identifier": 365, "quasi_identifier": 730,
             "non_personal": 1095, "unknown": 90}  # fmt: skip


# --- synthetic values -----------------------------------------------------------------------------------

_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hex(r: random.Random, n: int) -> str:
    return "".join(r.choice("0123456789abcdef") for _ in range(n))


def _name_en(r: random.Random) -> str:
    return f"{r.choice(GIVEN_EN)} {r.choice(SURNAME_EN)}"


def _email(r: random.Random) -> str:
    return f"{r.choice(GIVEN_EN).lower()}.{r.choice(SURNAME_EN).lower()}{r.randrange(10, 999)}@{r.choice(ALLOWED_EMAIL_DOMAINS)}"


def _phone(r: random.Random) -> str:
    kind = r.randrange(3)
    if kind == 0:
        return f"+1 202 555 01{r.randrange(100):02d}"
    if kind == 1:
        return f"+44 7700 900{r.randrange(1000):03d}"
    return f"+44 20 7946 0{r.randrange(1000):03d}"


def _gen_demographic(r: random.Random, fd: FieldDef) -> str:
    pools = {
        "age_band": ("18-24", "25-34", "35-44", "45-54", "55-64", "65+"),
        "job_grade": ("G1", "G2", "G3", "G4", "G5", "G6"),
        "department_code": ("DEP-FIN", "DEP-OPS", "DEP-HR", "DEP-IT", "DEP-SALES"),
        "gender": ("F", "M", "X", "undisclosed"),
    }
    return r.choice(pools[fd.leaf])


def _gen_ui(r: random.Random, fd: FieldDef) -> str:
    return r.choice(("light", "dark", "system") if fd.leaf == "theme" else ("en-US", "th-TH", "ja-JP"))


def _gen_free_text(r: random.Random, fd: FieldDef) -> str:
    return r.choice(SENTENCES).format(name=_name_en(r))


def _value_for(fd: FieldDef, gen: str, r: random.Random) -> Any:
    if gen == "email":
        return _email(r)
    if gen == "thai_id":
        return fake_thai_id(r, dashed=fd.idx % 2 == 1)
    if gen == "order_ref":
        return fake_thai_id(r)
    if gen == "phone":
        return _phone(r)
    if gen == "govid":
        return f"XZ{r.randrange(10**6, 10**7)}"
    if gen == "name_en":
        return _name_en(r)
    if gen == "name_th":
        return f"{r.choice(GIVEN_TH)} {r.choice(SURNAME_TH)}"
    if gen == "address":
        return f"{r.randrange(1, 300)}/{r.randrange(1, 40)} Example Road, Mockville, Sampleburg 00{r.randrange(100, 999)}"
    if gen == "birth_date":
        return date(1955, 1, 1) + timedelta(days=r.randrange(50 * 365))
    if gen == "employee_no":
        return f"EMP-{r.randrange(10000, 99999)}"
    if gen == "condition":
        return r.choice(CONDITIONS)
    if gen == "sick_reason":
        return r.choice(SICK_REASONS)
    if gen == "health_note":
        return f"{_name_en(r)}: {r.choice(CONDITIONS)} follow-up planned"
    if gen == "religion":
        return r.choice(("faith_a", "faith_b", "faith_c", "none_declared"))
    if gen == "biometric":
        return f"tpl_{_hex(r, 16)}"
    if gen == "union":
        return r.choice(("member", "non_member"))
    if gen == "birth_ym":
        return f"{r.randrange(1955, 2005)}-{r.randrange(1, 13):02d}"
    if gen == "postcode":
        return f"00{r.randrange(100, 999)}"
    if gen == "province":
        return r.choice(PROVINCES)
    if gen == "account_token":
        return f"tok_{_hex(r, 16)}"
    if gen == "email_hash":
        return hashlib.sha256(_email(r).encode()).hexdigest()
    if gen == "hmac":
        return f"hk1_{_hex(r, 32)}"
    if gen == "uuid":
        return str(uuid.UUID(int=r.getrandbits(128), version=4))
    if gen == "demographic":
        return _gen_demographic(r, fd)
    if gen == "ip":
        return f"{r.choice(IP_PREFIXES)}.{r.randrange(1, 255)}"
    if gen == "txn":
        return "TXN-" + "".join(r.choice(_ALPHA) for _ in range(10))
    if gen == "random_customer":
        return f"CUST-{r.randrange(10**7, 10**8)}"
    if gen == "company":
        return f"{r.choice(COMPANY_A)}{r.choice(COMPANY_B)} Co., Ltd."
    if gen == "ui":
        return _gen_ui(r, fd)
    if gen == "page_path":
        return r.choice(("/shop/cart", "/shop/checkout", f"/shop/product/{r.randrange(100, 999)}", "/help"))
    if gen == "sku":
        return f"SKU-{r.randrange(100000, 999999)}"
    if gen == "currency":
        return r.choice(("THB", "USD", "EUR"))
    if gen == "event_type":
        return r.choice(("page_view", "add_to_cart", "checkout_start", "purchase", "login", "update"))
    if gen == "cart_count":
        return r.randrange(1, 12)
    if gen == "reason":
        return r.choice(("late_delivery", "damaged_package", "wrong_item", "other"))
    if gen == "free_text":
        return _gen_free_text(r, fd)
    if gen == "opaque_code":
        return f"{r.choice(_ALPHA)}{r.randrange(2, 9)}-{r.randrange(10, 99)}{r.choice(_ALPHA)}"
    if gen == "opaque_num":
        return r.randrange(10**8, 10**9)
    raise KeyError(gen)


def field_value(seed: int, fd: FieldDef, row: int) -> Any:
    r = random.Random(f"e05|{seed}|{fd.field_id}|{row}")
    return _value_for(fd, ARCHE_BY_KEY[fd.arche].gen, r)


@cache
def column_values(seed: int, idx: int) -> tuple[Any, ...]:
    fd = FIELD_DEFS[idx]
    return tuple(field_value(seed, fd, i) for i in range(1, ROW_COUNTS[fd.qualified_table] + 1))


def value_text(v: Any) -> str:
    return str(v)


# --- specs --------------------------------------------------------------------------------------------------


def _difficulty_tags(a: Arche, fd: FieldDef) -> list[str]:
    tags = list(a.tags)
    if a.suff == "insufficient":
        tags.append("insufficient_evidence")
    if fd.path:
        tags.append("sql_identifier")
    text_th = f"{a.purpose[1]} {a.lineage[1]}"
    if re.search(r"[A-Za-z]{3,}", text_th):
        tags.append("code_switching")
    if re.search(r"\b(not|no|never|without)\b", f"{a.purpose[0]} {a.lineage[0]}".lower()) and "negation" not in tags:
        tags.append("negation")
    return list(dict.fromkeys(tags))


def _group_priority(a: Arche) -> int:
    # Groups that get a third spec first: insufficient/ambiguous, then sensitive and non-personal.
    if a.suff != "sufficient":
        return 0
    primary = primary_class(a.truth, a.suff)
    return {"sensitive_personal": 1, "non_personal": 2, "quasi_identifier": 3, "direct_identifier": 4}[primary]


def generate_specs(seed: int) -> list[ScenarioSpec]:
    rng = random.Random(seed + CASE_OFFSET)
    n_groups = len(FIELD_DEFS)
    extra = SCENARIOS - 2 * n_groups
    if not 0 <= extra <= n_groups:
        raise ValueError(f"{n_groups} groups cannot yield {SCENARIOS} specs with 2-3 per group")
    order = sorted(FIELD_DEFS, key=lambda fd: (_group_priority(ARCHE_BY_KEY[fd.arche]), rng.random()))
    triple = {fd.idx for fd in order[:extra]}
    specs: list[ScenarioSpec] = []
    for fd in sorted(FIELD_DEFS, key=lambda f: rng.random()):
        a = ARCHE_BY_KEY[fd.arche]
        gold = gold_for(a)
        for k in range(3 if fd.idx in triple else 2):
            specs.append(
                ScenarioSpec(
                    scenario_id="",
                    experiment=CASE,
                    group_id=f"e05-g{fd.idx:03d}-{a.key}",
                    facts={
                        "field_id": fd.field_id,
                        "schema": fd.schema,
                        "table": fd.table,
                        "column": fd.column,
                        "json_path": list(fd.path) or None,
                        "meta_version": 2 if k % 2 == 0 else 1,
                        "n_samples": rng.randrange(2, 6),
                        "offset": rng.randrange(0, 5000),
                        "data_seed": seed + CASE_OFFSET,
                        "_internal": {
                            "archetype": a.key,
                            "truth_flags": list(a.truth),
                            "meta_level": a.meta,
                            "policy": POLICY_VERSION,
                        },
                    },
                    gold=dict(gold),
                    difficulty_tags=_difficulty_tags(a, fd),
                    evidence_sufficiency=a.suff,
                    label_source=LABEL_SOURCE,
                    seed=seed,
                )
            )
    rng.shuffle(specs)
    for i, s in enumerate(specs, 1):
        s.scenario_id = scenario_id(CASE, i)
    return specs


# --- DB seed ------------------------------------------------------------------------------------------------

_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)
_STATUSES = ("active", "inactive", "pending")


def _catalog_rows() -> list[tuple[Any, ...]]:
    rows = []
    for fd in FIELD_DEFS:
        a = ARCHE_BY_KEY[fd.arche]
        primary = primary_class(a.truth, a.suff)
        has_p, has_l = a.meta in ("full", "partial"), a.meta == "full"
        for version in (1, 2):
            rows.append(
                (
                    fd.schema, fd.table, fd.catalog_path, version,
                    a.purpose[0] if has_p else None, a.purpose[1] if has_p else None,
                    a.lineage[0] if has_l else None, a.lineage[1] if has_l else None,
                    STEWARDS[fd.schema] if version == 2 else None,
                    RETENTION[primary] if version == 2 else None,
                )
            )  # fmt: skip
    return rows


def _nested_set(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def _json_safe(v: Any) -> Any:
    return v.isoformat() if isinstance(v, date) else v


def db_seed(specs: list[ScenarioSpec], seed: int) -> PiiSeed:
    """Full synthetic database (all fields, ~10,000 rows) plus the versioned catalog."""
    data_seed = specs[0].facts["data_seed"] if specs else seed + CASE_OFFSET
    columns: dict[str, list[tuple[str, str]]] = {t: [] for t in ROW_COUNTS}
    rows: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
    for fd in FIELD_DEFS:
        if not fd.path:
            columns[fd.qualified_table].append((fd.column, ARCHE_BY_KEY[fd.arche].sqltype))
    for qt, n in ROW_COUNTS.items():
        scalars = [fd for fd in FIELD_DEFS if fd.qualified_table == qt and not fd.path]
        jsons = [fd for fd in FIELD_DEFS if fd.qualified_table == qt and fd.path]
        names = ["id", "created_at", "status", "row_version", "attrs", *[fd.column for fd in scalars]]
        sc_vals = [column_values(data_seed, fd.idx) for fd in scalars]
        js_vals = [column_values(data_seed, fd.idx) for fd in jsons]
        table_rows = []
        for i in range(n):
            attrs: dict[str, Any] = {}
            for fd, vals in zip(jsons, js_vals):
                _nested_set(attrs, fd.path, _json_safe(vals[i]))
            table_rows.append(
                (
                    i + 1,
                    (_BASE_TS + timedelta(minutes=7 * i)).isoformat(),
                    _STATUSES[i % 3],
                    1 + i % 4,
                    json.dumps(attrs, ensure_ascii=False),
                    *[v[i] for v in sc_vals],
                )
            )
        rows[qt] = (names, table_rows)
    comments = [
        (f"{fd.schema}.{fd.table}.{fd.column}", ARCHE_BY_KEY[fd.arche].purpose[0])
        for fd in FIELD_DEFS
        if not fd.path and ARCHE_BY_KEY[fd.arche].meta != "none"
    ]
    return PiiSeed(
        columns=columns,
        rows=rows,
        catalog=_catalog_rows(),
        comments=comments,
        table_comments={qt: note[0] for qt, note in TABLE_NOTES.items()},
    )


def make_backend(settings: Settings) -> PostgresPiiBackend:
    return PostgresPiiBackend(settings)


# --- reference evidence -----------------------------------------------------------------------------------


def _neighbors(fd: FieldDef) -> list[str]:
    if fd.path:
        parent = fd.path[:-1]
        keys = {
            o.path[len(parent)]
            for o in FIELD_DEFS
            if o.qualified_table == fd.qualified_table and o.path[: len(parent)] == parent and len(o.path) > len(parent)
        }
        return sorted(keys - {fd.path[-1]})[:6]
    return [o.column for o in FIELD_DEFS if o.qualified_table == fd.qualified_table and not o.path and o is not fd][:6]


def _column_type(fd: FieldDef, ids: list[int], seed: int) -> str:
    if fd.path:
        vals = column_values(seed, fd.idx)
        kinds = {"number" if isinstance(vals[i - 1], int) else "string" for i in ids}
        return "jsonb:" + "|".join(sorted(kinds))
    return ARCHE_BY_KEY[fd.arche].sqltype


def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    """Pure-Python reference of what the DB retrieval must return (generator values + same masking)."""
    f = spec.facts
    fd = FIELD_BY_ID[f["field_id"]]
    a = ARCHE_BY_KEY[fd.arche]
    seed = f["data_seed"]
    vals = column_values(seed, fd.idx)
    n = ROW_COUNTS[fd.qualified_table]
    ids = sample_row_ids(n, f["offset"], f["n_samples"])
    primary = primary_class(a.truth, a.suff)
    v2 = f["meta_version"] == 2
    has_p, has_l = a.meta in ("full", "partial"), a.meta == "full"
    return {
        "field_id": fd.field_id,
        "schema": fd.schema,
        "table": fd.table,
        "column": fd.column,
        "json_path": list(fd.path) or None,
        "column_type": _column_type(fd, ids, seed),
        "catalog_version": f["meta_version"],
        "purpose_en": a.purpose[0] if has_p else None,
        "purpose_th": a.purpose[1] if has_p else None,
        "lineage_en": a.lineage[0] if has_l else None,
        "lineage_th": a.lineage[1] if has_l else None,
        "steward": STEWARDS[fd.schema] if v2 else None,
        "retention_days": RETENTION[primary] if v2 else None,
        "comment": a.purpose[0] if (has_p and not fd.path) else None,
        "row_count": n,
        "distinct_count": len({value_text(v) for v in vals}),
        "neighbor_fields": _neighbors(fd),
        "sample_row_ids": ids,
        "samples_masked": [mask_value(value_text(vals[i - 1])) for i in ids],
        "_internal": f["_internal"],
    }


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    """DB facts must equal the generator reference, and the gold must follow the documented policy."""
    problems = []
    exp = expected_evidence(spec)
    for key in sorted(set(exp) | set(facts)):
        if key == "_internal":
            continue
        if facts.get(key, "<missing>") != exp.get(key, "<missing>"):
            problems.append(f"db_mismatch:{key}")
    a = ARCHE_BY_KEY[spec.facts["_internal"]["archetype"]]
    if spec.gold != gold_for(a):
        problems.append("gold_policy_mismatch")
    if spec.evidence_sufficiency != a.suff:
        problems.append("sufficiency_mismatch")
    if (a.suff == "insufficient") != (spec.gold["data_class"] == "unknown"):
        problems.append("unknown_iff_insufficient")
    for s in facts.get("samples_masked", []):
        if s.isascii() and re.search(r"[A-Za-z0-9]", s) and "*" not in s:
            problems.append("unmasked_sample")
    if not 2 <= len(facts.get("samples_masked", [])) <= 5:
        problems.append("sample_count")
    return problems


# --- rendering -------------------------------------------------------------------------------------------------

MISSING = {"en": "Not documented in the catalog.", "th": "ไม่มีคำอธิบายในแค็ตตาล็อก"}
UNASSIGNED = {"en": "unassigned", "th": "ยังไม่กำหนด"}


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "field": facts["field_id"],
        "column_type": facts["column_type"],
        "metadata_version": f"v{facts['catalog_version']}",
        "purpose": facts[f"purpose_{lang}"] or MISSING[lang],
        "lineage": facts[f"lineage_{lang}"] or MISSING[lang],
        "masked_samples": list(facts["samples_masked"]),
    }
    if context_level == "natural_long":
        qt = f"{facts['schema']}.{facts['table']}"
        d, n = facts["distinct_count"], facts["row_count"]
        days = facts["retention_days"]
        state["table_note"] = TABLE_NOTES[qt][0 if lang == "en" else 1]
        state["profile"] = (
            f"{d} distinct values across {n} rows" if lang == "en" else f"มี {d} ค่าที่ไม่ซ้ำกันจากทั้งหมด {n} แถว"
        )
        state["steward"] = facts["steward"] or UNASSIGNED[lang]
        state["retention"] = UNASSIGNED[lang] if days is None else (f"{days} days" if lang == "en" else f"{days} วัน")
        state["sibling_fields"] = list(facts["neighbor_fields"])
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    return [facts["field_id"], facts["column_type"], f"v{facts['catalog_version']}", *facts["samples_masked"]]


# --- baseline ----------------------------------------------------------------------------------------------------

_SENS_NAME = re.compile(r"health|medical|sick|diagnos|condition|chronic|wellness|religio|faith|union|biometric|fingerprint|face_template|segment_med|cohort_h")
_GOV_NAME = re.compile(r"national_id|citizen|id_card|id_no|passport|licen[cs]e|permit")
_DIRECT_NAME = re.compile(r"e?_?mail|phone|mobile|(^|_)tel($|_)|(^|_)name($|_)|full_name|person|address|addr|dob|date_of_birth|birth_date|employee_no|staff_no")
_QUASI_NAME = re.compile(r"birth|postcode|postal|zip|province|token|hash|hmac|sha256|md5|uuid|device|age_band|gender|grade|department|(^|_)ip(_|$)|customer_id|user_id|account_id|member_id")
_NONPERSONAL_NAME = re.compile(r"txn|transaction|request|trace|theme|language|path|sku|currency|event_type|action_type|item_count|reason|order_ref")
_KEYWORDS = (
    (re.compile(r"\b(health|medical|religion|union|biometric)\b"), "sensitive_personal"),
    (re.compile(r"\b(email|phone|national identification|passport|address|full name|date of birth)\b"), "direct_identifier"),
    (re.compile(r"\b(hash|token|postal|province|ip address)\b"), "quasi_identifier"),
)  # fmt: skip
_EMAIL_MASKED = re.compile(r"@example\.(com|org|net)$")
_ID13_MASKED = re.compile(r"^[\d*\-]{13,17}$")
_PHONE_MASKED = re.compile(r"^\+[\d*]{1,3}( [\d*]+)+$")


def _baseline_class(facts: dict[str, Any]) -> tuple[str, bool]:
    leaf = (facts["json_path"][-1] if facts["json_path"] else facts["column"]).lower()
    gov = bool(_GOV_NAME.search(leaf))
    if _SENS_NAME.search(leaf):
        return "sensitive_personal", gov
    if gov or _DIRECT_NAME.search(leaf):
        return "direct_identifier", gov
    if _QUASI_NAME.search(leaf):
        return "quasi_identifier", gov
    if _NONPERSONAL_NAME.search(leaf):
        return "non_personal", gov
    samples = facts["samples_masked"]
    if samples and all(_EMAIL_MASKED.search(s) for s in samples):
        return "direct_identifier", gov
    if samples and all(_ID13_MASKED.match(s) for s in samples):
        return "direct_identifier", True
    if samples and all(_PHONE_MASKED.match(s) for s in samples):
        return "direct_identifier", gov
    text = f"{facts.get('purpose_en') or ''} {facts.get('lineage_en') or ''}".lower()
    for pattern, cls in _KEYWORDS:
        if pattern.search(text):
            return cls, gov
    return "unknown", gov


def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Column-name, masked-sample regex and English catalog keyword rules; ignores semantics and Thai text."""
    cls, gov = _baseline_class(facts)
    tier = handling_tier(cls, gov, False, False)
    return {"data_class": cls, "handling_tier": tier, "requires_masking_review": tier >= 2}
