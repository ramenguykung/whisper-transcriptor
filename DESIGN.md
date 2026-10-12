# Design

Operate mode. Code-first. The user pinned the visual world and composition in the implementation plan.

## Tokens

- Canvas `#f3f4f6`, surface `#ffffff`, muted surface `#e9edf2`.
- Primary text `#192332`, secondary text `#526072`, border `#cdd4df`.
- Accent `#245bd6`, hover `#1947ac`, selection `#e9f0ff`.
- Playback `#101722`, error `#a52b31`, success `#226742`.
- System sans family. Times and diagnostics use system monospace. Body 14 px, compact text 12 px, headings 18 to 24 px.
- Spacing 4, 8, 12, 16, 24, 32 px. Controls 6 px radius. Panels use separators rather than decorative cards.

## Workspace

Compact top bar with source, save status, and export. Job library and upload settings remain accessible. Desktop uses playback left and editable rows right; the shared timeline spans both. Narrow screens stack playback, timeline, and transcript. Long transcript rows are virtualized.

## Interaction

Accent identifies the active segment and primary action. Timings remain tabular. Labels are plain words. Hover and focus are visible. Disabled controls explain dependencies where needed. Processing uses honest phases; no invented ETA. Errors show a recovery action with diagnostics in Details.

## Surface brief

The main job is to correct subtitles while watching and listening. Initial state teaches upload and shows local runtime status. Ready state concentrates on playback and text. Autosave conflicts keep the local draft visible and downloadable. No decorative imagery is required.
