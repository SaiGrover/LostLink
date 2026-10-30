# Evaluation and retuning

The bundled demo reports are interface fixtures. `evaluation/pilot/dataset.json`
contains 180 constructed pairs from 60 Wikimedia Commons source images in six
categories. The manifest records the original file page, artist, licence,
licence URL and image hash. The split is source-disjoint. These are automated
source-identity labels and require manual review before use in a research claim.

The pilot builder can be rerun with `python manage.py build_pilot`. Positive
pairs use a brightness/compression variant of the same photo; candidate text
comes from the source title. This is an optimistic functional benchmark, not
representative of independently photographed or worded real campus reports.
Before making an accuracy claim, manually annotate independent report pairs,
check licensing and pair identity, and rerun evaluation with a source-disjoint
validation/test split.

The text evaluation command accepts CSV columns:
`query_id,query,candidate,label,split`. Labels are 0/1; splits are validation/test.
Each query should have several candidates including hard negatives. Run:

```
python manage.py evaluate_matches path/to/pairs.csv --split validation
python manage.py evaluate_matches path/to/pairs.csv --split test
```

Select `LOSTLINK_TEXT_MODEL=tfidf` or `sbert` in the process environment to compare
text candidates. Select `LOSTLINK_IMAGE_MODEL=histogram`, `resnet`, or `clip` for
visual extraction. Install requirements-ml.txt first. Pretrained model downloads
occur on first use. Errors are propagated rather than silently substituting models.
Run `python manage.py rebuild_matches` after changing image models; existing
vectors must not be mixed. This command refreshes derived matches without deleting
reports or claims. The application displays only current-version matches. Recomputed
pairs update existing match rows, including rows referenced by historical claims.

`python manage.py evaluate_hybrid evaluation/pilot/dataset.json` compares
TF-IDF/Sentence-BERT with ResNet/CLIP, tests candidate weights and thresholds
on validation, and measures the untouched test split. `--apply` writes the
selected profile and requires app restart plus `rebuild_matches`. The current
report is `evaluation/results.json`. All four model combinations tied on
validation F1, precision and MRR; deterministic order selected TF-IDF + ResNet.
Its test F1 was 1.0 on 90 constructed pairs after visual category curation. This number has no demonstrated
external validity. Precision@3 is 1/3 because each query has exactly one
positive among three candidates; it provides no useful ranking discrimination.
Record dependency versions, model revisions, weights, thresholds and code commit
with every experiment. Increment MATCHING.VERSION whenever weights change.

`python manage.py monitor_matching` stores a score histogram, mean and tier
distribution with the model manifest. It compares compatible snapshots by
total-variation distance and flags shifts above 0.2 for review. This monitors
score distribution, not accuracy; a periodic run must be scheduled by the host.

Before deployment, configure PostgreSQL through DATABASE_URL, a private secret
key, DEBUG=false, allowed hosts and trusted CSRF origins. Configure WSGI serving,
static files and protected media storage in the hosting environment.
