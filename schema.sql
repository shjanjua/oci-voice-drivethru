-- VOICEDT schema in SJALL (Oracle 23ai). Run as ADMIN (sjall_supremo).
-- Membership uses PHONIC_ENCODE/FUZZY_MATCH (verified live). Orders persisted.

-- members: short code (primary) + phonetic name fallback + birthday + language
CREATE TABLE voicedt.members (
  member_id            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  member_name          VARCHAR2(200) NOT NULL,
  membership_number    VARCHAR2(8) UNIQUE,
  full_name_phonetic   VARCHAR2(64) GENERATED ALWAYS AS (PHONIC_ENCODE(DOUBLE_METAPHONE, member_name)) VIRTUAL,
  pronunciation        VARCHAR2(200),
  preferred_language   VARCHAR2(8) DEFAULT 'en',
  date_of_birth        DATE,
  created_at           TIMESTAMP DEFAULT SYSTIMESTAMP
);
CREATE INDEX voicedt.ix_members_phonetic ON voicedt.members (full_name_phonetic);

CREATE TABLE voicedt.orders (
  order_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id    VARCHAR2(100),
  member_id     NUMBER REFERENCES voicedt.members(member_id),
  order_status  VARCHAR2(20) DEFAULT 'confirmed',
  total_price   NUMBER(8,2),
  created_at    TIMESTAMP DEFAULT SYSTIMESTAMP,
  confirmed_at  TIMESTAMP
);

CREATE TABLE voicedt.order_items (
  order_item_id   NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id        NUMBER NOT NULL REFERENCES voicedt.orders(order_id),
  item_id         VARCHAR2(40) NOT NULL,
  item_name       VARCHAR2(80),
  size_name       VARCHAR2(20),
  modifiers       VARCHAR2(200),
  unit_price      NUMBER(8,2),
  quantity        NUMBER(4) DEFAULT 1,
  discount_amount NUMBER(8,2) DEFAULT 0,
  discount_reason VARCHAR2(60)
);

-- sample member: birthday is TODAY (MM-DD = SYSDATE) so the birthday reward demos live
INSERT INTO voicedt.members (member_name, membership_number, pronunciation, preferred_language, date_of_birth)
VALUES ('James Okafor', '1234', 'JAYMS oh-KAH-for', 'en', ADD_MONTHS(TRUNC(SYSDATE), -34*12));
INSERT INTO voicedt.members (member_name, membership_number, preferred_language, date_of_birth)
VALUES ('Sofia Marquez', '2468', 'es', DATE '1992-03-14');
COMMIT;
