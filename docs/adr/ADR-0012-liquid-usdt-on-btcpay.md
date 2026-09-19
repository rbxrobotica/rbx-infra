# ADR-0012: USDT acceptance via a Liquid node beside BTCPay Server

## Status

**Proposed** · 2026-09-12

## Context

[[rbx-briefing-btc-logged-in-area]]'s USD checkout prefers USDT through the
RBX BTCPay Server, with card (Payrexx) as the alternative. Today
`rbx-btcpay-server` (`apps/prod/rbx-payments`) runs BTCPay Server 2.0.5 with
a single chain, `BTCPAY_CHAINS=btc`, backed by a pruned Bitcoin Core node
(`rbx-bitcoind`) and NBXplorer 2.5.20 indexing only `btc`. There is no USDT
support today: a customer who wants USDT has nothing to pay to.

BTCPay Server has one native path to USDT: **Tether on Liquid (USDt on
Liquid, asset id `ce091c998b83c78bb71a632313ba3760f1763d9cfcffae02258ffa9865a37bd1`)**,
which requires an Elements/Liquid full node and a second NBXplorer chain.
BTCPay Server also has a **plugin marketplace** (the same mechanism used for
things like the POS and Crowdfund apps), but there is no maintained BTCPay
plugin that adds a non-Liquid USDT rail (ERC-20 or Tron USDT); building or
adopting one would mean a bespoke, unaudited integration for a payment
processor that touches real customer funds. Liquid is the only USDT path
BTCPay Server's own team builds and supports.

## Decision

Add a Liquid node, not a plugin. Concretely:

1. **New Elements node** (`rbx-liquid`, `apps/prod/rbx-payments`), mirroring
   `bitcoind-statefulset.yml`: `btcpayserver/liquid` image, `-chain=liquidv1`,
   pruned where the federation allows it (Liquid's chain is small compared to
   Bitcoin mainnet; expect a multi-hour, not multi-day, initial sync), its own
   PVC, co-located on `altaica` with the existing bitcoind/NBXplorer pod (same
   node-pinning constraint as today, for the same PVC-locality reason).
2. **NBXplorer**: add `lbtc` to `NBXPLORER_CHAINS`, point it at the new
   Elements node's RPC and cookie file, same pattern as the existing `btc`
   wiring in `nbxplorer-deploy.yml`.
3. **BTCPay Server**: `BTCPAY_CHAINS=btc,lbtc`, add `BTCPAY_LBTCEXPLORERURL`
   pointing at the same NBXplorer instance (NBXplorer serves multiple chains
   from one process). BTCPay auto-recognizes the well-known Liquid assets
   (L-BTC, Tether USDt) once the `lbtc` chain is live; no extra asset-id
   configuration should be needed, but the store's Liquid settings need the
   USDt asset turned on manually in the UI after sync (browser step, needs
   Leandro's BTCPay login).
4. **Do not gate the rest of the USD checkout on this.** The store, API key,
   webhook and `payment_method=btcpay` wiring in rbx-commerce work against
   BTC today; USDT support turns on for that same store the moment the
   Liquid node finishes syncing and the store setting is flipped. No code or
   webhook contract change is needed when USDT support lands — BTCPay
   invoices already carry the paid currency/asset, and `internal/payment/btcpay.go`
   should key off that field rather than assuming BTC (verify this holds
   before flipping the store setting live, since it's the one place the
   assumption of BTC-only currently exists in the app-visible contract).

## Alternatives considered

- **A non-Liquid USDT plugin or a separate custodial USDT processor**:
  rejected. Nothing maintained exists for BTCPay; building one is a bespoke
  payment-security surface for a single altcoin rail, disproportionate to
  the ask. A separate custodial processor (e.g. a centralized USDT gateway)
  would also reintroduce the counterparty-custody problem BTCPay's
  self-hosting was chosen to avoid ([[bun-first-standard]] fleet-wide
  self-hosting posture applies to payment rails too).
- **Skip USDT, USD checkout stays card-only (Payrexx)**: rejected by
  Leandro's explicit preference for USDT as the default USD rail; kept as
  the fallback that already ships today while the Liquid node syncs.

## Consequences

- New stateful workload with real custody implications: the store must not
  be marked as accepting USDT, and no webhook should be trusted as
  USDT-settled, until the Liquid node is confirmed fully synced and
  validated (checking `getblockchaininfo`-equivalent status on the Elements
  node, not just "pod Running").
- One more chain to operate: monitor disk growth and sync health the same
  way as `rbx-bitcoind` today.
- This ADR does not cover Lightning on Liquid or Lightning on Bitcoin;
  out of scope, same as the existing Lightning deferral noted in
  `btcpay-deploy.yml`.
