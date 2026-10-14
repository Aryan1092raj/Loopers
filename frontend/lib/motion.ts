"use client";
import { animate, stagger } from "animejs";
import { useEffect, type RefObject } from "react";

/** Users who ask the OS for less motion get none. */
export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Fade and lift the element's direct children in, one after another, whenever `key` changes. */
export function useStaggerIn(ref: RefObject<HTMLElement | null>, key: unknown = null) {
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || !el.children.length) return;
    const a = animate(Array.from(el.children), {
      opacity: [0, 1],
      y: [12, 0],
      delay: stagger(45),
      duration: 380,
      ease: "out(3)",
    });
    return () => { a.revert(); };
  }, [ref, key]);
}

/** Scale-and-fade entrance for a single element (overlays, panels). */
export function usePopIn(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion()) return;
    const a = animate(el, {
      opacity: [0, 1], scale: [0.97, 1], duration: 260, ease: "out(3)",
      // anime.js can settle a hair short of scale(1); that residue blurs small text/icons
      // (e.g. React Flow's zoom controls) for the rest of the overlay's life, so drop it explicitly.
      onComplete: () => { el.style.transform = ""; },
    });
    return () => { a.revert(); };
  }, [ref]);
}
