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
| **Operational ERP/CRM** | Odoo 20 Community | CRM, sales, accounting, contacts, WhatsApp integration | VPS Jaguar (k3s) |
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
│  Odoo 20 Community       │    │  ClickUp (SaaS)          │
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

#### Odoo 20 Community Edition

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

#### WhatsApp Business API

**Why WhatsApp:**
- ✅ 99% adoption rate in Brazil
- ✅ ~98% open rate (vs ~20% email)
- ✅ Natural conversational interface
- ✅ Supports bot automation + human handoff
- ✅ Native integration with Odoo via modules

**Three Options for WhatsApp Integration:**

##### Option 1: WAHA (WhatsApp HTTP API) - Self-Hosted

**Use case:** Testing and MVP (2-4 weeks maximum)

**Pros:**
- ✅ Free (self-hosted)
- ✅ Fast setup (5 minutes)
- ✅ Good for validating WhatsApp funnel hypothesis
- ✅ Full API compatibility (webhook, send messages, media)

**Cons:**
- ❌ **Violates WhatsApp ToS** (risk of number ban)
- ❌ Not production-grade (no SLA, no support)
- ❌ Requires QR code scan (manual setup)
- ❌ Not suitable for business critical operations

**Cost:** R$ 0 (compute already included in VPS)

**Recommendation:** Use ONLY for 2-4 week proof-of-concept to validate funnel effectiveness.

---

##### Option 2: 360dialog (Business Solution Provider)

**Use case:** Production-ready alternative if Meta Cloud API verification fails or takes too long

**What is 360dialog:**
- Official Meta Business Solution Provider (BSP)
- Pre-verified WhatsApp Business API access
- Faster onboarding than Meta Cloud API (1-2 days vs 5-7 days)
- Managed infrastructure with SLA

**Pros:**
- ✅ **Official and compliant** (BSP partner, no ToS violation)
- ✅ Faster onboarding (no business verification needed initially)
- ✅ Full WhatsApp Business API features (templates, media, webhooks)
- ✅ Managed infrastructure (no self-hosting)
- ✅ Good documentation and support
- ✅ Multiple integration options (REST API, webhooks, SDKs)
- ✅ Production-grade reliability (99.9% uptime SLA)

**Cons:**
- ❌ Higher cost than Meta Cloud API (~30-50% markup)
- ❌ Intermediary layer (adds dependency)
- ❌ Still requires phone number verification (but simpler process)

**Cost Structure (360dialog):**

| Tier | Monthly Fee | Conversation Cost | Best For |
|------|-------------|-------------------|----------|
| Free Tier | €0 | €0.05-0.10/conv | Testing (1000 conv/month) |
| Starter | €49/month | €0.045/conv | Small business (<5K conv/month) |
| Growth | €149/month | €0.038/conv | Medium business (<20K conv/month) |
| Enterprise | Custom | €0.025-0.035/conv | High volume (>20K conv/month) |

**Estimated monthly cost for RBX (1000-3000 conversations):**
- Free Tier: €50-300 = **R$ 275-1650/month**
- Starter Plan: €49 + (3000 × €0.045) = €184 = **R$ 1012/month**

**Key Features:**
- Template messages (marketing campaigns)
- Media support (images, documents, location)
- Webhook events (message received, delivered, read)
- Multi-agent support (multiple users answering)
- Contact management API
- Analytics dashboard

**360dialog vs Meta Cloud API Comparison:**

| Feature | 360dialog | Meta Cloud API |
|---------|-----------|----------------|
| **Setup Time** | 1-2 days | 5-7 days (after verification) |
| **Business Verification** | Optional initially | Required upfront |
| **Cost** | Higher (~30-50% markup) | Lower (direct from Meta) |
| **Reliability** | 99.9% SLA | 99.9% SLA |
| **Support** | Email + documentation | Self-service + Meta support |
| **API Compatibility** | 100% (WhatsApp Business API) | 100% (native) |
| **Infrastructure** | Managed by 360dialog | Managed by Meta |
| **Best For** | Faster time-to-market | Long-term cost optimization |

**Integration with Odoo:**

