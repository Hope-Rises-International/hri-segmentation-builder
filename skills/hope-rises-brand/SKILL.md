---
name: hope-rises-brand
description: "Apply Hope Rises International brand identity (colors, fonts, logo, tone) to any generated output. Use this skill EVERY TIME the user asks to create presentations (PPTX), Word documents (DOCX), spreadsheets (XLSX), PDFs, HTML pages, dashboards, marketing materials, reports, one-pagers, memos, or any visual deliverable for Hope Rises International or HRI. Also trigger when the user mentions 'brand', 'on-brand', 'Hope Rises', 'HRI style', 'our colors', 'our fonts', 'branded', or requests output that should look professional and consistent with organizational identity. This skill should be used IN COMBINATION with the relevant output skill (docx, pptx, xlsx, pdf, frontend-design). Read this skill FIRST to get brand specs, then read the output-format skill for creation instructions."
---

# Hope Rises International — Brand Identity Skill

## Purpose

This skill ensures all Claude-generated deliverables for Hope Rises International (HRI) are visually and tonally on-brand. It is designed to be used **alongside** other creation skills (docx, pptx, xlsx, pdf, frontend-design).

**Workflow:** Read this skill → extract relevant brand specs → then read and follow the appropriate output skill (e.g., `/mnt/skills/public/pptx/SKILL.md` for presentations).

---

## Organization Overview

- **Full Name:** Hope Rises International
- **Formerly:** American Leprosy Missions (117+ year legacy)
- **Type:** International Christian nonprofit / parachurch ministry
- **Mission:** Works with Christian partners around the world to bring physical healing and enduring hope to people suffering from neglected tropical diseases (NTDs) like leprosy
- **Tagline / Hook:** "Present healing, eternal hope."
- **Headquarters:** One ALM Way, Greenville, South Carolina 29601
- **Locations:** US (Greenville SC HQ + 3 other US locations), Ghana, India
- **Staff:** ~17 full-time distributed team
- **Website:** hoperises.org

---

## Color Palette

### Primary Colors

| Name       | HEX       | RGB              | CMYK             | Pantone  | Usage |
|------------|-----------|------------------|------------------|----------|-------|
| **Tide**       | `#1E5773` | 30 / 87 / 115    | 91 / 61 / 37 / 18 | 7700 C   | **Most-used primary.** Headlines, body text, primary backgrounds. Always try Tide first. |
| **Rising Sun** | `#F26044` | 242 / 96 / 68    | 0 / 78 / 77 / 0   | 2026 C   | Accent color. Name highlights, CTA buttons, emphasis. Use sparingly. |

### Secondary Colors

| Name       | HEX       | RGB              | CMYK             | Pantone  | Usage |
|------------|-----------|------------------|------------------|----------|-------|
| **Golden**     | `#BC893F` | 188 / 137 / 63   | 25 / 46 / 88 / 5  | 4026 C   | Supporting color used frequently with primaries. |
| **Restore**    | `#97CBF0` | 151 / 203 / 240  | 38 / 8 / 0 / 0    | 291 C    | Emphasis, CTA highlights, decorative elements. Preferred for emphasis and calls-to-action. |
| **Empower**    | `#302625` | 48 / 38 / 37     | 64 / 68 / 65 / 68 | 4259 C   | Dark neutral. Body copy alternative to Tide. Use less frequently than Golden/Restore. |
| **Cleanse**    | `#F7EDE8` | 247 / 237 / 232  | 2 / 6 / 6 / 0     | 9285 C   | Light neutral / warm off-white. Backgrounds, body copy alternative. |

### Color Hierarchy Rules

1. **Tide** is the dominant color — use for headings, subheadings, labels, and table headers. Do NOT use Tide for body copy.
2. **Black** (`#000000`) is the default body copy color for readability.
3. **Rising Sun** is an accent — never as a primary background; use for highlights, eyebrows, and calls-to-action.
4. **Golden** and **Restore** are the most-used secondary colors, supporting Tide.
5. **Empower** (`#302625`) is an alternative dark color for body text or dark backgrounds. Use less frequently than Golden.
6. **White** is a primary background color.
7. **Cleanse** (`#F7EDE8`) is the preferred warm off-white for backgrounds instead of pure white when warmth is desired.

