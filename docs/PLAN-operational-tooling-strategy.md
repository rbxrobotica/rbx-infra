# ADR-0002: Operational Tooling Strategy (Odoo + ClickUp + WhatsApp)

## Status

**Accepted** - 2026-03-30

## Context

RBX Systems is building Strategos Core as a strategic cognition layer for high-stakes organizational decisions. Following the philosophy documented in `strategos-core/PHILOSOPHY.md`, Strategos must focus exclusively on strategic thinking (objectives, decisions, governance, strategic memory) and avoid becoming an operational system.

However, the business requires robust operational capabilities:

1. **CRM/Sales Pipeline**: Lead management, opportunity tracking, sales funnel, customer relationship management
2. **ERP Functions**: Accounting, invoicing, inventory (if applicable), HR/payroll
3. **Task Management**: Project tracking, daily operations, follow-ups, team coordination
4. **Customer Communication**: Direct channel for sales funnel, partnerships, proposals, and support

Building these capabilities into Strategos would violate its core principle of "Strategic-First, Not Operational-First" and create technical debt by reinventing well-solved problems.

### Key Architectural Principle

From `strategos-core/docs/architecture/strategic-vs-tactical.md`:

> **Strategic (Strategos)**: Long-term direction, objectives, memory, coherence
> - Timeframe: Months to years
> - Authority: CEO, Board, Executive Team
> - Nature: Thinking, deciding, remembering
>
> **Tactical (Operational Systems)**: Near-term execution, operations, tasks
> - Timeframe: Days to weeks
> - Authority: Managers, Teams
> - Nature: Doing, executing, delivering

## Decision

We will adopt a **layered operational tooling strategy** using best-in-class open source and SaaS solutions, with clear integration boundaries via Thalamus Core as the event routing layer.

### Chosen Stack

| Layer | Tool | Purpose | Hosting |
|-------|------|---------|---------|
| **Strategic Brain** | Strategos Core | Strategic decisions, objectives, governance, memory | VPS Jaguar (k3s) |
| **Event Router** | Thalamus Core | Webhook orchestration, event streaming, data transformation | VPS Jaguar (k3s) |
| **Operational ERP/CRM** | Odoo 17 Community | CRM, sales, accounting, contacts, WhatsApp integration | VPS Jaguar (k3s) |
| **Task Management** | ClickUp (SaaS) | Projects, tasks, notifications, team collaboration | ClickUp Cloud |
| **Customer Interface** | WhatsApp Business API | Sales funnel, customer communication, bot automation | Meta Cloud API |

### Responsibility Matrix

```
┌─────────────────────────────────────────────────────────────────┐
│                     STRATEGIC LAYER                              │
│  Strategos Core (Go + PostgreSQL)                               │
│  - Decision: "Focus enterprise B2B segment"                     │
│  - Objective: "R$ 2M ARR by Q4 2026"                            │
│  - Risk: "Churn >10% threatens sustainability"                  │
│  - Hypothesis: "Customers pay R$ 100K+ for full solution"       │
│  - Governance: "Sales >R$ 50K require CEO approval"             │
│                                                                  │
│  ❌ Does NOT: Manage sales pipeline, create tasks, answer       │
│              customer inquiries, process invoices               │
└───────────────────────────┬──────────────────────────────────────┘
                            │ Strategic context
                            │ Policies, thresholds, decisions
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     INTEGRATION LAYER                            │
│  Thalamus Core (Event Router)                                   │
│  - Routes: Odoo ↔ Strategos                                     │
│  - Webhooks: ClickUp ↔ Odoo                                     │
│  - Analytics: Aggregate operational data for strategic insights │
└───────────────────────────┬──────────────────────────────────────┘
                            │
            ┌───────────────┴──────────────┐
            │                              │
            ▼                              ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│   OPERATIONAL LAYER      │    │   EXECUTION LAYER        │
│  Odoo 17 Community       │    │  ClickUp (SaaS)          │
│  - CRM: Leads, deals     │    │  - Tasks, projects       │
│  - Sales: Proposals      │    │  - Desktop notifications │
│  - Accounting: Invoicing │    │  - Team collaboration    │
│  - WhatsApp integration  │    │  - Automations           │
│                          │    │                          │
│  ✅ Does: Day-to-day     │    │  ✅ Does: Execution      │
│          operations      │    │          tracking        │
└──────────┬───────────────┘    └──────────┬───────────────┘
           │                               │
           │                               │
           ▼                               ▼
┌────────────────────────────────────────────────────────────────┐
│                     CUSTOMER INTERFACE                          │
│  WhatsApp Business API (Meta Cloud)                            │
│  - Initial contact and lead qualification (bot)                │
│  - Human handoff to Odoo inbox for complex inquiries           │
│  - Sales funnel: Conversational commerce                       │
│  - Post-sales: Support, partnerships, proposals                │
│                                                                 │
│  Website Integration (rbx-robotica-frontend, lda-front)        │
│  - WhatsApp click-to-chat buttons                              │
│  - Contact forms → Odoo CRM                                    │
└────────────────────────────────────────────────────────────────┘
```