360dialog has native Odoo modules available:
- `whatsapp_360dialog` (community module)
- Webhook → Odoo CRM (automatic lead creation)
- Odoo inbox integration (respond directly from Odoo)

**Technical Integration:**

```yaml
# 360dialog webhook configuration
POST https://thalamus.rbx.ia.br/webhooks/360dialog/messages
{
  "messages": [{
    "from": "5551999999999",
    "text": { "body": "Olá, gostaria de um orçamento" },
    "timestamp": "1711800000"
  }]
}
```

```go
// Thalamus handler
func handle360DialogWebhook(w http.ResponseWriter, r *http.Request) {
    // Parse 360dialog webhook
    // Create lead in Odoo CRM
    // Send confirmation message via 360dialog API
}
```

**Migration Path:**

360dialog → Meta Cloud API migration is straightforward:
1. Both use same WhatsApp Business API standard
2. Same webhook payloads (minimal code changes)
3. Same phone number can be migrated
4. Downtime: ~2 hours for DNS/webhook updates

---

##### Option 3: Meta Cloud API (Direct)

**Use case:** Long-term production for cost optimization (after business verification)

**Pros:**
- ✅ Lowest cost (no intermediary markup)
- ✅ Direct relationship with Meta
- ✅ Full WhatsApp Business API features
- ✅ Best for high-volume (>5K conversations/month)

**Cons:**
- ❌ Requires business verification (Facebook Business Manager)
- ❌ Verification can take 5-7 days (or longer if rejected)
- ❌ More complex setup (Meta Business Suite, app creation)
- ❌ Requires valid business documents (CNPJ, proof of address)

**Cost Structure (Meta Cloud API):**

Based on conversation categories (Marketing, Utility, Authentication, Service):

| Conversation Type | Cost per Conversation | Use Case |
|-------------------|----------------------|----------|
| Marketing | R$ 0.50-0.80 | Promotional messages |
| Utility | R$ 0.30-0.50 | Order updates, notifications |
| Service | R$ 0.10-0.30 | Customer support (24h window) |
| Authentication | R$ 0.05-0.10 | OTP codes |

**Estimated monthly cost for RBX (1000-3000 service conversations):**
- Low: 1000 × R$ 0.10 = **R$ 100/month**
- High: 3000 × R$ 0.30 = **R$ 900/month**

**Business Verification Requirements:**
- Facebook Business Manager account
- CNPJ (company registration)
- Proof of business address
- Website with privacy policy
- Business category and description
- Phone number ownership proof

**Setup Time:** 5-7 days (if documents approved first try)

---

### Decision Matrix: Which WhatsApp Solution to Use?

| Scenario | Recommended Solution | Rationale |
|----------|---------------------|-----------|
| **Testing funnel hypothesis** | WAHA | Free, fast setup, validate before investing |
| **Need production ASAP** | 360dialog | Compliant, fast onboarding, managed |
| **Business verification likely to fail** | 360dialog | Alternative path, no Meta verification needed |
| **High volume (>5K conv/month)** | Meta Cloud API | Lower long-term cost |
| **Have valid business docs + time** | Meta Cloud API | Best cost, direct relationship |
| **Startup/small business** | 360dialog → Meta Cloud API | Start fast, migrate later |

### Recommended Approach for RBX Systems

**Phase 2A: MVP Testing (Week 1-2)**
```
WAHA (self-hosted)
├─ Purpose: Validate WhatsApp funnel effectiveness
├─ Risk: Limited (test period only)
└─ Cost: R$ 0

KPIs to measure:
- WhatsApp button click rate (website → WhatsApp)
- Response rate (bot vs human handoff)
- Lead quality (qualified vs spam)
- Conversion rate (WhatsApp lead → Odoo deal)
```

**Phase 2B: Production Rollout (Week 3-4)**

**If MVP succeeds:**

**Option A: 360dialog (RECOMMENDED for RBX)**
```
Why:
- ✅ Faster than Meta verification (1-2 days)
- ✅ Production-grade immediately
- ✅ Lower risk (official BSP)
- ✅ Can migrate to Meta later if volume grows

Cost: ~R$ 275-1012/month (depending on volume)
Setup: 1-2 days
```

