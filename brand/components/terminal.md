# Terminal

A mono block showing a real `duckgrep` command or SQL query and its table output — the site's hero element and the README's quickstart.

- Markup: `<pre class="dg-term">`, optional `<div class="dg-term__bar">` header (a `label`-style caption). The consumer provides the lines as spans:
  - `.prompt` for `$`; the command itself in `ink`.
  - duckgrep prints plain tables: wrap the header row and dashed rule in `.ctx`; in rows use `.path` for path columns and `.ln` for line numbers; other columns stay `ink`.
  - `.dg-match` around the symbol the query asks about — the yellow highlight is the brand in use.
  - SQL: `.kw`, `.str`, `.num`, `.fn`, `.comment`; errors `.err` plus the word "error".
- Paste real output from `duckgrep` (keep its column alignment); break a long command after a keyword and indent four spaces.
- Prefer the questions the brand leads with: `callers`, `callees`, `def`, and the recursive "within 3 hops" query from the README.
- Don't: fake window chrome (traffic-light dots), invented output, gradients or drop shadows.
