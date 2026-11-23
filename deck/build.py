"""Build an animated HTML deck from a .tt source file.

    python3 deck/build.py SOURCE.tt.md [-o OUT.html] [--tree] [--refresh]
    python3 deck/build.py SOURCE.tt.md --export ID [--export ID ...] [--format png,pdf,svg] [--scale 2]
                          [--state wave=wave1] [--step N] [--bg paper|white|transparent] [--pad PX] [--outdir DIR]
      ID: any element (a slide, a #Figure, one plot or band inside a slide), or "figures" / "slides" for all of them

Source format (see deck/README.md):
  ---                      slide separator (optional: a level-1 element also starts a new slide)
  #Type: header            an element; the number of # is its depth in the tree
  ##Field: text            a field of the enclosing element (Title, Text, Caption, ...)
  ##Column: L 0.75         a column of the slide body; the ## elements after it go inside it
  #Data: ens {src=x.py}    a data source: the script prints JSON; {{ens.key:fmt}} puts a value in any text

Element header grammar:  [ID] [step,col,row] [{key=val, ...}] [; comment]
  col/row accept  n | a:b | a: | :   (1-based, inclusive)
"""
import argparse, base64, csv, glob, hashlib, html, io, json, mimetypes, re, shlex, subprocess, sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- schema
# Element types. Any other #Key is a field of the element above it.
ELEMENTS = {
    'Settings':  {'fields': {'BMargin', 'Style', 'MathJax', 'Palette', 'Anim', 'Title', 'Python', 'Sections', 'Css'}},
    'Data':      {'fields': {'Src', 'Args', 'Inputs', 'Cwd', 'Timeout'}},
    'MainSlide': {'fields': {'Title', 'Subtitle', 'Top', 'Authors', 'Name'}, 'slide': True},
    'BodySlide': {'fields': {'SlideTop', 'SlideTitle', 'Section', 'Name'}, 'slide': True},
    'Figure':    {'fields': {'Title', 'Caption', 'Name'}, 'slide': True},   # a standalone figure: no slide chrome
    'Column':    {'fields': {'Width'}},
    'Band':      {'fields': set()},
    'PopUpCard': {'fields': {'Title', 'Text', 'Tag', 'Kicker'}},
    'Heading':   {'fields': {'Text'}, 'default_field': 'Text'},
    'Html':      {'fields': {'Text'}, 'default_field': 'Text'},
    'Plot':      {'fields': {'Kind', 'From', 'Caption'}},
    'Toggle':    {'fields': {'Options', 'Default'}},
    'Embed':     {'fields': {'Src', 'Caption'}},
    'BodyText':  {'fields': {'Text'}, 'default_field': 'Text'},
    'LoadImage': {'fields': {'Src', 'Caption', 'Alt'}},
    'Table':     {'fields': {'Data', 'Src', 'From', 'Caption', 'Columns', 'Filter', 'Format', 'Highlight', 'Reveal'},
                  'default_field': 'Data'},
}
# Fields any element may carry; they set the same things as the inline header ({...} and the tuple).
SETTING_FIELDS = {'Id', 'Step', 'Col', 'Row', 'Anim', 'Class', 'Style'}

KEY_RE = re.compile(r'^(#+)([A-Za-z][\w-]*)\s*(?::\s?(.*))?$')
LEGACY_REGION_RE = re.compile(r'^\((\w+)\)\s*:?\s*(.*)$')
TUPLE_RE = re.compile(r'^[\d:]*(?:,[\d:]*)+$|^\d+$')
NUMBER_RE = re.compile(r'^\d*\.?\d+$')
SETTINGS_RE = re.compile(r'\{([^}]*)\}')


@dataclass
class Node:
    type: str
    level: int
    line: int
    header: str = ''
    fields: dict = field(default_factory=dict)     # name -> text
    children: list = field(default_factory=list)
    id: str | None = None
    step: int | None = None
    col: tuple | None = None                       # (start, end) ; end None = open
    row: tuple | None = None
    width: float | str = 1.0                       # Column only; 'auto' = as wide as its content
    opts: dict = field(default_factory=dict)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Deck:
    def __init__(self):
        self.settings = Node('Settings', 1, 0)
        self.slides: list[Node] = []
        self.data: list[Node] = []                 # #Data sources, in source order
        self.values: dict[str, object] = {}        # source ID -> parsed JSON
        self.origin: dict[str, str] = {}           # source ID -> what produced it (for tooltips)
        self.toggles: dict[str, dict] = {}         # state variable -> {options: [(value, label)], default}
        self.embed: set[str] = set()               # data paths the page needs at runtime (may contain $vars)
        self.kinds: set[str] = set()               # plot kinds used
        self.warnings: list[str] = []
        self.where: list[str] | None = None        # line n -> "line n" or "other.tt.md:m" (set when #Include is used)

    def warn(self, line, msg):
        where = self.where[line - 1] if self.where and 0 < line <= len(self.where) else f'line {line}'
        w = f'{where}: {msg}'
        if w not in self.warnings:
            self.warnings.append(w)


# ---------------------------------------------------------------- includes
INCLUDE_RE = re.compile(r'^#Include:\s*(\S+)\s*(.*?)\s*$')
SLIDE_RE = re.compile(r'^#(MainSlide|BodySlide|Figure)\b:?\s*([\w-]*)')


def slide_blocks(lines):
    """The slides of a .tt file as {ID: [(line number, text), ...]}, in file order. #Settings and #Data are left out."""
    blocks, cur, fence = {}, None, False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith('```'):
            fence = not fence
        if fence or line.lstrip().startswith('```'):
            if cur is not None:
                cur.append((n, line))
            continue
        if line.strip() == '---' or (line.startswith('#') and not line.startswith('##') and not SLIDE_RE.match(line)):
            cur = None
            continue
        if m := SLIDE_RE.match(line):
            cur = blocks.setdefault(m.group(2) or f's{len(blocks) + 1}', [])
        if cur is not None:
            cur.append((n, line))
    return blocks


def expand_includes(text: str, base: Path):
    """Replace each `#Include: path [ID ...] [{section=Name}]` line by those slides of another .tt file
    (all of them if no ID is given), separated by ---. Returns the text, where each line came from, and warnings."""
    out, where, warns, fence = [], [], [], False
    for n, line in enumerate(strip_comments(text).splitlines(), 1):
        if line.lstrip().startswith('```'):
            fence = not fence
        m = None if fence else INCLUDE_RE.match(line)
        if not m:
            out.append(line); where.append(f'line {n}')
            continue
        rest, opts = m.group(2), {}
        if sm := SETTINGS_RE.search(rest):
            opts = dict(kv.split('=', 1) for kv in (x.strip() for x in sm.group(1).split(',')) if '=' in kv)
            rest = rest[:sm.start()] + rest[sm.end():]
        path = base / m.group(1)
        if not path.is_file():
            warns.append(f'line {n}: #Include: {m.group(1)} not found'); continue
        blocks = slide_blocks(strip_comments(path.read_text()).splitlines())
        ids = rest.split() or list(blocks)
        first = True
        for i in ids:
            if i not in blocks:
                warns.append(f'line {n}: #Include: no slide {i} in {m.group(1)} (have: {", ".join(blocks)})'); continue
            body = [(k, t) for k, t in blocks[i]]
            if sec := opts.get('section'):
                body = [(k, t) for k, t in body if not t.startswith('##Section:')]
                body.insert(1, (body[0][0], f'##Section: {sec}'))
            if not first:
                out.append('---'); where.append(f'line {n}')
            first = False
            for k, t in body:
                out.append(t); where.append(f'{m.group(1)}:{k}')
    return '\n'.join(out), where, warns


# ---------------------------------------------------------------- comments
COMMENT_LINE = '\x01'   # a line that held only a comment: skipped by the parser, so it never splits a paragraph


def strip_comments(text: str) -> str:
    """Remove <!-- ... --> comments (one line, inline, or over several lines), keeping the line count.
    Text inside ``` fences and inside `inline code` is left alone, so code can show the syntax."""
    out, fence, inside = [], False, False
    for line in text.split('\n'):
        if not inside and line.lstrip().startswith('```'):
            fence = not fence
        if fence and not inside:
            out.append(line)
            continue
        res, i, cut = [], 0, inside
        while i < len(line):
            if inside:
                j = line.find('-->', i)
                if j < 0:
                    i = len(line)
                else:
                    inside, i = False, j + 3
            elif line[i] == '`' and (j := line.find('`', i + 1)) > 0:
                res.append(line[i:j + 1]); i = j + 1
            elif line.startswith('<!--', i):
                inside, cut, i = True, True, i + 4
            else:
                res.append(line[i]); i += 1
        kept = ''.join(res).rstrip() if cut else ''.join(res)
        out.append(COMMENT_LINE if cut and not kept.strip() else kept)
    return '\n'.join(out)