**Option B: Meta Cloud API (If verification approved)**
```
Start verification process in parallel with WAHA testing.
If approved before 360dialog needed, go direct to Meta.

Cost: ~R$ 100-900/month
Setup: 5-7 days
```

**Decision Rule:**
- If Meta verification approved ≤ 3 days → Use Meta Cloud API
- If Meta verification pending > 3 days → Use 360dialog
- Migrate 360dialog → Meta Cloud API when volume > 5K conv/month

**Cost-Benefit Analysis (1 Year):**

| Solution | Setup Cost | Year 1 Cost (2K conv/month avg) | Total |
|----------|------------|----------------------------------|-------|
| WAHA | R$ 0 | R$ 0 ⚠️ (ToS violation risk) | R$ 0 |
| 360dialog | ~R$ 500 | R$ 6,072 (€49 + 2K×€0.045/mo) | R$ 6,572 |
| Meta Cloud API | ~R$ 500 | R$ 4,800 (2K×R$0.20/mo) | R$ 5,300 |

**Savings if migrating after 6 months:**
- Start with 360dialog: R$ 3,036 (6 months)
- Migrate to Meta: R$ 2,400 (6 months)
- Total: R$ 5,436 vs R$ 6,572 (saves R$ 1,136)

**Final Decision:** Start with WAHA (2 weeks test) → 360dialog (production) → Meta Cloud API (when volume justifies migration)

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

**Goal:** WhatsApp → Odoo CRM lead creation with production-grade reliability

#### Phase 2A: MVP Testing with WAHA (Week 1)

**Purpose:** Validate WhatsApp funnel hypothesis before investing in paid API

**Deploy WAHA:**
```yaml
# rbx-infra/apps/prod/waha/deploy.yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: waha
  namespace: odoo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: waha
  template:
    metadata:
      labels:
        app: waha
    spec:
      containers:
      - name: waha
        image: devlikeapro/waha:latest
        ports:
        - containerPort: 3000
        env:
        - name: WAHA_API_KEY
          valueFrom:
            secretKeyRef:
              name: waha-secrets
              key: api-key
        - name: WHATSAPP_HOOK_URL
          value: "https://thalamus.rbx.ia.br/webhooks/whatsapp/messages"
---
apiVersion: v1
kind: Service
metadata:
  name: waha
  namespace: odoo
spec:
  selector:
    app: waha
  ports:
  - port: 3000
    targetPort: 3000
```

**Configure Webhook Handler (Thalamus):**
```go
// thalamus/internal/webhooks/whatsapp.go
func HandleWhatsAppMessage(w http.ResponseWriter, r *http.Request) {
    var msg WhatsAppMessage
    json.NewDecoder(r.Body).Decode(&msg)

    // Create lead in Odoo CRM
    odooClient.CreateLead(Lead{
        Name: msg.From,
        Phone: msg.From,
        Message: msg.Text.Body,
        Source: "WhatsApp",
    })

    // Send confirmation
    waha.SendMessage(msg.From, "Obrigado! Em breve entraremos em contato.")
}
```

**Testing Checklist:**
- [ ] QR code scan successful
- [ ] Webhook receiving messages
- [ ] Lead created in Odoo
- [ ] Auto-reply sent
- [ ] Measure: click-through rate, response rate, lead quality

**Duration:** 1 week (5-7 days of data)

**Success Criteria:** If >20% of website visitors click WhatsApp button AND >50% send message → proceed to production

---

#### Phase 2B: Production Deployment (Week 2)

**Decision point:** Choose production WhatsApp solution based on business verification status

##### Option A: 360dialog (RECOMMENDED - fastest to production)

**When to use:**
- Meta business verification not approved yet
- Need production ASAP
- Want managed infrastructure

**Setup Steps:**

1. **Create 360dialog Account:**
   - Go to https://hub.360dialog.com/
   - Sign up with business email
   - Choose Free or Starter plan

2. **Connect Phone Number:**
   - Verify phone number ownership (SMS code)
   - Complete basic business profile
   - No extensive Meta verification required initially

3. **Generate API Key:**
   ```bash
   # 360dialog Hub → API Keys → Create New Key
   API_KEY=your_api_key_here
   ```

