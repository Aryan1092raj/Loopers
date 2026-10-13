"use client";
import { useEffect } from "react";
import { reducedMotion } from "@/lib/motion";

const TARGETS = "button, [role=button], [role=menuitem], summary, a[class*=rounded], [data-ripple]";

/** Light text on a filled element wants a pale ripple; anything else gets a faint indigo one. */
function rippleColor(el: HTMLElement): string {
  const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 0];
  const [r, g, b, a = 1] = m;
  const dark = a > 0.5 && 0.299 * r + 0.587 * g + 0.114 * b < 170;
  return dark ? "rgb(255 255 255 / 0.38)" : "rgb(91 91 214 / 0.2)";
}

/** One listener for the whole site: a slight ripple from the pointer on every button and tappable card. */
export default function Ripple() {
  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (e.button !== 0 || reducedMotion()) return;
      const el = (e.target as Element).closest<HTMLElement>(TARGETS);
      if (!el || (el as HTMLButtonElement).disabled || el.closest("[data-no-ripple]")) return;
      const box = el.getBoundingClientRect();
      const x = e.clientX - box.left, y = e.clientY - box.top;
      const size = Math.hypot(Math.max(x, box.width - x), Math.max(y, box.height - y)) * 2;
      if (getComputedStyle(el).position === "static") el.style.position = "relative";
      // the layer clips the ripple to the element's shape, so the element's own overflow and children are untouched
      const layer = document.createElement("span");
      layer.className = "ripple-layer";
      layer.setAttribute("aria-hidden", "true");
      const dot = document.createElement("span");
      dot.className = "ripple-dot";
      Object.assign(dot.style, { left: `${x - size / 2}px`, top: `${y - size / 2}px`, width: `${size}px`, height: `${size}px`, background: rippleColor(el) });
      layer.appendChild(dot);
      el.appendChild(layer);
      dot.animate(
        [{ transform: "scale(0)", opacity: 1 }, { transform: "scale(1)", opacity: 0 }],
        { duration: 560, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" },
      ).onfinish = () => layer.remove();
    };
    document.addEventListener("pointerdown", down);
    return () => document.removeEventListener("pointerdown", down);
  }, []);
  return null;
}