### Integration Flows

#### Flow 1: Lead Entry via WhatsApp

```
1. Customer sends WhatsApp message
   "Hi, I'd like a quote for a robotic solution"

2. WhatsApp Bot (Odoo module):
   Qualifies lead: name, company, interest, budget range

3. Odoo CRM:
   Creates lead automatically
   Status: "New - Qualification"

4. Odoo → ClickUp (webhook via Thalamus):
   Creates task: "Follow-up: ACME Corp lead"
   Assigned: Sales rep of the day

5. Sales rep responds via Odoo WhatsApp inbox

6. Deal value >R$ 50K?
   Odoo → Thalamus → Strategos
   Notification: "High-value deal requires CEO approval"

7. CEO reviews in Strategos UI
   Decision: "Approved - strategic customer"
   Strategos → Thalamus → Odoo
   Deal status: "Approved - Send proposal"

8. Proposal sent → ClickUp task auto-updates
   Status: "Waiting for customer response"
```

#### Flow 2: Deal Closed (Operational → Strategic)

```
1. Sales rep closes deal in Odoo
   Value: R$ 120K, Customer: ACME Corp

2. Odoo → Thalamus (webhook):
   {
     "event": "deal_won",
     "value": 120000,
     "segment": "enterprise",
     "customer": "ACME Corp"
   }

3. Thalamus analyzes:
   - Value >R$ 100K (threshold)
   - Validates hypothesis: "Customers pay R$ 100K+"

4. Thalamus → Strategos:
   Updates hypothesis confidence: 0.75 → 0.82
   Adds evidence: "+1 enterprise deal R$ 120K"

5. Strategos dashboard:
   Objective "R$ 2M ARR Q4 2026":
   Progress: R$ 1.2M → R$ 1.32M (55% → 66%)
```

#### Flow 3: Strategic Risk → Operational Action

```
1. Strategos detects risk:
   "Churn rate: 12% (threshold: 10%)"
   CEO Decision: "Launch retention program"

2. Strategos → Thalamus → Odoo:
   Creates campaign: "Q2 2026 Customer Retention"
   Tags at-risk customers

3. Odoo → ClickUp:
   Creates project: "Retention Program Q2"
   Auto-generates tasks: "Contact customer X, Y, Z"

4. Odoo WhatsApp:
   Sends personalized messages
   "Hi João, how has your experience been with our system?"
```

### Technology Choices

#### Odoo 17 Community Edition

**Why Odoo:**
- ✅ Open source (LGPL), self-hosted control
- ✅ Comprehensive: CRM + Sales + Accounting + Inventory + HR
- ✅ Native WhatsApp integration modules available
- ✅ REST/XML-RPC API for integrations
- ✅ Proven at scale (millions of users worldwide)
- ✅ Active community and extensive module ecosystem