# ---------------------------------------------------------------- parsing
def parse(text: str) -> Deck:
    deck = Deck()
    stack: list[Node] = []           # open elements, by depth
    cur_field: tuple[Node, str] | None = None
    column = None                    # the Column that collects the slide's following ## elements
    saw_sep = True

    fence = False                    # inside ``` in a field: lines are text, even if they look like keys
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if cur_field and (fence or line.lstrip().startswith('```')):
            if line.lstrip().startswith('```'):
                fence = not fence
            node, f = cur_field
            node.fields[f] += '\n' + line
            continue
        if line == COMMENT_LINE:              # a line that held only <!-- ... -->
            continue
        if line.lstrip().startswith(';'):     # the old comment syntax: still a comment
            deck.warn(n, f'"; ..." is the old comment syntax; write "<!-- {line.lstrip()[1:].strip()[:30]} -->"')
            continue
        if line.strip() == '---':
            stack, cur_field, column, saw_sep = [], None, None, True
            continue
        km = KEY_RE.match(line)
        if not km and (lm := LEGACY_REGION_RE.match(line)) and stack and ELEMENTS[stack[0].type].get('slide'):
            deck.warn(n, f'"({lm.group(1)})" is the old column syntax; write "##Column: {lm.group(1)} {lm.group(2).strip() or 1}"')
            km = KEY_RE.match(f'##Column: {lm.group(1)} {lm.group(2).strip()}')
        if not km:
            if re.search(r'#{1,6}[A-Za-z]\w*:', re.sub(r'`[^`]*`', '', line)):
                deck.warn(n, f'"{line.strip()[:40]}" looks like a key but does not start the line; kept as text')
            if cur_field:
                node, f = cur_field
                node.fields[f] += '\n' + line
            elif line.strip():
                deck.warn(n, f'text outside any field ignored: "{line.strip()[:40]}"')
            continue

        level, name, value = len(km.group(1)), km.group(2), (km.group(3) or '')
        # a name the enclosing element declares as a field is a field, even if it is also an element type
        # (Table's "###Data:" is its rows, not a #Data source)
        owner = next((x for x in reversed(stack) if x.level < level), None)
        is_field = owner is not None and name in ELEMENTS[owner.type]['fields']
        if name in ELEMENTS and not is_field:
            node = Node(name, level, n, header=value)
            if level == 1:
                if name == 'Settings':
                    node = deck.settings
                elif name == 'Data':
                    deck.data.append(node)
                elif ELEMENTS[name].get('slide'):
                    if not saw_sep and deck.slides and name != 'Figure':
                        deck.warn(n, f'#{name} starts a new slide without a preceding ---')
                    deck.slides.append(node)
                else:
                    deck.warn(n, f'#{name} is at depth 1; only slides and Settings can be')
                    cur_field = None
                    continue
                stack, column = [node], None
                if name != 'Data' and name != 'Settings':
                    saw_sep = False
            else:
                while stack and stack[-1].level >= level:
                    stack.pop()
                if not stack:
                    deck.warn(n, f'#{"#" * (level - 1)}{name} has no parent element; skipped')
                    cur_field = None
                    continue
                parent = stack[-1]
                if name == 'Data':
                    deck.warn(n, 'Data sources are deck-wide: write #Data at depth 1, outside slides')
                    cur_field = None
                    continue
                if name in ('Column', 'Band'):
                    if parent.level != 1:
                        deck.warn(n, f'{name} must sit directly in a slide (##{name}); it is inside {parent.type}')
                    else:
                        column = node      # collects the ## elements that follow
                elif column is not None and parent is stack[0] and level == column.level:
                    parent = column        # a ## element after ##Column / ##Band belongs to it
                elif level != parent.level + 1:
                    deck.warn(n, f'{name} is at depth {level} but its parent {parent.type} is at {parent.level}')
                parent.children.append(node)
                stack.append(node)
            default = ELEMENTS[name].get('default_field')
            cur_field = (node, default) if default else None
            if default:
                node.fields.setdefault(default, '')
        else:
            # a field: belongs to the closest open element shallower than it
            while stack and stack[-1].level >= level:
                stack.pop()
            if not stack:
                deck.warn(n, f'field {name} outside any element; skipped')
                cur_field = None
                continue
            owner = stack[-1]
            known = ELEMENTS[owner.type]['fields'] | SETTING_FIELDS
            if name not in known:
                deck.warn(n, f'{owner.type} has no field {name} (known: {", ".join(sorted(known))})')
            # "; comment" right after the colon is how the source marks a multi-line field: drop it
            owner.fields[name] = '' if value.strip().startswith(';') else value
            cur_field = (owner, name)
            if value.strip().startswith('```'):
                fence = True
    return deck


def parse_span(s: str):
    s = s.strip()
    if not s:
        return None
    if ':' not in s:
        return (int(s), int(s))
    a, b = s.split(':', 1)
    return (int(a) if a else 1, int(b) if b else None)


def strip_comment(header: str) -> str:
    """Drop a "; comment" from an element line; a ; inside {...} (CSS in style=) is not a comment."""
    depth = 0
    for i, ch in enumerate(header):
        depth += (ch == '{') - (ch == '}')
        if ch == ';' and depth == 0:
            return header[:i].strip()
    return header.strip()


def resolve(deck: Deck):
    """Header grammar, setting fields, IDs (child IDs are prefixed by the parent's: Slide.Element), duplicates."""
    seen = {}

    def apply(node: Node, prefix: str, index: int, counts: dict):
        h = strip_comment(node.header) if node.type != 'Settings' else ''
        if node.type != 'Settings' and h != node.header.strip():
            deck.warn(node.line, '"; ..." after an element header is the old comment syntax; write "<!-- ... -->"')
        for m in SETTINGS_RE.finditer(h):
            for kv in m.group(1).split(','):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    node.opts[k.strip().lower()] = v.strip()
        h = SETTINGS_RE.sub('', h)
        rest = []
        for tok in h.split():
            if node.type == 'Column' and (NUMBER_RE.match(tok) or tok == 'auto'):
                node.width = tok if tok == 'auto' else float(tok)
            elif TUPLE_RE.match(tok) and node.step is None:
                parts = tok.split(',') + ['', '']
                node.step = int(parts[0]) if parts[0] else None
                node.col, node.row = parse_span(parts[1]), parse_span(parts[2])
            else:
                rest.append(tok)
        if node.type in ('LoadImage', 'Table') and rest and 'Src' not in node.fields and ('/' in rest[0] or '.' in rest[0]):
            node.fields['Src'] = rest.pop(0)
        explicit = ' '.join(rest) or None

        f = node.fields
        if 'Id' in f: explicit = f.pop('Id').strip()
        if 'Step' in f: node.step = int(f.pop('Step'))
        if 'Col' in f: node.col = parse_span(f.pop('Col'))
        if 'Row' in f: node.row = parse_span(f.pop('Row'))
        for w in (f.pop('Width', None), node.opts.pop('w', None)):
            if w is not None:
                node.width = 'auto' if w.strip() == 'auto' else float(w)
        if 'src' in node.opts: f.setdefault('Src', node.opts.pop('src'))
        if 'from' in node.opts: f.setdefault('From', node.opts.pop('from'))
        if 'kind' in node.opts: f.setdefault('Kind', node.opts.pop('kind'))
        for k in ('Anim', 'Class', 'Style'):
            if k in f: node.opts[k.lower()] = f.pop(k).strip()

        counts[node.type] = counts.get(node.type, 0) + 1
        local = explicit or (f'{node.type}{counts[node.type]}' if prefix else f's{index + 1}')
        if not re.fullmatch(r'[A-Za-z][\w-]*', local):
            fallback = f'{node.type}{counts[node.type]}' if prefix else f's{index + 1}'
            hint = ' (text goes on the lines after the element line)' if ELEMENTS[node.type].get('default_field') else ''
            deck.warn(node.line, f'ID "{local}" must be one word (letters, digits, _ or -); using {fallback}{hint}')
            local = fallback
        nid = prefix + local
        if nid in seen:
            deck.warn(node.line, f'duplicate ID {nid} (first at line {seen[nid]}); using {nid}_{node.line}')
            nid = f'{nid}_{node.line}'
        seen[nid] = node.line
        node.id = nid
        sub = {}
        for c in node.children:
            apply(c, nid + '.', 0, sub)

    for i, s in enumerate(deck.slides):
        apply(s, '', i, {})

    names = set()
    for d in deck.data:
        h = strip_comment(d.header)
        for m in SETTINGS_RE.finditer(h):
            for kv in m.group(1).split(','):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    d.opts[k.strip().lower()] = v.strip()
        rest = SETTINGS_RE.sub('', h).split()
        d.id = d.fields.pop('Id', '').strip() or (rest[0] if rest else '')
        for k in ('src', 'args', 'inputs', 'cwd', 'timeout', 'cache'):
            if k in d.opts:
                d.fields.setdefault(k.capitalize(), d.opts.pop(k))
        if not re.fullmatch(r'[A-Za-z_]\w*', d.id):
            deck.warn(d.line, f'#Data needs a one-word ID without dots (got "{d.id}"); source skipped')
            d.id = None
        elif d.id in names:
            deck.warn(d.line, f'duplicate #Data ID {d.id}; the later one wins')
        names.add(d.id)


