# Next Phase Requirements - Chat Interface & Validation System

**To:** New Frontend/Testing Developer  
**From:** Core Pipeline Team  
**Date:** June 18, 2026  
**Project Phase:** Chat Interface + Dashboard + Validation Agent  
**Status:** Requirements & Integration Guide

---

## 📋 Table of Contents

- [What's Already Built](#whats-already-built)
- [New Requirements Overview](#new-requirements-overview)
- [Requirement 1: Chat Interface](#requirement-1-chat-interface)
- [Requirement 2: User Dashboard](#requirement-2-user-dashboard)
- [Requirement 3: Test Validation Agent](#requirement-3-test-validation-agent)
- [Integration Architecture](#integration-architecture)
- [Implementation Guide](#implementation-guide)
- [Technical Specifications](#technical-specifications)
- [Testing Strategy](#testing-strategy)
- [Timeline Estimate](#timeline-estimate)

---

## 🎯 What's Already Built

### Current System (Production-Ready)

**Core Pipeline:**
- ✅ **9 AI Agents** that analyze variants and apply ACMG criteria
- ✅ **Multi-agent debate** system for final classification
- ✅ **Report generation** in 3 formats (HTML, XLSX, TSV)
- ✅ **VEP annotation** with full database integration
- ✅ **Semantic memory** (MemPalace) that remembers all analyses
- ✅ **Solo & Trio analysis** modes (single sample or proband + parents)

**API Layer:**
- ✅ **REST API** with 15+ endpoints (FastAPI)
- ✅ **User management** (registration, authentication, quotas)
- ✅ **Admin panel** (6 admin endpoints)
- ✅ **Real-time progress** via Server-Sent Events (SSE)
- ✅ **Email system** for verification codes
- ✅ **Database** (PostgreSQL + pgvector) with 4 tables

**Current Frontend:**
- ✅ **Web UI** with HTML forms
  - Registration form
  - VCF upload form
  - Progress bar with live updates
  - Download buttons for reports

### How It Works Now

```
User Flow (Current):
1. User opens browser → Web form
2. User fills form (VCF, genome build, clinical notes, HPO terms)
3. For trio analysis: upload proband + parent VCFs (optional BAM files)
4. User clicks "Submit"
5. Progress bar shows real-time updates
6. User downloads reports (HTML/XLSX/TSV)

Analysis Modes:
- Solo: Single VCF (proband only)
- Trio: Proband + Mother + Father VCFs
  - Enables de novo detection (PS2, PM6)
  - Compound heterozygote analysis (PM3)
  - Segregation analysis (PP1, BS4, BS2)
```

### What You'll Replace

**Replace:** Form-based UI → **Chat-based UI**  
**Keep:** All backend (API, pipeline, database, agents)  
**Add:** Dashboard + Test validation system

---

## 🆕 New Requirements Overview

### Three New Components

| Component | Purpose | Complexity |
|-----------|---------|------------|
| **1. Chat Interface** | Replace forms with conversational UI | High |
| **2. User Dashboard** | Show patient analysis status | Medium |
| **3. Test Validation Agent** | Compare pipeline vs expected results | Medium |

### User Types

**End User (Chat + Dashboard):**
- Researchers/clinicians analyzing patient VCFs
- Interact via chat interface
- View analysis status on dashboard

**Admin (Validation System):**
- QA team validating pipeline accuracy
- Submit test cases with expected classifications
- Review comparison reports

---

## 💬 Requirement 1: Chat Interface

### Overview

**Replace the current form-based UI with a conversational AI assistant.**

Users interact with the pipeline through natural conversation instead of filling forms.

### User Experience

#### Registration Flow

```
Chat Interface:
┌─────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant                          [≡] │
├─────────────────────────────────────────────────┤
│                                                 │
│  🤖 Hi! I'm your ACMG variant analysis         │
│     assistant. Let's get you registered.       │
│     What's your email address?                  │
│                                                 │
│  👤 researcher@lab.edu                          │
│                                                 │
│  🤖 Great! What's your name?                    │
│                                                 │
│  👤 Dr. Jane Smith                              │
│                                                 │
│  🤖 And which organization are you with?        │
│                                                 │
│  👤 University Genomics Lab                     │
│                                                 │
│  🤖 Perfect! Do you have an NCBI API key        │
│     for faster PubMed searches? (optional)      │
│     Type 'skip' if you don't have one.          │
│                                                 │
│  👤 skip                                         │
│                                                 │
│  🤖 ✓ Registration complete!                    │
│     Your API key: abc123...xyz789               │
│     ⚠️ Save this key - you'll need it to       │
│     log in later.                               │
│                                                 │
│  [Type your message...]                     [→] │
└─────────────────────────────────────────────────┘
```

#### Analysis Submission Flow - Solo Mode

```
Chat Interface:
┌─────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant                          [≡] │
├─────────────────────────────────────────────────┤
│                                                 │
│  👤 I want to analyze a VCF file                │
│                                                 │
│  🤖 Sure! What type of analysis?                │
│     1️⃣ Solo (single sample)                    │
│     2️⃣ Trio (proband + parents)                │
│                                                 │
│  👤 1                                            │
│                                                 │
│  🤖 Perfect! Please upload your VCF file.       │
│     [📎 Attach File]                            │
│                                                 │
│  👤 [📄 proband.vcf.gz uploaded]                │
│                                                 │
│  🤖 Got it! Which genome build?                 │
│     1️⃣ GRCh38 (recommended)                    │
│     2️⃣ GRCh37                                   │
│                                                 │
│  👤 1                                            │
│                                                 │
│  🤖 Perfect! What's the patient's sex?          │
│     1️⃣ Male                                     │
│     2️⃣ Female                                   │
│     3️⃣ Unknown                                  │
│                                                 │
│  👤 Female                                       │
│                                                 │
│  🤖 Do you have clinical notes for this         │
│     patient? (helps with phenotype matching)    │
│                                                 │
│  👤 Patient presents with seizures and          │
│     developmental delay                         │
│                                                 │
│  🤖 Thanks! Any HPO terms? (optional)           │
│     Example: HP:0001250, HP:0001263             │
│     Type 'skip' if you don't have any.          │
│                                                 │
│  👤 HP:0001250, HP:0001263                      │
│                                                 │
│  🤖 Perfect! I have everything I need.          │
│     Ready to start analysis?                    │
│     [✓ Start Analysis] [✗ Cancel]               │
│                                                 │
│  👤 [clicks Start Analysis]                     │
│                                                 │
│  🤖 ✓ Analysis started!                         │
│     Session ID: session_abc123                  │
│                                                 │
│     ┌─────────────────────────────────────┐    │
│     │ 📊 Progress: 25%                    │    │
│     │ VEP annotation complete             │    │
│     │                                     │    │
│     │ [View Logs ▼]                      │    │
│     └─────────────────────────────────────┘    │
│                                                 │
│     I'll notify you when it's complete!         │
│     This usually takes 5-10 minutes.            │
│                                                 │
│  [Type your message...]                     [→] │
└─────────────────────────────────────────────────┘
```

#### Analysis Submission Flow - Trio Mode

```
Chat Interface:
┌─────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant                          [≡] │
├─────────────────────────────────────────────────┤
│                                                 │
│  👤 I want to analyze a VCF file                │
│                                                 │
│  🤖 Sure! What type of analysis?                │
│     1️⃣ Solo (single sample)                    │
│     2️⃣ Trio (proband + parents)                │
│                                                 │
│  👤 2                                            │
│                                                 │
│  🤖 Great! Trio analysis enables:               │
│     • De novo variant detection (PS2, PM6)      │
│     • Compound het analysis (PM3)               │
│     • Segregation analysis (PP1, BS4)           │
│                                                 │
│     Let's start! Upload proband VCF:            │
│     [📎 Attach Proband VCF]                     │
│                                                 │
│  👤 [📄 proband.vcf.gz uploaded]                │
│                                                 │
│  🤖 ✓ Got it! Now upload mother's VCF:          │
│     [📎 Attach Mother VCF]                      │
│                                                 │
│  👤 [📄 mother.vcf.gz uploaded]                 │
│                                                 │
│  🤖 ✓ Perfect! Now upload father's VCF:         │
│     [📎 Attach Father VCF]                      │
│                                                 │
│  👤 [📄 father.vcf.gz uploaded]                 │
│                                                 │
│  🤖 Excellent! Do you have BAM files?           │
│     (Optional - enables phasing for compound    │
│     heterozygote detection)                     │
│     1️⃣ Yes, upload BAM files                   │
│     2️⃣ No, skip BAM files                      │
│                                                 │
│  👤 1                                            │
│                                                 │
│  🤖 Upload proband BAM:                         │
│     [📎 Attach Proband BAM]                     │
│                                                 │
│  👤 [📄 proband.bam uploaded]                   │
│                                                 │
│  🤖 Upload mother BAM:                          │
│     [📎 Attach Mother BAM]                      │
│                                                 │
│  👤 [📄 mother.bam uploaded]                    │
│                                                 │
│  🤖 Upload father BAM:                          │
│     [📎 Attach Father BAM]                      │
│                                                 │
│  👤 [📄 father.bam uploaded]                    │
│                                                 │
│  🤖 Perfect! Now, which genome build?           │
│     1️⃣ GRCh38 (recommended)                    │
│     2️⃣ GRCh37                                   │
│                                                 │
│  👤 1                                            │
│                                                 │
│  🤖 What's the proband's sex?                   │
│     1️⃣ Male                                     │
│     2️⃣ Female                                   │
│                                                 │
│  👤 Male                                         │
│                                                 │
│  🤖 Clinical notes for proband?                 │
│                                                 │
│  👤 Suspected recessive disorder, consanguinity │
│                                                 │
│  🤖 Any HPO terms? (optional)                   │
│                                                 │
│  👤 skip                                         │
│                                                 │
│  🤖 ✓ Ready! Review:                            │
│     • Mode: Trio with BAM files                 │
│     • Proband: proband.vcf.gz, proband.bam      │
│     • Mother: mother.vcf.gz, mother.bam         │
│     • Father: father.vcf.gz, father.bam         │
│     • Build: GRCh38                             │
│                                                 │
│     [✓ Start Trio Analysis] [✗ Cancel]          │
│                                                 │
│  👤 [clicks Start Trio Analysis]                │
│                                                 │
│  🤖 ✓ Trio analysis started!                    │
│     Session ID: session_trio456                 │
│                                                 │
│     ┌─────────────────────────────────────┐    │
│     │ 📊 Progress: 15%                    │    │
│     │ Running WhatsHap phasing...         │    │
│     │                                     │    │
│     │ [View Logs ▼]                      │    │
│     └─────────────────────────────────────┘    │
│                                                 │
│     Trio analysis takes 15-30 minutes.          │
│     I'll notify you when complete!              │
│                                                 │
│  [Type your message...]                     [→] │
└─────────────────────────────────────────────────┘
```

#### Results Delivery - Solo Analysis

```
Chat Interface:
┌─────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant                          [≡] │
├─────────────────────────────────────────────────┤
│                                                 │
│  🤖 🎉 Solo analysis complete!                  │
│                                                 │
│     ┌─────────────────────────────────────┐    │
│     │ 📊 Results Summary                  │    │
│     │                                     │    │
│     │ • Variants analyzed: 3              │    │
│     │ • Pathogenic: 1                     │    │
│     │ • VUS: 1                            │    │
│     │ • Benign: 1                         │    │
│     │                                     │    │
│     │ [View Logs ▼]                      │    │
│     └─────────────────────────────────────┘    │
│                                                 │
│     Download your reports:                      │
│     📄 [HTML Report]                            │
│     📊 [Excel Report]                           │
│     📋 [TSV Report]                             │
│                                                 │
│     Want to analyze another VCF?                │
│     Just say "analyze new vcf"!                 │
│                                                 │
│  [Type your message...]                     [→] │
└─────────────────────────────────────────────────┘
```

#### Results Delivery - Trio Analysis

```
Chat Interface:
┌─────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant                          [≡] │
├─────────────────────────────────────────────────┤
│                                                 │
│  🤖 🎉 Trio analysis complete!                  │
│                                                 │
│     ┌─────────────────────────────────────┐    │
│     │ 📊 Results Summary                  │    │
│     │                                     │    │
│     │ • Variants analyzed: 5              │    │
│     │ • Pathogenic: 2                     │    │
│     │   - 1 de novo (PS2)                 │    │
│     │   - 1 compound het (PM3)            │    │
│     │ • VUS: 2                            │    │
│     │ • Benign: 1                         │    │
│     │                                     │    │
│     │ Trio-specific findings:             │    │
│     │ • De novo variants: 1               │    │
│     │ • Compound hets: 1 pair             │    │
│     │ • Segregating variants: 2           │    │
│     │                                     │    │
│     │ [View Logs ▼]                      │    │
│     └─────────────────────────────────────┘    │
│                                                 │
│     Download your reports:                      │
│     📄 [HTML Report]                            │
│     📊 [Excel Report]                           │
│     📋 [TSV Report]                             │
│                                                 │
│     💡 Trio mode applied these criteria:        │
│     • PS2 (de novo, parental testing)           │
│     • PM3 (compound heterozygote)               │
│     • PP1 (segregation with disease)            │
│                                                 │
│     Want to analyze another case?               │
│     Just say "analyze new vcf"!                 │
│                                                 │
│  [Type your message...]                     [→] │
└─────────────────────────────────────────────────┘
```

### Chat Features Required

#### 1. **Conversational Flow**
- Natural language understanding
- Context awareness (remember user's previous inputs)
- Smart follow-up questions
- Handle typos/variations ("yes"/"yep"/"sure" all work)

#### 2. **Validation & Error Handling**
```
Example - Missing Required Field:

🤖 Please upload your VCF file.
👤 [tries to proceed without uploading]

🤖 ⚠️ I still need a VCF file to proceed.
   Please upload your file using the
   [📎 Attach File] button.
```

#### 3. **File Uploads**
- Drag & drop VCF files into chat
- Show file name and size
- Validate file format (.vcf or .vcf.gz)
- Progress indicator for large uploads

#### 4. **Progress Display**
```
┌─────────────────────────────────────┐
│ 📊 Progress: 48%                    │
│ Processing BRCA2 variant (2/3)      │
│                                     │
│ [View Logs ▼]                      │
└─────────────────────────────────────┘

Expanded logs view:
┌─────────────────────────────────────┐
│ 📊 Progress: 48%                    │
│ Processing BRCA2 variant (2/3)      │
│                                     │
│ [Hide Logs ▲]                      │
│                                     │
│ ┌─ Logs ─────────────────────────┐ │
│ │ [12:30] VEP annotation started  │ │
│ │ [12:31] VEP complete - 3 vars   │ │
│ │ [12:32] Agent 1: Pop Freq       │ │
│ │ [12:33] Agent 2: Consequence    │ │
│ │ [12:34] Agent 3: In Silico      │ │
│ │ [12:35] Processing variant 2... │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

#### 5. **Quick Actions**
- Predefined buttons for common choices
- Example: "GRCh38 / GRCh37" instead of typing
- "Start Analysis" button instead of typing "yes"

#### 6. **Command Support**
Users can type commands:
- `/register` - Start registration
- `/analyze` - Start new analysis (choose solo or trio)
- `/analyze-solo` - Quick start solo analysis
- `/analyze-trio` - Quick start trio analysis
- `/status [session_id]` - Check analysis status
- `/history` - View past analyses
- `/download [session_id]` - Download reports
- `/help` - Show available commands

### Chat Capabilities by Feature

| Feature | Chat Command/Flow |
|---------|-------------------|
| **Registration** | Natural conversation collecting email, name, org, NCBI key |
| **Login** | "My API key is..." or paste key |
| **Submit Solo Analysis** | Upload VCF → Answer questions → Confirm |
| **Submit Trio Analysis** | Upload 3 VCFs (+ optional BAM files) → Questions → Confirm |
| **Check Status** | `/status session_abc` or "How's my analysis going?" |
| **View History** | `/history` or "Show my past analyses" |
| **Download Reports** | Click download buttons in results message |
| **Key Recovery** | "I forgot my API key" → Email verification flow |
| **Help** | `/help` or "What can you do?" |

### Technical Implementation Notes

**Frontend Stack:**
- React + TypeScript (recommended)
- Chat UI library: `@chatscope/chat-ui-kit-react` or similar
- File upload: `react-dropzone`
- Real-time updates: EventSource (existing SSE from backend)

**Backend Integration:**
- **No changes needed to existing API!**
- Chat frontend calls existing REST endpoints
- Use SSE for progress updates (already working)
- Parse natural language on frontend, map to API calls

**State Management:**
- Track conversation context (what info collected)
- Remember user's session across messages
- Store uploaded files in memory until submission

---

## 📊 Requirement 2: User Dashboard

### Overview

**A dashboard showing the status of all patient analyses for logged-in users.**

Separate from chat - accessible via navigation menu.

### Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ 🤖 ACMG Assistant              [💬 Chat] [📊 Dashboard] [⚙️]    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  📊 My Analyses Dashboard                                       │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  ┌─ Filters ──────────────────────────────────────────────┐    │
│  │ Status: [All ▼]  Date: [Last 30 days ▼]  [Search...]  │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Patient ID: PT001 (Solo)                  [View] [📥]   │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │ VCF Submitted:      ✓ Yes  │  Jun 18, 2026 10:30 AM    │   │
│  │ Clinical History:   ✓ Yes  │  Jun 18, 2026 10:32 AM    │   │
│  │ Results Available:  ✓ Yes  │  Jun 18, 2026 10:45 AM    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Patient ID: PT002 (Trio)                  [View] [📥]   │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │ VCF Submitted:      ✓ Yes (3 samples)  │  09:15 AM      │   │
│  │ BAM Files:          ✓ Yes (3 samples)  │  09:20 AM      │   │
│  │ Clinical History:   ✓ Yes  │  Jun 18, 2026 09:17 AM    │   │
│  │ Results Available:  ⏳ In Progress (67%)                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Patient ID: PT003 (Solo)                  [View]        │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │ VCF Submitted:      ✓ Yes  │  Jun 18, 2026 08:00 AM    │   │
│  │ Clinical History:   ✗ No   │  -                        │   │
│  │ Results Available:  ⏳ Queued                            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  [← Prev]  Page 1 of 3  [Next →]                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Dashboard Features

#### 1. **Overview Cards**
Each patient analysis shown as a card with:
- **Patient ID** (user-defined or auto-generated)
- **Analysis Mode**: Solo or Trio (badge/label)
- **VCF Status**: 
  - Solo: Yes/No + timestamp
  - Trio: Yes (3 samples) + timestamp
- **BAM Status** (Trio only): Yes (3 samples) / No
- **Clinical History Status**: Yes/No + timestamp
- **Results Status**: 
  - ✓ Complete + timestamp + download button
  - ⏳ In Progress (XX%)
  - 🕐 Queued
  - ✗ Failed + error message

#### 2. **Filters & Search**
- Filter by status (Complete, In Progress, Queued, Failed)
- Filter by date range
- Search by patient ID or VCF filename

#### 3. **Actions**
- **[View]** - Open detailed view with:
  - Full analysis parameters
  - Progress timeline
  - Variant summary
  - Download links
- **[📥]** - Quick download all reports (HTML/XLSX/TSV as ZIP)

#### 4. **Detailed View Modal - Solo Analysis**

```
┌─────────────────────────────────────────────────────────────┐
│ Patient Analysis Details                           [✗ Close] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Patient ID: PT001                                          │
│  Session ID: session_abc123                                 │
│  Mode: Solo                                                 │
│                                                             │
│  📄 Input Files                                             │
│  ─────────────────────────────────────────────────────────  │
│  VCF File:     proband.vcf.gz (2.3 MB)                     │
│  Uploaded:     Jun 18, 2026 10:30 AM                       │
│                                                             │
│  📝 Clinical Information                                    │
│  ─────────────────────────────────────────────────────────  │
│  Clinical Notes:  Patient presents with seizures and       │
│                   developmental delay.                      │
│  HPO Terms:       HP:0001250, HP:0001263                    │
│  Provided:        Jun 18, 2026 10:32 AM                    │
│                                                             │
│  📊 Results                                                 │
│  ─────────────────────────────────────────────────────────  │
│  Status:          ✓ Complete                                │
│  Completed:       Jun 18, 2026 10:45 AM                    │
│  Duration:        15 minutes                                │
│                                                             │
│  Variants Analyzed:  3                                      │
│    • Pathogenic:     1 (BRCA2 p.Arg2520His)                │
│    • VUS:            1 (CFTR p.Ile506Val)                  │
│    • Benign:         1 (OR4F5 p.Thr123Ala)                 │
│                                                             │
│  📥 Download Reports                                        │
│  [HTML Report] [Excel Report] [TSV Report] [All as ZIP]    │
│                                                             │
│  📈 Analysis Timeline                                       │
│  ─────────────────────────────────────────────────────────  │
│  10:30 AM  ✓ VCF uploaded                                  │
│  10:32 AM  ✓ Clinical history provided                     │
│  10:32 AM  ✓ Analysis started                              │
│  10:33 AM  ✓ VEP annotation complete                       │
│  10:40 AM  ✓ Evidence collection complete                  │
│  10:43 AM  ✓ Debate complete                               │
│  10:45 AM  ✓ Reports generated                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 4b. **Detailed View Modal - Trio Analysis**

```
┌─────────────────────────────────────────────────────────────┐
│ Patient Analysis Details                           [✗ Close] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Patient ID: PT002                                          │
│  Session ID: session_trio456                                │
│  Mode: Trio (Proband + Parents)                            │
│                                                             │
│  📄 Input Files                                             │
│  ─────────────────────────────────────────────────────────  │
│  Proband VCF:  proband.vcf.gz (2.3 MB)                     │
│  Mother VCF:   mother.vcf.gz (2.1 MB)                      │
│  Father VCF:   father.vcf.gz (2.2 MB)                      │
│  Proband BAM:  proband.bam (15.2 GB)                       │
│  Mother BAM:   mother.bam (14.8 GB)                        │
│  Father BAM:   father.bam (15.0 GB)                        │
│  Uploaded:     Jun 18, 2026 09:15 AM                       │
│                                                             │
│  📝 Clinical Information                                    │
│  ─────────────────────────────────────────────────────────  │
│  Clinical Notes:  Proband has suspected recessive disorder,│
│                   consanguinity reported, parents unaffected│
│  HPO Terms:       HP:0001250, HP:0001263                    │
│  Provided:        Jun 18, 2026 09:17 AM                    │
│                                                             │
│  📊 Results                                                 │
│  ─────────────────────────────────────────────────────────  │
│  Status:          ✓ Complete                                │
│  Completed:       Jun 18, 2026 09:45 AM                    │
│  Duration:        30 minutes                                │
│                                                             │
│  Variants Analyzed:  5                                      │
│    • Pathogenic:     2                                      │
│      - BRCA2 p.Arg2520His (de novo, PS2)                   │
│      - ATM p.Val2424Gly + p.Ser1691del (compound het, PM3) │
│    • VUS:            2                                      │
│    • Benign:         1                                      │
│                                                             │
│  Trio-Specific Findings:                                    │
│    • De novo variants: 1 (confirmed with parental VCFs)    │
│    • Compound heterozygotes: 1 pair (phased with BAMs)     │
│    • Segregating variants: 2 (PP1 applied)                 │
│                                                             │
│  📥 Download Reports                                        │
│  [HTML Report] [Excel Report] [TSV Report] [All as ZIP]    │
│                                                             │
│  📈 Analysis Timeline                                       │
│  ─────────────────────────────────────────────────────────  │
│  09:15 AM  ✓ All VCF & BAM files uploaded                  │
│  09:17 AM  ✓ Clinical history provided                     │
│  09:17 AM  ✓ Trio analysis started                         │
│  09:18 AM  ✓ WhatsHap phasing initiated                    │
│  09:20 AM  ✓ Phasing complete                              │
│  09:21 AM  ✓ VEP annotation complete                       │
│  09:25 AM  ✓ De novo detection complete                    │
│  09:30 AM  ✓ Compound het analysis complete                │
│  09:40 AM  ✓ Evidence collection complete                  │
│  09:43 AM  ✓ Debate complete                               │
│  09:45 AM  ✓ Reports generated                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Data Model

**Add to database (extend `sessions` table):**

```sql
ALTER TABLE sessions ADD COLUMN patient_id VARCHAR;
ALTER TABLE sessions ADD COLUMN vcf_submitted_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN clinical_history_submitted_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN results_available_at TIMESTAMP;
```

**Or create new table:**

```sql
CREATE TABLE patient_analyses (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(user_id),
    session_id VARCHAR REFERENCES sessions(session_id),
    patient_id VARCHAR,              -- User-provided or auto-generated
    vcf_submitted BOOLEAN DEFAULT FALSE,
    vcf_submitted_at TIMESTAMP,
    clinical_history_provided BOOLEAN DEFAULT FALSE,
    clinical_history_submitted_at TIMESTAMP,
    results_available BOOLEAN DEFAULT FALSE,
    results_available_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### API Endpoints Needed

**All can use existing `/history` endpoint with modifications:**

```bash
# Get dashboard data
GET /api/dashboard
Headers: X-API-Key: user-api-key
Response: {
  "analyses": [
    {
      "patient_id": "PT001",
      "session_id": "session_abc123",
      "vcf_submitted": true,
      "vcf_submitted_at": "2026-06-18T10:30:00Z",
      "clinical_history_provided": true,
      "clinical_history_submitted_at": "2026-06-18T10:32:00Z",
      "results_available": true,
      "results_available_at": "2026-06-18T10:45:00Z",
      "status": "complete",
      "progress_pct": 100,
      "variant_count": 3
    }
  ]
}

# Get detailed view
GET /api/dashboard/{session_id}
Headers: X-API-Key: user-api-key
Response: {
  "patient_id": "PT001",
  "session_id": "session_abc123",
  "vcf_filename": "patient001.vcf.gz",
  "genome_build": "GRCh38",
  "clinical_notes": "Patient presents with...",
  "hpo_terms": ["HP:0001250", "HP:0001263"],
  "status": "complete",
  "duration_seconds": 900,
  "variants": [
    {"gene": "BRCA2", "variant": "p.Arg2520His", "classification": "Pathogenic"},
    ...
  ],
  "timeline": [
    {"timestamp": "2026-06-18T10:30:00Z", "event": "VCF uploaded"},
    {"timestamp": "2026-06-18T10:32:00Z", "event": "Clinical history provided"},
    ...
  ],
  "report_paths": {
    "html": "/path/to/report.html",
    "xlsx": "/path/to/report.xlsx",
    "tsv": "/path/to/report.tsv"
  }
}
```

---

## 🧪 Requirement 3: Test Validation Agent

### Overview

**A separate testing system to validate pipeline accuracy against known classifications.**

Clinical labs provide test cases with:
- VCF file(s) - solo or trio
- Clinical history
- **Expected classifications** (human expert results)
- Expected ACMG criteria

The test agent:
1. Runs these through the pipeline (solo or trio mode)
2. Compares pipeline results vs expected results
3. Generates comparison report with accuracy metrics

### Use Case

**Scenario:**
- Clinical lab sends 50 VCF files with known pathogenic variants
- Each has expert classification (ground truth)
- QA team uses test agent to validate pipeline
- Report shows: accuracy, sensitivity, specificity, false positives/negatives

### Test Input Format

**CSV file with test cases (Solo):**

```csv
test_id,mode,vcf_path,genome_build,clinical_notes,hpo_terms,variant_id,expected_classification,expected_criteria,notes
TEST001,solo,/path/to/proband.vcf.gz,GRCh38,"Breast cancer, family history","HP:0003002",13:32338080:A:C,Likely_Pathogenic,"PM2,PP3,PP4",BRCA2 missense
TEST002,solo,/path/to/proband.vcf.gz,GRCh38,"Cystic fibrosis","HP:0006538,HP:0002099",7:117548628:A:G,Pathogenic,"PVS1,PS3,PM2",CFTR nonsense
TEST003,solo,/path/to/proband.vcf.gz,GRCh37,"Developmental delay","HP:0001263",1:69091:A:T,Benign,"BA1",OR4F5 common
```

**CSV file with test cases (Trio):**

```csv
test_id,mode,proband_vcf,mother_vcf,father_vcf,proband_bam,mother_bam,father_bam,genome_build,clinical_notes,hpo_terms,variant_id,expected_classification,expected_criteria,notes
TEST004,trio,/path/to/proband.vcf.gz,/path/to/mother.vcf.gz,/path/to/father.vcf.gz,,,GRCh38,"De novo variant suspected","HP:0001263",17:41245466:G:A,Pathogenic,"PS2,PM2,PP3",BRCA1 de novo
TEST005,trio,/path/to/proband.vcf.gz,/path/to/mother.vcf.gz,/path/to/father.vcf.gz,/path/to/proband.bam,/path/to/mother.bam,/path/to/father.bam,GRCh38,"Recessive disorder","HP:0001250,HP:0001263",11:108121410:C:T,Pathogenic,"PM3,PP1,PM2",ATM compound het
TEST006,trio,/path/to/proband.vcf.gz,/path/to/mother.vcf.gz,/path/to/father.vcf.gz,,,GRCh38,"Healthy parents","",3:10183768:C:T,Benign,"BS2",Healthy adult homozygous
```

**Columns Explanation:**
- `mode`: "solo" or "trio"
- **Solo mode**: Only `vcf_path` required
- **Trio mode**: `proband_vcf`, `mother_vcf`, `father_vcf` required
- **BAM files**: Optional (enables phasing for trio)
- `expected_criteria`: Should include trio-specific criteria (PS2, PM3, PM6, PP1, BS2, BS4) for trio cases

### Test Agent Workflow

```
Test Agent Flow:
┌────────────────────────────────────────────────┐
│ 1. Load Test CSV                               │
│    • Parse test cases                          │
│    • Validate VCF files exist                  │
└────────────────────────────────────────────────┘
                    ↓
┌────────────────────────────────────────────────┐
│ 2. Run Tests Through Pipeline                  │
│    • Submit each VCF via API                   │
│    • Wait for completion                       │
│    • Collect pipeline results                  │
└────────────────────────────────────────────────┘
                    ↓
┌────────────────────────────────────────────────┐
│ 3. Compare Results                             │
│    • Pipeline classification vs Expected       │
│    • Pipeline criteria vs Expected criteria    │
│    • Calculate metrics                         │
└────────────────────────────────────────────────┘
                    ↓
┌────────────────────────────────────────────────┐
│ 4. Generate Report                             │
│    • Accuracy metrics (CSV)                    │
│    • Detailed comparison (HTML)                │
│    • Failed cases summary                      │
└────────────────────────────────────────────────┘
```

### Comparison Report Format

**CSV Output (comparison.csv):**

```csv
test_id,mode,variant_id,expected_class,pipeline_class,match,expected_criteria,pipeline_criteria,criteria_match,notes
TEST001,solo,13:32338080:A:C,Likely_Pathogenic,Likely_Pathogenic,✓,PM2;PP3;PP4,PM2;PP3;PP4,✓,Perfect match
TEST002,solo,7:117548628:A:G,Pathogenic,Likely_Pathogenic,✗,PVS1;PS3;PM2,PVS1;PM2,Partial,Missing PS3
TEST003,solo,1:69091:A:T,Benign,Benign,✓,BA1,BA1,✓,Perfect match
TEST004,trio,17:41245466:G:A,Pathogenic,Pathogenic,✓,PS2;PM2;PP3,PS2;PM2;PP3,✓,De novo detected
TEST005,trio,11:108121410:C:T,Pathogenic,Likely_Pathogenic,Partial,PM3;PP1;PM2,PP1;PM2,Partial,Missing PM3 (compound het)
TEST006,trio,3:10183768:C:T,Benign,Benign,✓,BS2,BS2,✓,Healthy adult homozygous
```

**Summary Metrics (summary.txt):**

```
Test Run Summary
================
Date: 2026-06-18 12:00:00
Test Cases: 60 (40 solo + 20 trio)
Pipeline Version: 1.0.0

Overall Classification Accuracy:
  Total: 60
  Exact Match: 50 (83.3%)
  Partial Match: 7 (11.7%)
  Mismatch: 3 (5.0%)

Solo Analysis (40 cases):
  Exact Match: 33 (82.5%)
  Partial Match: 5 (12.5%)
  Mismatch: 2 (5.0%)
  
Trio Analysis (20 cases):
  Exact Match: 17 (85%)
  Partial Match: 2 (10%)
  Mismatch: 1 (5%)

By Category:
  Pathogenic/Likely Pathogenic:
    Sensitivity: 90% (27/30 detected)
    False Negatives: 3
  
  Benign/Likely Benign:
    Specificity: 93% (28/30 detected)
    False Positives: 2
  
  VUS:
    Pipeline: 5, Expected: 0 (5 downgrades from Path/Ben)

Criteria Accuracy:
  Exact criteria match: 45/60 (75%)
  Partial criteria match: 12/60 (20%)
  Criteria mismatch: 3/60 (5%)

Trio-Specific Criteria Performance:
  PS2 (de novo): 8/10 detected (80%)
  PM3 (compound het): 6/8 detected (75%)
  PM6 (assumed de novo): 2/2 detected (100%)
  PP1 (segregation): 10/12 detected (83%)
  BS2 (healthy adult): 5/5 detected (100%)
  BS4 (non-segregation): 3/3 detected (100%)

Failed Cases:
  TEST002: Expected Pathogenic, got Likely_Pathogenic (missing PS3)
  TEST015: Expected Likely_Benign, got VUS (missing BS1)
  TEST005: Expected Pathogenic (compound het), got Likely_Pathogenic (missing PM3)

Recommendations:
  - Review PS3 (functional) evidence collection
  - Calibrate BS1 frequency thresholds
  - Improve PM3 detection: Check phasing accuracy (requires BAM files)
  - Review de novo calling thresholds (PS2 missed in 2/10 cases)
```

**HTML Report (comparison.html):**

Interactive report with:
- Summary dashboard (accuracy charts)
- Detailed comparison table (sortable, filterable)
- Failed case analysis
- Criteria breakdown
- Confusion matrix

### Test Agent Implementation

**New Python module:**

```python
# src/testing/validation_agent.py

import pandas as pd
from typing import List, Dict
import requests
import time

class ValidationAgent:
    """
    Test agent to validate pipeline against known classifications.
    """
    
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key
        self.results = []
    
    def load_test_cases(self, csv_path: str) -> pd.DataFrame:
        """Load test cases from CSV."""
        df = pd.read_csv(csv_path)
        required_cols = ['test_id', 'vcf_path', 'variant_id', 
                        'expected_classification']
        assert all(col in df.columns for col in required_cols)
        return df
    
    def run_test_case(self, test_case: Dict) -> Dict:
        """Submit one test case to pipeline and get results."""
        mode = test_case.get('mode', 'solo')
        
        # Prepare files
        files = {}
        data = {
            "genome_build": test_case.get('genome_build', 'GRCh38'),
            "clinical_notes": test_case.get('clinical_notes', ''),
            "patient_hpo_terms": test_case.get('hpo_terms', ''),
        }
        
        if mode == 'solo':
            files['vcf_file'] = open(test_case['vcf_path'], 'rb')
        elif mode == 'trio':
            files['vcf_file'] = open(test_case['proband_vcf'], 'rb')
            files['parent1_vcf'] = open(test_case['mother_vcf'], 'rb')
            files['parent2_vcf'] = open(test_case['father_vcf'], 'rb')
            
            # Add BAM files if provided
            if test_case.get('proband_bam'):
                files['proband_bam'] = open(test_case['proband_bam'], 'rb')
            if test_case.get('mother_bam'):
                files['parent1_bam'] = open(test_case['mother_bam'], 'rb')
            if test_case.get('father_bam'):
                files['parent2_bam'] = open(test_case['father_bam'], 'rb')
        
        # Submit to pipeline
        response = requests.post(
            f"{self.api_url}/analyze",
            headers={"X-API-Key": self.api_key},
            files=files,
            data=data
        )
        session_id = response.json()['session_id']
        
        # Wait for completion
        while True:
            status = requests.get(
                f"{self.api_url}/status/{session_id}",
                headers={"X-API-Key": self.api_key}
            ).json()
            
            if status['status'] == 'complete':
                break
            elif status['status'] == 'failed':
                raise Exception(f"Analysis failed: {status.get('error')}")
            
            time.sleep(10)
        
        # Extract result for target variant
        variant_id = test_case['variant_id']
        classifications = status.get('classifications', {})
        
        return {
            'test_id': test_case['test_id'],
            'session_id': session_id,
            'pipeline_classification': classifications.get(variant_id, 'Not Found'),
            'expected_classification': test_case['expected_classification'],
        }
    
    def run_all_tests(self, test_csv: str) -> pd.DataFrame:
        """Run all test cases and collect results."""
        test_cases = self.load_test_cases(test_csv)
        
        results = []
        for idx, test_case in test_cases.iterrows():
            print(f"Running test {test_case['test_id']}...")
            try:
                result = self.run_test_case(test_case.to_dict())
                results.append(result)
            except Exception as e:
                print(f"  Failed: {e}")
                results.append({
                    'test_id': test_case['test_id'],
                    'error': str(e)
                })
        
        return pd.DataFrame(results)
    
    def compare_results(self, results_df: pd.DataFrame) -> Dict:
        """Calculate accuracy metrics."""
        # Classification match
        results_df['match'] = results_df['pipeline_classification'] == \
                              results_df['expected_classification']
        
        accuracy = results_df['match'].sum() / len(results_df) * 100
        
        # Calculate sensitivity/specificity
        # (pathogenic vs benign detection)
        
        return {
            'total_tests': len(results_df),
            'exact_match': results_df['match'].sum(),
            'accuracy': accuracy,
            # ... more metrics
        }
    
    def generate_report(self, results_df: pd.DataFrame, 
                       output_dir: str):
        """Generate comparison reports."""
        metrics = self.compare_results(results_df)
        
        # CSV report
        results_df.to_csv(f"{output_dir}/comparison.csv", index=False)
        
        # Summary text
        with open(f"{output_dir}/summary.txt", 'w') as f:
            f.write("Test Run Summary\n")
            f.write("================\n")
            f.write(f"Total Tests: {metrics['total_tests']}\n")
            f.write(f"Accuracy: {metrics['accuracy']:.1f}%\n")
            # ... more metrics
        
        # HTML report (interactive)
        # Use plotly/bokeh for charts
        # ...

# Usage:
if __name__ == "__main__":
    agent = ValidationAgent(
        api_url="http://localhost:8000",
        api_key="test-api-key"
    )
    
    results = agent.run_all_tests("test_cases.csv")
    agent.generate_report(results, "test_results/")
```

### Test Agent Features

#### 1. **Batch Testing**
- Run 50-100 test cases automatically
- Parallel execution (submit all, wait for completion)
- Progress tracking

#### 2. **Comparison Logic**
```python
def compare_classification(expected: str, pipeline: str) -> str:
    """
    Exact Match: Both are identical
    Partial Match: One tier difference (P vs LP, LB vs B)
    Mismatch: Major disagreement (P vs VUS, B vs LP)
    """
    if expected == pipeline:
        return "Exact"
    
    # Define tiers
    pathogenic = {"Pathogenic", "Likely_Pathogenic"}
    benign = {"Benign", "Likely_Benign"}
    
    if expected in pathogenic and pipeline in pathogenic:
        return "Partial"
    if expected in benign and pipeline in benign:
        return "Partial"
    
    return "Mismatch"
```

#### 3. **Criteria Comparison**
```python
def compare_criteria(expected: str, pipeline: str) -> Dict:
    """
    Compare ACMG criteria strings.
    
    Expected: "PM2,PP3,PP4"
    Pipeline: "PM2,PP3,PP5"
    
    Returns:
        matched: ["PM2", "PP3"]
        missing: ["PP4"]
        extra: ["PP5"]
    """
    exp_set = set(expected.split(','))
    pip_set = set(pipeline.split(','))
    
    return {
        'matched': list(exp_set & pip_set),
        'missing': list(exp_set - pip_set),
        'extra': list(pip_set - exp_set),
    }
```

#### 4. **Error Analysis**
- Group failed cases by:
  - Gene
  - Variant type (missense, nonsense, etc.)
  - Classification category
  - Missing criteria

#### 5. **Admin Interface**

```
Admin Test Dashboard:
┌─────────────────────────────────────────────────┐
│ 🧪 Test Validation System                      │
├─────────────────────────────────────────────────┤
│                                                 │
│  📂 Upload Test Cases                          │
│  [📎 Select CSV file with test cases]          │
│                                                 │
│  API Configuration:                            │
│  API URL:    http://localhost:8000             │
│  API Key:    [********************]            │
│                                                 │
│  [▶️ Run Tests]                                 │
│                                                 │
│  ───────────────────────────────────────────   │
│                                                 │
│  📊 Past Test Runs                             │
│                                                 │
│  ┌───────────────────────────────────────┐    │
│  │ Run: 2026-06-18 10:00                 │    │
│  │ Tests: 50 | Accuracy: 84%             │    │
│  │ [View Report] [Download CSV]          │    │
│  └───────────────────────────────────────┘    │
│                                                 │
│  ┌───────────────────────────────────────┐    │
│  │ Run: 2026-06-15 14:30                 │    │
│  │ Tests: 30 | Accuracy: 80%             │    │
│  │ [View Report] [Download CSV]          │    │
│  └───────────────────────────────────────┘    │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 🔗 Integration Architecture

### How New Components Connect to Existing System

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERFACE LAYER                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │ Chat Interface   │  │ User Dashboard   │               │
│  │ (NEW)            │  │ (NEW)            │               │
│  │ - Conversation   │  │ - Patient cards  │               │
│  │ - File upload    │  │ - Status view    │               │
│  │ - Progress       │  │ - Downloads      │               │
│  └────────┬─────────┘  └────────┬─────────┘               │
│           │                     │                          │
│           └──────────┬──────────┘                          │
│                      │                                     │
└──────────────────────┼─────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│                    EXISTING REST API                        │
│                    (NO CHANGES NEEDED)                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  POST /register           GET /status/{id}                 │
│  POST /analyze            GET /history                     │
│  GET  /stream/{id}        GET /download/{id}/{format}      │
│  POST /regenerate-key     ... (all 15+ existing endpoints) │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│                    PIPELINE & DATABASE                      │
│                    (NO CHANGES)                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  • Celery workers running pipeline                         │
│  • PostgreSQL storing user/session data                    │
│  • MemPalace semantic memory                               │
│  • 9 agents + debate system                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────┐
│                  TEST VALIDATION SYSTEM                     │
│                    (SEPARATE MODULE)                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────────────────────────────────┐      │
│  │ Test Agent (Python Script)                       │      │
│  │ - Reads test CSV                                 │      │
│  │ - Calls /analyze API (like any user)            │      │
│  │ - Compares results                               │      │
│  │ - Generates reports                              │      │
│  └────────────────┬─────────────────────────────────┘      │
│                   │                                        │
│                   └───────→ Uses existing API              │
│                                                             │
│  ┌──────────────────────────────────────────────────┐      │
│  │ Admin Test UI (Optional)                         │      │
│  │ - Upload test CSV                                │      │
│  │ - Run tests                                      │      │
│  │ - View reports                                   │      │
│  └──────────────────────────────────────────────────┘      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Key Integration Points

**1. Chat Interface → Existing API**
- Chat UI calls existing REST endpoints
- No backend changes required
- Natural language → structured API calls (frontend parsing)
- SSE for progress (already working)

**2. Dashboard → Database**
- Query existing `sessions` table
- Add `patient_id` field (optional, can use session_id)
- Add timestamp fields for tracking

**3. Test Agent → API**
- Uses same `/analyze` endpoint as regular users
- Special admin API key with high quota
- Batch submission via API calls
- Independent comparison logic

---

## 🛠️ Implementation Guide

### Phase 1: Chat Interface (4-6 weeks)

#### Week 1-2: Chat UI Foundation
**Goal:** Basic chat interface with hardcoded flows

**Tasks:**
1. Set up React project
   ```bash
   npx create-react-app chat-ui --template typescript
   cd chat-ui
   npm install @chatscope/chat-ui-kit-react axios react-dropzone
   ```

2. Create chat component
   - Message list
   - Input box
   - File upload
   - Progress display

3. Implement registration flow
   - Hardcode question sequence
   - Collect: email, name, org, NCBI key
   - Call POST /register

4. Implement analysis flow
   - File upload
   - Collect: genome build, sex, clinical notes, HPO
   - Call POST /analyze
   - Show session ID

**Deliverable:** Working chat that can register and submit analysis

#### Week 3-4: Smart Features
**Goal:** Context awareness, validation, SSE integration

**Tasks:**
1. State management
   - Track conversation state
   - Remember user inputs
   - Handle context across messages

2. Input validation
   - Detect missing required fields
   - Ask follow-up questions
   - Validate file formats

3. Real-time progress
   - Connect to existing SSE (`/stream/{id}`)
   - Display progress in chat
   - Expandable logs box

4. Results display
   - Show summary in chat
   - Download buttons for reports
   - Pretty formatting

**Deliverable:** Fully functional chat interface

#### Week 5-6: Polish & Commands
**Goal:** Professional UX, command support

**Tasks:**
1. Command system
   - `/help`, `/status`, `/history`, etc.
   - Parse user intent
   - Handle variations ("check status" = `/status`)

2. Quick actions
   - Button for common choices (GRCh38/GRCh37)
   - Confirm dialogs
   - Error recovery

3. UI polish
   - Loading states
   - Error messages
   - Empty states
   - Mobile responsive

4. Testing
   - Unit tests for state logic
   - Integration tests with API
   - User acceptance testing

**Deliverable:** Production-ready chat interface

---

### Phase 2: User Dashboard (2-3 weeks)

#### Week 1: Basic Dashboard
**Goal:** Display list of analyses

**Tasks:**
1. Create dashboard page
   - Navigation from chat
   - Authentication check

2. Fetch data from API
   - Use existing `/history` endpoint
   - Or create new `/dashboard` endpoint

3. Display analysis cards
   - Patient ID
   - VCF/Clinical/Results status
   - Timestamps

4. Basic filtering
   - By status
   - By date

**Deliverable:** Working dashboard with list view

#### Week 2: Detailed View & Downloads
**Goal:** Full analysis details, download reports

**Tasks:**
1. Detailed modal
   - Click on card → open modal
   - Show all parameters
   - Timeline view

2. Download functionality
   - Individual reports (HTML/XLSX/TSV)
   - All as ZIP
   - Quick download from card

3. Search & advanced filters
   - Search by patient ID
   - Filter by multiple criteria

**Deliverable:** Complete dashboard

#### Week 3: Polish & Database Updates
**Goal:** Production ready

**Tasks:**
1. Update database schema
   - Add `patient_id` field
   - Add timestamp fields
   - Migration script

2. API endpoints (if needed)
   - GET `/dashboard`
   - GET `/dashboard/{session_id}`

3. Performance optimization
   - Pagination
   - Caching
   - Lazy loading

**Deliverable:** Production dashboard

---

### Phase 3: Test Validation Agent (2-3 weeks)

#### Week 1: Core Agent
**Goal:** Run tests and compare results

**Tasks:**
1. Create validation agent module
   ```
   src/testing/
   ├── __init__.py
   ├── validation_agent.py
   ├── comparison.py
   └── report_generator.py
   ```

2. Implement test runner
   - Load CSV
   - Submit via API
   - Wait for results

3. Implement comparison logic
   - Classification match
   - Criteria match
   - Calculate metrics

**Deliverable:** Working CLI test agent

#### Week 2: Reports & Analysis
**Goal:** Generate comprehensive reports

**Tasks:**
1. CSV output
   - Detailed comparison
   - Export results

2. Summary metrics
   - Accuracy, sensitivity, specificity
   - Criteria accuracy
   - Failed cases analysis

3. HTML report
   - Interactive charts (plotly)
   - Sortable tables
   - Filterable views

**Deliverable:** Complete reporting system

#### Week 3: Admin UI (Optional)
**Goal:** Web interface for test agent

**Tasks:**
1. Upload interface
   - CSV upload
   - Validate format

2. Test execution
   - Start test run
   - Progress tracking
   - Live results

3. Report viewing
   - List past runs
   - View reports
   - Download CSVs

**Deliverable:** Admin test interface

---

## 📐 Technical Specifications

### Frontend Stack

**Recommended:**
- **Framework:** React 18 + TypeScript
- **State:** Redux Toolkit or Zustand
- **Chat UI:** @chatscope/chat-ui-kit-react
- **HTTP:** Axios
- **File Upload:** react-dropzone
- **Charts:** Recharts or Chart.js
- **Styling:** Tailwind CSS or Material-UI

**Alternative:**
- **Framework:** Vue 3 + TypeScript
- **State:** Pinia
- **Chat UI:** Custom or vue-advanced-chat

### Backend Modifications (Minimal)

**Only if needed:**

1. **Dashboard endpoint (optional):**
   ```python
   # src/api/main.py
   
   @app.get("/dashboard", tags=["Dashboard"])
   def get_dashboard(user: User = Depends(verify_api_key), db: Session = Depends(get_db)):
       """Get dashboard data for user."""
       sessions = db.query(DBSession).filter(
           DBSession.user_id == user.user_id
       ).order_by(DBSession.created_at.desc()).all()
       
       return {
           "analyses": [
               {
                   "patient_id": s.patient_id or s.session_id,
                   "session_id": s.session_id,
                   "vcf_submitted": bool(s.vcf_filename),
                   "vcf_submitted_at": s.created_at,
                   "clinical_history_provided": bool(s.clinical_notes),
                   "clinical_history_submitted_at": s.created_at,  # Same as VCF for now
                   "results_available": s.status == "complete",
                   "results_available_at": s.completed_at,
                   "status": s.status,
                   "progress_pct": s.progress_pct,
               }
               for s in sessions
           ]
       }
   ```

2. **Add patient_id field:**
   ```python
   # src/api/db.py
   class Session(Base):
       # ... existing fields ...
       patient_id = Column(String)  # NEW - user-provided patient ID
   ```

3. **Accept patient_id in analyze:**
   ```python
   # src/api/main.py
   @app.post("/analyze")
   async def analyze_vcf(
       vcf_file: UploadFile = File(...),
       patient_id: str = Form(None),  # NEW - optional patient ID
       # ... rest of parameters ...
   ):
       # Store patient_id in session record
       session = DBSession(
           session_id=session_id,
           patient_id=patient_id,  # NEW
           # ... rest of fields ...
       )
   ```

### Test Agent Structure

```
src/testing/
├── __init__.py
├── validation_agent.py       # Main test runner
├── comparison.py             # Comparison logic
├── metrics.py                # Accuracy calculations
├── report_generator.py       # HTML/CSV reports
└── test_cases_example.csv    # Example test format
```

### Database Schema Changes

**Option 1: Extend sessions table**
```sql
ALTER TABLE sessions 
ADD COLUMN patient_id VARCHAR,
ADD COLUMN vcf_submitted_at TIMESTAMP DEFAULT created_at,
ADD COLUMN clinical_history_submitted_at TIMESTAMP,
ADD COLUMN results_available_at TIMESTAMP DEFAULT completed_at;
```

**Option 2: New table (cleaner)**
```sql
CREATE TABLE patient_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(user_id),
    session_id VARCHAR REFERENCES sessions(session_id),
    patient_id VARCHAR NOT NULL,
    vcf_submitted_at TIMESTAMP,
    clinical_history_submitted_at TIMESTAMP,
    results_available_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_patient_analyses_user ON patient_analyses(user_id);
CREATE INDEX idx_patient_analyses_patient ON patient_analyses(patient_id);
```

---

## 🧪 Testing Strategy

### Chat Interface Testing

**Unit Tests:**
- State management (context tracking)
- Input validation
- Command parsing

**Integration Tests:**
- API call sequences
- SSE connection
- File upload

**E2E Tests:**
- Full registration flow
- Full analysis flow
- Error recovery

**User Testing:**
- 5-10 users test chat
- Gather feedback on UX
- Iterate on conversation flow

### Dashboard Testing

**Unit Tests:**
- Data transformation
- Filtering logic
- Search functionality

**Integration Tests:**
- API data fetching
- Download functionality

**Performance Tests:**
- Large dataset (1000+ analyses)
- Pagination
- Load times

### Test Agent Testing

**Validation:**
- Run with 10 known test cases
- Verify accuracy calculations
- Check report generation

**Scale Testing:**
- Run with 100 test cases
- Parallel execution
- Error handling

---

## ⏱️ Timeline Estimate

### Overall Timeline: 8-12 weeks

**Phase 1: Chat Interface**
- Weeks 1-2: Basic chat with hardcoded flows (2 weeks)
- Weeks 3-4: Smart features, SSE, validation (2 weeks)
- Weeks 5-6: Polish, commands, testing (2 weeks)
- **Total: 6 weeks**

**Phase 2: User Dashboard**
- Week 7: Basic dashboard, list view (1 week)
- Week 8: Detailed view, downloads (1 week)
- Week 9: Polish, database updates (1 week)
- **Total: 3 weeks**

**Phase 3: Test Validation Agent**
- Week 10: Core agent, comparison logic (1 week)
- Week 11: Reports and analysis (1 week)
- Week 12: Admin UI (optional) (1 week)
- **Total: 3 weeks**

**Parallel Work:**
- Dashboard can start in Week 4 (different dev)
- Test agent can start in Week 7 (different dev)
- Total with parallelization: **8-10 weeks**

### Resource Needs

**Team:**
- 1 Frontend Developer (Chat + Dashboard)
- 1 Backend/Testing Developer (Test Agent)
- 1 Designer (optional, for UI/UX)

**Or:**
- 1 Full-Stack Developer (all components)
- Timeline: 12 weeks sequential

---

## 📚 Key Resources

### Existing Documentation
- [README.md](README.md) - Complete system overview
- [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) - All API endpoints
- [docs/STEP6_COMPLETE_GUIDE.md](docs/STEP6_COMPLETE_GUIDE.md) - Full guide

### Recommended Libraries

**Chat UI:**
- https://chatscope.io/ - @chatscope/chat-ui-kit-react
- https://github.com/chatscope/chat-ui-kit-react

**Charts:**
- https://recharts.org/ - Recharts (simple)
- https://plotly.com/javascript/ - Plotly (advanced)

**File Upload:**
- https://react-dropzone.js.org/ - react-dropzone

**Testing:**
- https://testing-library.com/docs/react-testing-library/intro/
- https://playwright.dev/ - E2E tests

---

## 🎯 Success Criteria

### Chat Interface
- ✅ User can register through conversation
- ✅ User can submit VCF analysis through chat
- ✅ Real-time progress displayed
- ✅ Results shown with download links
- ✅ All existing features accessible via chat
- ✅ Intuitive, requires minimal instructions

### User Dashboard
- ✅ Shows all patient analyses
- ✅ Clear status indicators
- ✅ Timestamps for all stages
- ✅ Easy download of reports
- ✅ Search and filter working
- ✅ Fast (<1s load time for 100 analyses)

### Test Validation Agent
- ✅ Accepts CSV with test cases
- ✅ Runs all tests automatically
- ✅ Compares results accurately
- ✅ Generates comprehensive reports
- ✅ Calculates accuracy metrics
- ✅ Identifies failed cases with reasoning

---

## 🚀 Getting Started

**For the new developer:**

1. **Read existing system docs** (Day 1)
   - [README.md](README.md)
   - [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md)

2. **Set up environment** (Day 1)
   - Clone repo
   - Start existing services
   - Test API with Postman/curl

3. **Start with Phase 1** (Week 1)
   - Create React project
   - Build basic chat UI
   - Test with existing API

4. **Iterate and test** (Ongoing)
   - User feedback
   - Refine UX
   - Add features

---

## 📧 Questions?

**For clarification:**
- Check [docs/](docs/) directory
- Review [API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md)
- Test existing endpoints with curl

**Integration questions:**
- All backend APIs already work
- No major backend changes needed
- Frontend calls existing REST endpoints

---

**Last Updated:** June 18, 2026  
**Document Version:** 1.0  
**Status:** Requirements Ready for Implementation
