// Last check before notes HTML reaches the live page. The server sanitiser is
// the main gate; this covers rows saved before a sanitiser fix and any place
// where a Python parser and a browser parser read the same markup differently.
// DOMParser builds an inert document: nothing in it loads or runs.

const BLOCKED_ELEMENTS = [
  "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet",
  "link", "meta", "base", "form", "input", "button", "textarea", "select",
  "svg", "math", "template", "img", "video", "audio", "source", "track",
  // Their contents parse differently in DOMParser and on the live page.
  "noscript", "noembed", "noframes", "xmp", "plaintext", "title",
].join(",");

const URL_ATTRIBUTES = new Set([
  "href", "xlink:href", "src", "srcset", "srcdoc", "action", "formaction",
  "background", "poster", "ping", "data",
]);

const UNSAFE_STYLE = /url\s*\(|expression\s*\(|javascript:/i;

/** Parse HTML without running it and remove anything that could run or load. */
export function parseSafeHtml(html: string): Document {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  parsed.body.querySelectorAll(BLOCKED_ELEMENTS).forEach((node) => node.remove());
  parsed.body.querySelectorAll("*").forEach((node) => {
    for (const attribute of Array.from(node.attributes)) {
      const name = attribute.name.toLowerCase();
      const unsafe = name.startsWith("on")
        || URL_ATTRIBUTES.has(name)
        || (name === "style" && UNSAFE_STYLE.test(attribute.value));
      if (unsafe) node.removeAttribute(attribute.name);
    }
  });
  return parsed;
}

export function safeNotesHtml(html: string): string {
  return parseSafeHtml(html).body.innerHTML;
}
