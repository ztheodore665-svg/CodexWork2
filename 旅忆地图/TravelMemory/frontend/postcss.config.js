/** PostCSS 管线。
 *
 * Tailwind 必须在 autoprefixer **之前**：它产出的是未来语法 + `@apply`，
 * autoprefixer 负责补厂商前缀，顺序反了会漏补齐 Safari 的 `-webkit-` 前缀。
 */

export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
