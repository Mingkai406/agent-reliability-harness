# Figures and report presentation

The README uses count plots and a labeled gate matrix over committed example evidence.
The HTML report uses the same count plot, followed by a searchable case table with expandable
checks. All output is self-contained: no web fonts, chart CDN or analytics scripts.

## Design references

The [ggplot2 extension gallery](https://exts.ggplot2.tidyverse.org/gallery/) informed the choice
of small, aligned plots. In particular, [cowplot](https://wilkelab.org/cowplot/articles/introduction.html)
uses restrained themes and aligned axes, and [patchwork](https://patchwork.data-imaginist.com/)
composes related plots into panels. This project implements those layout ideas in lightweight
SVG; R and these packages are not runtime dependencies.

- Outcome counts share a zero-based axis and have direct numeric labels. Different applications
  have different case counts; the plot is a description of the fixture, not a ranking.
- Completed tasks, observed rejections and detected negative controls stay separate. The
  matched-expectation column counts scenario outcomes, including scheduled fault coverage.
- Gate cells carry text as well as color. A policy rejection leaves gates **not run**; timeout
  is distinct from a failed gate. Multiple failures in a candidate remain visible.
- README text provides the counts in an expandable table and links to source evidence.
- Report filters affect only the case table. The chart and summary always describe the full suite.
- Report evidence links appear only for files present in that export. The committed full
  example includes aggregate results; fresh runs retain individual case evidence too.

## Regenerate

From the repository root, using Python 3.11+:

```sh
python scripts/render_readme_figures.py
python scripts/render_example_reports.py
```

The first command reads `examples/showcase/results.json` and the code-validation results and
snapshots. It writes `docs/assets/outcomes.svg` and `docs/assets/validation-matrix.svg`.
The second regenerates HTML and Markdown for the four committed normalized example suites.
Neither command runs an experiment or changes recorded JSON, timestamps or source fingerprints.
Those fingerprints describe the original execution, not the later presentation refresh.

New runs automatically use `src/agent_reliability/report_template.html` and the shared SVG
renderer. The legacy refund-only `demo` report has its own renderer.
