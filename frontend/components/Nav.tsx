"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import StatusDot from "./StatusDot";

const LINKS = [
  ["/upload", "Upload"],
  ["/study", "Study"],
  ["/graph", "Graph"],
  ["/quiz", "Quiz"],
  ["/dashboard", "Dashboard"],
  ["/onboarding", "Onboarding"],
] as const;

export default function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="mx-auto flex w-full max-w-[96rem] flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm sm:px-4">
      <Link href="/" className="font-semibold">Loopers</Link>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {LINKS.map(([href, label]) => (
          <li key={href}>
            <Link
              href={href}
              aria-current={path === href ? "page" : undefined}
              className={`rounded px-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${path === href ? "font-semibold underline" : "text-neutral-600 hover:text-neutral-900"}`}
            >
              {label}
            </Link>
          </li>
        ))}
      </ul>
      <span className="ml-auto"><StatusDot /></span>
    </nav>
  );
}
