---
id: "014"
title: "Login Page UX Redesign — Template separation, brand palette, dark mode"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-11
updated: 2026-04-11
---

# 014 — Login Page UX Redesign — Template separation, brand palette, dark mode

## Problem Statement

The web chat login page (introduced in spec 013) has three maintainability and
UX problems:

1. **HTML/CSS embedded in Python** — `_render_login_page()` in `router.py` is a
   600-byte f-string that mixes Python logic with markup and inline styles.
   This violates separation of concerns: designers cannot edit the page without
   touching Python, and Python developers cannot refactor routing logic without
   navigating HTML.

2. **Off-brand colour palette** — The current page uses generic blue-grey hex
   colours (`#0f1117`, `#4299e1`, …) unrelated to the Prometheus visual identity.
   The approved brand palette uses Electric Blue / Serene Blue as primary colours
   with a defined accent set and grey scale.

3. **No dark / light mode toggle** — The page ships dark-only.  Users on bright
   environments have no way to switch to a light theme, and the brand palette
   includes explicit light-mode counterparts.

## Goals

- [ ] Move all HTML markup and CSS out of Python into standalone template and
      stylesheet files under `gateway/src/prometheus_gateway/ui/templates/` and
      `gateway/src/prometheus_gateway/ui/static/`.
- [ ] Apply the approved Prometheus brand colour palette (Electric Blue, Serene Blue,
      Midnight, accent set, grey scale — see Design Tokens below).
- [ ] Add a dark / light mode toggle button that persists the preference in
      `localStorage` and applies immediately without a page reload.
- [ ] Keep all existing functional behaviour and security controls from spec 013 unchanged.
- [ ] No external CSS framework or JavaScript library dependencies (self-contained).

## Non-Goals

- No server-side session or cookie changes to store the theme preference.
- No changes to authentication, JWT validation, rate limiting, or proxy logic.
- No redesign of the post-login chat proxy page (`/ui/<model_id>/…`).
- No internationalisation (i18n).

## Proposed Solution

### File separation

Introduce Jinja2 templating (already a FastAPI transitive dependency via Starlette):

```
gateway/src/prometheus_gateway/ui/
├── router.py          # Python only — no HTML strings
├── templates/
│   └── login.html     # Jinja2 template — markup + inline SVG icons
└── static/
    └── login.css      # External stylesheet — brand tokens + light/dark theme
```

`_render_login_page()` is replaced by a `TemplateResponse` (or equivalent
`templates.TemplateResponse`) call that passes only the Python-computed variables:
`models`, `next_path`, `error`, `no_models_warning`.

### Brand colour palette reference

The following is the **complete and exclusive** colour palette.  No hex value
outside this set may appear anywhere in `login.css` or `login.html`.

**Primary**
| Name | Hex | Usage |
|------|-----|-------|
| Electric Blue | `#001391` | Primary actions, button bg (light), focus ring (dark) |
| Serene Blue | `#85C8FF` | Button bg (dark), focus ring (light), accents on dark bg |
| Midnight | `#060E46` | Body text (light), card bg (dark), button label (dark) |

**Accents** (small highlights only)
| Name | Hex |
|------|-----|
| Lime | `#88E783` |
| Mandarin | `#FFB56B` |
| Canary | `#FFE761` |
| Ice | `#8BE1E9` |
| Purple | `#9694FF` |

**Greys**
| Name | Hex |
|------|-----|
| Sand | `#F7F8F8` |
| Grey-1 | `#E2E6EA` |
| Grey-2 | `#CAD1D8` |
| Grey-3 | `#ADB8C2` |
| Grey-4 | `#46536D` |
| Grey-5 | `#000519` |

### Design tokens

CSS custom properties map every UI role to an entry from the palette above.
No hex value outside the palette is permitted in any token definition.