### Approved Tints

Tints may ONLY be created in these increments: **100%, 75%, 50%, 25%, 10%**. No other tint percentages.

### Color Accessibility — Approved Combinations (AA compliant)

| Foreground | Background | Status |
|------------|------------|--------|
| Tide | Cleanse/White | ✅ Pass |
| Restore | Tide | ✅ Pass |
| Empower | Golden | ✅ Pass |
| Empower | Rising Sun | ✅ Pass |
| Cleanse/White | Empower | ✅ Pass |

### Color Accessibility — FAIL (Do NOT use)

| Foreground | Background | Status |
|------------|------------|--------|
| Cleanse/White | Restore | ❌ Fail |
| Golden | Tide | ❌ Fail |
| Rising Sun | Cleanse/White | ❌ Fail |
| Rising Sun | Golden | ❌ Fail |
| Tide | Empower | ❌ Fail |

---

## Typography

### Primary Font: Golos Text (Google Font)

- **Source:** Google Fonts — `fonts.google.com/specimen/Golos+Text` (free, commercially licensed)
- **Available Weights:** Regular, Medium, SemiBold, Bold, ExtraBold, Black

| Element | Weight | Case | Color |
|---------|--------|------|-------|
| **Headings (H1)** | ExtraBold or Black | Title case | Tide (`#1E5773`) preferred |
| **Subheadings (H2-H3)** | Bold | Title case | Tide preferred; Golden or Rising Sun when needed |
| **Body copy** | Regular (preferred) or Medium | Sentence case | Black (`#000000`) for readability; Empower (`#302625`) as alternative dark |
| **Eyebrows / labels** | Bold | ALL CAPS | Tide or Rising Sun |
| **CTA / Buttons** | Bold | ALL CAPS | Tide on light bg, White on Tide bg |
| **Quote attribution** | Regular | Sentence case | Tide |
| **Emphasis in body** | Bold | — | Tide or Rising Sun |

### Secondary Font: Gitan Bold (Campaign/Logo only)

- **Source:** Adobe Creative Cloud or rosettatype.com/GitanLatin (requires license)
- **Usage:** Campaign headlines and logo design ONLY. All caps preferred for headlines.
- **NEVER use Gitan for body copy or paragraphs.**
- **Note:** Since Gitan requires a commercial license and may not be available in all generation contexts, use **Golos Text Black** as a fallback for campaign-style headlines.

### Email Fallback Font: Verdana

- For Gmail email signatures and email body copy, use **Verdana** as the system font replacement for Golos Text.
- Name: Verdana Bold, Rising Sun
- Org name: Verdana Bold All Caps, Tide
- Address/details: Verdana Italic, Tide

---

## Logo

### Files Available

| File | Description | Best Use |
|------|-------------|----------|
| `assets/logo_full_color.png` | Full color logo with tagline, 1000px, 300ppi PNG (has black bg — avoid for embedding) | Reference only |
| `assets/logo_clean.png` | Full color logo rendered from SVG, 800x338px, transparent background | **Use this for DOCX/PPTX embedding** |
| `assets/logo_full_color.svg` | Full color logo with tagline, SVG | HTML/web, scalable contexts, source for rendering |
| `assets/logo_icon.png` | Star icon only, 1000px, 300ppi PNG (transparent) | Slide footers, spreadsheet headers, small placements, watermarks |
| `assets/logo_icon.svg` | Star icon only, SVG | HTML favicons, small web UI elements |

### Logo Variations

1. **Primary:** Horizontal lockup with star icon + "Hope Rises International" text + tagline — use `logo_full_color.*`
2. **Stacked:** Centered/stacked with "Formerly American Leprosy Missions" subtitle (not bundled; reference only)
3. **Icon:** Star icon only (golden star with Cleanse circle and Restore arc) — use `logo_icon.*`

### Logo Rendering Notes

