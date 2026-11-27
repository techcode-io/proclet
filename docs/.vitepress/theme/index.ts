import { h } from "vue";
import type { Theme } from "vitepress";
import DefaultTheme from "vitepress/theme";

import "@fontsource-variable/schibsted-grotesk";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./custom.css";

import HeroPipeline from "./components/HeroPipeline.vue";
import InstallCommand from "./components/InstallCommand.vue";

export default {
  extends: DefaultTheme,
  Layout: () =>
    h(DefaultTheme.Layout, null, {
      "home-hero-image": () => h(HeroPipeline),
      "home-hero-actions-after": () => h(InstallCommand),
    }),
} satisfies Theme;
