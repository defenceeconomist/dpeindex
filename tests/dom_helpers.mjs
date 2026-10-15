// Browser behaviour that linkedom does not implement (select.value and layout).
export function installDomHelpers(window) {
  const proto = Object.getPrototypeOf(window.document.createElement("select"));
  Object.defineProperty(proto, "value", {configurable: true,
    get() { return (this.querySelector("option[selected]") || this.querySelector("option"))?.value || ""; },
    set(value) { this.querySelectorAll("option").forEach((option) => option.toggleAttribute("selected", option.value === String(value))); },
  });
  window.HTMLElement.prototype.scrollIntoView = function () { window.lastScrolled = this; };
  window.scrollTo = () => {};
  window.HTMLElement.prototype.getBoundingClientRect = () => ({top: 100, bottom: 300, height: 200});
  window.matchMedia = () => ({matches: false});
  window.innerHeight = 800;
}

export async function settle() { for (let i = 0; i < 16; i++) await new Promise(setImmediate); }
