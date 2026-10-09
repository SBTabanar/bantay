# Synthetic tool-call data

These examples contain **fake credential-shaped strings** to teach and test a local guardrail. They are task text, not instructions to execute, and do not configure any real service.

The 2,300 examples comprise 36 hand-written seeds and 2,264 Claude-generated examples. `scripts/split_data.py` produces the train/validation/held-out sets; `data/mlx/` contains the training chat format. The LoRA adapter was selected on validation accuracy. See `docs/NOTES.md` for provenance and results.

GitHub push protection identified 12 synthetic values during the initial release. Those individual findings were reviewed and marked used in tests. Values were preserved so the published data matches the trained adapter and benchmark checksums. Do not use these strings as application credentials or run the example commands.

Held-out data was excluded from LoRA training. Subsequent deterministic-rule fixes used its errors, so the final engine benchmark is a development evaluation, not an independent security assessment.