# ---------------------------------------------------------------- data sources
RUNNERS = {'.R': ['Rscript'], '.r': ['Rscript'], '.jl': ['julia'], '.sh': ['bash']}


def file_sig(p: Path) -> str:
    st = p.stat()
    return f'{p}:{st.st_size}:{st.st_mtime_ns}'


def run_data(deck: Deck, base: Path, refresh: bool):
    """Run every #Data source and keep its JSON. A .json file is read; a script is run and must print JSON.
    Results are cached in <deck dir>/.deck_cache only when the source declares its Inputs (files it reads), keyed on
    the script, its arguments, the interpreter and those files: without Inputs the cache could serve stale numbers."""
    py = deck.settings.fields.get('Python', '').strip() or sys.executable
    cache_dir = base / '.deck_cache'
    for d in deck.data:
        if not d.id:
            continue
        f = d.fields
        src = f.get('Src', '').strip()
        if not src:
            deck.warn(d.line, f'#Data {d.id} has no Src'); continue
        p = Path(src).expanduser()
        p = p if p.is_absolute() else base / p
        if not p.is_file():
            deck.warn(d.line, f'#Data {d.id}: {src} not found'); continue
        deck.origin[d.id] = src
        if p.suffix == '.json':
            try:
                deck.values[d.id] = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                deck.warn(d.line, f'#Data {d.id}: {src} is not valid JSON ({e})')
            continue
        args = shlex.split(f.get('Args', ''))
        cwd = Path(f['Cwd'].strip()).expanduser() if f.get('Cwd', '').strip() else base
        cwd = cwd if cwd.is_absolute() else base / cwd
        if p.suffix == '.py':
            cmd = [str(Path(py).expanduser() if '/' in py else py)]
            if '/' in py and not Path(cmd[0]).is_absolute():
                cmd = [str(base / py)]
        else:
            cmd = RUNNERS.get(p.suffix, [])
        cmd = cmd + [str(p)] + args

        inputs = [x.strip() for x in f.get('Inputs', '').split(',') if x.strip()]
        key = None
        if inputs and f.get('Cache', 'yes').strip().lower() not in ('no', 'false', '0'):
            files = sorted({Path(g) for pat in inputs for g in glob.glob(str(cwd / pat), recursive=True)})
            missing = [pat for pat in inputs if not glob.glob(str(cwd / pat), recursive=True)]
            if missing:
                deck.warn(d.line, f'#Data {d.id}: Inputs match no file: {", ".join(missing)}')
            sig = '\n'.join([' '.join(cmd), str(cwd), p.read_text()] + [file_sig(x) for x in files if x.is_file()])
            key = cache_dir / f'{d.id}-{hashlib.sha256(sig.encode()).hexdigest()[:16]}.json'
            if key.is_file() and not refresh:
                deck.values[d.id] = json.loads(key.read_text())
                deck.origin[d.id] += ' (cached)'
                continue
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=float(f.get('Timeout', 600)))
        except (OSError, subprocess.TimeoutExpired) as e:
            deck.warn(d.line, f'#Data {d.id}: could not run {" ".join(cmd)}: {e}'); continue
        if r.returncode:
            tail = ' | '.join(r.stderr.strip().splitlines()[-3:])
            deck.warn(d.line, f'#Data {d.id}: {src} exited with {r.returncode}: {tail}'); continue
        try:
            # the JSON is the last non-empty line of stdout, so the script may print progress before it
            out = [l for l in r.stdout.splitlines() if l.strip()]
            val = json.loads(out[-1]) if out else None
            if val is None: raise ValueError('nothing printed')
        except (ValueError, IndexError) as e:
            deck.warn(d.line, f'#Data {d.id}: {src} must print JSON as its last line ({e})'); continue
        deck.values[d.id] = val
        if key:
            cache_dir.mkdir(exist_ok=True)
            for old in cache_dir.glob(f'{d.id}-*.json'):
                old.unlink()
            key.write_text(json.dumps(val))


# ---------------------------------------------------------------- {{source.key:fmt}} interpolation
# Strict on purpose: LaTeX also writes {{ }} (\frac{{a}}{b}, x^{{2}}). A placeholder needs a dot after a word and the
# word must be a declared #Data ID; anything else is left exactly as written.
PH_RE = re.compile(r'\{\{\s*([A-Za-z_]\w*)\.([\w.$]+)(?::([^{}]*?))?\s*\}\}')
CTX = {'deck': None, 'line': 0}
# MathJax's \text{} shows characters literally (unlike LaTeX): only braces and $ need escaping; a backslash would
# start a macro, so it becomes a look-alike character
TEX_ESC = {'{': r'\{', '}': r'\}', '$': r'\$', '\\': '∖'}


def lookup(src: str, path: str):
    v = CTX['deck'].values[src]
    for part in (path.split('.') if path else []):
        if isinstance(v, list) and part.isdigit() and int(part) < len(v):
            v = v[int(part)]
        elif isinstance(v, dict) and part in v:
            v = v[part]
        else:
            raise KeyError(part)
    return v


def fmt_value(v, spec: str, math: bool):
    """Format a value; returns (text, is_number). 'tex' as the type gives mantissa x 10^exp (e.g. .2tex)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        if spec:
            raise ValueError(f'format "{spec}" needs a number, got {type(v).__name__}')
        return (json.dumps(v) if isinstance(v, (list, dict)) else str(v)), False
    if spec.endswith('tex'):
        m, e = format(v, spec[:-3] + 'e').split('e')
        e = int(e)
        if e == 0:
            return m, True
        return (f'{m} \\times 10^{{{e}}}' if math else f'{m} × 10<sup>{str(e).replace("-", "−")}</sup>'), True
    if not spec:
        return (str(v) if isinstance(v, int) else format(v, 'g')), True
    return format(int(round(v)) if spec.endswith('d') else v, spec), True


def interpolate(text: str, math: bool) -> str:
    """Replace placeholders in one stretch of text. Math: TeX-safe, wrapped in \\class{val}. Text: HTML spans."""
    deck = CTX['deck']
    def rep(m):
        src, path, spec = m.group(1), m.group(2), (m.group(3) or '').strip()
        if deck is None or src not in {d.id for d in deck.data}:
            if not math and deck is not None:
                deck.warn(CTX['line'], f'{m.group(0)}: no #Data source "{src}"; left as written')
            return m.group(0) if math else html.escape(m.group(0), quote=False)
        name = f'{src}.{path}'
        if '$' in path:            # depends on a toggle: filled in the browser, starts at the default state
            if math:
                deck.warn(CTX['line'], f'{{{{{name}}}}}: values that follow a toggle cannot go inside math; left out')
                return r'\text{?}'
            deck.embed.add(name)
            first = concrete(name, deck, defaults=True)[0]
            try:
                txt, _ = fmt_value(lookup(*first.split('.', 1)), spec, False)
            except (KeyError, ValueError, IndexError):
                txt = '⚠'
            tip = html.escape(f'{name} ← {deck.origin.get(src, src)}')
            return f'<span class="val" data-bind="{html.escape(name)}" data-fmt="{html.escape(spec)}" title="{tip}">{txt}</span>'
        try:
            if src not in deck.values:
                raise KeyError(None)
            txt, num = fmt_value(lookup(src, path), spec, math)
        except KeyError as e:
            have = deck.values.get(src)
            keys = f' (has: {", ".join(map(str, have))})' if isinstance(have, dict) else ''
            deck.warn(CTX['line'], f'{{{{{name}}}}}: no key "{e.args[0]}" in {src}{keys}' if src in deck.values
                      else f'{{{{{name}}}}}: source {src} produced no data')
            return rf'{{\class{{val missing-val}}{{\text{{[{name}?]}}}}}}' if math else \
                f'<span class="val missing-val" title="missing: {html.escape(name)}">⚠ {html.escape(name)}</span>'
        except ValueError as e:
            deck.warn(CTX['line'], f'{{{{{name}}}}}: {e}')
            return rf'{{\class{{val missing-val}}{{\text{{[{name}?]}}}}}}' if math else \
                f'<span class="val missing-val" title="missing: {html.escape(name)}">⚠ {html.escape(name)}</span>'
        if math:
            body = txt if num else r'\text{' + ''.join(TEX_ESC.get(c, c) for c in txt) + '}'
            return rf'{{\class{{val}}{{{body}}}}}'
        tip = html.escape(f'{name} ← {deck.origin.get(src, src)}')
        return f'<span class="val" title="{tip}">{txt if num else html.escape(txt)}</span>'
    return PH_RE.sub(rep, text)


def concrete(path: str, deck: Deck, defaults: bool = False) -> list:
    """Expand $vars in a data path over the toggles' options (or only their defaults)."""
    out = [path]
    for v in re.findall(r'\$(\w+)', path):
        t = deck.toggles.get(v)
        if not t:
            deck.warn(CTX['line'], f'{path}: no ##Toggle defines ${v}')
            return []
        vals = [t['default']] if defaults else [o for o, _ in t['options']]
        out = [p.replace('$' + v, o) for p in out for o in vals]
    return out


