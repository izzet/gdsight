# GDSight project site

The static research page for GDSight. It is separate from the tracer and
workload code, so the website toolchain does not affect the artifact.

## Local preview

```bash
cd website
npm install
npm run dev
```

Use `npm run build` to type-check and create the static production site in
`website/dist/`.

## Release policy

The site deploys through GitHub Pages. Its paper preview and paper link are
intentionally easy to disable if the workshop's camera-ready or public-release
rules require it.
