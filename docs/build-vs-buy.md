# Build versus buy for integration middleware: a neutral decision guide

> Independent, unofficial reference. This guide names categories of solution, not products, and does not recommend any vendor. It reflects general engineering trade-offs, not the position of any organisation.

## The question

When an ERP such as a freight management system needs to exchange messages with other systems, there are four broad routes. Most organisations end up with a mixture.

| Route | What it is | Examples of the category (generic) |
|---|---|---|
| **A. Custom code** | Services or scripts you write and run | A small service like the one in this repository; scheduled jobs; serverless functions |
| **B. Integration platform (iPaaS or hosted)** | A vendor-run platform with connectors, mapping tools and monitoring | Subscription integration services |
| **C. Integration engine or ESB you operate** | Packaged software you host, with routing, transformation and queuing built in | Commercial or open-source integration engines and enterprise service buses |
| **D. Vendor or partner connector** | A ready-made connector for a specific pairing of systems, often from one of the two vendors or a specialist partner | Marketplace connectors, partner-built adapters |

A fifth ingredient often sits beneath any of these: a **message broker** for queuing. It is a component, not a complete answer.

## How to decide

Score each route against the criteria that matter to **your** context. The weights below are examples only. Change them.

| Criterion | Why it matters | A (custom) | B (iPaaS) | C (engine) | D (connector) |
|---|---|---|---|---|---|
| Time to first working integration | Business value starts here | Slow to moderate | Fast when connectors exist | Moderate | Fastest if it fits |
| Fit for unusual requirements | Odd formats, odd rules | Excellent | Good within platform limits | Good | Poor if the connector is rigid |
| Ongoing licence cost | Often per connection, message or user | None, but staff time | Recurring, can scale with volume | Licence or support contract | Recurring |
| Ongoing engineering cost | Who maintains it when things change | High: you own everything | Lower | Moderate: you run it | Lowest |
| Operational burden | Patching, scaling, on-call | High | Low (vendor runs it) | High | Low to moderate |
| Observability and audit out of the box | Needed for support and compliance | Only what you build | Usually good | Usually good | Varies |
| Skills required | Who you must hire or keep | Software engineers | Integration analysts | Specialists in the engine | Few |
| Vendor lock-in | Cost of leaving later | Low | Higher | Moderate | Higher |
| Data residency and control | Where payloads live | Full control | Depends on platform regions and contract | Full control | Depends |
| Resilience features (retry, dead-letter, replay) | The hard part | You build and test them | Provided | Provided | Varies |
| Change of ERP or partner | Future flexibility | Rewrite adapters | Remap flows | Remap flows | Often start again |

## When each route tends to win

**Custom code tends to win when**

* the requirements are narrow and unusual, and no product models them well
* volumes are modest, or so high that per-message pricing hurts
* you have engineers who will own it long term, and the integration is core enough to justify that
* you need tight control of data location or timing

**An integration platform tends to win when**

* you have many standard integrations and few engineers
* connectors already exist for your systems
* you value built-in monitoring and replay and are comfortable with the running cost and the contract

**An integration engine tends to win when**

* you need a platform's features but must host it yourself
* you have, or can hire, people who know the engine
* several teams will share one integration capability

**A vendor or partner connector tends to win when**

* the pairing is common and the connector is mature
* your needs are close to its defaults
* you can accept its release cycle and limits

## Hidden costs to check on every route

* **The resilience tail.** Retries, deduplication, dead-lettering, replay and reconciliation are where custom builds spend unplanned time, and where products differ most. Ask to see each one working, with a failure injected.
* **Per-message and per-connection pricing.** Model the cost at ten times today's volume, and during a backlog replay.
* **Exit cost.** How would you extract your mappings and history if you left?
* **Key-person risk.** A custom service understood by one person, or a platform flow understood by one analyst, are the same problem.
* **Change control.** How are changes tested, reviewed and rolled back? Many low-code tools make change easy and review hard.
* **Support boundaries.** When the ERP, the platform and the connector each blame another, who owns the problem?
* **Security review effort.** A product still needs a review of credentials, data handling and access. A custom build needs it too, plus code review.

## Questions to put to any option

1. What happens, step by step, if the ERP times out after storing the message?
2. How is a duplicate detected, and by which key?
3. Where does a message go if it can never be delivered, and who is told?
4. How do I replay one message, or a hundred, safely, and what stops stale data going out of order?
5. How do I prove, for any message, what happened to it and when?
6. How do I compare what was sent with what the ERP holds?
7. What is the cost at ten times the volume?
8. What would it take to leave?

## A pragmatic pattern: hybrid

Many sound designs use a product for the commodity parts and custom code for the distinctive parts: a platform or broker for transport, queuing and monitoring, and a small amount of code for the business rules that no product captures. If you do build part of it, keep the custom surface small, and keep the patterns in this repository's [catalogue](patterns-catalogue.md) as a checklist.

## What this repository does and does not say

This repository shows what option A involves at small scale, including the unglamorous resilience work. That does not make option A the right answer. It makes the cost of option A visible, so that the comparison with B, C and D is honest.