def is_data_path(v: str, deck: Deck) -> bool:
    m = re.match(r'^([A-Za-z_]\w*)(?:\.[\w.$]+)?$', v or '')   # a whole source, or a key inside it
    return bool(m) and m.group(1) in {d.id for d in deck.data}


def collect_toggles(deck: Deck):
    for s in deck.slides:
        for n in s.walk():
            if n.type != 'Toggle':
                continue
            var = n.id.rsplit('.', 1)[-1]
            opts = []
            for item in n.fields.get('Options', '').split(','):
                if item.strip():
                    v, _, lab = item.partition('=')
                    opts.append((v.strip(), (lab or v).strip()))
            if not opts:
                deck.warn(n.line, f'{n.id}: Toggle needs Options (value = label, ...)'); continue
            default = n.fields.get('Default', '').strip() or opts[0][0]
            if var in deck.toggles and [o for o, _ in deck.toggles[var]['options']] != [o for o, _ in opts]:
                deck.warn(n.line, f'two toggles drive ${var} with different options')
            deck.toggles.setdefault(var, {'options': opts, 'default': default})


def local_ref(ref: str, slide: Node, deck: Deck, line: int) -> str:
    """#name -> the full ID of the element called name on the same slide."""
    name = ref[1:]
    hits = [n.id for n in slide.walk() if n.id == name or n.id.endswith('.' + name)]
    if not hits:
        deck.warn(line, f'{ref}: no element with that ID on this slide')
        return name
    return hits[0]


def interpolate_plain(text: str) -> str:
    """For attribute values (slide titles in the counter): placeholders become bare text."""
    return re.sub(r'<[^>]+>', '', interpolate(text, False))


# ---------------------------------------------------------------- inline markdown
def md(text: str) -> str:
    """Small markdown subset: paragraphs, - / 1. lists, **bold**, *italic*, `code`; $...$ and $$...$$ kept for MathJax."""
    text = text.strip('\n')
    if not text.strip():
        return ''
    keep = []
    def stash_code_block(m):
        keep.append(code_block(m.group(2), m.group(1)))
        return f'\n\x00{len(keep) - 1}\x00\n'
    text = re.sub(r'^[ \t]*```(\w*)[ \t]*\n(.*?)\n[ \t]*```[ \t]*$', stash_code_block, text, flags=re.S | re.M)
    def stash(m, math=True):
        keep.append(html.escape(interpolate(m.group(0), math), quote=False))
        return f'\x00{len(keep) - 1}\x00'
    def stash_html(m):          # a filled placeholder in plain text: final HTML, hidden from the markdown pass
        keep.append(interpolate(m.group(0), False))
        return f'\x00{len(keep) - 1}\x00'
    text = re.sub(r'\$\$.+?\$\$', stash, text, flags=re.S)
    def stash_code(m):          # inside `code` a value is bare text
        keep.append(html.escape(interpolate_plain(m.group(0)), quote=False))
        return f'\x00{len(keep) - 1}\x00'
    text = re.sub(r'`[^`\n]+`', stash_code, text)
    text = re.sub(r'(?<!\\)\$[^$\n]+?\$', stash, text)
    text = PH_RE.sub(stash_html, text)

    def inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<em>\1</em>', s)
        s = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', s)
        s = re.sub(r'\^([^\s^]+)\^', r'<sup>\1</sup>', s)        # pandoc-style R^eth^
        s = re.sub(r'~([^\s~]+)~', r'<sub>\1</sub>', s)            # pandoc-style R~ab~
        return s

    out, para, items, kind = [], [], [], None
    def flush():
        nonlocal para, items, kind
        if para:   # a line ending in a backslash is a hard line break (pandoc)
            out.append('<p>' + inline('\n'.join(para)).replace('\\\n', '<br>').replace('\n', ' ') + '</p>'); para = []
        if items: out.append(f'<{kind}>' + ''.join(f'<li>{inline(i)}</li>' for i in items) + f'</{kind}>'); items = []
        kind = None
    for ln in text.split('\n'):
        s = ln.strip()
        lm = re.match(r'^([-*]|\d+\.)\s+(.*)', s)
        if not s:
            flush()
        elif (dm := re.fullmatch(r'\x00(\d+)\x00', s)) and keep[int(dm.group(1))].startswith('<pre'):
            flush(); out.append(s)
        elif (dm := re.fullmatch(r'\x00(\d+)\x00', s)) and keep[int(dm.group(1))].startswith('$$'):
            flush(); out.append(f'<div class="eq">{s}</div>')
        elif lm:
            k = 'ol' if lm.group(1)[0].isdigit() else 'ul'
            if para or (kind and kind != k): flush()
            kind = k; items.append(lm.group(2))
        else:
            if items: flush()
            para.append(s)
    flush()
    res = '\n'.join(out)
    # \x00 is not escaped by html.escape, so placeholders survive inline(); `code` gets its tag here
    return re.sub(r'\x00(\d+)\x00', lambda m: (lambda t: f'<code>{t[1:-1]}</code>' if t.startswith('`') else t)(keep[int(m.group(1))]), res)


def code_block(code: str, lang: str) -> str:
    """A <pre> block. Language 'tt' gets the .tt syntax coloured: keys, tuples, {settings}, {{values}}, comments."""
    import textwrap
    code = textwrap.dedent(code)
    esc = lambda t: html.escape(t, quote=False)
    if lang != 'tt':
        body = esc(code)
        if lang in ('python', 'py', 'bash', 'sh'):
            body = re.sub(r'(?m)(^|\s)(#.*)$', r'\1<span class="c-com">\2</span>', body)
        elif lang in ('js', 'javascript', 'css'):
            body = re.sub(r'(?m)(//.*|/\*.*?\*/)$', r'<span class="c-com">\1</span>', body)
        return f'<pre class="code" data-lang="{lang}"><code>{body}</code></pre>'
    def rest(t):
        t = esc(t)
        t = re.sub(r'\{\{.*?\}\}', lambda m: f'<span class="c-val">{m.group(0)}</span>', t)
        t = re.sub(r'(?<![\w{])\{[^{}]*\}(?!\})', lambda m: f'<span class="c-set">{m.group(0)}</span>', t)
        t = re.sub(r'\$\w+', lambda m: f'<span class="c-var">{m.group(0)}</span>', t)
        return t
    def colour(ln):
        """One line of .tt without comments."""
        if ln.strip() == '---':
            return f'<span class="c-sep">{esc(ln)}</span>'
        if ln.lstrip().startswith(';'):
            return f'<span class="c-com">{esc(ln)}</span>'
        m = KEY_RE.match(ln)
        if not m:
            return rest(ln)
        hashes, name, val = m.group(1), m.group(2), m.group(3)
        cls = 'c-el' if name in ELEMENTS else 'c-field'
        head = f'<span class="c-hash">{hashes}</span><span class="{cls}">{name}</span>'
        if val is None:
            return head
        com = ''
        if name in ELEMENTS and (cut := strip_comment(val)) != val.strip():
            com = f' <span class="c-com">{esc(val[val.index(";", len(cut)):])}</span>'
            val = cut
        if name in ELEMENTS:
            val = ' '.join(f'<span class="c-tup">{t}</span>' if TUPLE_RE.match(t) else t for t in rest(val).split(' '))
        else:
            val = rest(val)
        return f'{head}<span class="c-colon">:</span> {val}{com}'

    out, incom = [], False
    for ln in code.split('\n'):
        # <!-- ... -->: the code before it is coloured as usual, the comment grey; it may span lines
        parts, code_part = [], None
        while ln:
            if incom:
                j = ln.find('-->')
                end = len(ln) if j < 0 else j + 3
                parts.append(f'<span class="c-com">{esc(ln[:end])}</span>')
                ln, incom = ln[end:], j < 0
            elif (i := ln.find('<!--')) >= 0:
                if code_part is None:
                    parts.append(colour(ln[:i])); code_part = True
                else:
                    parts.append(rest(ln[:i]))
                ln, incom = ln[i:], True
                j = ln.find('-->', 4)
                end = len(ln) if j < 0 else j + 3
                parts.append(f'<span class="c-com">{esc(ln[:end])}</span>')
                ln, incom = ln[end:], j < 0
            else:
                parts.append(colour(ln) if code_part is None else rest(ln))
                ln = ''
        out.append(''.join(parts))
    return f'<pre class="code" data-lang="tt"><code>' + '\n'.join(out) + '</code></pre>'


def plain(text: str) -> str:
    return md(text).removeprefix('<p>').removesuffix('</p>')


def lines(text: str) -> str:
    """Titles: each source line is one line on the slide."""
    return '<br>'.join(plain(l.rstrip().removesuffix('\\')) for l in text.strip().splitlines() if l.strip())


