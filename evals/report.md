# Eval report

Not yet generated - this repo doesn't have a working `GEMINI_API_KEY`
configured, so no baseline numbers have been recorded here.

Generate it with:

```
python -m evals.run_eval --mode full --report evals/report.md
```

To get the actual "accuracy went from X% to Y%" story, run all three modes
against the same sample set and compare:

```
python -m evals.run_eval --mode cheap-only     --report evals/report-cheap-only.md
python -m evals.run_eval --mode cheap+validate --report evals/report-cheap-validate.md
python -m evals.run_eval --mode full           --report evals/report-full.md
```

Remember the samples in `evals/samples/` are synthetic by default (see
`evals/samples/README.md`) - regenerate this report against real, hand-labeled
invoices before treating the numbers as a real accuracy claim.