- **IMPORTANT:** The original `logo_full_color.png` has a black background (RGB mode, no transparency). When embedding logos in documents, **always render from the SVG** (`logo_full_color.svg`) to get true transparency, or use the pre-rendered `logo_clean.png`.
- `assets/logo_clean.png` — Clean render from SVG with transparent background, 800x338px (use this for DOCX/PPTX embedding)
- When sizing the full logo, maintain the **2.37:1 aspect ratio** (e.g., 200px wide × 85px tall).
- The icon PNG (`logo_icon.png`) has proper RGBA transparency and can be used directly.

### Which Logo to Use

- **Full logo** (`logo_full_color.*`): Title slides, first page of documents, letterheads, marketing headers, any placement wider than ~2 inches
- **Icon only** (`logo_icon.*`): Slide footers, document footers, spreadsheet corner, small repeated placements, watermarks, anywhere space is limited

### Logo Usage Rules

- Minimum print size: **1.4 inches** wide for primary and stacked logos; **0.25 inches** for icon
- Maintain clear space equal to the distance of the "H" in "Hope" on all sides
- Never stretch, rotate, or alter the logo colors
- Place logo on white, Cleanse, or light backgrounds for full-color version
- For dark backgrounds (Tide, Empower), use white/reversed version of the logo

### Logo Placement in Documents

- **Presentations:** Full logo on title slide (upper left or centered); icon in footer of content slides
- **Documents:** Full logo in header of first page; icon in footer of subsequent pages
- **Marketing:** Full logo prominent; icon for repeated/small placements
- **Spreadsheets:** Icon in top-left cell area; keep small and professional

---

## Brand Tone & Voice

### Archetype: Visionary (Discovery & Transformation)

"We look at the world and know it is not as it was meant to be. We seek to advance Jesus' kingdom on Earth through our work, coming alongside Christian partners to offer hope and healing to people with NTDs like leprosy."

### Tone Guardrails

| Be this... | ...but NOT this |
|------------|-----------------|
| Compassionate | Impractical |
| Knowledgeable | Stuffy |
| Inspirational | Naive |
| Confident | Arrogant |
| Christ-centered | Insensitive |
| Contemporary | Inexperienced |

### Brand Attributes (how we feel)

- **Determined & Hopeful** — We believe a world without the enduring pain and stigma of NTDs is possible
- **Genuine & Compassionate** — We see people, not statistics
- **Strategic & Future-Oriented** — We carefully plan paths forward for greatest impact
- **Focused & Passionate** — We stay committed to our unique mission
- **Humble Partners** — We link arms with others; it is not about us

### Visual Style

- Established & current
- Relational & professional
- Christ-centered & kingdom-focused
- Positive & encouraging
- Inviting & approachable
- Capable & well-resourced

---

## Output-Specific Guidelines

### Presentations (PPTX)

- **Use the existing template** at `assets/presentation_template.pptx` when editing/extending presentations
- **For new presentations from scratch:**
  - Title slide: Tide background with white text, or white/Cleanse background with Tide text. Logo prominently placed.
  - Content slides: White or Cleanse background. Tide headings (Golos Text ExtraBold). Body in Golos Text Regular, Tide color.
  - Accent slides: Rising Sun or Golden background for divider/section slides (use white text).
  - Tables: Tide header row with white text; alternating Cleanse/white body rows.
  - Charts: Use brand color palette in order: Tide, Rising Sun, Golden, Restore, Empower, Cleanse.
  - Final slide: Logo centered, optional tagline "Present healing, eternal hope."

### Word Documents (DOCX)

