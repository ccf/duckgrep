---
name: duckgrep.dev
description: The duckgrep site, a recorded race between Claude Code alone and Claude Code with duckgrep, set in the brand's flat DuckDB vocabulary.
colors:
  duck-yellow: "#fff100"
  duck-yellow-hover: "#eddd0c"
  on-yellow: "#0d0d0d"
  match-bg: "#fff100"
  match-ink: "#0d0d0d"
  surface: "#fcfcfc"
  surface-raised: "#ffffff"
  surface-code: "#f7f7f7"
  ink: "#0d0d0d"
  ink-muted: "#666666"
  border: "#e6e6e6"
  border-strong: "#808080"
  link: "#665d00"
  focus: "#357197"
  code-keyword: "#005fff"
  code-string: "#875f00"
  code-number: "#b8009a"
  code-function: "#007a00"
  code-comment: "#626262"
typography:
  display:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(2.5rem, 1.1rem + 5vw, 4.5rem)"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(28px, 1.1rem + 1.6vw, 40px)"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.015em"
  claim:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(22px, 1.1rem + 1vw, 30px)"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  question:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(18px, 1rem + 0.4vw, 22px)"
    fontWeight: 600
    lineHeight: 1.4
  title:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: "28px"
  body-lg:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: "28px"
  body:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: "24px"
  small:
    fontFamily: "Hanken Grotesk, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "20px"
  numeral:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "clamp(20px, 1rem + 0.9vw, 28px)"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  command:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: "22px"
  terminal:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: "20px"
  output:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "18px"
  label:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: "16px"
    letterSpacing: "0.08em"
rounded:
  radius-sm: "4px"
  radius-md: "8px"
  radius-pill: "999px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "16px"
  space-4: "24px"
  space-5: "32px"
  space-6: "48px"
  space-7: "96px"
components:
  button-primary:
    backgroundColor: "{colors.duck-yellow}"
    textColor: "{colors.on-yellow}"
    rounded: "{rounded.radius-md}"
    padding: "0 12px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.duck-yellow-hover}"
    textColor: "{colors.on-yellow}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.radius-md}"
    padding: "0 12px"
    height: "32px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
  command:
    backgroundColor: "{colors.surface-code}"
    textColor: "{colors.ink}"
    typography: "{typography.command}"
    rounded: "{rounded.radius-md}"
    padding: "4px 4px 4px 16px"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.radius-pill}"
    padding: "0 14px"
    height: "32px"
  tab-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.radius-pill}"
  lane:
    backgroundColor: "{colors.surface-code}"
    textColor: "{colors.ink}"
    typography: "{typography.terminal}"
    rounded: "{rounded.radius-md}"
    padding: "0 16px"
  meter:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.numeral}"
    padding: "12px 16px 14px"
  results-cell-duckgrep:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.command}"
    padding: "11px 16px"
  verdict:
    backgroundColor: "transparent"
    textColor: "{colors.code-function}"
    rounded: "{rounded.radius-pill}"
    padding: "0 8px"
---

# Design System: duckgrep.dev

This file describes the site as shipped (`site/index.html`, `site/site.css`, `site/site.js`). The site is built on the binding brand system in `brand/`, and that system wins every conflict. Token values come from `brand/tokens.json`; `brand/tokens.css` (generated) and `brand/components.css` are loaded before `site/site.css`. The frontmatter mirrors the light-theme brand values the site uses, so tools can read them. To change a colour, space or radius, edit `brand/tokens.json` and regenerate; never fork a value here. Name, messaging, voice, mark and iconography rules live only in `brand/README.md`.

## Overview

**Creative North Star: "The Instrument Panel"**

The site is a calibrated readout, not a brochure. One real question is replayed from recorded transcripts in two terminal lanes side by side, with counters that tick up to the recorded totals. Everything around the race (the results table, the reasons, the install steps) is set in the same vocabulary: hairlines, mono numerals and plain tables, with the evidence placed next to the claim.

The world is DuckDB's, taken from the brand: a near-white `surface` ground, near-black `ink`, hairline `border` edges, no shadows and no gradients that read as gradients. Yellow is spent only on the match highlight and on the one primary button in view. Hanken Grotesk carries every word a person reads; JetBrains Mono carries everything a user could type or an agent could read, and every number that is compared.

Density changes on purpose. Prose sections are calm, on one 1120px column with lines capped between 46ch and 75ch. The race is the one dense, wide moment: its lanes widen to 1360px, the type steps down to 13px/12px mono, and output stays verbatim.