# ---------------------------------------------------------------- tables
def to_num(v: str):
    try:
        return float(v.replace(',', '').rstrip('%'))
    except (ValueError, AttributeError):
        return None


def split_pipe(line: str):
    cells = re.split(r'(?<!\\)\|', line.strip().strip('|'))
    return [c.strip().replace('\\|', '|') for c in cells]


def records(d, node: Node, deck: Deck):
    """JSON table shapes: {"columns": [...], "rows": [[...]], "align": [...]?} or a list of records."""
    if isinstance(d, dict) and 'columns' in d:
        header, rows = [str(c) for c in d['columns']], d.get('rows', [])
        aligns = (list(d.get('align', [])) + [None] * len(header))[:len(header)]
    elif isinstance(d, list) and all(isinstance(r, dict) for r in d):
        header = list(d[0]) if d else []
        rows, aligns = [[r.get(k, '') for k in header] for r in d], [None] * len(header)
    else:
        deck.warn(node.line, f'{node.id}: table data must be {{columns, rows}} or a list of records')
        return [], [], []
    return header, [['' if v is None else str(v) for v in r] for r in rows], aligns


def load_table(node: Node, base: Path, deck: Deck):
    """Return (header, rows, aligns) from the inline pipe table, a file (Src) or a #Data key (From)."""
    src = node.fields.get('Src', '').strip()
    if (frm := node.fields.get('From', '').strip()):
        source, _, path = frm.partition('.')
        try:
            if source not in deck.values:
                raise KeyError(f'#Data source "{source}" has no data')
            d = lookup(source, path) if path else deck.values[source]
        except KeyError as e:
            deck.warn(node.line, f'{node.id}: From={frm}: {e}')
            return [], [], []
        return records(d, node, deck)
    if src:
        p = Path(src).expanduser()
        p = p if p.is_absolute() else base / p
        if not p.is_file():
            deck.warn(node.line, f'{node.id}: table source not found: {src}')
            return [], [], []
        if p.suffix == '.json':
            return records(json.loads(p.read_text()), node, deck)
        else:
            rd = list(csv.reader(io.StringIO(p.read_text()), delimiter='\t' if p.suffix == '.tsv' else ','))
            header, rows = (rd[0], rd[1:]) if rd else ([], [])
        return header, rows, [None] * len(header)
    lines = [l for l in node.fields.get('Data', '').splitlines() if l.strip()]
    if not lines:
        deck.warn(node.line, f'{node.id}: table has neither Data nor Src')
        return [], [], []
    rows = [split_pipe(l) for l in lines]
    header, rows = rows[0], rows[1:]
    aligns = [None] * len(header)
    if rows and all(re.fullmatch(r':?-+:?', c) for c in rows[0]):
        aligns = [('center' if c.startswith(':') and c.endswith(':') else 'right' if c.endswith(':')
                   else 'left' if c.startswith(':') else None) for c in rows[0]]
        rows = rows[1:]
    for r in rows:
        if len(r) != len(header):
            deck.warn(node.line, f'{node.id}: a row has {len(r)} cells, the header has {len(header)}')
    rows = [(r + [''] * len(header))[:len(header)] for r in rows]
    return header, rows, (aligns + [None] * len(header))[:len(header)]


def table_ops(node: Node, header, rows, aligns, deck: Deck):
    """Filter -> Columns (pick, order, rename) -> Format. Columns are referred to by original name or new label."""
    f = node.fields
    idx = {h: i for i, h in enumerate(header)}

    def col(name):
        name = name.strip().strip('"')
        if name not in idx:
            deck.warn(node.line, f'{node.id}: no column "{name}" (have: {", ".join(header)})')
        return idx.get(name)

    if f.get('Filter', '').strip():
        for cond in re.split(r'\s+and\s+', f['Filter'].strip()):
            m = re.fullmatch(r'\s*(.+?)\s*(==|!=|<=|>=|<|>|=)\s*(.+?)\s*', cond)
            if not m:
                deck.warn(node.line, f'{node.id}: cannot read filter "{cond}" (use: column op value [and ...])')
                continue
            j, op, val = col(m.group(1)), m.group(2), m.group(3).strip('"\'')
            if j is None:
                continue
            NULLS = ('', 'null', 'none', 'na', 'nan')
            def keep(r):
                if val.lower() in NULLS and op in ('==', '=', '!='):   # "null" matches empty and NA-like cells
                    return (r[j].strip().lower() in NULLS) == (op != '!=')
                a, b = to_num(r[j]), to_num(val)
                x, y = (a, b) if a is not None and b is not None else (r[j], val)
                return {'==': x == y, '=': x == y, '!=': x != y, '<': x < y, '<=': x <= y, '>': x > y, '>=': x >= y}[op]
            rows = [r for r in rows if keep(r)]

    if f.get('Columns', '').strip():
        sel = []
        for item in next(csv.reader([f['Columns'].strip()], skipinitialspace=True)):
            m = re.fullmatch(r'(.+?)(?:\s+as\s+(.+))?', item.strip())
            j = col(m.group(1))
            if j is not None:
                sel.append((j, (m.group(2) or header[j]).strip().strip('"')))
        header = [lab for _, lab in sel]
        rows = [[r[j] for j, _ in sel] for r in rows]
        aligns = [aligns[j] for j, _ in sel]
        idx = {h: i for i, h in enumerate(header)}

    if f.get('Format', '').strip():
        for item in re.split(r'\s*,\s*(?=[^,=]+=)', f['Format'].strip()):
            name, _, spec = item.partition('=')
            j = col(name)
            if j is None:
                continue
            for r in rows:
                x = to_num(r[j])
                if x is None or r[j] == '':
                    continue
                try:
                    r[j] = format(int(round(x)) if spec.endswith('d') else x, spec.strip())
                except ValueError:
                    deck.warn(node.line, f'{node.id}: bad format "{spec}" for {name}')
                    break
    return header, rows, aligns, col


def render_table(node: Node, base: Path, deck: Deck) -> str:
    header, rows, aligns = load_table(node, base, deck)
    header, rows, aligns, col = table_ops(node, header, rows, aligns, deck)
    f = node.fields
    # numeric columns are right-aligned unless the pipe table says otherwise; the first column stays left
    for j in range(len(header)):
        if aligns[j] is None and j == 0:
            aligns[j] = 'left'
        elif aligns[j] is None:
            vals = [r[j] for r in rows if r[j].strip()]
            aligns[j] = 'right' if vals and all(to_num(v) is not None for v in vals) else 'left'

    hl_cells, hl_rows = set(), set()
    for kind, arg in re.findall(r'(min|max|row|col|cell)\(([^)]*)\)', f.get('Highlight', '')):
        if kind == 'col':
            j = col(arg)
            if j is not None:
                hl_cells |= {(i, j) for i in range(len(rows))}
        elif kind == 'cell':
            try:
                i, j = (int(x) for x in arg.split(','))
                hl_cells.add((i - 1, j - 1))
            except ValueError:
                deck.warn(node.line, f'{node.id}: cell() takes "row,col" (1-based numbers)')
        elif kind == 'row':
            name, _, val = arg.partition('=')
            j = col(name)
            if j is not None:
                hl_rows |= {i for i, r in enumerate(rows) if r[j].strip() == val.strip().strip('"')}
        else:
            j = col(arg)
            nums = [(to_num(r[j]), i) for i, r in enumerate(rows)] if j is not None else []
            nums = [(x, i) for x, i in nums if x is not None]
            if nums:
                best = (min if kind == 'min' else max)(x for x, _ in nums)
                hl_cells |= {(i, j) for x, i in nums if x == best}

    # Reveal: rows or columns come in one per click, starting right after the table's own step
    row_step, col_step = {}, {}
    if (rv := f.get('Reveal', '').strip()):
        m = re.fullmatch(r'(rows|cols)\s*(\S+)?', rv)
        if not m:
            deck.warn(node.line, f'{node.id}: Reveal must be "rows [a:b]" or "cols [a:b]"')
        else:
            n = len(rows) if m.group(1) == 'rows' else len(header)
            a, b = parse_span(m.group(2) or '1:') or (1, None)
            target = row_step if m.group(1) == 'rows' else col_step
            for k, i in enumerate(range(a - 1, min(b or n, n))):
                target[i] = (node.step or 1) + k + 1

    anim = node.opts.get('anim', DEFAULT_ANIM[0])
    def step_attr(s):
        return f' data-step="{s}" data-anim="{anim}"' if s else ''
    def cell(tag, v, i, j):
        cls = ' class="hl"' if (i, j) in hl_cells else ''
        al = f' style="text-align:{aligns[j]}"' if aligns[j] else ''
        return f'<{tag}{cls}{al}{step_attr(col_step.get(j))}>{plain(v)}</{tag}>'

    slug = lambda s: re.sub(r'\W+', '_', s).strip('_') or 'col'
    head = '<tr>' + ''.join(cell('th', h, -1, j).replace('<th', f'<th id="{node.id}.{slug(h)}"', 1)
                            for j, h in enumerate(header)) + '</tr>'
    body = ''.join(f'<tr id="{node.id}.r{i + 1}"' + (' class="hl"' if i in hl_rows else '') + step_attr(row_step.get(i)) + '>'
                   + ''.join(cell('td', v, i, j) for j, v in enumerate(r)) + '</tr>' for i, r in enumerate(rows))
    cap = f.get('Caption', '').strip()
    return (f'<figure {attrs(node, "tbl")}><table><thead>{head}</thead><tbody>{body}</tbody></table>'
            + (f'<figcaption>{plain(cap)}</figcaption>' if cap else '') + '</figure>')


