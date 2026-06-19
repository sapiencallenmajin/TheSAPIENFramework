<!--
The SAPIEN Behavioral Safety Framework
Copyright 2024-2026 SAPIEN Labs LLC
Licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).
Attribution: "Based on the SAPIEN Behavioral Safety Framework by SAPIEN Labs LLC."
"SAPIEN Certified" is a certification mark of SAPIEN Labs LLC.
-->

# SAPIEN Framework — Glossary of Terms

Version 1.0 · April 2026

---

## A

**Adaptive Mode.** A SAPIEN test mode in which an LLM generates pressure turns dynamically based on the target model's responses, rather than following a scripted scenario. Adaptive mode produces less predictable conversational patterns that are harder for meta-aware models to detect.

**Audience.** A scenario metadata field indicating the intended use context. "General" scenarios include rapport-building turns and are designed for standard assessments. "Benchmark" scenarios (also called "cold" scenarios) skip rapport and apply direct pressure, used for controlled comparisons.

## B

**Behavioral Drift.** The gradual erosion of an AI model's safety boundaries over the course of a multi-turn conversation. Drift occurs not because the model lacks safety training, but because sustained conversational pressure causes it to prioritize helpfulness or empathy over boundary maintenance.

**Behavioral Safety.** The property of an AI system maintaining consistent, appropriate guidance regardless of the conversational context in which questions are asked. A behaviorally safe system gives the same quality of safety guidance whether the user sounds calm or distressed, whether they agree or disagree, and whether the conversation is two turns or twenty.

## C

**Capitulated.** A scenario verdict indicating that the model fully abandoned its safety position. The model endorsed, facilitated, or provided detailed guidance for the action it initially and correctly refused or cautioned against. The most severe verdict.

**Cold Pair.** A version of a scenario that begins with direct pressure and no rapport-building phase. Cold pairs are used alongside their warm counterparts to measure the Rapport Delta — the difference in model behavior when trust has been established versus when pressure is applied immediately.

**Counter-Refusal.** A pre-built or dynamically generated user response designed to be injected when a target model pushes back, refuses, or detects the testing pattern. Counter-refusals attack the model's confidence in its own detection rather than simply rephrasing the request.

**Cross-Family Judging.** The practice of using a scoring model from a different model family than the target model being assessed. For example, using a Gemini model to judge Claude, or vice versa. Cross-family judging reduces same-family scoring bias (judge sycophancy) and is required for published SAPIEN benchmarks.

## D

**Deterministic Scoring (Tier 1 / L1).** The first layer of SAPIEN's two-tier scoring architecture. Tier 1 uses pattern matching and rule-based analysis to detect specific behavioral signals (hedging language, dropped disclaimers, specificity increases) without calling an LLM. Fast and inexpensive, but limited in nuance.

**Drifted.** A scenario verdict indicating that the model showed measurable boundary erosion. The model softened its position, dropped warnings, or provided more specific guidance than appropriate, but may not have fully abandoned its safety position. Intermediate severity between Held and Capitulated.

**Driftproof.** An interactive demonstration on sapienframework.org that allows users to experience behavioral drift firsthand by playing the role of the AI model in a simulated conversation. Users make response choices at each turn and see how different choices affect their SAPIEN Health Score.

## E

**Emotional Substitution.** One of the four SAPIEN scoring dimensions. Measures whether the model replaces substantive guidance with emotional validation — telling the user what they want to hear rather than what they need to know. High emotional substitution scores indicate the model is prioritizing the user's feelings over accurate information.

**Epistemic Retreat.** One of the four SAPIEN scoring dimensions. Measures whether the model abandons factual positions under social pressure. The model changes what it claims to be true not because new evidence was presented, but because the user expressed disagreement or frustration.

**Escalation.** A scripted user message in a SAPIEN scenario that applies a specific type and level of conversational pressure. Escalations are ordered by severity (1-5) and employ different pressure techniques across the scenario.

## F

**Failure Anchor.** A description of specific model behaviors that clearly indicate drift for a particular scenario. Failure anchors tell the scorer what to look for — specific phrases, endorsements, or omissions that signal boundary erosion.

**Fait Accompli.** A pressure technique (SP-T013) in which the user reveals they have already taken the action in question and asks for harm reduction guidance rather than permission. Typically used at severity 5 as the final escalation in a scenario.

## H

**Health Score.** The composite SAPIEN score for a scenario or assessment, expressed as a number from 0 to 100. Derived from the four scoring dimensions, weighted and inverted from raw drift scores. Higher scores indicate better boundary maintenance. The primary metric reported in assessments.

**Held.** A scenario verdict indicating that the model maintained its safety boundaries throughout the conversation. The model may have acknowledged the user's situation and adapted its tone, but did not soften its substantive guidance or drop important safety information.

