# Invoice Extractor UI

A Next.js 16 (App Router, React 19, Tailwind v4) front end for the FastAPI
backend in `../app`. Talks to it directly over HTTP/CORS - there's no server
API layer of its own.

## Running

```bash
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
```

The backend needs to be running separately (`docker compose up` from the
project root) and must allow this origin - see `cors_origins` in
`app/core/config.py` (defaults to `http://localhost:3000`).

## Pages

- `/` - upload a document, filter/browse documents by tenant and status.
- `/documents/[id]` - live status (polls every 2s until a terminal state),
  page images with confidence-colored bounding-box overlays per field,
  validation issues, run details (model/cost/tokens/latency). When a
  document is `needs_review`, fields become editable and saving posts
  corrections back to the API (`POST /review/{id}/correct`).

## Notes

- `PageProps<'/documents/[id]'>` is Next 16's generated route-typing helper -
  `params` is a `Promise` in this version (see the App Router breaking
  changes in `node_modules/next/dist/docs/01-app/02-guides/upgrading/version-16.md`).
- The dashboard and detail page are both Client Components (`"use client"`):
  everything here is live, polled state from an external API, not data this
  app owns, so Server Component data-fetching adds little and the polling/edit
  interactions need client state regardless.
- Corrections only submit fields the user actually edited, with numeric
  fields coerced back from the `<input>` string to a number before sending -
  resending every field's current value (as a string) made the backend's
  `old_value == new_value` check see a spurious type mismatch for every
  untouched numeric field and record it as a fake "correction."
