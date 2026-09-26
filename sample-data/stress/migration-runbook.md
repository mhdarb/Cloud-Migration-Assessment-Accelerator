# Zephyr Order API — Failover Runbook

This runbook covers the manual failover procedure for the Order API tier.
Last exercised: 2024-03-14 (see DR plan — the exercise partially failed).

## Preconditions

- On-call engineer has access to DC-North and DC-South jump hosts (jump-01).
- Payments Service health check is green.
- Change ticket raised in ServiceNow (standard change CHG-ORD-FAILOVER).

## Node roles

| hostname | role | datacenter | priority |
| --- | --- | --- | --- |
| ord-api-01 | primary | DC-North | 1 |
| ord-api-02 | primary | DC-North | 1 |
| ord-api-03 | standby | DC-South | 2 |

## Procedure

1. Announce the maintenance window in the operations channel.
2. Drain ord-api-01, verify connections migrate to ord-api-02.
3. Promote the DC-South standby only if both DC-North nodes are unhealthy.
4. Re-point edge-lb-01 virtual server pool to ord-api-03 (manual F5 change).

## Known issues

- ord-api-03 has 8 vCPU while ord-api-01 now has 16; the standby cannot carry month-end load alone.
