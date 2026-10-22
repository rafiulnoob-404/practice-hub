# Practice Hub: question file format

## File names
`B1.md` Biology · `C1.md` Chemistry · `P1.md` Physics · `GK1.md` GK · `E1.md` English · `W1.md` Weekly (mixed) · `M1.md` Monthly (mixed)
Put all files in the `questions/` folder. Number increases per file (B1, B2, B3 …).

## Question block (exact pattern)

```
### Q 1

Question text here (Bangla or English; LaTeX allowed: $H_2SO_4$, $x^2$)

* Option A text
* Option B text
* Option C text
* Option D text

## answer
D

## explanation
Short paragraph, or a small markdown table. LaTeX allowed.

## status

```

## Rules
- Heading is exactly `### Q n`, n = 1, 2, 3 … (restart at 1 in every file).
- Question text sits between the heading and the options.
- Underlined word in the question: wrap it as `<u>word</u>` (e.g. `He is a man of <u>few</u> words.`). Bold `**word**` and italic `*word*` also work.
- Exactly 4 options, each starting with `* `. No A/B/C/D letters inside the option text.
- `## answer` holds one letter: A, B, C or D.
- `## explanation` is optional (the app can generate it with Gemini). Keep it a short paragraph or table. No `#` headings inside.
- `## status` stays empty. The app fills it with `correct`, `incorrect` or `skipped`.
- Weekly and monthly files mix subjects (B, C, P, GK, E) in random order; same block format.
- One blank line between blocks. No other sections.

## Prompt 1: convert my questions into this format (paste into any chat)

```
Convert the questions I give you into Markdown using exactly this pattern for every question:

### Q n

question text

* option 1
* option 2
* option 3
* option 4

## answer
A/B/C/D

## explanation
short paragraph or small table

## status

Rules: restart numbering at Q 1; exactly 4 options with "* " bullets and no letters inside; answer is a single letter A-D; keep "## status" empty; keep the original language (Bangla/English); write chemical formulas and math in LaTeX with $...$; no headings inside the explanation; wrap any underlined word in the question as <u>word</u>; output one single code block I can save as <FILENAME>.md.
```

## Prompt 2: extend or debug the app (paste into any chat)

```
App: "Practice Hub", local Flask app. Files: app.py, templates/index.html, questions/*.md. 
Neumorphic light/dark UI with 4 tabs: Overview, Subjects (ring per subject, tap to filter), Questions (inline practice), Settings (theme, accent color, font, Gemini key/model saved in config.json). Shows total questions, files, accuracy %, attempted %, donut chart, per-subject accuracy bars; questions appear inline with subject chips (Biology, Chemistry, Physics, GK, English, Weekly, Monthly) and a status filter. Questions are shuffled across the selected files.
Question files: names B#, C#, P#, GK#, E#, W#, M#. Format: "### Q n", question text, "* " options, "## answer" (A-D), "## explanation", "## status" (correct/incorrect/skipped; written back to the md file on every answer).
Gemini integration (default model gemini-3.6-flash-lite, key via GEMINI_API_KEY env) writes a short paragraph/table explanation with LaTeX (KaTeX) into "## explanation".
Prefer Flask + HTML templates. Task: <WRITE YOUR CHANGE HERE>
```
