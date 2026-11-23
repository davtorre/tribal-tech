# deck: animated slides from a `.tt` text file

```
python3 deck/build.py path/to/presentation.tt.md -o deck.html --tree   # --refresh re-runs every #Data script
```

Output: one self-contained HTML file (images inlined as base64, fonts and MathJax from CDN).
`--tree` prints every element with its resolved ID, step and grid position. Warnings go to stderr.

Navigation: → / space / click = next step, ← = back, `f` = fullscreen. The URL hash is `#<SlideID>` or `#<SlideID>.<step>`.

## Syntax

| line | meaning |
|---|---|
| `---` | slide separator (a new `#…Slide` also starts a slide; a missing `---` gives a warning) |
| `#Type: header` | an **element**. The number of `#` is its depth. Types: `Settings`, `Data`, `MainSlide`, `BodySlide`, `Column`, `Band`, `PopUpCard`, `BodyText`, `Heading`, `LoadImage`, `Table`, `Plot`, `Html` |
| `##Name: text` | a **field** of the closest shallower element (`Title`, `Text`, `Caption`, …). Following lines are added to it until the next key, region or `---` |
| `##Column: L 0.75` | a body column with ID `L` and relative width 0.75, or `auto` = as wide as its content (see below). The old `(L): 0.75` still works, with a warning |
| `<!-- text -->` | a comment, as in HTML: a whole line, the end of a line, or several lines. Inside ``` fences and `` `code` `` it is kept as text. The old `; text` still works, with a warning |

### Element header: `[ID] [step,col,row] [{key=val, ...}] [<!-- comment -->]`

```
##PopUpCard: hh 2,1:,2 {anim=zoom}   <!-- ID hh, 2nd step, columns 1→end, row 2 -->
```

- **ID**: optional, one word. The full ID is always the path from the slide: `hh` on a card in column `L` of slide
  `ModelCnx1` is `ModelCnx1.L.hh`, so `L` can be reused on every slide. If missing, it is `<Type><n>`
  (`ModelCnx1.L.PopUpCard3`). Slides without one become `s<index>`. Duplicates give a warning and a `_<line>` suffix.
- **tuple**: `step,col,row`. Any part can be left empty (`3` = step only, `,2,1` = position only).
  - `step k`: visible after k−1 clicks (step 1 = visible when the slide opens). No step = always visible.
  - `col`/`row`: `n`, `a:b` (inclusive), `a:` (open), `:` (= `1:`). The grid of each column region is as large as the
    largest index used in it. Open spans: rows are resolved first and stop before the next row where another element
    starts; columns then extend until they reach an occupied cell.
- **`{…}`**: settings: `anim` (`up` | `zoom` | `left` | `fade`), `class`, `style` (raw CSS).
- **`<!-- … -->`**: a comment (`;` is normal text).

The header and the block form are equivalent. Every header part can also be written as a field:

```
##PopUpCard:
###Id: hh
###Step: 2
###Col: 1:
###Row: 2
###Anim: zoom
###Title: Households
###Text: people sharing a household
```

### Fields per element

| element | fields |
|---|---|
| `Settings` | `Sections` (footer order; default: order of first use), `Css` (deck-specific CSS, on the lines after it), `Python` (interpreter for `.py` data scripts, e.g. `.venv/bin/python`; default: the one running the build), `BMargin` (footer text), `Style` (template folder), `MathJax` (yes/no), `Palette` (`hex-hex-…`, gives `--p1…--pN`), `Anim` (default reveal), `Title` |
| `MainSlide` | `Top` (eyebrow), `Title`, `Subtitle`, `Authors`, `Name` |
| `BodySlide` | `SlideTop` (eyebrow), `SlideTitle`, `Section` (footer bar), `Name` (counter; default: SlideTop) |
| `PopUpCard` | `Tag` (label chip; `{tag=warn}` = orange), `Kicker` (small caps line), `Title` (empty = no title), `Text` |
| `Heading` | text on the lines after it: a slide-size heading anywhere (e.g. inside a column) |
| `Html` | raw HTML on the lines after it. A last resort: each use marks an element that is missing |
| `Plot` | `Kind`, `From` (a #Data key), `Caption`; options in `{…}` (see Plots) |
| `Toggle` | `Options` (`value = label, …`), `Default`. Its ID is the state variable it sets (see State) |
| `BodyText` | `Text` (lines right after the header also count as `Text`) |
| `Column` | `Width` (or a bare number in the header, or `{w=…}`) |
| `LoadImage` | `Src` (or the first path-like token in the header, or `{src=…}`), `Caption`, `Alt` |
| `Table` | `Data`, `Src` or `From` (a #Data key holding `{columns, rows, align?}` or records), `Caption`, `Columns`, `Filter`, `Format`, `Highlight`, `Reveal` (see below) |

Text: paragraphs, `-` / `1.` lists, `**bold**`, `*italic*`, `` `code` ``, `[link](url)`, `R^eth^` (superscript), `R~ab~` (subscript),
a `\` at the end of a line for a line break, `$…$` and `$$…$$` (MathJax). Titles keep their line breaks as written.

Styles for `{class=…}`: `small`, `muted`, `cite`, `take`; on a slide: `roomy` (larger card text), `compact`.
Spacing: `{style=--grid-gap:48px}` between grid cells (two values = rows, columns), `--band-gap` between bands.
`{cols=auto}` on a Column, Band or slide: grid columns as wide as their content, packed left (default: equal widths).

## Including slides from another file

```
#Include: ../talk/tribal_tech.tt.md Grid Syntax {section=Tribal Tech}
```

Puts those slides of another `.tt` file here, in the order given (no IDs = all of its slides), separated by `---`.
Write `---` before and after the line as for any slide. `{section=…}` replaces their `##Section`.
Only slides come along: the other file's `#Settings` and `#Data` do not, and relative paths inside the slides
resolve against *this* file, so include slides that do not need either (or declare the same `#Data` here).
Warnings name the file and line the slide came from. Lines inside ``` are never expanded.

## Columns and bands

`##Column: L 0.75` splits the slide body side by side; widths are relative (0.75 + 0.25, or 3 + 1).
Every `##` element after it belongs to it, up to the next `##Column` or the end of the slide (like markdown sections),
so the cards keep their `##`. Writing them one level deeper (`###PopUpCard` with `####` fields) means the same thing.
The slide body is a stack of **bands**: elements before the first Column form a full-width band above the columns,
consecutive Columns sit side by side, and `##Band:` closes the columns and starts a new full-width band (whose
`##` elements follow it, like a Column's). Each column has its own grid, sized by the tuples
inside it. A Column can have a step too (`##Column: R 0.4 {anim=left}` + `###Step: 3`) to bring in a whole side at once.

## Tables

Rows either written in place, as a markdown pipe table (the lines after `##Table:` are its `Data`):

```
##Table: waves 1
###Reveal: rows
###Data:
| wave     | respondents | mean $k$ |
|----------|------------:|---------:|
| Wave 1   | 1,203       | 7.8      |
```

or read from a file, `{src=…}` or `###Src:` (`.csv`, `.tsv`, `.json` as a list of records or `{columns, rows}`;
relative to the `.tt` file):

```
##Table: metrics 3,1:,2 {src=results/full_metrics.csv}
###Columns: wave, run as "Fit", canberra_eth_all as "Canberra"
###Filter: run != null and wave == wave1
###Format: Canberra=.3f, N=,d
###Highlight: min(Canberra), row(Fit=stage3)
###Reveal: rows 2:4
```

- `Filter`: `column op value`, joined with `and`; ops `== != < <= > >=`; numbers compare as numbers;
  `null` matches empty / null / NA / NaN cells. Applied first, so it uses the file's column names.
- `Columns`: pick, order, rename (`name as "Label"`).
- `Format`: Python format specs per column (`.3f`, `,d`, `.1%`). `Format` and `Highlight` come after `Columns`, so they use the new labels.
- `Highlight`: `min(col)`, `max(col)` bolds the cell(s); `row(col=value)` a row; `col(name)` a column; `cell(r,c)` one cell (1-based).
- `Reveal`: `rows [a:b]` or `cols [a:b]`, one per click, starting right after the table's own step
  (table at step 1 + `Reveal: rows` on 3 rows → rows at steps 2, 3, 4). Rows/columns outside the range come with the table.
- Alignment: from the pipe table's `:--:` row if given; otherwise numeric columns right, text left.
- Row IDs `<table>.r<n>`, header cells `<table>.<Label>`. Cells accept `**bold**` and `$math$`.

Not supported on purpose: merged cells, multi-level headers.

## Data and values in text

A `#Data` source is a deck-wide element (depth 1, anywhere outside slides). Its script prints JSON; the last
non-empty line of stdout is read, so progress prints before it are fine. Any value can then go into any text:

```
#Data: ens {src=scripts/ensemble.py}
##Args: ensemble.csv
##Inputs: ensemble.csv, data/*.parquet

##PopUpCard:
###Text: The average of the ensemble is $\bar T = {{ens.T_avg:.1f}}$ over {{ens.n:,d}} runs.
```

- **Source**: `.py` runs with `Settings.Python`, `.R` with Rscript, `.jl` with julia, `.sh` with bash, `.json` is
  just read. Paths are relative to the `.tt` file, and so is the working directory (`Cwd` changes it). `Args` are
  passed to the script; `Timeout` in seconds (default 600).
- **Cache** in `.deck_cache/` next to the `.tt` file, **only when `Inputs` lists the files the script reads**
  (globs allowed). The key is the script's text, its arguments, the interpreter and the size/mtime of those files,
  so editing the data reruns the script. Without `Inputs` the script runs on every build, so a number can never be
  stale without you knowing. `--refresh` reruns everything; `{cache=no}` turns it off for one source.
- **Placeholder** `{{source.key}}` or `{{source.key:fmt}}`. Keys can go deeper: `{{ens.q.1}}`, `{{fit.stage1.rmse}}`.
  `fmt` is a Python format spec (`.2f`, `,d`, `.1%`) or `.Ntex` for powers of ten (`3.2 × 10⁻⁵`).
  Without a format, floats print with 6 significant digits.
- **Works everywhere text goes**: cards, titles, eyebrows, captions, table cells, the footer, and inside `$…$` / `$$…$$`.
  Inside math a number goes in as is and a text value as `\text{…}`, so `x_{{ens.label}}` gives a valid subscript.
  The match is strict (a word, a dot, a key, and the word must be a declared source), so LaTeX like
  `\frac{{a}}{b}` or `{{0.5}}` is never touched.
- **Provenance**: in text, a value has a dotted underline and its tooltip says where it came from
  (`ens.T_avg ← scripts/ensemble.py (cached)`). In math it is wrapped in `\class{val}{…}`.
- **Mistakes**: a missing key, a format that does not fit, or a failed script gives a warning with the line number,
  and the slide shows a red `⚠ ens.key` (in math: `[ens.key?]`) instead of a silent gap.

## Plots

```
##Plot: full {kind=heatmap, from=ciro.collapse.full, width=448, gamma=0.5, vmax=q98, veil=2}
###Caption: hidden truth
```

A plot kind is a file `plots/<kind>.js` that registers `DeckPlots.<kind> = {draw(host, data, opts, ctx), enter?, leave?, lazy?}`.
The builder includes only the kinds a deck uses, and the libraries they name in a `// requires: <url>` line.
`data` is the value at `from`; every option whose value is a #Data path is replaced by that value too
(`rowlabels=ciro.view.eth_short`); `#name` becomes the full ID of the element `name` on the same slide.
Plots can add their own steps (elements with `data-step`), so a chart can build up click by click.

Plots are deliberately generic: the shaping (which series, labels, tooltips, colours) happens in the data script,
which outputs the shape the kind documents at the top of its file.

| kind | data | main options |
|---|---|---|
| `heatmap` | number[][] | `width`, `gamma`, `vmax` (number, `max`, `q98`), `rowlabels`, `collabels`, `labels=yes`, `values=yes`, `gap`, `blocks`, `veil` (step) |
| `motif` | (number \| null)[][]: opacity, null = unknown tile | `size`, `gap`, `radius`, `color`, `q` |
| `strip` | `{axis, series, rows: [{points, clouds}]}` | `reveal=series` (one series per click), `highlight` (row id), `width`, `height` |
| `lines` | `{x: {label, log?}, y: {label, log?, fmt?}, series: [{id, legend, color, x, y, dash?}], refs?: [{y, label}]}` | `width`, `height`, `reveal=series`, `legend` |
| `scatter3d` | `{layout, modes: {name: {traces, notes, legend}}}` (Plotly) | `mode`, `spin=yes`, `aside=#id` (where notes + legend go), `height` |
| `flowchart` | a JSON Canvas (`.canvas`) as is, `{nodes, edges}`, or a part of one picked by the script | `scale` (0.7) or `width`, `font` (22, canvas units), `highlight` (node id, several joined with `+`) |

Colours: `--name` is read from the page's CSS variables; in Plotly specs any string `"var(--name)"` is.

## State

`##Toggle: wave` with `###Options: wave1 = Wave 1, wave2 = Wave 2` draws a button group and creates the variable
`$wave`. Use it in data paths, in plot options and in placeholders:

```
##Plot: c1 {kind=heatmap, from=ciro.mats.$wave.eth.stage1, vmax=ciro.view.vmax_eth.$wave}
###Caption: c.1 OHE · {{ciro.metrics.$wave.stage1.canberra_eth_all:.2f}}
##Plot: strip {kind=strip, from=ciro.view.strips.raw, highlight=$wave}
```

A click redraws every plot and value that mentions `$wave` (lazy plots on a hidden slide wait until it opens).
The builder embeds the data for every option. State is deck-wide: two toggles with the same ID stay in sync.
Values that follow a toggle cannot go inside `$…$` math (MathJax typesets once); the builder warns.

## Figures and export

`#Figure: Name {width=1240}` is a top-level element like a slide, without the slide frame: `Title`, `Caption`, and a
body laid out exactly like a slide's (bands, columns, grid). A file with only figures opens as a sheet of figures.
Toggles work in figures too, and are left out of exports.

Every element can be exported by its ID: a whole slide, a `#Figure`, or one plot or band inside either.

```
python3 deck/build.py figs.tt.md --export figures --format png,pdf            # every #Figure
python3 deck/build.py talk.tt.md --export REth --state wave=wave2 --step 5    # a slide as it is after 4 clicks
python3 deck/build.py talk.tt.md --export Hide.full --format png --bg white --pad 0
python3 deck/build.py figs.tt.md --export Strip.strip --format svg            # the raw SVG of one plot
```

| format | what you get |
|---|---|
| `png` | the element at `--scale` (default 2 = retina). `--bg transparent` gives a transparent background |
| `pdf` | vector, page sized to the element. 3D (WebGL) plots are embedded as an image |
| `svg` | only for an element holding exactly one SVG plot; fonts are written into it (the reader needs them installed). HTML parts (legend, caption) are left out, with a note |

`--state var=value` sets toggles, `--step N` shows the element as at that step (default: all steps), `--pad` (default 24;
0 for a #Figure), `--outdir` (default: next to the HTML). Files are named `<ID>.<format>`.
Export runs headless Chrome (set `CHROME=/path` if it is not found); the page itself does the layout: open
`page.html?only=<ID>&state=wave:wave1` to see exactly what will be exported. About 4 s per element and format.

## Templates

`templates/<Style>/template.html` + `style.css`. `engine.js` (navigation and reveals) is the same for every template.
`plex-grotesque` is the IBM Plex + Bricolage Grotesque look of `contact_matrices/reports/presentation_ciro`.
`seminar` is the look of the *Synthetic Populations Seminar* deck: navy and paper, Space Grotesk headings, IBM Plex Sans
text, Source Serif 4 statements, cards with a coloured edge, a five-colour stripe on the cover
(`examples/seminar/seminar.tt.md` shows all of it). Its extra classes:

| where | class | effect |
|---|---|---|
| slide | `dark` | navy slide, gold eyebrow; cards become ruled columns |
| card | `red`, `orange`, `gold`, `steel` | edge colour (default blue); `top` puts the edge on top |
| text | `stat` | `**bold**` becomes a big red number (gold on `dark`) |
| text | `serif`, `take` | a serif statement (medium, large) |

`Palette` maps to `--p1` ink, `--p2` blue, `--p3` red, `--p4` orange, `--p5` gold, `--p6` steel.

`isi` is inspired by the ISI Foundation presentation pack (`examples/media/isi`): black and light-grey grounds with
the pack's own artwork and logo (in `templates/isi/img/`), six flat brand colours, hard-edged white cards, Inter for text
and Space Mono for labels, a small coloured square as the recurring mark (`examples/isi/isi.tt.md` shows all of it):

| where | class | effect |
|---|---|---|
| slide | `dark` | black ground |
| slide | `art` | the pack's pattern on the right; content keeps to the left 54% (with `dark`: the dark pattern) |
| slide | `cyan`, `purple`, `pink`, `green`, `red`, `orange` | a solid colour slide, for section breaks and statements |
| slide | `logo` | closing slide with the big logo |
| card | `cyan`, `purple`, `pink`, `green`, `red`, `orange` | colour of the card's square mark (default cyan); add `fill` to colour the whole card |
| text | `stat`, `take` | a big number (`**bold**`), a large statement |

A template's `style.css` can start with `/* extends: plex-grotesque */`: that template's CSS comes first, so the new one
only holds what it changes (`seminar` does this; its `template.html` only swaps the fonts).
In a template's CSS, `url(asset:img/x.png)` is replaced by the file `templates/<Style>/img/x.png` as a data URI,
so a deck stays one self-contained file.
