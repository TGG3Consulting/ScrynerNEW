import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

const watch = process.argv.includes("--watch");
mkdirSync("dist/static", { recursive: true });
cpSync("public", "dist", { recursive: true });

const ctx = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  format: "esm",
  target: ["es2022"],
  outfile: "dist/static/app.js",
  sourcemap: true,
  minify: !watch,
  define: { "process.env.NODE_ENV": watch ? '"development"' : '"production"' },
  logLevel: "info",
});
if (watch) {
  await ctx.watch();
} else {
  await ctx.rebuild();
  await ctx.dispose();
}