**Key Characteristics:**
- Flat surfaces, separated by 1px hairlines and tonal steps (`surface` → `surface-code` → `surface-raised`).
- Two families with a hard split: sans for reading, mono for typed, read-by-agent and compared values.
- Yellow as a highlighter, not as decoration.
- Numbers are tabular, signed and named; differences are never shown by colour alone.
- Motion only replays something that happened; it settles on the real values and never repeats on its own.
- Light and dark from the same tokens; dark follows the OS.

## Colors

The brand palette, used flat: a neutral ground with one loud yellow, plus the DuckDB-CLI syntax hues inside terminals only. Dark-theme values come from the same tokens (see `brand/tokens.css`).

### Primary
- **Duck Yellow** (`duck-yellow`): the fill of the one primary button per view (the hero's Copy) and, through `match-bg`, of every match highlight. Never a text colour on light grounds.
- **Highlighter Yellow** (`match-bg` with `match-ink`): wraps the tagline's "answers questions.", the asked-about symbol in every terminal row and query, and text selection. In dark mode it turns to a deep olive band with pale yellow ink.

### Neutral
- **Paper** (`surface`): the page ground.
- **Code Paper** (`surface-code`): terminals, race lanes, command boxes and inline code chips.
- **Raised White** (`surface-raised`): the meter strip in each lane, the selected-column cells of the results table, and the tablist track.
- **Ink** (`ink`): text, the selected tab's fill, the 2px rule over each reason and the rule under the table header.
- **Muted Ink** (`ink-muted`): secondary text, ledes in the results section, terminal bar labels on the "alone" lane, deltas and table headers outside the duckgrep column.
- **Hairline** (`border`): every container edge, row rule and the dashed rule between race steps.
- **Strong Hairline** (`border-strong`): the duckgrep lane's edge, group rules in the results table, the install step numerals' circles, and a lane's idle status dot.
- **Olive Link** (`link`): links; underlined on hover only.
- **Focus Blue** (`focus`): the 2px focus ring, chosen so it never reads as a match.

### Tertiary (terminal syntax only)
- **Keyword Blue, String Amber, Number Magenta, Function Green, Comment Grey** (`code-*`): SQL and output highlighting inside terminals; paths are `code-keyword`, line numbers `code-function`, the `$` prompt `code-comment`. Function Green also marks a finished lane's status dot and the "correct" verdict pill.

### Named Rules
**The Highlighter Rule.** Yellow marks what was asked for or the one action to take, never anything else. One primary (yellow) button per view; every other Copy and Replay button is secondary.

**The No Traffic Lights Rule.** Deltas are not red or green. They are signed with a true minus (−), set in mono, muted outside the duckgrep column and ink inside it; a dagger (†) marks a change that is not significant.

## Typography

**Display Font:** Hanken Grotesk (with Helvetica Neue, Arial)
**Body Font:** Hanken Grotesk
**Label/Mono Font:** JetBrains Mono (with ui-monospace, Menlo, Consolas)

**Character:** A neutral grotesk that stays out of the way, paired with a mono that does the proving. Every number that is compared is mono and tabular.

### Hierarchy
- **Display** (700, fluid 40–72px, 1.02, −0.03em): the tagline only, once per page, on two lines. The site sets it larger and tighter than the brand's `type-display` (64px/60px, −0.02em).
- **Headline** (700, fluid 28–40px, 1.1, −0.015em): section titles, sentence case, with no label above them.
- **Claim** (600, fluid 22–30px, 1.25, max 30ch): the one headline number sentence under "What it saves".
- **Question** (600, fluid 18–22px, 1.4): the race's question line; its "Both agents were asked" lead-in drops to 400 in `ink-muted`. The race's result line uses the same scale at 400 with the numbers in 700.
- **Title** (700, 20–21px, 28–32px): reason heads and install step heads.
- **Body-lg** (400, 17–18px, 26–28px): section ledes (max 68ch) and the hero subhead (fluid 17–20px, 1.5, max 60ch, `ink-muted`).
- **Body** (400, 16px/24px): running text, max 52–72ch depending on the block.
- **Small** (400, 13–14px, 20px, `ink-muted`): race notes, the fine-print method line, footer text.
- **Numeral** (mono 700, fluid 20–28px, 1.15, −0.02em, tabular): the meter totals.
- **Command** (mono 400, 15px/22px): copyable commands and the results table's cells.
- **Terminal / Output** (mono 400, 13px/20px and 12px/18px): race steps and how-it-works terminals, and the verbatim tool output inside them.
- **Label** (mono 700, 11–12px, 0.08em, uppercase): terminal bar captions and the tool name on each race step. A code name inside a bar keeps its own case.

### Named Rules
**The Typed Things Are Mono Rule.** Commands, paths, SQL, tool names, output and compared numbers are JetBrains Mono with ligatures off (`->` stays two characters). Words a person reads are Hanken Grotesk.

**The Verbatim Output Rule.** Terminal output is never rewrapped or reflowed; a line wider than its lane scrolls sideways, and a "scroll sideways for the rest →" note appears under it only while it overflows.

## Layout

- **The one column:** header, every section and footer sit in `min(1120px, 100% − 2 × clamp(16px, 4vw, 32px))`, centred.
- **The wide race:** the race section alone widens to 1360px on the same gutter, because its two terminal lanes need the room. Its words (tabs, question, result, note) stay on the 1120px column inside it; only the lanes take the extra width.
- **Rhythm:** the brand's 8px grid (`space-1`…`space-7`). Sections open with a fluid gap of 64–96px (`space-7` at the top end); the header is 72px tall; cards and lanes sit `space-3` apart, reasons `space-5`/`space-6`.
- **Grids:** two equal lanes; a 3-column meter strip; two-column reasons and result notes; how-it-works at 4fr : 7fr, with the text sticky beside the terminals.
- **Line length:** prose is capped between 46ch and 75ch; the results claim at 30ch.
- **At 760px and below:** the lanes stack ("alone" first), every two-column grid goes to one, the sticky how-it-works text is released, and the nav keeps only Install and the external links.
- **At 560px and below:** the results table drops its sideways scroll and fixes its layout, each delta moves under its value, commands outside the hero wrap with Copy below, and the meter numerals step to 20px with their deltas on their own line.
- **At 420px and below:** the hero command takes the full width.

## Elevation & Depth

Flat. There are no drop shadows anywhere; depth is tonal (`surface` → `surface-code` → `surface-raised`) and edged with 1px hairlines. The only `box-shadow` in the build is an inset 1px hairline on the duckgrep column of the results table, used as a border that collapsed table borders cannot draw. The tagline highlight is a single-colour background band sized to 0.9em, a highlighter stroke rather than a gradient.

### Named Rules
**The Hairline Rule.** Containers are separated by a 1px `border` hairline and a tonal step, never by shadow. Emphasis is a stronger hairline (`border-strong`, or a 2px `ink` rule over a reason), not lift.

## Shapes

The brand's mark-derived language: discs and pills, with gentle corners elsewhere. `radius-md` (8px) on buttons, commands, lanes and terminals; `radius-sm` (4px) on inline code, highlights and focus rings; `radius-pill` on the question tabs and verdict pills. Discs appear as a lane's 7px status dot and the 32px circled numerals of the install steps. The FAQ's open/close sign is a plus drawn from two 2px bars that turns 45° into a cross; no chevrons.

## Components

### Buttons
Brand buttons from `brand/components.css`, used at the small size (32px tall, 12px padding, 600 14px sans) for Copy and Replay.
- **Shape:** gently rounded (`radius-md`).
- **Primary:** `duck-yellow` on `on-yellow`; only the hero's Copy.
- **Secondary:** transparent with a `border-strong` edge; Replay and every other Copy. On hover the edge goes to `ink` and the fill to `surface-raised`.
- **Feedback:** Copy reads "Copied" (or "Selected" when the clipboard is refused and the text is selected instead) for 1.6s, then reverts. The minimum width is 72px so the label change doesn't shift the layout.
- **Focus:** 2px `focus` ring at 2px offset, on every interactive element.

### Command
A one-line, copyable shell command: a `surface-code` box with a hairline edge and `radius-md`, a muted unselectable `$` prompt, mono 15px text that scrolls without a scrollbar, and the Copy button docked inside its right edge. The block variant holds a paragraph (the agent instruction) at 14px and wraps.

### Question tabs
A pill track on `surface-raised` with a hairline edge holds pill tabs (32px, 600 14px sans, `ink-muted`). The selected tab is filled `ink` with `surface` text. The arrow keys move between tabs; selecting one replays its race. The tabs and Replay are hidden until the script runs, and without it both questions are on the page.

### Race lane (signature)
A terminal set as a figure: a `surface-code` container with a hairline edge and `radius-md`. Top to bottom:
- **Bar:** the brand terminal bar (mono uppercase label) naming the setup, with a status on the right: a 7px dot and a sentence-case word ("ready", "working", "done in 7.9 s"). The dot is `border-strong` when idle, `ink` while running, `code-function` when done.
- **Meter:** a `surface-raised` strip of three cells (tool calls, tokens, cost) split by hairlines: a muted 13px sans term over a mono tabular numeral. A muted mono delta (−1, −15%) sits beside each duckgrep total.
- **Steps:** an ordered list of calls separated by dashed hairlines. Each has the uppercase mono tool name, the call in mono, verbatim output at 12px, the agent's notes in `code-comment` and a final answer line with a "correct" verdict pill (`code-function` text and edge).

The duckgrep lane is distinguished only by a `border-strong` edge and `ink` (not muted) bar and tool labels, never by colour.

### Meters and deltas
The comparison grammar of the whole page: the value first, then the signed change in a smaller muted mono. In the duckgrep column the value is 700 and the change `ink`. A change that is not significant carries a dagger.

### Results table
A plain, collapsed table with tabular numerals. The header row is 600 14px sans in `ink-muted` over an `ink` rule; row heads are sans 400; cells are mono 15px, right-aligned, on hairline rows. Language groups open with a 700 18px row head over a `border-strong` rule, with the question count muted beside it. The duckgrep column is the only emphasised one: header in `ink`, cells in 700 on `surface-raised` between inset hairlines. Beside it, the result notes sit in two columns, and a 13px `ink-muted` fine-print paragraph gives the method.

### How-it-works terminals
Brand terminals inside a hairline figure, with a caption bar naming the query and the repo version. SQL uses the `code-*` hues, result headers and dashed rules are `ink-muted`, and the asked-about name is highlighted.

### Install steps
A numbered list: a 32px disc with a `border-strong` edge holds a mono numeral, beside a title head, a muted note and a command.

### Navigation
Header: the lockup (32px, light and dark versions through `<picture>`) and a row of 600 15px sans links in `ink-muted`, turning `ink` on hover. Footer: the 24px lockup with a muted credit line, and a wrapping row of 600 links, above a hairline.

### FAQ disclosure
Native `details` between hairlines (max 880px): a 600 18px summary with the drawn plus at the right, underlined on hover; answers capped at 72ch.

### Motion and states
- **Replay:** the race plays once, the first time its lanes come into view, and again on Replay or a tab change. Each step lands at its recorded millisecond offset: opacity and a top-down clip over 0.45–0.55s on `cubic-bezier(0.16, 1, 0.3, 1)`. Pending steps keep their space, so nothing below moves.
- **Counters:** each meter value tweens over 500ms with an exponential ease-out (1 − 2^(−10t)) and stops on the recorded total. The slower lane keeps working after the faster one is done.
- **Results:** the deltas and the result sentence appear 400ms after both lanes finish (opacity, and a 6px rise for the sentence), because a difference only means something then.
- **Repetition:** the only repeating motion is a running lane's status dot, which pulses (1.1s) until the lane finishes.
- **Reduced motion:** no replay. Every panel shows its final state with the real totals; smooth scrolling is off.
- **No script:** the page is complete; every step and total is in the HTML.

## Do's and Don'ts

### Do:
- **Do** take every colour, space, radius and font from `brand/tokens.css`; change values in `brand/tokens.json`, never in site CSS.
- **Do** keep one primary (yellow) button per view and make every other action secondary.
- **Do** set compared numbers in tabular mono, with the signed change beside or under the value and a dagger on non-significant changes.
- **Do** keep terminal output verbatim; let it scroll sideways and say so only when it overflows.
- **Do** keep text on the 1120px column; only the race lanes take the 1360px frame.
- **Do** separate surfaces with 1px hairlines and tonal steps; emphasise with `border-strong` or a 2px `ink` rule.
- **Do** make every animation a replay of something recorded that settles on the real value, with a complete final state for reduced motion and for no script.

### Don't:
- **Don't** use drop shadows or visible gradients; depth is tonal.
- **Don't** colour deltas red or green, or let colour alone carry a difference.
- **Don't** use yellow for anything but the match highlight and the one primary action.
- **Don't** put a label or eyebrow above a section title; sections open with their sentence-case headline.
- **Don't** use chevrons or blobs; open/close and status are drawn from bars and discs.
- **Don't** animate anything that didn't happen in the recorded run, or loop motion that has no running process behind it.
