# rca calibration: held-out seeds 10001-11000 per scenario

Cut-offs frozen 2026-11-17 from the dev seeds (`tuning.json`), unchanged here: `{'high_consistency': 0.75, 'high_share': 0.5, 'high_z': 3.0, 'medium_consistency': 0.5, 'medium_share': 0.25, 'medium_z': 2.0, 'z_total': 2.0}`. Read once; a missed target is reported, not re-tuned.

| Measure | Target | k / n | Rate | 95% CI (Clopper-Pearson) | Met |
|---|---|---|---|---|---|
| share of candidates labelled high that are the planted cause | >= 90% | 4,682 / 4,684 | 100.0% | 99.8%-100.0% | yes |
| share of candidates labelled medium that are the planted cause | 50% to 90% | 323 / 416 | 77.6% | 73.3%-81.6% | yes |
| share of candidates labelled low that are the planted cause | < 50% | 1,995 / 68,856 | 2.9% | 2.8%-3.0% | yes |
| share of no-change datasets with any candidate above low | <= 5% | 25 / 1,000 | 2.5% | 1.6%-3.7% | yes |
