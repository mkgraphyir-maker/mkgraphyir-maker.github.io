/**
 * Adds the `js` class before first paint so progressive-enhancement CSS applies
 * without a flash of unstyled content. It lives in its own file so the
 * Content-Security-Policy can forbid all inline <script> blocks.
 */
document.documentElement.classList.add('js');
