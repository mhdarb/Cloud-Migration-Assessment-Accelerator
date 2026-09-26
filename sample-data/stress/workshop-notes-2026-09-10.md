# Discovery workshop #3 — Order & Payments — 2026-09-10

**Attendees:** Priya Nair (Order API lead), Tom Becker (Payments owner), Ana Silva (Fleet),
Marcus Lee (Infrastructure), Northwind MSP (2 engineers), migration partner (3)
**Apologies:** Finance IT

## Facts confirmed

- ord-api-01 was resized from 8 to 16 vCPU in July after the month-end incident
  (INC-20260703). Marcus: "the CMDB sync job has been broken since November, so the
  CMDB still says 8."
- pay-db-01 is physical (Dell R740) and runs PostgreSQL 11 on RHEL 7 with ELS. It is not
  in vCenter, so it won't show in the RVTools export.
- Finance Reporting (rpt-01) reads the Payments database directly over ODBC every night.
  Nobody in the room knew this until the dependency scan — Tom was surprised.
- rpt-01 also talks to an Oracle listener at 10.20.5.77. Nobody recognises the IP. Marcus
  thinks it might be the old freight-rating box in the DC-North comms room. Unconfirmed.

## Decisions

- Payments moves last. PCI QSA audit window is 2027-02-15 to 2027-03-05 and there is a
  change freeze for in-scope systems from 2027-02-01.
- Fleet Tracker goes first (lowest coupling; database is the managed-PostgreSQL pilot).
- zl-as400 (WMS) is out of scope for wave 1 — retain on-prem, revisit in 2027.

## Open questions

- Can rpt-01 be retired? Priya believes so; Finance uses it for month-end close, so
  probably not before a replacement exists. **Owner:** Finance IT (absent).
- Is the orphan VM restore-test-0412 needed? Nobody claims it.

## Action items

| # | Action | Owner | Due |
|---|--------|-------|-----|
| 1 | Fix CMDB sync job and re-export | Marcus Lee | 2026-09-24 |
| 2 | Confirm what 10.20.5.77 is | Northwind MSP | 2026-09-17 |
| 3 | PostgreSQL 11 -> 16 upgrade plan for pay-db-01 | Tom Becker | 2026-10-01 |
| 4 | Decide rpt-01 retirement | Finance IT | TBC |

_Minutes by the migration partner; circulated 2026-09-26._
