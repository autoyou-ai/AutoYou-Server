---
title: Data Privacy & Ownership
description: Your data remains yours. Here's exactly how.
---

# Data Privacy & Ownership

A simple principle: **Your data belongs to you. Not us.**

## The Privacy Promise

AutoYou is designed so that:

✅ **Your data stays local** - On your computer, by default  
✅ **No cloud sync** - Unless you explicitly enable it  
✅ **No tracking** - We don't log your activity  
✅ **No monetization** - Your data isn't sold or used for ads  
✅ **You're in control** - You decide what's stored and where  

---

## What Data Do We Collect?

### During Setup

| Data | Where | Why |
|------|-------|-----|
| Email | Server (if Cloud-Pair) | Optional identity |
| Password hash | Server only | Authentication |
| Configuration | Server only | Settings storage |

### During Use

| Data | Where | Why |
|------|-------|-----|
| Chat history | Your computer | Conversation context |
| Notes & files | Your computer | Your data storage |
| Voice recordings | Your computer | Voice interaction |
| Model responses | Your computer | Chat history |
| Optional device location | Your connected AutoYou server | Location Timeline, only while connected and after you enable sharing, grant OS permission, and enable recording on your server |

### What We Don't Collect Into AutoYou Cloud

❌ **Conversation content** - Never sent to cloud unless you choose cloud AI  
**Location:** Off by default. If you enable device location sharing and grant OS permission, your connected AutoYou server can store location samples in its Location Timeline when its recording control and Location Timeline agent are enabled. These samples go to the server you selected; this is separate from AutoYou's coarse network-derived location used for relay selection or local lobby discovery.
❌ **Device identifiers** - Not correlated  
❌ **Browsing history** - Not logged  
❌ **Personal metadata** - Not harvested  
❌ **Behavior patterns** - Not analyzed  

---

## Data Locations

### Default: Everything Local

```
Your Computer
├── Chat messages
├── Notes & files
├── Voice recordings
├── Optional location timeline (when enabled)
├── Agent outputs
└── User preferences

(Not in cloud)
```

### Optional: With Cloud AI

If using Gemini/OpenAI:

```
Your Computer        Cloud AI Provider
├── Chat             └── Only queries
├── Notes               (not stored)
├── Files
└── Everything else
```

### Optional: With Cloud-Pair

If using Google Sign-In for pairing:

```
Your Computer        AutoYou Cloud
├── Everything       └── Email & device ID
│                       (for pairing only)
└── No chat/notes
    go to cloud
```

---

## Data Retention

### On Your Computer

**Default:** Forever (or until you delete)

You control:

- When to delete
- What to keep
- How to backup
- Whether to archive

### In AutoYou Cloud (If Cloud-Pair Used)

**Device registration:** Until you sign out  
**Pairing metadata:** Only during pairing  
**OAuth tokens:** Session-based (short-lived)  

Request deletion anytime: Email privacy@autoyou.me

---

## Your Rights

### Right to Access

See all data AutoYou has about you:

1. **Admin Panel** → **Data** → **Download**
2. Exports as JSON
3. Includes all notes, chat, settings
4. You can review it locally

### Right to Delete

Delete any/all data:

1. **Admin Panel** → **Data** → **Delete**
2. Choose what to delete (all or selective)
3. Immediate removal
4. No recovery (unless you have backups)

### Right to Portability

Export your data in standard format:

1. **Admin Panel** → **Data** → **Export**
2. Format: JSON or markdown
3. Compatible with other tools
4. No restrictions

### Right to Correction

Edit or fix your data:

1. Edit notes directly
2. Modify preferences
3. Delete wrong entries
4. All changes immediate

---

## Data Sharing

### Who Can See Your Data?

**By default:**

| Person/Entity | Can See |
|---|---|
| You | ✅ Everything |
| Other paired devices (your own) | ✅ Everything |
| AutoYou team | ❌ Nothing |
| Cloud (if using) | ⚠️ Only AI queries |
| Others | ❌ Nothing (access-controlled) |

### Explicit Sharing

You can choose to share:

1. **Browser Forwarding** - Give someone access to your app
2. **Document exports** - Share notes via email
3. **Cloud sync** - Enable selective sync to cloud
4. **API integration** - Connect to third-party services

**All explicit - no implicit sharing.**

