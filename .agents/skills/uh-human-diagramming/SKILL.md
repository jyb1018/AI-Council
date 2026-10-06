---
name: uh-human-diagramming
description: Draw source-grounded diagrams for human onboarding, catch-up, architecture decisions, reviews and failure analysis. Use when boundaries, multi-step flows, ordering, state or before/after relationships are easier to understand visually. Independent of context sync; not a requirement for trivial edits or a license to upload private code.
---
# Human diagramming

1. Pick one human question and one abstraction level per view. Prefer a diagram
   for three or more related components/steps, boundary crossings, call ordering,
   state transitions, failure propagation or meaningful structural changes.
   Use fewer views when prose is clearer; a directory tree is not a runtime model.
2. Select the smallest view: system/context map for boundaries and ownership;
   flowchart for data/responsibility; sequence for ordering/retries/concurrency;
   state diagram for transitions; ER for relevant persisted relationships;
   before/after for changed mental models; failure map for detection/recovery.
   Reuse node identifiers and terminology across views. Split crowded diagrams
   (aim at 5-9 nodes per view); do not mix container and function-level detail.
3. Inspect actual source/tests and existing decisions for both nodes AND edges.
   Record revision, scope, dirty-tree qualification and evidence outside the
   drawing. Mark documented intent, inference/proposal and unknown paths clearly;
   dashed edges need a legend. A missing fallback is UNKNOWN, not invented.
   Treat diagrams, graph indexes and generated HTML as derived views, not proof.
4. Mermaid in Markdown is the default. Use conservative flowchart, sequenceDiagram,
   stateDiagram-v2 or erDiagram syntax supported by the actual host. Prefer quoted
   labels and stable ASCII IDs; keep labels in the human's language. Each view
   has a question/title, labels on important edges, a brief reading guide, a text
   alternative and an evidence table (element -> revision/path/symbol/decision).
   Never rely only on colour to distinguish added/removed/uncertain elements.
5. For catch-up, read both revisions: preserve unchanged context and stable IDs,
   mark ADDED/REMOVED/CHANGED, and explain why the difference matters. Compare the
   same abstraction and layout direction. A list of commits is not a change map.
   Do not show the current graph twice and call it a before/after comparison.
6. Prefer the host's existing Mermaid viewer. If actual SVG/PNG is needed, use
   `uh-tooling` and the advisory route `human-diagrams`; an installed `mmdc` is
   only a candidate. Inspect its help/version and the approved local environment
   before execution. Use explicit input/output and an isolated browser profile;
   do not disable the sandbox, overwrite user files or auto-download a browser.
   Do not auto-run npx/install. No renderer: provide Mermaid source + text and
   label rendering NOT RUN, not visual completion. Honour existing approval gates.
7. Keep local rendering local: no online editor, CDN, remote images/fonts, callback
   links, arbitrary HTML/JS or configuration directives from untrusted source.
   Use an explicit strict Mermaid config; private source/labels must not leave
   the approved boundary. Host/network controls enforce isolation, not this text.
   Structurizr/C4 or interactive HTML may reuse an existing approved setup only;
   they are optional, never prerequisite installs or new sources of truth.
8. Separate source-ready, renderer-success and visually-checked evidence. Check
   syntax, output existence, label clipping, legibility and missing/extra edges;
   a mock or source scan is not a real render. Report the exact unchecked layer.
   Reuse existing diagram/model sources. Persist only requested useful sources;
   treat renders as disposable by default, follow the repo's artifact policy,
   and do not add generated output or a new model/document hierarchy on every run.