| Token | Light value | Dark value | Role |
|-------|-------------|------------|------|
| `--color-bg` | `#E2E6EA` (Grey-1) | `#000519` (Grey-5) | Page background |
| `--color-surface` | `#F7F8F8` (Sand) | `#060E46` (Midnight) | Card background |
| `--color-border` | `#CAD1D8` (Grey-2) | `#46536D` (Grey-4) | Card / input border |
| `--color-text` | `#060E46` (Midnight) | `#F7F8F8` (Sand) | Body text |
| `--color-text-muted` | `#ADB8C2` (Grey-3) | `#ADB8C2` (Grey-3) | Labels, hints |
| `--color-primary` | `#001391` (Electric Blue) | `#85C8FF` (Serene Blue) | Sign-in button background |
| `--color-primary-hover` | `#46536D` (Grey-4) | `#001391` (Electric Blue) | Sign-in button hover |
| `--color-primary-text` | `#F7F8F8` (Sand) | `#060E46` (Midnight) | Sign-in button label |
| `--color-input-bg` | `#F7F8F8` (Sand) | `#46536D` (Grey-4) | Input / select background |
| `--color-input-text` | `#060E46` (Midnight) | `#F7F8F8` (Sand) | Input / select text |
| `--color-focus-ring` | `#85C8FF` (Serene Blue) | `#85C8FF` (Serene Blue) | Focus outline |
| `--color-toggle-bg` | `#001391` (Electric Blue) | `#85C8FF` (Serene Blue) | Dark-mode toggle button |
| `--color-toggle-icon` | `#F7F8F8` (Sand) | `#060E46` (Midnight) | Toggle button icon |
| `--color-error` | `#FFB56B` (Mandarin) | `#FFB56B` (Mandarin) | Error message text |
| `--color-warn` | `#FFE761` (Canary) | `#FFE761` (Canary) | Warning message text |
| `--color-success` | `#88E783` (Lime) | `#88E783` (Lime) | Success / confirmation |

### Dark mode toggle

- A toggle button rendered in the top-right corner of the card.
- On click: add/remove CSS class `dark` on `<html>` element + write
  `"dark"` / `"light"` to `localStorage` key `prometheus-theme`.
- On page load: inline `<script>` reads `localStorage` and applies the class
  before first paint (no flash of unstyled content).
- No server round-trip required.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Jinja2 templates via `Jinja2Templates` | Already available via Starlette; zero new dependencies |
| CSS custom properties (variables) | Single token swap for dark/light; no Sass/PostCSS needed |
| `localStorage` for theme persistence | Client-side only; no additional cookie or session surface |
| Inline `<script>` for FOUC prevention | Runs before CSS render; keeps the toggle instant |
| No external fonts or icon libraries | Keeps page fully self-contained (no CDN calls) |

## API Contract

No new API endpoints. Existing `GET /ui/login` and `POST /ui/login` signatures are unchanged.

Static files served from:
- `GET /ui/static/login.css` — via `StaticFiles` mount added to the UI router.

## Data Model

No changes to data models.

## Security Considerations

- **Template injection**: all Python-computed values inserted into the template
  must be Jinja2 auto-escaped (`{{ value }}` — no `| safe` filter).  The only
  exception is the pre-built `<option>` list; use a Jinja2 `for` loop inside the
  template instead of Python f-string construction to keep escaping automatic.
- **`localStorage` key**: stores only the string `"dark"` or `"light"` — no
  credentials or tokens.  No server read-back of this value.
- **`<script>` tag for FOUC prevention**: must contain only a literal key read and
  class set; no eval, no external src, no user-controlled data interpolated in.
- **Static files**: served under `/ui/static/`; the StaticFiles mount must be
  scoped only to the `ui/static/` directory, not the full package tree.
- **No new auth bypass**: `/ui/static/login.css` is public (unauthenticated) by
  design — CSS files carry no sensitive data.
- All existing security controls from spec 013 (HttpOnly cookie, HTTPS-only, rate
  limiting, open-redirect guard, scope enforcement) remain unchanged.

## Acceptance Criteria

- [ ] AC-1: Given `GET /ui/login`, when the response is returned, then the HTML
      is rendered from `login.html` (Jinja2 template) and `_render_login_page`
      no longer exists as a Python f-string function in `router.py`.

