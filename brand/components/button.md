# Button

A 40px action in three weights: `dg-btn--primary` (yellow), `dg-btn--secondary` (outlined) and `dg-btn--ghost`.

- Markup: `<a class="dg-btn dg-btn--primary">` or `<button class="dg-btn …">`; add `dg-btn--sm` for 32px. The consumer provides the label (sentence case, a verb: "Get started", "Copy").
- One primary per view — it is the only yellow on most screens. Everything else is secondary or ghost.
- A command inside a button goes in `<code>` so it sets in mono.
- Focus: 2px `focus` ring, 2px offset. Disabled: the `disabled` attribute.
- Don't: put icons-only in a primary, use orange, add shadows.