- **Use the existing letterhead template** at `assets/letterhead_template.docx` when creating letters, memos, or formal correspondence. It includes the full logo header, Golos Text 11pt body, and branded footer with address.
- **For new documents from scratch:**
  - **Heading 1:** Golos Text ExtraBold, 18-24pt, Tide (`#1E5773`)
  - **Heading 2:** Golos Text Bold, 14-16pt, Tide
  - **Heading 3:** Golos Text Bold, 12pt, Tide or Golden
  - **Body:** Golos Text Regular, 11pt, Black (`#000000`). Use Empower (`#302625`) as an alternative dark color if needed. Do NOT use Tide for body copy — reserve Tide for headings and labels.
  - **Header:** Full logo left-aligned on first page; icon on subsequent pages. Document title right-aligned in Golos Text Bold, Tide.
  - **Footer:** Page numbers centered, Golos Text Regular, 9pt. Address line: One ALM Way, Greenville, South Carolina 29601
  - **Page margins:** Top 2340 twips (~1.625"), Left 1872 twips (~1.3"), Right 1080 twips (~0.75"), Bottom 270 twips (~0.19")
  - **Horizontal rules / dividers:** Use Tide or Restore colored lines
  - **Tables:** Tide header row (white text), Cleanse alternating rows
  - **Hyperlinks:** Rising Sun (`#F26044`)

### Spreadsheets (XLSX)

- **Header row:** Tide background (`#1E5773`), white text, Golos Text Bold
- **Alternating rows:** White and Cleanse (`#F7EDE8`)
- **Totals/summary rows:** Golden background (`#BC893F`), white text
- **Charts:** Use brand colors in order: Tide, Rising Sun, Golden, Restore, Empower
- **Sheet tab naming:** Use clear, professional names
- **Logo:** Optional in top-left cell area; keep small

### Marketing Materials / HTML / PDF

- **Primary CTA color:** Rising Sun (`#F26044`) button with white text
- **Secondary CTA:** Tide (`#1E5773`) button with white text
- **Background sections:** Alternate between white, Cleanse, and 10% Tide tint
- **Pull quotes:** Golos Text Regular italic, Tide color, with Rising Sun accent line
- **Icons/decorative elements:** Use brand shape elements (half circles, diamonds, dots, lines) in brand colors
- **Photography direction:** Prefer HRI's own image library. People should look valued and affirmed. Stock imagery used sparingly, matching brand tone.

---

## Brand Elements & Textures

The brand includes decorative elements that can be referenced in descriptions or recreated:

- **Shape elements:** Half circles, diamonds, circles, horizontal lines, dot sequences
- **Brush stroke textures:** Available in all brand colors; used for visual interest
- **Watercolor line textures:** Primarily in Restore blue; used as subtle backgrounds
- **Star pattern:** Repeating star/cross motif from the logo icon; used in tints as background patterns
- **Photo textures:** Ocean, linen, paper textures in brand-adjacent colors

When creating HTML/React artifacts, these can be approximated with CSS gradients, SVG shapes, or subtle background patterns.

---

## Key Messaging (for content generation)

When Claude generates text content for HRI, use these approved messages:

- **Introduction:** "Hope Rises International works with Christian partners around the world to bring physical healing and enduring hope to people suffering from neglected tropical diseases like leprosy."
- **Hook:** "Present healing, eternal hope."
- **Purpose:** "To help restore lives to dignity, fullness and community."
- **Promise:** "Hope and Healing"
- **Position:** "Bringing physical healing and spiritual hope to people with NTDs like leprosy, through the Church."

---

## Quick Reference Card

```
COLORS (copy-paste ready):
  Tide:       #1E5773  (primary — use most)
  Rising Sun: #F26044  (primary accent)
  Golden:     #BC893F  (secondary — frequent)
  Restore:    #97CBF0  (secondary — emphasis/CTA)
  Empower:    #302625  (secondary — dark neutral)
  Cleanse:    #F7EDE8  (secondary — warm off-white bg)

FONTS:
  Primary:    Golos Text (Google Font, free)
  Campaign:   Gitan Bold (Adobe/Rosetta, licensed) — fallback: Golos Text Black
  Email:      Verdana (system font)

LOGO:
  assets/logo_full_color.png  (full logo + tagline, 1000px, 300ppi)
  assets/logo_full_color.svg  (full logo + tagline, scalable)
  assets/logo_icon.png        (star icon only, 1000px, 300ppi)
  assets/logo_icon.svg        (star icon only, scalable)

TEMPLATES:
  assets/presentation_template.pptx  (branded PPTX with slide layouts)
  assets/letterhead_template.docx    (branded letterhead with header/footer)

TONE: Compassionate, knowledgeable, inspirational, confident, Christ-centered, contemporary
```
