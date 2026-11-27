import { defineConfig } from "vitepress";

const repository = "https://github.com/techcode-io/proclet";

export default defineConfig({
  title: "proclet",
  description:
    "Fluent, typed subprocess wrapper with sync and async execution and pipelines.",
  base: "/proclet/",
  cleanUrls: true,
  lastUpdated: true,
  markdown: {
    theme: { light: "vitesse-light", dark: "vitesse-dark" },
  },
  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: "/proclet/logo.svg" }],
    ["meta", { name: "theme-color", content: "#0b7f80" }],
  ],
  themeConfig: {
    logo: "/logo.svg",
    nav: [
      { text: "Guide", link: "/guide/getting-started", activeMatch: "/guide/" },
      { text: "Recipes", link: "/recipes/", activeMatch: "/recipes/" },
      { text: "API", link: "/reference/api" },
      { text: "Changelog", link: `${repository}/blob/main/CHANGELOG.md` },
      { text: "PyPI", link: "https://pypi.org/project/proclet/" },
    ],
    sidebar: [
      {
        text: "Introduction",
        items: [
          { text: "Getting started", link: "/guide/getting-started" },
          { text: "Why proclet", link: "/guide/why-proclet" },
        ],
      },
      {
        text: "Migration",
        items: [
          { text: "Coming from sh", link: "/migration/from-sh" },
          { text: "Coming from plumbum", link: "/migration/from-plumbum" },
        ],
      },
      {
        text: "Guide",
        items: [
          { text: "Running commands", link: "/guide/running-commands" },
          { text: "Input and output", link: "/guide/input-output" },
          { text: "asyncio", link: "/guide/asyncio" },
          { text: "Pipelines", link: "/guide/pipelines" },
          { text: "Environment and PATH", link: "/guide/environment" },
          { text: "Privileges and priority", link: "/guide/privileges" },
          { text: "Reusing and wrapping commands", link: "/guide/reuse-and-wrap" },
          { text: "Observing processes", link: "/guide/observers" },
        ],
      },
      {
        text: "Recipes",
        link: "/recipes/",
        items: [
          { text: "Stream output to a logger", link: "/recipes/stream-output-to-logger" },
          { text: "Timeouts", link: "/recipes/timeout" },
          { text: "Binary pipes without a shell", link: "/recipes/binary-pipes" },
          { text: "Run as another user", link: "/recipes/run-as-another-user" },
          { text: "Concurrency with asyncio", link: "/recipes/asyncio-concurrency" },
          { text: "Wrap a CLI", link: "/recipes/wrap-a-cli" },
        ],
      },
      {
        text: "Reference",
        items: [{ text: "API", link: "/reference/api" }],
      },
    ],
    socialLinks: [{ icon: "github", link: repository }],
    editLink: {
      pattern: `${repository}/edit/main/docs/:path`,
      text: "Edit this page on GitHub",
    },
    search: { provider: "local" },
    footer: {
      message: "Released under the Apache-2.0 license.",
    },
  },
});