4. **Configure Webhook:**
   ```bash
   curl -X POST https://waba.360dialog.io/v1/configs/webhook \
     -H "D360-API-KEY: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://thalamus.rbx.ia.br/webhooks/360dialog/messages"
     }'
   ```

5. **Update Thalamus Handler:**
   ```go
   // thalamus/internal/webhooks/360dialog.go
   func Handle360DialogWebhook(w http.ResponseWriter, r *http.Request) {
       var payload struct {
           Messages []struct {
               From string `json:"from"`
               Text struct {
                   Body string `json:"body"`
               } `json:"text"`
           } `json:"messages"`
       }
       json.NewDecoder(r.Body).Decode(&payload)

       for _, msg := range payload.Messages {
           // Create lead in Odoo
           odooClient.CreateLead(Lead{
               Name: msg.From,
               Phone: msg.From,
               Message: msg.Text.Body,
               Source: "WhatsApp (360dialog)",
           })

           // Send via 360dialog API
           send360DialogMessage(msg.From, "Obrigado! Em breve entraremos em contato.")
       }
   }

   func send360DialogMessage(to, text string) error {
       return http.Post(
           "https://waba.360dialog.io/v1/messages",
           map[string]interface{}{
               "to": to,
               "type": "text",
               "text": map[string]string{"body": text},
           },
       )
   }
   ```

6. **Install Odoo Module:**
   ```bash
   # Install community module for 360dialog
   cd odoo/addons
   git clone https://github.com/OCA/social.git
   # Enable module: whatsapp_360dialog
   ```

**Timeline:** 1-2 days from signup to production

**Cost:** R$ 275-1012/month (depending on volume)

---

##### Option B: Meta Cloud API (if verification approved)

**When to use:**
- Business verification already completed
- Lower long-term cost priority
- Direct relationship with Meta preferred

**Setup Steps:**

1. **Meta Business Manager Setup:**
   - Create Business Manager account
   - Add business details (CNPJ, address)
   - Upload verification documents

2. **Create WhatsApp Business App:**
   - Go to developers.facebook.com
   - Create App → Business → WhatsApp
   - Add phone number
   - Generate permanent access token

3. **Configure Webhook:**
   ```bash
   # Register webhook with Meta
   curl -X POST "https://graph.facebook.com/v18.0/$PHONE_NUMBER_ID/messages" \
     -H "Authorization: Bearer $ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "messaging_product": "whatsapp",
       "to": "test_number",
       "type": "template",
       "template": { "name": "hello_world", "language": { "code": "en_US" } }
     }'
   ```

4. **Update Thalamus for Meta API:**
   ```go
   // thalamus/internal/webhooks/meta_whatsapp.go
   func HandleMetaWebhook(w http.ResponseWriter, r *http.Request) {
       // Verify webhook signature
       if !verifyMetaSignature(r) {
           w.WriteHeader(403)
           return
       }

       // Process messages (similar structure to 360dialog)
   }
   ```

**Timeline:** 5-7 days (after verification approval)

**Cost:** R$ 400-600/month

---

#### Website Integration (Both Options)

**Add WhatsApp Button to Frontend:**

```typescript
// rbx-robotica-frontend/components/WhatsAppButton.tsx
export function WhatsAppButton() {
  const phoneNumber = "5551999999999"; // Replace with actual number
  const defaultMessage = encodeURIComponent(
    "Olá! Vim pelo site e gostaria de saber mais sobre soluções robóticas."
  );

  return (
    <a
      href={`https://wa.me/${phoneNumber}?text=${defaultMessage}`}
      target="_blank"
      rel="noopener noreferrer"
      className="whatsapp-button"
    >
      <WhatsAppIcon />
      Fale Conosco
    </a>
  );
}
```

```tsx
// lda-front/components/ContactSection.tsx
export function ContactSection() {
  const phoneNumber = "5551999999999";
  const message = encodeURIComponent(
    "Hi! I came from the website and would like to know more about your services."
  );

  return (
    <section>
      <h2>Get in Touch</h2>
      <a href={`https://wa.me/${phoneNumber}?text=${message}`}>
        <WhatsAppButton />
      </a>
    </section>
  );
}
```

**Deploy Updates:**
```bash
# rbx-robotica-frontend
git add components/WhatsAppButton.tsx
git commit -m "feat: add WhatsApp contact button"
git push

