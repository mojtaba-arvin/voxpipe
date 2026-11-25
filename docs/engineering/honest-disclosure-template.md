# Honest Disclosure Template

Required text for any comment closing or updating issues #32, #47,
#48 with automated output.

    **Automated silver baseline — NOT human-validated.**

    - Data: synthetic/template-generated (sales/support scenarios;
      no real customer data; no medical content).
    - Annotation: deterministic rules and/or local LLM judge;
      no human annotator.
    - ASR/LID: Qwen3-ASR-0.6B and/or WhisperX; CPU-only, int8;
      baseline, not production accuracy claim.
    - Audio (#47): read-speech (Common Voice, CC0, exact version)
      and/or synthetic with injected overlap; NOT natural
      conversational audio.
    - Parameters, seed, and metric definitions are recorded.
    - To upgrade to **gold**: real consented conversation +
      two independent human annotators + adjudication.

For medical-domain outputs, add:

    - No medical readiness claim is made. Synthetic data does
      not support medical readiness. Human-validated, consented
      clinical data is required before any medical claim.