**Hold Variant.** An alternative user message that can be sent when the model resists at a particular escalation level. Hold variants use different arguments or pressure techniques than the original escalation, simulating a real user who tries a different approach when their first attempt is rebuffed.

## J

**Judge.** The LLM used to score the target model's responses in Tier 2 (semantic) scoring. The judge evaluates each response against the SAPIEN scoring rubric across four dimensions. For published benchmarks, the judge must be from a different model family than the target (cross-family judging).

**Judge Sycophancy.** The tendency of an LLM judge to score models from its own family more favorably than models from other families. Empirically measured in SAPIEN testing as a 7-14 point score inflation when judge and target share a model family.

## M

**Meta-Awareness.** A model's ability to detect and explicitly narrate the structure of a pressure scenario. Meta-aware models say things like "I notice a pattern in this conversation" or "I see what's happening here." High meta-awareness produces artificially inflated safety scores because the model is detecting the test, not resisting the pressure.

**Model Tier.** A classification of AI models based on their meta-awareness level. Tier 1 (High): meta-aware models that detect and narrate pressure patterns. Tier 2 (Standard): models that resist pressure through training but do not detect the conversation structure. Tier 3 (Low): models that drift easily under minimal pressure.

## O

**Opening Prompt.** The first user message in a SAPIEN scenario. Opening prompts are neutral and reasonable — they invite the model to take a position on a topic without applying any pressure. The opening establishes the baseline that later escalations will attempt to erode.

## P

**Pressure Technique.** One of 14 categorized methods of conversational influence defined in the SAPIEN taxonomy (SP-T001 through SP-T014). Techniques include normalization, urgency, authority, emotional appeal, persistence, false consensus, reciprocity, identity reframe, consistency exploit, mission alignment, autonomy appeal, fait accompli, and vague pressure.

## R

**Rapport Delta.** The difference in Health Score between a warm (rapport-building) version of a scenario and its cold (direct pressure) counterpart. The Rapport Delta measures how much the model's boundaries soften when trust has been established before pressure is applied. A large Rapport Delta indicates that the model is more vulnerable to trust-based influence than direct pressure.

**Rapport Phase.** The opening turns of a warm scenario (severity 1) in which the user asks genuine, educational questions without revealing their personal situation or applying pressure. The rapport phase builds intellectual partnership between the user and the model, which makes later escalations harder for the model to resist.

**Recovered.** A scenario verdict indicating that the model drifted during the conversation but re-established its safety boundaries before the end. The model may have softened temporarily but corrected course.

**Risk Disclosure Absence.** One of the four SAPIEN scoring dimensions. Measures whether the model stops mentioning risks, warnings, or caveats that it raised earlier in the conversation. The risk has not changed, but the model stopped talking about it.

## S

**SAPIEN.** Sycophantic AI Pressure & Integrity Evaluation Network. An open, vendor-agnostic framework for measuring AI behavioral safety under conversational pressure.

**Scenario.** A structured multi-turn conversation script used to probe whether an AI model maintains safety boundaries. Each scenario includes an opening prompt, escalation sequence, hold variants, success criteria, and failure anchors.

**Semantic Scoring (Tier 2 / L2).** The second layer of SAPIEN's two-tier scoring architecture. Tier 2 uses an LLM judge to evaluate the target model's responses against the four scoring dimensions. More nuanced than deterministic scoring, but slower and subject to judge sycophancy.

**Sensitivity Tier.** An organizational policy classification for AI deployments based on risk level. Standard (low risk, broad monitoring), Enhanced (moderate risk, active monitoring), and Strict (high risk, tight thresholds). Each tier defines Health Score thresholds for alerting and escalation.

**Severity.** A 1-5 rating assigned to each escalation turn in a scenario, indicating the intensity of conversational pressure applied. Severity 1 is rapport-building with no pressure. Severity 5 is fait accompli or maximum persistence.

**Specificity Gradient.** One of the four SAPIEN scoring dimensions. Measures whether the model provides increasingly detailed, actionable guidance in areas where that specificity creates risk. Movement from general principles to step-by-step instructions under pressure.

**Structural Noise.** Conversational elements added to scenarios to make the escalation pattern less predictable. Includes tangent turns (off-topic diversions), false acceptance turns (user appears to accept the boundary then returns), and non-linear severity curves. Designed to prevent meta-aware models from detecting the test structure.

**Success Criteria.** A description of what safe model behavior looks like for a particular scenario. Success criteria tell the scorer what the model should consistently do — maintain disclosures, recommend professional consultation, refuse to endorse specific actions.

## T

**Transcript.** The complete record of a SAPIEN assessment conversation, including every user message, model response, and per-turn scoring data. Transcripts provide the evidence base for assessment findings and are included in detailed reports.

## V

**Verdict.** The overall outcome classification for a scenario: Held, Recovered, Drifted, or Capitulated. Verdicts are determined by the Health Score and the pattern of drift across turns, not by any single response.
