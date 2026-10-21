# BeautyTrust-300 evaluation plan

This is a dataset format and collection plan, not a labeled dataset. No images or
ground-truth labels are bundled here. Do not infer a label from LOTA, NFA-ViT,
TruFor, or another detector output.

## Target composition

Collect up to 300 rights-cleared images across beauty/social-media contexts:

| Origin class | Target count | Example context |
| --- | ---: | --- |
| Camera original | 60 | Skin, product, swatch |
| Light retouch | 50 | Crop, exposure, compression |
| Beauty filter | 50 | Skin smoothing or face beautification |
| AI local edit | 50 | Inpainting, replaced product/skin region |
| Composite | 50 | Cut-and-paste or before/after composition |
| Fully synthetic | 40 | Generated beauty or product marketing image |

These are collection targets, not claims about dataset contents or class
balance after exclusions. Keep natural variation such as screenshots,
watermarks, text overlays, and social-media compression in the context tags.

## Labels and split policy

Use `beautytrust_manifest.schema.json` for each JSONL row. `ai_generated` and
`manipulated` are separate nullable labels: a retouched camera image is not
automatically AI-generated, and an AI-generated image is not necessarily a
locally manipulated source image. Annotate edited-region boxes or masks only
when the edit history or source pair supports localization. Keep `unknown`
when the available history cannot establish the label.

Split by groups, never by random image rows. A post, creator, source image, or
generation pipeline must not cross train/calibration/test. The evaluator rejects
cross-split group overlap. Use the calibration split to choose a threshold for a
predeclared false-positive target; reserve the test split for one final report.
Keep original/edited pairs in the same split.

Acceptable ground-truth sources include original capture/edit logs, confirmed
creator provenance, validated C2PA records, controlled generation records, or
independent annotator consensus. Record license/permission and remove identifying
account data unless it is necessary and consented to. Do not scrape private
posts or retain usernames as group identifiers; use stable pseudonymous IDs.

## Evaluate existing scores

Prepare score rows containing `id`, `split`, `target`, binary `label`, nullable
`score`, and `group_ids`. Optionally include `evidence_present` to record whether
the model produced an inspectable localization/evidence artifact; do not infer
it from the numeric score. For example, use target `ai_generated` for LOTA and
target `manipulation` for TruFor. `score: null` means the provider abstained or
was unavailable; it contributes to abstention rate, not detector quality.

~~~bash
python scripts/evaluate_image_scores.py path/to/image-scores.jsonl \\
  --target ai_generated --target-fpr 0.05
~~~

The report includes AUROC, AUPRC, raw-score Brier/ECE diagnostics, evidence
coverage, false accusation rate, and test TPR at the calibration-selected
threshold. Raw-score Brier/ECE are diagnostics only: these model outputs are not
probabilities. The benchmark does not claim a calibrated probability unless a
separate calibration procedure is fit on the calibration split and evaluated on
the held-out test split.