- [ ] AC-2: Given `GET /ui/static/login.css`, when requested, then the response
      has `Content-Type: text/css` and HTTP 200.

- [ ] AC-3: Given a browser using the default (light) theme, when the login page
      loads, then the page background is `#E2E6EA` (Grey-1) and the card background
      is `#F7F8F8` (Sand).

- [ ] AC-4: Given a browser using the dark theme (`localStorage["prometheus-theme"] = "dark"`),
      when the login page loads, then the page background is `#000519` (Grey-5)
      and the card background is `#060E46` (Midnight) — with no visible flash of
      the light theme on load.

- [ ] AC-5: Given the login page is open in light mode, when the user clicks the
      dark-mode toggle, then the page switches to the dark palette immediately
      (no reload) and `localStorage["prometheus-theme"]` is set to `"dark"`.

- [ ] AC-6: Given the login page is open in dark mode, when the user clicks the
      toggle again, then the page switches back to light mode and
      `localStorage["prometheus-theme"]` is set to `"light"`.

- [ ] AC-7: Given the login page, when the primary Sign-in button is rendered,
      then its background colour is `#001391` (Electric Blue) in light mode and
      `#85C8FF` (Serene Blue) in dark mode.

- [ ] AC-8: Given the login page, when the button label is rendered, then the text
      colour is `#F7F8F8` (Sand) in light mode and `#060E46` (Midnight) in dark mode,
      ensuring WCAG AA contrast in both modes.

- [ ] AC-9: Given `POST /ui/login` returns an error (invalid credentials), when
      the login page re-renders, then the error message is displayed using the
      Mandarin accent colour (`#FFB56B`).

- [ ] AC-10: Given no discoverable models exist, when the login page renders, then
      the warning notice is displayed using the Canary accent colour (`#FFE761`).

- [ ] AC-11: Given all template values (`error`, `model id`, `next_path`), when
      rendered by Jinja2, then they are HTML-escaped (no raw Python string
      interpolation of user-controlled data in the template).

- [ ] AC-12: Given `router.py`, when reviewed, then it contains no inline HTML
      strings, no `<style>` blocks, and no hex colour literals.

- [ ] AC-13: Given the login page HTML, when the page is inspected, then there are
      no references to external CDNs, external fonts, or external icon libraries.

- [ ] AC-14: Given the `login.css` stylesheet, when reviewed, then all colour
      values are expressed as CSS custom properties defined in `:root` and
      `html.dark` blocks — no hardcoded hex literals outside the token definitions.

- [ ] AC-15: Given the existing spec-013 POST /ui/login functional tests, when the
      test suite is run against the refactored implementation, then all tests pass
      without modification.

- [ ] AC-16: Given the `login.css` file, when all hex colour literals in the
      `:root` and `html.dark` token blocks are enumerated, then every value is
      exactly one of the 14 approved palette entries: `#001391`, `#85C8FF`,
      `#060E46`, `#F7F8F8`, `#E2E6EA`, `#CAD1D8`, `#ADB8C2`, `#46536D`,
      `#000519`, `#88E783`, `#FFB56B`, `#FFE761`, `#8BE1E9`, `#9694FF` —
      no other hex value appears anywhere in the stylesheet.

## Open Questions

- [x] Q1: Should `/ui/static/` be versioned (e.g. cache-busted with a hash) for
      production deployments, or is a simple `Cache-Control: no-cache` header
      sufficient for the current MVP scope?
      → **Decision**: `StaticFiles` mount will include `Cache-Control: no-cache`
      via a custom middleware wrapper on the `/ui/static` route. No URL
      versioning or ETags required for MVP. A cache-busting strategy can be
      introduced in a future spec when the UI stabilises.

## References

- Related specs: `memory/specs/013-web-chat-ui-proxy.md`
- Implementation file: `gateway/src/prometheus_gateway/ui/router.py`
- WCAG 2.1 AA contrast ratio: minimum 4.5:1 for normal text
