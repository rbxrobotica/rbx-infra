# ADR-0002: Operational Tooling Strategy - Odoo, ClickUp, WhatsApp

## Status

**Accepted** - 2026-03-30

## Context

Strategos Core is designed as a strategic cognition layer, not an operational system. However, RBX Systems requires robust operational capabilities for CRM, sales pipeline, task management, and customer communication.

The core question: **Should we build these capabilities into Strategos, or integrate best-in-class operational tools?**

## Decision

**We will NOT build operational features into Strategos.** Instead, we adopt a layered architecture using proven open source and SaaS tools:

- **Strategos Core**: Strategic decisions, objectives, governance, memory
- **Thalamus Core**: Event routing and integration layer
- **Odoo 17 Community**: ERP/CRM for sales pipeline, accounting, contacts
- **ClickUp**: Task management and team notifications
- **WhatsApp Business API**: Customer communication and sales funnel

### Architecture

```
Strategos (Strategic) ↔ Thalamus (Integration) ↔ Odoo + ClickUp + WhatsApp (Operational)
```

## Rationale

### Why NOT Build Custom

1. **Strategic-First Principle**: Strategos must remain focused on strategic thinking, not operational tasks
2. **Solved Problems**: CRM and task management are mature, well-solved domains
3. **Time Cost**: Building equivalent to Odoo would take 6-12 months
4. **Maintenance Burden**: Ongoing operational features distract from strategic innovation
5. **User Experience**: Purpose-built tools (ClickUp, WhatsApp) have superior UX

### Why These Tools

- **Odoo**: Open source, comprehensive (CRM+Sales+Accounting), WhatsApp integration, proven at scale
- **ClickUp**: Superior task UX, desktop notifications, free for small teams
- **WhatsApp**: 99% adoption in Brazil, conversational commerce, high engagement

## Consequences

### Positive

- Strategos remains pure strategic layer (aligned with philosophy)
- Faster time-to-market (no need to build CRM)
- Best-in-class tools for each domain
- Lower total cost of ownership (R$ 100-300/month)

### Negative

- Integration complexity (multiple systems)
- Data synchronization required
- Thalamus becomes critical integration point

### Mitigations

- Thalamus serves as central event router (prevents integration spaghetti)
- Odoo as source of truth for customer data
- Clear responsibility boundaries documented

## Implementation

See detailed implementation plan: [PLAN-operational-tooling-strategy.md](../PLAN-operational-tooling-strategy.md)

**Phases:**
1. Odoo standalone (1-2 weeks)
2. WhatsApp integration (1-2 weeks)
3. ClickUp integration (1 week)
4. Strategos integration (2-3 weeks)

**Total timeline:** ~6 weeks to full operational capability

## Alternatives Considered

1. **Build everything in Strategos** - Rejected (violates strategic-first principle)
2. **Odoo for everything** - Rejected (Odoo Projects has poor task management UX)
3. **Telegram instead of WhatsApp** - Rejected (lower adoption in Brazil)

## Related

- [Detailed Implementation Plan](../PLAN-operational-tooling-strategy.md)
- [Strategos Philosophy](../../strategos-core/PHILOSOPHY.md)
- [Strategic vs Tactical Boundaries](../../strategos-core/docs/architecture/strategic-vs-tactical.md)

## References

- Odoo Documentation: https://www.odoo.com/documentation/17.0/
- ClickUp API: https://clickup.com/api
- WhatsApp Business Platform: https://developers.facebook.com/docs/whatsapp
