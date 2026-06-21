---
name: vue
description: Vue 3 (Composition API) do's and don'ts
match: ["vue", "@vue/runtime-core", "@vitejs/plugin-vue"]
see_also: ["platform-native#html-elements", "platform-native#css-capabilities", "platform-native#javascript-browser-apis"]
---
## Do
- Use `<script setup>` with the Composition API; type props and emits with `defineProps<…>()` / `defineEmits<…>()`.
- Use `ref` / `reactive` / `computed` for state and `watch` / `watchEffect` for side effects; clean up listeners/timers in `onUnmounted`.
- Extract reusable stateful logic into composables (`useXxx`), one concern each.
- Give every `v-for` a stable `:key`.
- Scope component styles (`<style scoped>`) or use CSS modules.

## Don't
- Don't write new components in the Options API when the project uses Composition; don't reach for `this` in `<script setup>`.
- Don't mutate props; emit an event or use `v-model` with a declared prop.
- Don't use `any`; type props, emits, refs, and composable returns.
- Don't put heavy logic in templates; move it to a `computed` or a method.
- Don't manipulate the DOM by hand (`document.querySelector`, manual `innerHTML`) when a binding or directive will do — and never set unsanitized HTML.

## Patterns
- Smart/presentational split: container components own data and effects; presentational components take props and emit events (props down, events up).
- Composables for cross-component logic; co-locate a component with its test.
- Prefer the native platform before a dependency (see platform-native).

## Review checklist
- [ ] New components use `<script setup>` (Composition API), not the Options API.
- [ ] `defineProps` / `defineEmits` are typed (no untyped props or emits).
- [ ] No `any` in changed component/composable signatures.
- [ ] Every `v-for` has a stable `:key`.
- [ ] No prop mutation — state changes go through an emit or a local `ref`.
- [ ] Every added listener / timer / `watch` in a component or composable has matching teardown (`onUnmounted`, or the `watch` stop handle).
- [ ] `v-html` is only fed sanitized content (a sanitizer such as DOMPurify in the same path) — never raw user input.
- [ ] No date/color/range/time-picker dependency where a native `<input type=…>` covers it (see platform-native).