# ---------------------------------------------------------------- rendering
def span_css(sp):
    if not sp:
        return None
    a, b = sp
    return f'{a} / {"-1" if b is None else b + 1}'


DEFAULT_ANIM = ['up']


def attrs(node: Node, cls: str, style: str = '') -> str:
    a = [f'id="{node.id}"', f'class="{(cls + " " + node.opts.get("class", "")).strip()}"']
    st = [style] if style else []
    if node.type not in ('Column', 'Band'):
        if (c := span_css(node.col)): st.append(f'grid-column:{c}')
        if (r := span_css(node.row)): st.append(f'grid-row:{r}')
    if node.opts.get('style'): st.append(node.opts['style'])
    if st: a.append(f'style="{";".join(st)}"')
    if node.step is not None:
        a.append(f'data-step="{node.step}"')
        a.append(f'data-anim="{node.opts.get("anim", DEFAULT_ANIM[0])}"')
    return ' '.join(a)


def load_image(src: str, base: Path, deck: Deck, line: int):
    p = Path(src).expanduser()
    p = p if p.is_absolute() else base / p
    if p.is_file():
        mime = mimetypes.guess_type(p.name)[0] or 'image/png'
        return f'data:{mime};base64,' + base64.b64encode(p.read_bytes()).decode()
    deck.warn(line, f'image not found: {src} (placeholder drawn)')
    return None


def render_element(node: Node, base: Path, deck: Deck) -> str:
    f = node.fields
    CTX['line'] = node.line
    if node.type == 'PopUpCard':
        title, tag, kick = (f.get(k, '').strip() for k in ('Title', 'Tag', 'Kicker'))
        tcls = 'tag warn' if node.opts.get('tag') == 'warn' else 'tag'
        return (f'<div {attrs(node, "card")}>'
                + (f'<span class="{tcls}">{plain(tag)}</span>' if tag else '')
                + (f'<span class="kicker">{plain(kick)}</span>' if kick else '')
                + (f'<h3>{lines(title)}</h3>' if title else '') + md(f.get('Text', '')) + '</div>')
    if node.type == 'Heading':
        return f'<h2 {attrs(node, "heading")}>{lines(f.get("Text", ""))}</h2>'
    if node.type == 'Html':
        return f'<div {attrs(node, "html")}>{f.get("Text", "").strip()}</div>'
    if node.type == 'Plot':
        kind, frm = f.get('Kind', '').strip(), f.get('From', '').strip()
        if not (HERE / 'plots' / f'{kind}.js').is_file():
            deck.warn(node.line, f'{node.id}: no plot kind "{kind}" (have: {", ".join(sorted(p.stem for p in (HERE / "plots").glob("*.js")))})')
        deck.kinds.add(kind)
        opts, paths = {}, []
        for k, v in node.opts.items():
            if k in ('class', 'style', 'anim'):
                continue
            if v.startswith('#'):
                v = local_ref(v, CTX['slide'], deck, node.line)
            elif is_data_path(v, deck):
                paths.append(k); deck.embed.add(v)
            opts[k] = v
        if frm:
            if is_data_path(frm, deck):
                deck.embed.add(frm)
            else:
                deck.warn(node.line, f'{node.id}: From={frm} is not a #Data path')
        spec = html.escape(json.dumps({'kind': kind, 'from': frm, 'opts': opts, 'paths': paths}), quote=True)
        cap = f.get('Caption', '').strip()
        return (f'<figure {attrs(node, "plot")} data-spec="{spec}">'
                + (f'<figcaption>{lines(cap)}</figcaption>' if cap else '') + '<div class="plot-host"></div></figure>')
    if node.type == 'BodyText':
        return f'<div {attrs(node, "bodytext")}>{md(f.get("Text", ""))}</div>'
    if node.type == 'LoadImage':
        src = f.get('Src', '').strip()
        uri = load_image(src, base, deck, node.line) if src else None
        alt = html.escape(f.get('Alt', f.get('Caption', '')).strip())
        img = f'<img src="{uri}" alt="{alt}">' if uri else f'<div class="missing">image not found<br>{html.escape(src)}</div>'
        cap = f.get('Caption', '').strip()
        return f'<figure {attrs(node, "photo")}>{img}' + (f'<figcaption>{plain(cap)}</figcaption>' if cap else '') + '</figure>'
    if node.type == 'Embed':
        src = f.get('Src', '').strip()
        h = node.opts.get('height', '640').removesuffix('px')
        zoom = float(node.opts.get('zoom', '1'))
        frame = f'width:calc(100% / {zoom});height:{float(h) / zoom:.0f}px;transform:scale({zoom});transform-origin:0 0'
        cap = f.get('Caption', '').strip()
        p = Path(src).expanduser()
        p = p if p.is_absolute() else base / p
        if not re.match(r'https?://', src) and p.is_file():   # a local web app: inline it, the deck stays one file
            doc = bundle_app(p, deck, node.line)
            inner = (f'<iframe title="{html.escape(node.id)}" style="{frame}" data-pending="1"></iframe>'
                     f'<script type="text/plain" class="embed-src">{base64.b64encode(doc.encode()).decode()}</script>')
        else:
            if not re.match(r'https?://', src):
                deck.warn(node.line, f'{node.id}: {src} not found')
            inner = f'<iframe title="{html.escape(node.id)}" src="{html.escape(src)}" style="{frame}" loading="lazy"></iframe>'
        return (f'<figure {attrs(node, "embed")}><div class="embed-box" style="height:{h}px">{inner}</div>'
                + (f'<figcaption>{lines(cap)}</figcaption>' if cap else '') + '</figure>')
    if node.type == 'Toggle':
        var = node.id.rsplit('.', 1)[-1]
        t = deck.toggles.get(var)
        if not t:
            return ''
        return (f'<div {attrs(node, "toggle seg")} data-var="{html.escape(var)}" role="group">'
                + ''.join(f'<button data-value="{html.escape(v)}" aria-pressed="{str(v == t["default"]).lower()}">{plain(lab)}</button>'
                          for v, lab in t['options']) + '</div>')
    if node.type == 'Table':
        return render_table(node, base, deck)
    deck.warn(node.line, f'{node.type} cannot be placed here')
    return ''


def grid_size(nodes):
    """Columns/rows of a grid = the largest index any tuple names (at least 1)."""
    def top(sp):
        return 0 if not sp else max(sp[0], sp[1] or 0)
    return max([1] + [top(n.col) for n in nodes]), max([1] + [top(n.row) for n in nodes])


def close_open_spans(nodes, nc, nr):
    """Resolve "a:" spans. Rows first: an open row span stops before the next row where another element starting in
    an overlapping column begins. Then columns: an open column span stops before the first cell already taken.
    So a full-width banner (2,1:,2) wins over cards above it that say "rows 1:"."""
    rng = lambda sp, size: range(sp[0], (size if sp[1] is None else sp[1]) + 1) if sp else range(0, 0)
    for n in nodes:
        if n.row and n.row[1] is None:
            mine = set(rng(n.col, nc)) or {0}
            starts = [m.row[0] for m in nodes if m is not n and m.row and m.row[0] > n.row[0]
                      and (not m.col or not n.col or mine & set(rng(m.col, nc)))]
            n.row = (n.row[0], min(starts) - 1 if starts else nr)
    for n in nodes:
        if n.col and n.col[1] is None:
            taken = {(c, r) for m in nodes if m is not n and m.col
                     for c in (rng(m.col, nc) if m.col[1] is not None else [m.col[0]]) for r in (rng(m.row, nr) or [0])}
            rows = list(rng(n.row, nr)) or [0]
            end = n.col[0]
            while end < nc and not any((end + 1, r) in taken for r in rows):
                end += 1
            n.col = (n.col[0], end)


IMPORT_RE = r'((?:from|import)\s*\(?\s*)([\'"])(\.{1,2}/[^\'"]+)\2'


