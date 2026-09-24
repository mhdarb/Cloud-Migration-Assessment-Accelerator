# Zephyr Order API — Failover Runbook

This runbook covers the manual failover procedure for the Order API tier.

## Preconditions

- On-call engineer has access to DC-North and DC-South jump hosts.
- Payments Service health check is green.

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