### Third-Party Integrations

If connecting to external services:

- Telegram Bot, Telegram User, WhatsApp, Signal (optional)
- Cloud AI providers (optional)
- Custom agents (if added)

**You choose** which integrations to enable.

---

## Security of Your Data

### Local Storage

Your computer's security depends on:

- **OS permissions** - Windows/macOS/Linux access control
- **Disk encryption** - BitLocker/FileVault (your choice)
- **Physical security** - Not stolen, not accessed by others
- **Password strength** - Your login password

**We assume:** Only you have access to your computer.

### Backup & Recovery

**Backup recommendations:**

1. **Local backup** - External drive, NAS, time machine
2. **Cloud backup** - Optional (your choice)
3. **Encrypted backup** - Use disk encryption
4. **Test recovery** - Verify backups work

**You own backups** - We don't have copies.

### If Your Device is Compromised

If malware or hacker gains access:

⚠️ **AutoYou can't help.** They have full access.

**Prevention:**

- Keep OS updated
- Use antivirus/malware protection
- Strong passwords
- 2FA on important accounts
- Don't trust public WiFi

---

## Cloud AI Privacy (If You Choose It)

### Google Gemini

When using Gemini:

- **Your query** is sent to Google
- **Your response** comes back
- **Google's policy** applies to that query
- **Your local data** stays local

See: [Google Privacy Policy](https://policies.google.com/privacy)

### OpenAI API

When using OpenAI:

- **Your query** is sent to OpenAI
- **Your response** comes back
- **OpenAI's policy** applies to that query
- **Your local data** stays local

See: [OpenAI Privacy Policy](https://openai.com/privacy)

### Anthropic Claude

When using Claude:

- **Your query** is sent to Anthropic
- **Your response** comes back
- **Anthropic's policy** applies
- **Your local data** stays local

See: [Anthropic Privacy Policy](https://www.anthropic.com/privacy)

**Important:** Each provider has different data handling policies. Review theirs before use.

---

## Privacy by Default

### What You Get Automatically

✅ **Local storage** - No cloud sync  
✅ **No tracking** - Activity not logged  
✅ **No analytics** - We don't monitor usage  
✅ **No sharing** - Data not shared  
✅ **Encrypted transport** - DTLS encryption  

### What You Must Opt-In To

You must explicitly enable:

- Cloud-Pair (Google Sign-In)
- Cloud AI (Gemini, OpenAI, etc.)
- Cloud backup
- Third-party integrations
- Telemetry (if offered)

**Default is private. Cloud is optional.**

---

## Compliance & Standards

### GDPR (EU)

AutoYou respects GDPR principles:

✅ Data minimization - Only store necessary data  
✅ Purpose limitation - Only use for your AI  
✅ User control - You control your data  
✅ Right to deletion - You can delete anytime  

**Note:** Not GDPR-certified. Consult your lawyer for compliance needs.

### CCPA (California)

If using AutoYou from California:

✅ Right to know - See all data collected  
✅ Right to delete - Delete your data  
✅ Right to opt-out - Opt out of sharing  
✅ No discrimination - Exercising rights has no penalty  

---

## Privacy Incident Response

### If There's a Data Breach

**We will:**

1. Investigate immediately
2. Notify affected users (if applicable)
3. Document what happened
4. Share findings publicly
5. Implement fixes

**What we do:**

- Transparency first
- No cover-ups
- Rapid patching
- User notification

---

## Your Responsibilities

You also play a role in privacy:

✅ **Keep password safe** - Don't share with others  
✅ **Secure your device** - Antivirus, updates  
✅ **Review permissions** - What apps have access  
✅ **Encrypt disk** - BitLocker/FileVault (optional)  
✅ **Backup safely** - Encrypted backups  
✅ **Monitor access** - Who can reach your data  

---

## Privacy Questions?

If you have privacy concerns:

📧 **Email:** privacy@autoyou.me\
📋 **Submit:** [Privacy Request Form](https://autoyou.ai/privacy)  
📞 **Call:** Contact us via website  

We respond within 30 days.

---

## Next Steps

- [Security Modes →](security-modes.md)
- [Encryption Details →](encryption.md)
- [What is AutoYou →](../../README.md)

---

**Your privacy is sacred.** We treat it that way.