def bundle_app(index: Path, deck: Deck, line: int) -> str:
    """One HTML document from a static web app: local stylesheets inlined, ES modules shipped as text and loaded
    in the browser from blob: URLs (relative imports rewritten), so no server is needed."""
    import posixpath
    root, doc = index.parent, index.read_text()
    def css(m):
        f = root / m.group(1)
        return f'<style>{f.read_text()}</style>' if f.is_file() else m.group(0)
    doc = re.sub(r'<link rel="stylesheet" href="(?!https?:)([^"]+)">', css, doc)
    entry = re.search(r'<script type="module" src="([^"]+)"></script>', doc)
    if not entry:
        return doc
    mods = {}
    def collect(rel):
        if rel in mods:
            return
        f = root / rel
        if not f.is_file():
            deck.warn(line, f'embed: module {rel} not found'); mods[rel] = ''; return
        mods[rel] = f.read_text()
        for m in re.finditer(IMPORT_RE, mods[rel]):
            collect(posixpath.normpath(posixpath.join(posixpath.dirname(rel), m.group(3))))
    collect(entry.group(1))
    loader = ('<script>(() => { const M = ' + json.dumps(mods).replace('</', '<\\/') + ', U = {};\n'
              'const norm = (b, r) => { const p = b.split("/").slice(0, -1); for (const s of r.split("/")) { if (s === "..") p.pop(); else if (s !== ".") p.push(s); } return p.join("/"); };\n'
              'const url = f => U[f] || (U[f] = URL.createObjectURL(new Blob([M[f].replace(/' + IMPORT_RE.replace('/', '\\/') + '/g, '
              '(m, a, q, r) => a + q + url(norm(f, r)) + q)], { type: "text/javascript" })));\n'
              f'import(url({json.dumps(entry.group(1))})); }})();</script>')
    return doc.replace(entry.group(0), loader)


def render_column(col: Node, kids: list, base: Path, deck: Deck) -> str:
    nc, nr = grid_size(kids)
    close_open_spans(kids, nc, nr)
    # {cols=auto} on the column/band (or the slide): columns as wide as their content, packed to the left
    auto = (col.opts.get('cols') or CTX['slide'].opts.get('cols')) == 'auto'
    track = 'auto' if auto else 'minmax(0,1fr)'
    grid = f'grid-template-columns:repeat({nc},{track});grid-template-rows:repeat({nr},auto)' + (';justify-content:start' if auto else '')
    flex = '0 0 auto' if col.width == 'auto' else col.width
    style = '' if col.type == 'Band' else f'flex:{flex}'
    return (f'<div {attrs(col, "region band" if col.type == "Band" else "region", style)}>'
            f'<div class="grid" style="{grid}">' + ''.join(render_element(c, base, deck) for c in kids) + '</div></div>')


def render_slide(s: Node, deck: Deck, base: Path) -> str:
    f = s.fields
    CTX['line'], CTX['slide'] = s.line, s
    if s.type == 'MainSlide':
        name = html.escape(interpolate_plain(f.get('Name', '').strip()) or 'Title')
        return (f'<section {attrs(s, "slide title-slide")} data-title="{name}">'
                + (f'<div class="eyebrow">{plain(f["Top"])}</div>' if f.get('Top', '').strip() else '')
                + f'<h1>{lines(f.get("Title", ""))}</h1>'
                + (f'<p class="sub">{lines(f["Subtitle"])}</p>' if f.get('Subtitle', '').strip() else '')
                + (f'<p class="who">{lines(f["Authors"])}</p>' if f.get('Authors', '').strip() else '')
                + ''.join(render_element(c, base, deck) for c in s.children) + '</section>')
    if s.type == 'Figure':
        title, cap = f.get('Title', '').strip(), f.get('Caption', '').strip()
        width = s.opts.get('width', '1200').removesuffix('px')
        name = interpolate_plain(f.get('Name', '').strip()) or s.id
        return (f'<section {attrs(s, "figure", f"width:{width}px")} data-title="{html.escape(name)}">'
                + (f'<h2>{lines(title)}</h2>' if title else '') + f'<div class="body">{body_html(s, deck, base)}</div>'
                + (f'<figcaption>{lines(cap)}</figcaption>' if cap else '') + '</section>')
    parts = body_html(s, deck, base)
    title = re.sub('<[^>]+>', '', plain(f.get('SlideTitle', '')))
    CTX['line'] = s.line
    name = interpolate_plain(f.get('Name', '').strip()) or interpolate_plain(f.get('SlideTop', '').strip()) or title
    sec = f.get('Section', '').strip()
    return (f'<section {attrs(s, "slide")} data-title="{html.escape(name)}"'
            + (f' data-section="{html.escape(sec)}"' if sec else '') + '>'
            + (f'<div class="eyebrow">{plain(f["SlideTop"])}</div>' if f.get('SlideTop', '').strip() else '')
            + (f'<h2>{lines(f["SlideTitle"])}</h2>' if f.get('SlideTitle', '').strip() else '')
            + f'<div class="body">{parts}</div></section>')


def body_html(s: Node, deck: Deck, base: Path) -> str:
    """The body is a stack of bands: a run of loose elements (before the first Column) is one full-width band,
    consecutive Columns sit side by side in one band, and each ##Band is a full-width band of its own."""
    parts, loose, cols = [], [], []
    def flush():
        nonlocal loose, cols
        if loose:
            parts.append(render_column(Node('Band', 2, s.line, id=f'{s.id}.top'), loose, base, deck))
        if cols:
            parts.append('<div class="cols">' + ''.join(render_column(c, c.children, base, deck) for c in cols) + '</div>')
        loose, cols = [], []
    for c in s.children:
        if c.type == 'Column':
            if loose: flush()
            cols.append(c)
        elif c.type == 'Band':
            flush()
            parts.append(render_column(c, c.children, base, deck))
        else:
            if cols: flush()
            loose.append(c)
    flush()
    return ''.join(parts)


def lint(deck: Deck):
    for s in deck.slides:
        for node in s.walk():
            for k, v in node.fields.items():
                if k in ('Data', 'From', 'Src', 'Kind', 'Options', 'Default'):
                    continue
                v = re.sub(r'(?ms)^[ \t]*```.*?^[ \t]*```', '', v)      # code blocks show $ literally
                v = re.sub(r'`[^`\n]+`', '', v)                          # so does inline code
                if PH_RE.sub('', v).replace('$$', '').count('$') % 2:
                    deck.warn(node.line, f'{node.id}.{k}: odd number of $ (unclosed math)')


def runtime(deck: Deck) -> str:
    """Embedded data (only the paths the page uses, expanded over toggle options), initial state, plot kinds."""
    data = {}
    for path in sorted(deck.embed):
        for p in concrete(path, deck):
            src, _, rest = p.partition('.')
            try:
                data[p] = lookup(src, rest) if src in deck.values else None
            except KeyError as e:
                deck.warn(0, f'{p}: no key "{e.args[0]}" in {src}')
    safe = lambda x: json.dumps(x, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    out = [f'<script type="application/json" id="deck-data">{safe(data)}</script>',
           f'<script type="application/json" id="deck-state">{safe({v: t["default"] for v, t in deck.toggles.items()})}</script>']
    libs, code = [], ['window.DeckPlots = {};']
    for k in sorted(deck.kinds):
        p = HERE / 'plots' / f'{k}.js'
        if p.is_file():
            js = p.read_text()
            libs += [u for u in re.findall(r'^// requires: (\S+)', js, re.M) if u not in libs]
            code.append(js)
    out += [f'<script src="{u}"></script>' for u in libs]
    out.append('<script>\n' + '\n'.join(code) + '\n</script>')
    return '\n'.join(out)


def sections(deck: Deck, S: dict) -> list:
    """Footer sections: Settings.Sections if given, otherwise the slides' sections in order of first use."""
    if S.get('Sections'):
        return [x.strip() for x in S['Sections'].split(',') if x.strip()]
    out = []
    for s in deck.slides:
        sec = s.fields.get('Section', '').strip()
        if sec and sec not in out:
            out.append(sec)
    return out


def embed_assets(css: str, tdir: Path) -> str:
    """url(asset:img/x.png) in a template's CSS becomes a data: URI of that file, so the deck stays one file."""
    def data(m):
        f = tdir / m.group(1)
        if not f.is_file():
            sys.exit(f'{tdir.name}/style.css: asset {m.group(1)} not found')
        mime = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        return f'url("data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()}")'
    return re.sub(r'url\(\s*["\']?asset:([^"\')]+)["\']?\s*\)', data, css)


def template_css(tdir: Path, seen=()) -> str:
    """A template's style.css; one that starts with /* extends: other */ comes after that template's CSS,
    so it only has to hold what it changes."""
    css = embed_assets((tdir / 'style.css').read_text(), tdir)
    m = re.match(r'\s*/\*\s*extends:\s*([\w-]+)', css)
    if not m:
        return css
    base = tdir.parent / m.group(1)
    if m.group(1) in seen or not (base / 'style.css').is_file():
        sys.exit(f'{tdir.name}/style.css extends "{m.group(1)}": no such template (or a loop)')
    return template_css(base, seen + (tdir.name,)) + f'\n/* ---- {tdir.name} */\n' + css


def build(src: Path, out: Path, refresh: bool = False) -> Deck:
    text, where, inc_warns = expand_includes(src.read_text(), src.resolve().parent)
    deck = parse(text)
    deck.where, deck.warnings[:0] = where, inc_warns
    resolve(deck)
    lint(deck)
    CTX['deck'] = deck
    run_data(deck, src.resolve().parent, refresh)
    collect_toggles(deck)
    S = {k: v.strip() for k, v in deck.settings.fields.items()}
    DEFAULT_ANIM[0] = S.get('Anim', 'up')
    style = S.get('Style', 'plex-grotesque')
    tdir = HERE / 'templates' / style
    if not tdir.is_dir():
        sys.exit(f'unknown Style "{style}": no {tdir}')
    palette = ''
    if S.get('Palette'):
        cols = [c.strip().lstrip('#') for c in S['Palette'].split('-') if c.strip()]
        palette = ' '.join(f'--p{i + 1}: #{c};' for i, c in enumerate(cols))
    mathjax = ''
    if S.get('MathJax', 'no').lower() in ('yes', 'true', '1'):
        mathjax = ("<script>window.MathJax = { tex: { inlineMath: [['$','$']], displayMath: [['$$','$$']] }, svg: { fontCache: 'global' } };</script>\n"
                   '<script src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-svg.min.js"></script>')
    main = next((s for s in deck.slides if s.type == 'MainSlide'), None)
    title = S.get('Title') or (re.sub('<[^>]+>', '', plain(main.fields.get('Title', ''))) if main else src.stem)
    page = (tdir / 'template.html').read_text()
    for k, v in {'TITLE': html.escape(title), 'CSS': template_css(tdir) + '\n/* deck: Settings.Css */\n' + S.get('Css', ''), 'PALETTE': palette,
                 'FOOT': plain(S.get('BMargin', '')), 'SECTIONS': html.escape(json.dumps(sections(deck, S))), 'MATHJAX': mathjax, 'ENGINE': (HERE / 'engine.js').read_text(),
                 'SLIDES': '\n'.join(render_slide(s, deck, src.resolve().parent) for s in deck.slides if s.type != 'Figure'),
                 'FIGURES': '\n'.join(render_slide(s, deck, src.resolve().parent) for s in deck.slides if s.type == 'Figure')}.items():
        page = page.replace('{{' + k + '}}', v)
    page = page.replace('{{RUNTIME}}', runtime(deck))
    out.write_text(page)
    return deck


# ---------------------------------------------------------------- export (headless Chrome)
CHROME_PATHS = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                '/Applications/Chromium.app/Contents/MacOS/Chromium']


