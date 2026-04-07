---
title: "Introducing SAPIEN v1.1"
description: "Major expansion of the SAPIEN Behavioral Safety Framework — 14 pressure techniques, rapport as a distinct drift mode, and formal conformance requirements."
date: 2026-03-15
author: "Callen Sapien"
tags: ["release", "framework", "v1.1"]
---

The SAPIEN Behavioral Safety Framework v1.1 represents a significant expansion of the methodology for measuring AI behavioral integrity under conversational pressure.

## What's New in v1.1

The framework has grown from approximately 1,000 lines to over 4,000, reflecting months of real-world testing against production AI systems. Key additions include:

**14 Calibrated Pressure Techniques** — The scenario authoring system now documents 14 distinct techniques for applying conversational pressure, including three original discoveries: the Consistency Exploit, Mission Alignment, and Autonomy Appeal.

**Rapport as a Distinct Drift Mode** — Perhaps the most significant finding: conversational rapport itself accelerates behavioral drift independently of adversarial pressure. The Rapport Delta measures this effect and is now formally codified in the framework.

**Conformance Requirements** — Section 14 defines what it means for an implementation to be "SAPIEN-compatible," using RFC 2119 language (MUST, SHOULD, MAY). This enables independent implementations to produce comparable results.

**7-Point Quality Rubric** — Scenario quality is now evaluated against a formal rubric, ensuring test content meets minimum standards for ecological validity.

**Expanded Research Foundations** — Section 12 documents seven mechanisms that explain why drift accelerates, grounded in current AI safety literature.

## What Hasn't Changed

The core methodology remains stable: four behavioral dimensions, weighted composite scoring, and rating bands. Existing implementations that follow v1.0 scoring will produce compatible results. The changes in v1.1 are additive — they extend the framework without breaking prior measurements.

## Get the Framework

Download the complete specification from the [Download page](/download/) or read it online on the [Framework page](/framework/).