**Why NOT build custom:**
- ❌ CRM is a solved problem (don't reinvent)
- ❌ Would take 6-12 months to match Odoo's feature set
- ❌ Ongoing maintenance burden
- ❌ Distracts from Strategos core mission

**Why Community vs Enterprise:**
- Community = R$ 0/month, Enterprise = ~R$ 150/user/month
- Community includes all core modules (CRM, Sales, Accounting)
- Enterprise adds: Helpdesk, Studio, official support
- Decision: Start Community, evaluate Enterprise after 6 months

#### ClickUp (SaaS - Free Plan)

**Why ClickUp:**
- ✅ Superior UX for daily task management vs Odoo Projects
- ✅ Native desktop/mobile notifications
- ✅ Free plan supports up to 5 users (sufficient for RBX)
- ✅ Robust webhook/API for integrations
- ✅ Zapier/n8n compatibility

**Why NOT use Odoo Projects:**
- Odoo Projects is functional but clunky for daily use
- ClickUp's notification system is significantly better
- Team adoption/satisfaction is critical for task tools

#### WhatsApp Business API (Meta Cloud)

**Why WhatsApp:**
- ✅ 99% adoption rate in Brazil
- ✅ ~98% open rate (vs ~20% email)
- ✅ Natural conversational interface
- ✅ Supports bot automation + human handoff
- ✅ Native integration with Odoo via modules

**Meta Cloud API vs Self-Hosted (WAHA/Baileys):**
- Meta Cloud API:
  - ✅ Official, robust, SLA-backed
  - ✅ Compliant with WhatsApp ToS
  - ❌ Requires business verification (2-5 days)
  - ❌ Cost per conversation (~R$ 0.10-0.50)
- Self-hosted (WAHA):
  - ✅ Free, fast to test
  - ❌ Violates WhatsApp ToS (risk of ban)
  - ❌ Not recommended for production

**Decision:** Start with WAHA for 2-week MVP test, migrate to Meta Cloud API for production.

## Consequences

### Positive

1. **Strategic Focus**: Strategos remains pure strategic brain, no operational bloat
2. **Best-in-Class Tools**: Odoo/ClickUp/WhatsApp are proven, mature solutions
3. **Reduced Development Time**: Avoid building CRM/task management from scratch (save 6-12 months)
4. **Lower Maintenance Burden**: Operational tools maintained by vendors/communities
5. **Better User Experience**: Purpose-built tools (ClickUp for tasks, WhatsApp for customers)
6. **Cost Effective**: R$ 100-300/month total (95% WhatsApp API), vs building custom
7. **Scalability**: Each layer scales independently
8. **Team Productivity**: Familiar tools (ClickUp, WhatsApp) reduce onboarding

### Negative

1. **Integration Complexity**: Multiple systems require webhook orchestration via Thalamus
2. **Data Synchronization**: Customer data spans Odoo + ClickUp + Strategos (needs consistency)
3. **Vendor Lock-in (ClickUp)**: SaaS dependency (mitigated: data export via API available)
4. **Learning Curve**: Team must learn Odoo (though well-documented)
5. **WhatsApp API Dependency**: Meta controls the platform (risk: policy changes)

### Mitigations

1. **Thalamus as Integration Layer**: Central event router prevents point-to-point integration mess
2. **Odoo as Source of Truth**: Customer/sales data primarily in Odoo, synced to others
3. **ClickUp Export Strategy**: Regular backups via API, documented migration path
4. **WhatsApp Fallback Plan**: If Meta API becomes problematic, fallback to web contact forms
5. **Clear Boundaries**: Documented responsibility matrix prevents scope creep

## Implementation

### Phase 1: Odoo Standalone (1-2 weeks)

**Goal:** Odoo running in k3s with core modules configured

```bash
# rbx-infra/apps/prod/odoo/
kubectl apply -f rbx-infra/apps/prod/odoo/

# Configure modules:
- CRM (sales_crm)
- Sales (sale_management)
- Accounting (account_accountant)
- Contacts (base)

# Add users:
- Admin
- Sales rep(s)
```

**Deliverable:** Odoo CRM operational, manual lead entry working

### Phase 2: WhatsApp Integration (1-2 weeks)

**Goal:** WhatsApp → Odoo CRM lead creation

**MVP (WAHA for testing):**
```yaml
# rbx-infra/apps/prod/waha/
apiVersion: apps/v1
kind: Deployment
metadata:
  name: waha
spec:
  containers:
  - name: waha
    image: devlikeapro/waha:latest
    env:
    - name: WAHA_API_KEY
      valueFrom:
        secretKeyRef:
          name: waha-secrets
          key: api-key
```

**Production (Meta Cloud API):**
- Business verification at Meta Business Suite
- Webhook: WhatsApp → Odoo via Thalamus
- Install Odoo module: `whatsapp_connector`

**Website Integration:**
```html
<!-- rbx-robotica-frontend / lda-front -->
<a href="https://wa.me/5551999999999?text=Olá,%20gostaria%20de%20um%20orçamento">
  <WhatsAppButton />
</a>
```

**Deliverable:** WhatsApp button on websites → lead in Odoo CRM

### Phase 3: ClickUp Integration (1 week)

**Goal:** Odoo lead qualified → ClickUp task auto-created

**Setup:**
1. Create ClickUp workspace: "RBX Operations"
2. Spaces: Sales, Projects, Administrative
3. Configure Odoo webhook:

```python
# Odoo automation rule
@api.model
def _create_clickup_task_on_qualified_lead(self):
    if self.stage_id.name == "Qualified":
        requests.post(
            'https://thalamus.rbx.ia.br/webhooks/odoo/lead-qualified',
            json={
                'lead_id': self.id,
                'company': self.partner_name,
                'value': self.expected_revenue
            }
        )
```

```go
// Thalamus webhook handler
func handleOdooLeadQualified(w http.ResponseWriter, r *http.Request) {
    // Parse Odoo webhook
    // Create ClickUp task via API
    clickupAPI.CreateTask(clickupListID, task)
}
```

**Deliverable:** Seamless Odoo → ClickUp task creation

### Phase 4: Strategos Integration (2-3 weeks)

**Goal:** Strategic decisions inform operations, operational metrics inform strategy

**Strategos Adapter:**
```go
// strategos-core/internal/adapters/odoo/client.go
package odoo

type Client struct {
    baseURL string
    apiKey  string
}

func (c *Client) GetMonthlyRevenue() (float64, error) {
    // Query Odoo XML-RPC API
}

func (c *Client) GetChurnRate() (float64, error) {
    // Calculate from Odoo data
}
```

**Webhooks:**
- Odoo → Thalamus → Strategos: High-value deals, churn alerts
- Strategos → Thalamus → Odoo: Strategic policies, approvals

**Strategos UI Dashboard:**
```
Objective: R$ 2M ARR Q4 2026
├─ Current: R$ 1.32M (66%)
├─ Source: Odoo monthly recurring revenue
└─ Risk: Churn 12% (threshold 10%) ⚠️
```

**Deliverable:** Integrated strategic + operational system

## Costs

| Item | Monthly Cost | Notes |
|------|--------------|-------|
| VPS Jaguar (existing) | R$ 0 | Already running k3s cluster |
| Odoo Community | R$ 0 | Open source, self-hosted |
| ClickUp Free Plan | R$ 0 | Up to 5 users |
| WhatsApp Business API | R$ 100-300 | Depends on message volume (~1000-3000 conversations/month) |
| **Total** | **R$ 100-300** | 95% of cost is WhatsApp API |

## Alternatives Considered

### Alternative 1: Build Everything Custom in Strategos

**Rejected because:**
- Violates Strategos philosophy (strategic-first, not operational)
- 6-12 months development time for CRM equivalent
- Ongoing maintenance burden distracts from strategic features
- Reinventing solved problems (CRM, task management)

### Alternative 2: Use Odoo for Everything (No ClickUp)

**Rejected because:**
- Odoo Projects module has poor UX for daily task management
- Team adoption/satisfaction critical for task tools
- ClickUp's notification system far superior
- Cost is R$ 0 (free plan sufficient)

### Alternative 3: Telegram Instead of WhatsApp

**Rejected because:**
- WhatsApp has 99% adoption in Brazil vs ~30% Telegram
- Business context: WhatsApp is expected channel
- Can add Telegram later if needed for internal notifications

### Alternative 4: Odoo Enterprise Instead of Community

**Deferred because:**
- Community edition sufficient for current needs
- R$ 150/user/month cost not justified yet
- Re-evaluate after 6 months of usage

## Related

- [Strategos Core Philosophy](../../strategos-core/PHILOSOPHY.md)
- [Strategic vs Tactical Boundaries](../../strategos-core/docs/architecture/strategic-vs-tactical.md)
- [Thalamus-Strategos Interaction](../../strategos-core/docs/architecture/thalamus-interaction.md)
- [Odoo Official Documentation](https://www.odoo.com/documentation/17.0/)
- [ClickUp API Documentation](https://clickup.com/api)
- [WhatsApp Business Platform](https://developers.facebook.com/docs/whatsapp)

## Next Steps

1. **Immediate (This Week):**
   - Install Odoo in k3s cluster
   - Configure CRM, Sales, Contacts modules
   - Add initial users

2. **Short Term (2 Weeks):**
   - Deploy WAHA for WhatsApp testing
   - Add WhatsApp buttons to websites
   - Test lead flow: WhatsApp → Odoo

3. **Medium Term (1 Month):**
   - Migrate to Meta Cloud API for production WhatsApp
   - Integrate ClickUp (Odoo → ClickUp webhooks)
   - Begin Strategos adapter development

4. **Long Term (3 Months):**
   - Full Strategos integration (bidirectional sync)
   - Strategic dashboard with Odoo metrics
   - Automated governance policies (approval workflows)

## Decision Makers

- **Proposed by:** AI Assistant (Claude Sonnet 4.5)
- **Approved by:** @psyctl (Leandro Damásio)
- **Date:** 2026-03-30
- **Status:** Accepted - Ready for Phase 1 implementation