def chrome_bin():
    import os, shutil
    for c in [os.environ.get('CHROME', '')] + CHROME_PATHS:
        if c and Path(c).is_file():
            return c
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome'):
        if (w := shutil.which(name)):
            return w
    sys.exit('export needs Chrome or Chromium: install it or set CHROME=/path/to/chrome')


def export(page: Path, ids: list, formats: list, scale: float, state: dict, step, bg: str, pad, outdir: Path, deck: Deck):
    from urllib.parse import urlencode
    chrome = chrome_bin()
    flags = ['--headless=new', '--hide-scrollbars', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
             '--virtual-time-budget=20000', '--run-all-compositor-stages-before-draw']
    if 'figures' in ids or 'slides' in ids:
        want = {'figures': 'Figure', 'slides': None}
        ids = [i for i in ids if i not in want] + [s.id for s in deck.slides
               if ('figures' in ids and s.type == 'Figure') or ('slides' in ids and s.type != 'Figure')]
    outdir.mkdir(parents=True, exist_ok=True)
    done = []
    for el in ids:
        q = {'only': el, 'bg': bg}
        if state: q['state'] = ','.join(f'{k}:{v}' for k, v in state.items())
        if step: q['step'] = step
        if pad is not None: q['pad'] = pad
        url = lambda **extra: page.resolve().as_uri() + '?' + urlencode({**q, **extra})
        run = lambda *args: subprocess.run([chrome, *flags, *args], capture_output=True, text=True, timeout=180)
        dom = run('--window-size=2400,2400', '--dump-dom', url()).stdout
        if (err := re.search(r'<meta id="tt-error" content="([^"]*)"', dom)):
            print(f'export {el}: {html.unescape(err.group(1))}', file=sys.stderr); continue
        size = re.search(r'<meta id="tt-size" content="(\d+) (\d+)"', dom)
        if not size:
            print(f'export {el}: the page did not report a size (did it load?)', file=sys.stderr); continue
        W, H = int(size.group(1)), int(size.group(2))
        for fmt in formats:
            path = outdir / f'{el}.{fmt}'
            if fmt == 'png':
                extra = ['--default-background-color=00000000'] if bg == 'transparent' else []
                run(f'--window-size={W},{H}', f'--force-device-scale-factor={scale}', *extra, f'--screenshot={path}', url())
            elif fmt == 'pdf':
                run('--no-pdf-header-footer', f'--print-to-pdf={path}', url(print=1))
            elif fmt == 'svg':
                d = run('--window-size=2400,2400', '--dump-dom', url(svg=1)).stdout
                if (err := re.search(r'<meta id="tt-error" content="([^"]*)"', d)):
                    print(f'export {el}.svg: {html.unescape(err.group(1))}', file=sys.stderr); continue
                if (note := re.search(r'<meta id="tt-note" content="([^"]*)"', d)):
                    print(f'export {el}.svg: note: {html.unescape(note.group(1))}', file=sys.stderr)
                m = re.search(r'<textarea id="tt-svg"[^>]*>(.*?)</textarea>', d, re.S)
                if not m:
                    print(f'export {el}.svg: no SVG came back', file=sys.stderr); continue
                path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + html.unescape(m.group(1)))
            else:
                print(f'export: unknown format {fmt} (png, pdf, svg)', file=sys.stderr); continue
            if path.is_file():
                done.append(f'{path}  ({W}×{H}' + (f' @{scale:g}x' if fmt == 'png' else '') + ')')
            else:
                print(f'export {el}.{fmt}: Chrome wrote nothing', file=sys.stderr)
    return done


def tree(deck: Deck):
    sp = lambda s: '-' if not s else f'{s[0]}' if s[0] == s[1] else f'{s[0]}:{"" if s[1] is None else s[1]}'
    def show(n, depth):
        extra = f'  width={n.width}' if n.type == 'Column' else ''
        if n.step is not None or n.col or n.row:
            extra += f'  step={n.step} col={sp(n.col)} row={sp(n.row)}'
        print(f'{"  " * depth}{n.type:10} {n.id}{extra}')
        for c in n.children:
            show(c, depth + 1)
    for d in deck.data:
        if d.id:
            got = deck.values.get(d.id)
            keys = ', '.join(list(got)[:8]) + (' …' if len(got) > 8 else '') if isinstance(got, dict) else type(got).__name__
            print(f'{"Data":10} {d.id}  ← {deck.origin.get(d.id, d.fields.get("Src", "?"))}  [{keys if got is not None else "no data"}]')
    for s in deck.slides:
        show(s, 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', type=Path)
    ap.add_argument('-o', '--out', type=Path)
    ap.add_argument('--tree', action='store_true', help='print the element tree with resolved IDs and positions')
    ap.add_argument('--refresh', action='store_true', help='re-run every #Data script, ignoring the cache')
    ex = ap.add_argument_group('export (needs Chrome or Chromium)')
    ex.add_argument('--export', action='append', default=[], metavar='ID',
                    help='element to export: a slide, a #Figure, any element ID; or "figures" / "slides"')
    ex.add_argument('--format', default='png', help='comma list of png, pdf (vector), svg (one SVG plot only)')
    ex.add_argument('--scale', type=float, default=2, help='PNG pixel density (2 = retina)')
    ex.add_argument('--state', action='append', default=[], metavar='VAR=VALUE', help='toggle values, e.g. wave=wave1')
    ex.add_argument('--step', type=int, help='show the element as at this step (default: everything)')
    ex.add_argument('--bg', default='paper', help='paper, white, transparent (PNG) or any CSS colour')
    ex.add_argument('--pad', type=int, help='padding around an element, px (default 24; 0 for a #Figure)')
    ex.add_argument('--outdir', type=Path, help='where exports go (default: next to the HTML)')
    a = ap.parse_args()
    out = a.out or a.src.with_name(a.src.name.split('.')[0] + '.html')
    deck = build(a.src, out, a.refresh)
    if a.tree:
        tree(deck)
    for w in deck.warnings:
        print('warning:', w, file=sys.stderr)
    nfig = sum(s.type == 'Figure' for s in deck.slides)
    what = f'{len(deck.slides) - nfig} slides' + (f', {nfig} figures' if nfig else '')
    print(f'wrote {out}  ({what}, {len(deck.warnings)} warnings)')
    if a.export:
        state = dict(kv.split('=', 1) for kv in a.state)
        for line in export(out, a.export, [f.strip() for f in a.format.split(',') if f.strip()], a.scale, state,
                           a.step, a.bg, a.pad, a.outdir or out.parent, deck):
            print('exported', line)