# lda-front
git add components/ContactSection.tsx
git commit -m "feat: add WhatsApp contact integration"
git push

# ArgoCD auto-syncs and deploys
```

---

#### Phase 2 Deliverables

**Week 1 (WAHA MVP):**
- [ ] WAHA deployed and running
- [ ] WhatsApp buttons live on both websites
- [ ] Webhook → Odoo CRM working
- [ ] Metrics collected (click rate, conversion)

**Week 2 (Production):**
- [ ] 360dialog OR Meta Cloud API configured
- [ ] Production webhooks operational
- [ ] Odoo CRM receiving real leads
- [ ] Auto-reply messages working
- [ ] WAHA decommissioned

**Success Metrics:**
- Website → WhatsApp click rate: >20%
- WhatsApp → Lead conversion: >50%
- Response time: <5 minutes (business hours)
- Lead quality: >70% qualified (not spam)

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

### Monthly Operating Costs

| Item | Monthly Cost | Notes |
|------|--------------|-------|
| VPS Jaguar (existing) | R$ 0 | Already running k3s cluster |
| Odoo Community | R$ 0 | Open source, self-hosted |
| ClickUp Free Plan | R$ 0 | Up to 5 users |
| **WhatsApp Integration** | **Variable** | **See breakdown below** |

### WhatsApp Integration Cost Breakdown

| Solution | Setup Cost | Monthly Cost (2K conversations) | Production Ready | Compliance |
|----------|------------|----------------------------------|------------------|------------|
| **WAHA (self-hosted)** | R$ 0 | R$ 0 | ❌ Testing only | ⚠️ Violates ToS |
| **360dialog** | ~R$ 500 | R$ 505-1012 | ✅ Yes | ✅ Official BSP |
| **Meta Cloud API** | ~R$ 500 | R$ 400-600 | ✅ Yes | ✅ Official |

**Recommended Path (Phased Approach):**

| Phase | Duration | Solution | Cost | Purpose |
|-------|----------|----------|------|---------|
| 1. MVP Test | 2 weeks | WAHA | R$ 0 | Validate funnel hypothesis |
| 2. Production Start | Month 1-6 | 360dialog | ~R$ 505-1012/mo | Fast production rollout |
| 3. Cost Optimization | Month 7+ | Meta Cloud API | ~R$ 400-600/mo | Long-term cost reduction |

**Total First Year Cost Estimate:**

```
Phase 1 (WAHA): R$ 0 × 0.5 months = R$ 0
Phase 2 (360dialog): R$ 758 × 6 months = R$ 4,548
Phase 3 (Meta Cloud): R$ 500 × 6 months = R$ 3,000
Setup costs: R$ 1,000

Total Year 1: ~R$ 8,548 (~R$ 712/month average)
```

**Cost Comparison (3-year projection):**

| Scenario | Year 1 | Year 2 | Year 3 | Total |
|----------|--------|--------|--------|-------|
| **360dialog only** | R$ 9,096 | R$ 9,096 | R$ 9,096 | R$ 27,288 |
| **Meta Cloud API only** | R$ 6,000 | R$ 6,000 | R$ 6,000 | R$ 18,000 |
| **Recommended (360d→Meta)** | R$ 8,548 | R$ 6,000 | R$ 6,000 | R$ 20,548 |

**Savings with recommended approach:** R$ 6,740 over 3 years vs 360dialog-only

### Total Monthly Cost Summary

| Scenario | Infrastructure | WhatsApp | Total/Month |
|----------|----------------|----------|-------------|
| **MVP Testing** | R$ 0 | R$ 0 | **R$ 0** |
| **Production (360dialog)** | R$ 0 | R$ 505-1012 | **R$ 505-1012** |
| **Production (Meta Cloud)** | R$ 0 | R$ 400-600 | **R$ 400-600** |

**Key Insight:** 100% of production cost is WhatsApp API. All other tools (Odoo, ClickUp, VPS) are zero marginal cost.

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
- [Odoo Official Documentation](https://www.odoo.com/documentation/19.0/)
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
